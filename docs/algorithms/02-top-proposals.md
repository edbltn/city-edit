---
title: Top proposals — point-based (PBTP)
description: How a handful of square pins are chosen out of every voted edge on the map.
sources:
  - path: client-react/src/components/GraphLayer/topProposals.ts
    anchors: [selectTopProposals, computeVoteTypeWinners, computeWinnerCandidates, dedupedBlockNetResolver, dedupeWinnersByEdge, dedupeWinnersByBlock, spaceOutWinners, applyTopProposalLimit, compareWinners, shuffleKey, topLabelForEdges, edgeMidpointResolver, TOP_PROPOSAL_MIN_NET, ROUTE_PROPOSAL_MIN_NET, TOP_PROPOSALS_PER_TYPE, TOP_PROPOSAL_MIN_SPACING_M]
  - path: client-react/src/components/GraphLayer/spatialLookup.ts
    anchors: [TOP_PROPOSAL_LIMIT]
---

# Top proposals — point-based (PBTP)

## Why it exists

A busy map has tens of thousands of edges carrying votes. Showing a pin per
voted edge shows nothing. The map's headline claim — *here is what this city
wants* — rests on picking a few dozen, and picking them in a way that survives
the obvious failure: a single popular avenue does not get to be all of them.

A **PBTP** is one hot **block**, drawn as a **square** pin at the midpoint of the
block's strongest edge. Votes are stored on edges and counted on blocks
([three-layer model](../three-layer-model.md) §2), so the ranking is over the
block's **deduped** count — how many distinct voters asked for this on this
block. The two directions of a two-way street are one proposal backed by
everyone who voted either, not two rivals splitting them; and one person routing
the length of a block is one voice, not one per edge they crossed
([dossier 07](07-counts.md)). Its
counterpart, the **RBTP** (a hot corridor, diamond pin), is
[dossier 03](03-route-proposals.md). The two families are disjoint by vote-type
kind, so the map never argues with itself about what
is important.

Selection is **pure and client-side**: a deterministic function of (topology,
vote state), re-run on every incoming vote delta. No server round-trip, no
stored ranking to invalidate.

## Inputs and outputs

| | |
|---|---|
| **In** | `vote_type_legend` (label per legend index) and `edge_vote_types` (per edge: `[legendIdx, up, down][]`) |
| **In** | `block_vote_types` + `block_vote_type_legend` — the server's DEDUPED per-(block, vote type) `[up, down]`, the ranking value. Bridged to `vote_type_legend` by label (`dedupedBlockNetResolver`) |
| **In** | `blockKeyOfEdge(edge)` — the block mapping (`graphTopology.blockKeyOf`): block identity, both for the ranking lookup and for the one-pin-per-block rule. Omitted, every edge is its own block (its singleton key), which is exactly what an unmapped edge or a blockless station network wants |
| **In** | `kindOf(label)` → `"route" \| "point" \| null`, and `isVisible(label)` for legend toggles |
| **In** | `salt` — a per-session shuffle seed that breaks ties reproducibly without favouring alphabetical labels |
| **Out** | up to `limit` `VoteTypeWinner`s: `{ edgeIdx, label, legendIdx, net }`, sorted by net. `net` is the winning BLOCK's deduped count; `edgeIdx` is the block's representative edge, which is where the pin is drawn and what a click selects |

## Pseudocode

```
selectTopProposals(data, salt, limit, minSpacing, kindOf, minNet, isVisible):

  1. computeVoteTypeWinners            # ONE PASS over the edge table
       for each vote type in the legend:
           skip if ROUTE-kind      (it surfaces as a corridor, not a point —
                                    a route type's hot edge is a fragment)
           skip if hidden by the legend filter
           for each edge carrying that type:            # WHERE, not how much
               block = blockKeyOfEdge(edge)
               block.pinAt = the block's strongest edge for this type
                             (strictly-greater ⇒ ties keep the lowest edge id)
               block.edgeSum += up - down               # fallback value only
           for each block reached:                      # HOW MUCH
               block.net = dedupedBlockNet(block, type) # distinct DEVICES
                        ?? block.edgeSum                # only when there is no
                                                        # deduped row to read
           keep its top TOP_PROPOSALS_PER_TYPE blocks by net, where net > minNet
       # per-type, not global: a popular type can surface several distinct
       # LOCATIONS rather than only its single best block.

  2. dedupeWinnersByEdge
       winners sharing an edge collapse to one representative (compareWinners)
       # one edge shows one indicator and occupies one slot. Still needed after
       # step 1: two TYPES can name the same representative edge.

  3. dedupeWinnersByBlock
       at most ONE pin per block, ACROSS ALL vote types
       # step 1 gives each type one candidate per block; this is the cross-TYPE
       # half of the same rule — two pins on one block read as clutter even
       # when their types differ. Same blockKeyOfEdge as step 1, or the two
       # disagree about what a block is.

  4. spaceOutWinners            # greedy per-type non-max suppression
       walk candidates strongest-first
       keep one only if no STRONGER SAME-TYPE winner already sits
           within TOP_PROPOSAL_MIN_SPACING_M
       # collapses a hot corridor — whose top edges are adjacent segments —
       # to a single pin instead of a stack of identical ones.
       # Same-type spacing is deliberately WIDER than the cross-type block
       # grain: identical pins carry zero extra information nearby, while
       # different-type pins are distinct proposals.

  5. applyTopProposalLimit
       sort by net (compareWinners tiebreak), cap at `limit`
```

