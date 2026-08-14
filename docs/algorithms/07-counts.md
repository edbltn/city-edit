---
title: Displayed counts
description: What a "voter" is, what a "block" is, and why the honest number sometimes has to come from the server.
sources:
  - path: server/vote_identity.py
    anchors: [counting_identity, COUNT_BY, SQL_IDENTITY]
  - path: server/database.py
    anchors: [count_unique_voters_for_edges, count_unique_voters_for_edge_sets, list_maps]
  - path: server/block_votes.py
    anchors: [bd_key, bagg_key, pack_block_field, apply_block_delta, build_block_arrays, read_block_vt_counts]
  - path: client-react/src/components/GraphLayer/routeVoteRows.ts
    anchors: [routeVotesQuery, useRouteVoteRows, primeRouteVoteRows, ROUTE_VOTES_HOVER_DELAY_MS]
  - path: client-react/src/components/GraphLayer/routeVotesCache.ts
    anchors: [loadRouteVoteRows, loadRouteVoteRowsBatch, cachedRouteVoteRows]
  - path: client-react/src/utils/blockSelection.ts
    anchors: [selectionVoteRows, selectionCoverage, SelectionCoverage]
  - path: client-react/src/components/GraphLayer/graphTopology.ts
    anchors: [touchedBlockKeys, edgesOfBlockKey, blockKeyOf]
---

# Displayed counts

## Why it exists

Every number on a card is a claim about people, and the data model does not
store people — it stores **vote rows**, one per (map, edge, vote type, device).
Between those two things sit three layers of fan-out, and each one multiplies.

Getting this wrong is not a rendering bug. A corridor card that says **432
voters** when 36 people voted is the app misrepresenting public support, which
is the one thing it exists to represent accurately.

## The three grains, and where each inflates

```
EDGE      one row per (edge, vote type, device, direction)
          A route cast writes a row on EVERY edge of EVERY block the corridor
          covers. One person, hundreds of rows.
                │
                │  dedupe per (block, vote type, direction, IDENTITY)
                ▼
BLOCK     block_vote_types — "how many distinct voters asked for this type on
          this block". Correct FOR ONE BLOCK.
                │
                │  sum across a selection's blocks   ← THE TRAP
                ▼
SELECTION Summing block counts counts one person ONCE PER BLOCK.
          Across a 12-block corridor, 36 people read as 432.
                │
                │  COUNT(DISTINCT identity) over the whole edge set
                ▼
PEOPLE    /api/route-votes — the honest number.
```

**IDENTITY is not `device_id`.** Rows are OWNED by the device that cast them —
that is what decides whether a press is a re-vote and what an unvote may
delete — but they are COUNTED on the identity `server/vote_identity.py`
returns, which is the IP hash (falling back to the device when there is no IP).
The two split apart in the case that matters most: a QR sticker scanned on a
phone whose localStorage does not survive the visit mints a fresh `device_id`
per page load, and every one of them could vote the same block again. See
`vote_identity.py` for the full reasoning and the NAT trade-off it accepts.

The dedup at the block level is real and useful: it is what makes the
one-direction-per-block invariant enforceable and what block heat is built on.
It simply does not compose by addition, and nothing in the type system says so.

## Pseudocode

### "voters" — a single block or a small selection

```
selectionVoteRows(data, edgeIds):
    keys = touchedBlockKeys(data, edgeIds)
    for each key:
        if it is a real block:  add its block_vote_types breakdown
        else:                   add that edge's own per-edge breakdown
                                # singleton fallback: mixed selections still total
    return rows sorted by (up - down) DESC
    # returns null when the map has no block layer, so callers fall back
```

Computed locally, instantly, from data already in memory. Correct for one block.
Over-counts across many — knowingly.

### "voters" — a route or corridor

```
useRouteVoteRows(edgeIds):
    key = map + the (capped) edge set
    if the session cache has it:  return immediately
    otherwise:
        rows = null                         # callers render PENDING, not a guess
        POST /api/route-votes { map, edge_ids }
            -> SELECT vote_type_id, direction, COUNT(DISTINCT <identity>)
               FROM edge_votes
               WHERE map_slug = ? AND edge_id = ANY(?)
               GROUP BY vote_type_id, direction
        cache and return
```

