---
title: Top proposals — point-based (PBTP)
description: How a handful of square pins are chosen out of every voted edge on the map.
sources:
  - path: client-react/src/components/GraphLayer/topProposals.ts
    anchors: [selectTopProposals, computeVoteTypeWinners, dedupeWinnersByEdge, dedupeWinnersByBlock, spaceOutWinners, applyTopProposalLimit, compareWinners, shuffleKey, topLabelForEdges, edgeMidpointResolver, TOP_PROPOSAL_MIN_NET, ROUTE_PROPOSAL_MIN_NET, TOP_PROPOSALS_PER_TYPE, TOP_PROPOSAL_MIN_SPACING_M]
  - path: client-react/src/components/GraphLayer/spatialLookup.ts
    anchors: [TOP_PROPOSAL_LIMIT]
---

# Top proposals — point-based (PBTP)

## Why it exists

A busy map has tens of thousands of edges carrying votes. Showing a pin per
voted edge shows nothing. The map's headline claim — *here is what this city
wants* — rests on picking a few dozen, and picking them in a way that survives
the obvious failure: a single popular avenue does not get to be all of them.

A **PBTP** is one hot edge, drawn as a **square** pin at its midpoint. Its
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
| **In** | the block mapping, for the one-pin-per-block rule |
| **In** | `kindOf(label)` → `"route" \| "point" \| null`, and `isVisible(label)` for legend toggles |
| **In** | `salt` — a per-session shuffle seed that breaks ties reproducibly without favouring alphabetical labels |
| **Out** | up to `limit` `VoteTypeWinner`s: `{ edgeIdx, label, legendIdx, net }`, sorted by net |

## Pseudocode

```
selectTopProposals(data, salt, limit, minSpacing, kindOf, minNet, isVisible):

  1. computeVoteTypeWinners
       for each vote type in the legend:
           skip if ROUTE-kind      (it surfaces as a corridor, not a point —
                                    a route type's hot edge is a fragment)
           skip if hidden by the legend filter
           net(edge) = up - down
           keep its top TOP_PROPOSALS_PER_TYPE edges by net, where net > minNet
       # per-type, not global: a popular type can surface several distinct
       # LOCATIONS rather than only its single best edge.

  2. dedupeWinnersByEdge
       winners sharing an edge collapse to one representative (compareWinners)
       # one edge shows one indicator and occupies one slot

  3. dedupeWinnersByBlock
       at most ONE pin per block, ACROSS ALL vote types
       # blocks are the interaction grain; two pins on one block read as
       # clutter even when their types differ

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

Steps 1–2 scan the full edge list; 3–5 operate on the few dozen survivors, so the
O(n²) spacing pass is negligible even though the whole thing re-runs on every
vote.

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
| `TOP_PROPOSAL_MIN_NET` | `0` | `topProposals.ts` | The pin support floor, applied strictly (`net > floor`). Zero since 2026-08-12: a single net vote earns a pin, and the ranking plus `TOP_PROPOSAL_LIMIT` decides who keeps one. A map can raise it for itself (`topProposalMinNet`). |
| `ROUTE_PROPOSAL_MIN_NET` | `100` | `topProposals.ts` | The corridor floor, as a minimum path score — where the shared floor used to sit. Corridors keep it because a route grown from one or two votes wanders; a map's `topProposalMinNet` override still wins (`nyc-proposals` sets 0). |
| `TOP_PROPOSAL_LIMIT` | `50` | `spatialLookup.ts` | Global cap on rendered pins, applied last, by net descending. With no support floor this is the legibility control. RBTP corridors are capped separately (`DEFAULT_LIMIT`, `routeProposals.ts`). |
| `TOP_PROPOSALS_PER_TYPE` | `6` | `topProposals.ts` | How many edges per type reach the spacing step. Raise it and a popular type surfaces more distinct locations — at the cost of crowding other types out of the final `limit`. |
| `TOP_PROPOSAL_MIN_SPACING_M` | `1000` | `topProposals.ts` | Same-type non-max suppression radius, ≈ 12 NYC avenue blocks. Lower it and a hot avenue grows a stack of identical pins; raise it and a genuinely city-wide demand shows up as one pin in one neighbourhood. Tune for denser or sparser networks. |

The families no longer share one bar (2026-08-12): `createRouteProposalJob` is
called with `minRouteScore = routeProposalMinNet + 1`, which resolves to a map's
`topProposalMinNet` override when set and to `ROUTE_PROPOSAL_MIN_NET` otherwise.
Pins are ranked and capped; corridors are gated.

## Invariants

Enforced by `topProposals.test.ts`:

- **One pin per block, globally.** Not per type — across all of them.
- **Nothing below the floor.** Net ≤ `minNet` never appears, including ties at
  exactly the floor. With the default `minNet = 0` that means any positive net
  qualifies and net ≤ 0 never does.
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

## Extension points

- **Ranking by something other than raw net.** `computeVoteTypeWinners` is the
  single place net is computed. Recency weighting, distinct-voter weighting (see
  [dossier 07](07-counts.md) — net here counts vote rows, not people), or a
  confidence interval would all slot in there without touching steps 2–5.
- **Non-uniform spacing.** `spaceOutWinners` takes a flat metre radius. A density-
  aware radius (tighter downtown, looser in the outer boroughs) would be a change
  of one comparison.
- **Per-city knobs.** All three constants are module-level and NYC-calibrated;
  they are already threaded as parameters, so a per-map override is a plumbing
  change, not an algorithmic one.