Step 1 scans the full edge list once, tallying into a map per vote type; 2–5
operate on the few dozen survivors, so the O(n²) spacing pass is negligible even
though the whole thing re-runs on every vote.

**The ranking value is the DEDUPED block count, not a sum of edge nets.** A cast
writes its direction onto every selected edge, so one person routing lengthwise
through a block leaves +1 on each edge they covered. Summing those would rank a
block by *how many edges somebody selected* — the exact over-count
[dossier 07](07-counts.md) is about. The honest number already exists:
`block_vote_types`, which the server maintains by counting distinct voters per
(block, vote type, direction) inside the vote lock (`server/block_votes.py`,
`build_block_arrays` / `apply_block_delta`), which the heatmap already colours by
([dossier 05](05-heat-coloring.md)), and which `voteApply.applyBlockCounts`
patches live from each delta. So:

| Concern | Source |
|---|---|
| **Which blocks exist**, and which (block, type) pairs carry votes | the edge scan over `edge_vote_types` |
| **Block identity** | `blockKeyOf` — the same mapping step 3 dedupes on |
| **How much support a block has** (the ranking value, and what the floor is applied to) | `block_vote_types` — deduped, per voter (`server/vote_identity.py`) |
| **Where the pin goes** | the edge scan again: the block's strongest edge for that type. The deduped arrays contain no edges and cannot answer this |

The two legends are **different index spaces** — `block_vote_type_legend` is
built from whichever types have block rows, in their own order — so they are
bridged by **label**, once, into a lookup array (`dedupedBlockNetResolver`),
never per edge.

**The edge sum survives only as a fallback**, and it is the one place the
over-counting semantics remain: when the deduped arrays are absent (station
networks, maps with no block layer), when the key is a singleton (an unmapped
edge is not a block and has no row), or when a block has no deduped row for that
type yet — the block arrays lag an optimistic cast by one round trip, and a
caster has to see their own vote land.

**Tiebreaks are seeded, not alphabetical.** `compareWinners` orders by net, then
by `shuffleKey(label, salt)`. Two proposals tied at 140 votes would otherwise
resolve by string comparison forever, and "Add a bike lane" would outrank
"Widen the sidewalk" on every map in the world for no reason.

**The two families divide by kind.** Every vote type carries a route/point
`pointType`, declared where the type is authored or recorded by the cast that
created it. Point-kind types surface only here; route-kind types only as RBTPs.
A legacy type of unknown kind stays eligible for both. Same-type PBTPs that an
RBTP's blocks already cover are dropped downstream by `dropPointsCoveredByRoutes`
— see [dossier 03](03-route-proposals.md).

## Tuning knobs

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `TOP_PROPOSAL_MIN_NET` | `0` | `topProposals.ts` | The pin support floor, applied strictly (`net > floor`) to a BLOCK's DEDUPED net — so it now reads as "more than this many people", not "more than this many vote rows". Zero since 2026-08-12: a single net vote earns a pin, and the ranking plus `TOP_PROPOSAL_LIMIT` decides who keeps one. A map can raise it for itself (`topProposalMinNet`); a raised floor is meaningfully HARDER to clear than it was at edge grain, where a lone route-length cast could clear it alone. |
| `ROUTE_PROPOSAL_MIN_NET` | `100` | `topProposals.ts` | The corridor floor, as a minimum path score — where the shared floor used to sit. Corridors keep it because a route grown from one or two votes wanders; a map's `topProposalMinNet` override still wins (`nyc-proposals` sets 0). |
| `TOP_PROPOSAL_LIMIT` | `50` | `spatialLookup.ts` | Global cap on rendered pins, applied last, by net descending. With no support floor this is the legibility control. RBTP corridors are capped separately (`DEFAULT_LIMIT`, `routeProposals.ts`). |
| `TOP_PROPOSALS_PER_TYPE` | `5` | `topProposals.ts` | How many BLOCKS per type reach the spacing step. Raise it and a popular type surfaces more distinct locations — at the cost of crowding other types out of the final `limit`. Dropped from 6 with the move to block grain (2026-08-12): the slot that used to be eaten by a street's second direction now doesn't exist. |
| `TOP_PROPOSAL_MIN_SPACING_M` | `1000` | `topProposals.ts` | Same-type non-max suppression radius, ≈ 12 NYC avenue blocks. Lower it and a hot avenue grows a stack of identical pins; raise it and a genuinely city-wide demand shows up as one pin in one neighbourhood. Tune for denser or sparser networks. |