Three deliberate choices here:

- **`null` means pending, and pending renders as a glyph** (`PENDING_GLYPH`), not
  as the local sum. A number that is wrong for 300 ms and then silently changes
  is worse than a number that visibly hasn't arrived — the first teaches people
  to distrust every number on the card.
- **POST, not GET.** A corridor's block-edge union routinely exceeds URL length
  limits.
- **One hook, one cache, three consumers.** The route-summary card, the hovered
  corridor, and the corridor's floating map LABEL share it, so they can never
  disagree. All three used to.

The label is the third consumer because it is the number most people read and
the only one nobody clicks to check. It cannot use the hook (it needs every
drawn corridor at once, not the one under the cursor), so it primes the same
cache in bulk:

```
primeRouteVoteRows(queries):            # queries = routeVotesQuery per corridor
    POST /api/route-votes { map, sets: [[edge ids], …] }
        -> ONE query: unnest(idx[], edge_id[]) JOIN edge_votes
                      GROUP BY idx, vote_type_id, direction
        -> { results: [{ rows } | { rows: null }, …] }   # null = over budget
    remember each set's rows under ITS key
```

Two rules keep this honest. The ids and the key come from `routeVotesQuery` —
the ONE normalization — so the label's key for a corridor is byte-identical to
the key the card computes when it opens, and the card then finds the label's own
rows already cached rather than asking a second time. And a set the server
declined to count answers `null`, never `[]`: nothing is cached, and the label
draws its claim with NO number rather than a confident `+0`.

### "blocks" — coverage

```
selectionCoverage(data, edgeIds):
    total   = number of blocks (or segments) the selection touches
    byLabel = for each vote type, how many of those units hold >= 1 vote
              of that type, in either direction
    # one unit counts ONCE per label, however many of its edges carry it
    # a [type, 0, 0] entry is a type whose votes cancelled — present in the
    #   breakdown, but nobody's vote stands there, so it doesn't count
```

Coverage is the **reach** axis, and it exists because the voter count says
nothing about it: 36 voters bunched on one corner of a 12-block corridor and 36
spread along all of it produce identical vote rows. "Covers 12 blocks" and "8/12"
per type are what distinguish them.

The unit is a **block** on maps that have a block layer and a **segment**
otherwise — and the label on screen changes to match (`coverageUnit`), because
silently changing what a number counts is how you get numbers nobody believes.

### "votes" — a map card

`COUNT(*)` over that map's `edge_votes` rows. This is the row count, with all the
fan-out above baked in. It is used for the landing-page ranking
([dossier 06](06-ranking.md)) and shown as a headline figure — the weakest number
in the app, and the one most worth fixing.

## Tuning knobs

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `ROUTE_VOTES_HOVER_DELAY_MS` | `200` | `routeVoteRows.ts` | How long a corridor must stay hovered before its distinct-voter query fires. Lower it and sweeping the mouse across a dense map fires a query per diamond. |
| `ROUTE_VOTES_MAX_SETS` | `32` | `server/app.py` | How many corridors one batch may count. Below the drawn-corridor limit and the extras go unlabelled (`rows: null`). |
| `ROUTE_VOTES_BATCH_EDGE_CAP` | `60000` | `server/app.py` | Total edges one batch may scan. The budget is spent in the order the client sends (strongest corridor first), and what it couldn't afford is logged, not silently zeroed. |

`ROUTE_VOTES_EDGE_CAP` (`server/app.py`) bounds the edge set one request may ask
about; the client caps its cache key at the same list.

The label batch runs on the CORRIDORS' cadence, not the votes' — it is
deliberately not wired to `votesVersion`. Any vote that moves one of these
numbers also moves that corridor's score, which republishes `routeProposals` on
the next recompute and re-runs the batch; putting it on every vote tick would
park a 40k-edge `COUNT(DISTINCT)` behind each one.

## Invariants

- **Deduped per identity at the block grain.** One voter casting a
  type/direction on many edges of one block counts once for that block, and
  devices sharing an identity collapse into that one count.
