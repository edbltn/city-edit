---
title: Sorting and ranking
description: Every ordered list in the app — maps, proposal rows, the legend — and the rule behind each.
sources:
  - path: server/database.py
    anchors: [list_maps, count_unique_voters_for_edges, _MAP_SELECT, _MAP_COLUMNS]
  - path: client-react/src/utils/blockSelection.ts
    anchors: [selectionVoteRows, selectionCoverage]
  - path: client-react/src/components/GraphLayer/graphTopology.ts
    anchors: [touchedBlockKeys, edgesOfBlockKey, blockKeyOf]
  - path: client-react/src/map/voteTypeRegistry.ts
    anchors: [buildVoteTypeLegend, VoteTypeLegendEntry]
---

# Sorting and ranking

## Why it exists

There is no single ranking function in City Edit — there are five ordered lists,
each answering a different question, and the mistakes are always the same two:
ranking by a number that means something else, and tie-breaking alphabetically
so the same label wins everywhere forever.

This dossier collects all of them in one place, because they are individually
trivial and collectively easy to get subtly inconsistent.

## The five orderings

### 1. Maps on the landing page

```
ORDER BY vote_count DESC, LOWER(m.name) ASC        -- server, list_maps
```

Ranked **entirely by the server**; the client renders the list as received and
never re-sorts. Search filters (substring match over a precomputed haystack) but
does not reorder — so a search result's position still carries the ranking
signal.

**`vote_count` is a row count, not a people count** (`COUNT(*)` over
`edge_votes`). A route cast writes one row per edge of every block it covers, so
a map with a few long-corridor votes can outrank a map with many single-block
ones. This is the most questionable ranking in the app; see Extension points.

The name tiebreak is `LOWER(name)`, so ordering doesn't depend on capitalisation.

### 2. Proposal rows inside a selection modal

```
sort by (up - down) DESC          -- selectionVoteRows / voteRowsForEdges
```

Net support, descending. Rows come from the **block-grained** deduped counts
(`block_vote_types`) when the map has a block layer, and fall back to per-edge
breakdowns otherwise — the two regimes are chosen together with
`selectionCoverage` so counts and coverage always describe the same units.

For a **route** selection the rows come from `/api/route-votes` instead, which
sorts server-side by `down - up` ascending — the same net-descending order,
computed over distinct devices rather than summed per block. Why that difference
matters is [dossier 07](07-counts.md).

### 3. Top proposals — points

Net descending, with a **seeded** tiebreak (`compareWinners` / `shuffleKey`),
after four filtering passes. See [dossier 02](02-top-proposals.md).

### 4. Top proposals — routes

Corridor score descending, with a per-vote-type diversity quota
(`MAX_PER_TYPE`) applied before the cap and leftover slots backfilled by pure
score. Ties break on proposal id, which is content-derived and therefore stable.
See [dossier 03](03-route-proposals.md).

### 5. The vote-type legend

Not ranked by popularity at all — ranked by **provenance**, then partitioned by
castability:

```
discovery order:
    1. types authored on the map (cfg.voteTypes), in their authored order
    2. searchable preset types that have votes on this map
    3. any other label that has votes on this map
    4. labels this session cast
then: stable partition — castable in the current mode first
```

Authored order comes first because a map's author chose that order and it
carries meaning ("the four things we're asking about"). Sorting the legend by
net would make it jump around under the user as votes arrive, which is exactly
what a legend must not do.

## Tuning knobs

Ranking has no numeric constants of its own — the knobs that shape the two
proposal rankings live with their algorithms
([02](02-top-proposals.md), [03](03-route-proposals.md)). The rules above are
the whole surface.

## Invariants

- **The server ranks maps; the client never re-sorts them.** One ranking, one
  place. Search filters only.
- **Every proposal-row list is net-descending.** Whichever source computed it.
- **Ties never break alphabetically where a human authored the labels.** PBTPs
  use a session-seeded shuffle; maps use `LOWER(name)` only as a last resort
  after vote count; routes use a content-derived id.
- **Counts and coverage share a regime.** `selectionVoteRows` and
  `selectionCoverage` make the same block-vs-edge choice, so a row can never
  report counts at one grain and coverage at another.
- **The legend is stable under incoming votes.** Its order depends on
  provenance and castability, never on live totals.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| Broadway's real corridor ranked #87 | Bulk-imported vote types outscore organic ones by orders of magnitude, and the ranked list was pure score | `MAX_PER_TYPE` diversity quota |
| The same proposal always won every tie | Alphabetical tiebreak | Session-seeded `shuffleKey` |
| A route card's rows disagreed with the corridor's hover card | One summed local per-block counts, the other asked the server | Both now go through one cache-shared hook ([dossier 07](07-counts.md)) |

## Extension points

- **Rank maps by people, not rows.** `COUNT(DISTINCT device_id)` is the honest
  number and the query already exists in spirit (`count_unique_voters_for_edges`).
  It is more expensive, which is why it isn't used on a page that loads for every
  visitor — a materialized per-map counter refreshed on write would fix both.
- **Recency in the map ranking.** A map that got 5 000 votes during a campaign
  two years ago outranks a live one forever. A decayed count, or a "trending"
  secondary list, would surface active maps.
- **Locality.** The landing page shows every city's maps in one global order. Any
  geographic signal (viewer location, the map's bbox) would make the top of that
  list mean something to the person reading it.
- **Legend ordering as a map-author choice.** Authored order is currently the
  only option; a map could plausibly opt into net-descending for an analysis
  view, as long as it is stable within a session.