The families no longer share one bar (2026-08-12): `createRouteProposalJob` is
called with `minRouteScore = routeProposalMinNet + 1`, which resolves to a map's
`topProposalMinNet` override when set and to `ROUTE_PROPOSAL_MIN_NET` otherwise.
Pins are ranked and capped; corridors are gated.

## Invariants

Enforced by `topProposals.test.ts`:

- **One pin per block, globally.** Not per type — across all of them. Step 1
  enforces it within a type; step 3 across types.
- **Ranking is at block grain, by people.** A block's score is its deduped
  `block_vote_types` net, so one voter covering four edges of a block never
  outranks a block three separate voters asked for. What counts as one voter is
  `server/vote_identity.py`, NOT the device that owns the row — on 2026-08-13 one
  iPhone whose storage kept resetting held the map's top-ranked PBTP with a
  count of 3. The edge sum is used only where no deduped row exists.
- **The pin lands on the block's strongest edge**, chosen without the salt:
  same votes → same location, on every device and every reload. Ties go to the
  lowest edge id. This stays edge-derived — the deduped arrays have no edges in
  them.
- **Nothing below the floor.** Block net ≤ `minNet` never appears, including
  ties at exactly the floor. With the default `minNet = 0` that means any
  positive net qualifies and net ≤ 0 never does.
- **No route-kind pins.** A route-kind type never yields a PBTP, whatever its net.
- **Determinism.** Same (topology, votes, salt) → byte-identical winners, in the
  same order. This is what lets selection re-run freely on every delta.
- **Hidden types are invisible to ranking**, not merely to rendering: with a type
  toggled off, the remaining pins are re-selected as if it never existed.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| Three identical pins stacked on one avenue | Only per-edge dedup existed; a corridor's top edges are adjacent segments | `spaceOutWinners`, now at 1000 m ([2026-07-08](https://github.com/edbltn/city-edit/blob/main/changelog/2026-07-08-top-proposal-kinds.html)) |
| Route-shaped demands rendered as scattered points | Both families drew from the same pool | `pointType` kind split, PBTP/RBTP disjoint ([2026-07-08](https://github.com/edbltn/city-edit/blob/main/changelog/2026-07-08-top-proposal-kinds.html)) |
| Pins backed by a handful of votes read as citywide consensus | No support floor | `TOP_PROPOSAL_MIN_NET` = 100, with a per-map override ([2026-07-30](https://github.com/edbltn/city-edit/blob/main/changelog/index.html)) — **superseded 2026-08-12**: the floor moved to 0 for pins and stayed at 100 for corridors, so a quiet map shows its proposals and rank decides the rest |
| A pin and a corridor claimed the same street | Independent selection | `dropPointsCoveredByRoutes` |
| A two-way street's support was split in half, so a genuinely popular block lost to a single hotter edge — and its two directions ate two of the type's candidate slots before `dedupeWinnersByBlock` threw one away | Ranking was at EDGE grain while votes are counted at BLOCK grain (three-layer model §2); dedup ran after selection instead of before it | Step 1 tallies to blocks and keeps the block's strongest edge as the pin's home; `TOP_PROPOSALS_PER_TYPE` 6 → 5 (2026-08-12) |
| Long blocks outranked popular ones: whoever routed the furthest through a block won it | The first pass at block grain scored a block by Σ of its edge nets, which counts one person once per edge their cast covered — the over-count of [dossier 07](07-counts.md) | Rank by the deduped `block_vote_types` net (`dedupedBlockNetResolver`); the edge scan kept only for block enumeration and the representative edge (2026-08-12) |

## Extension points

- **Ranking by something other than the deduped net.** `dedupedBlockNetResolver`
  is the single place a block's score comes from. Recency weighting or a
  confidence interval would replace that one function without touching the scan
  or steps 2–5. (Distinct-voter weighting is no longer an extension point — it
  is what the deduped arrays already are.)
- **A different representative edge.** The pin currently sits on the block's
  strongest edge for the winning type. The block's geometric centre, or its
  longest edge, would be a change to one comparison in the same accumulation —
  everything downstream only reads `edgeIdx`.
- **Non-uniform spacing.** `spaceOutWinners` takes a flat metre radius. A density-
  aware radius (tighter downtown, looser in the outer boroughs) would be a change
  of one comparison.
- **Per-city knobs.** All three constants are module-level and NYC-calibrated;
  they are already threaded as parameters, so a per-map override is a plumbing
  change, not an algorithmic one.