- **One direction per block per (device, type).** Casting is clear-then-cast, so a
  device can never hold both an up and a down on the same block for the same
  type — see [three-layer-model.md](../three-layer-model.md) §4. Note this is a
  per-DEVICE invariant: two people behind one NAT share a counting identity and
  may legitimately hold opposite directions on a block, which is a disagreement
  and not corruption.
- **Ownership and counting never merge.** `device_id` owns a row; the counting
  identity aggregates it. Any code that unvotes, reads "my votes", or decides
  what a press means uses the device — using the identity there would let one
  person retract a stranger's vote.
- **Distinct-voter numbers never come from local sums.** If the server hasn't
  answered, the UI shows pending.
- **Counts and coverage share a regime.** Both pick block-vs-edge the same way,
  so a card cannot report counts at one grain and coverage at another.
- **The cache is keyed by the edge set**, so the same corridor resolves to the
  same rows for every consumer in the session — and every consumer normalizes
  that set through `routeVotesQuery`, because two spellings of one corridor are
  two keys and two answers.
- **A surface that prints a corridor number reads those rows.** Not a local sum,
  not the corridor's `score`. `score` (Σ per-edge nets) is what GROWS and RANKS
  a corridor and is systematically bigger than the number of people; it must
  never reach a user's eye as a count.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| A 12-block corridor's card read **432 voters** for 36 people | Local per-block sums added the same device once per block | `/api/route-votes` distinct-device counts, via `useRouteVoteRows` |
| The number silently corrected itself ~300 ms after appearing | The card rendered the inflated local sum, then swapped in server truth | `null` → `PENDING_GLYPH`; never substitute a guess |
| The hover card and the summary card showed different totals | Two independent code paths | One hook, one shared cache |
| A corridor's map label read **+59** while its own card read **+1** (nyc-tactical, "Pick up trash along here", 2026-08-14) | The label printed the corridor's `score` — Σ per-edge nets — so the one voter who drew a route across 59 edges was counted 59 times. Ranking wants that number; a reader does not | The label reads the distinct-voter rows, primed in bulk into the same cache the cards read (`primeRouteVoteRows`), keyed identically |
| Negative block counts on prod | Counter-vote imports wrote `-1` rows; block counts dedupe per voter, so any `-1` row showed the block negative | Flips banned on prod imports; all 308 k `-1` rows deleted |
| One iPhone became the city's **top** "Fix signal timing" proposal (2026-08-13) | Six page loads in three minutes minted six `voter_id`s — storage was not surviving the visit — so three of them counted as three separate people on one block, and each reload's "my votes" was empty, inviting the next press | Count on the IP hash, not the device (`vote_identity.py`); `bver:` carries the scheme so the aggregate rebuilds |
| A vote type appeared in coverage with nobody standing behind it | `[type, 0, 0]` entries — up and down cancelled | Skipped explicitly in `selectionCoverage` |

## Extension points

- **Distinct voters for map cards.** The headline "votes" number is a row count.
  A materialized `COUNT(DISTINCT <identity>)` per map, refreshed on write, would
  make the number mean what readers already assume it means — and would fix the
  landing-page ranking at the same time ([dossier 06](06-ranking.md)).
- **Distinct voters in heat.** Block heat sums deduped block counts, which is the
  right grain for one block and drifts for corridors. A distinct-voter heat would
  need the count precomputed server-side per block set — expensive, but the
  aggregate structures in `block_votes.py` are the natural place.
- **Neither key is a person.** Counting moved from the device to the IP hash
  because the device was fragmenting (see the failure table), but an IP is only
  a better proxy, not a right one: it over-collapses an office and
  under-collapses a phone that changes network between visits. Both limits are
  worth stating wherever these numbers are published, and no amount of
  query-writing fixes either — whether accounts exist is a product decision.
  `VOTE_COUNT_IDENTITY=device` restores the old key without a redeploy.
- **Coverage weighted by length.** Coverage counts blocks equally, so a 40 m
  block and a 280 m one contribute the same. Length-weighted coverage would be a
  truer picture of reach, at the cost of a number that is harder to explain.
