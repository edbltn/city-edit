---
title: Top proposals — route-based (RBTP)
description: How the vote graph is explored to grow a hot corridor, and what stops it.
sources:
  - path: client-react/src/components/GraphLayer/routeProposals.ts
    anchors: [createRouteProposalJob, computeRouteProposals, growCorridor, peelCorridors, connectedComponents, buildTypeAdj, netsByType, groupBlocks, dedupeRoutes, makeSegmentShortestCheck, makeSegmentRecoveryCheck, boundedAStar, routeLengthBudgetM, isRouteCovered, dropPointsCoveredByRoutes, chooseAnchorOrder, MIN_NET, PEEL_MAX_PATHS, PEEL_DOMINANCE, MAX_GHOST_WAYPOINTS, RECOVERY_MIN_BLOCK_COVERAGE, MAX_RECOVERY_CHECKS, MAX_PRUNE_PASSES, MIN_ROUTE_SCORE, MIN_ROUTE_EDGES, MIN_ROUTE_BLOCKS, DEFAULT_LIMIT, MAX_PER_TYPE, DEFAULT_JACCARD, ROUTE_LENGTH_BASE_M, ROUTE_LENGTH_PER_SQRT_SCORE_M, ROUTE_LENGTH_MAX_M, ROUTE_CHECK_MAX_POPS]
---

# Top proposals — route-based (RBTP)

## Why it exists

Some demands are not a place, they are a **line**: a protected bike lane the
length of an avenue, a bus corridor, a greenway. Those votes land on hundreds of
separate edges, and a point-based selection ([dossier 02](02-top-proposals.md))
renders them as scattered confetti — the one thing they are not.

An RBTP is a **corridor**: a simple path through the vote graph, expressed in
block units, with two anchors and up to three intermediate waypoints. It draws as
a **diamond** pin at its middle edge.

This is the hardest algorithm in the codebase, because "find the hot corridor"
is a search problem with no natural stopping point, and because **the answer has
to survive being turned into a URL**. When you select a proposal, its waypoints
go into the share link. The link must still route back into the same corridor
months later, after the proposal itself has retired. So growth cannot simply
follow votes — it must only accept extensions the router would reproduce.

Like PBTPs, this runs **client-side, purely, deterministically**, recomputed as
vote deltas arrive.

## Inputs and outputs

| | |
|---|---|
| **In** | topology (`edge_ends`, node coords, block mapping), node adjacency, `edge_vote_types` |
| **In** | `kindOf` / `isVisible` filters, and `minRouteScore` (the shared support floor from dossier 02) |
| **Out** | up to `DEFAULT_LIMIT` `RouteProposal`s: `{ id, label, score, edgeIds, blocks, blockEdgeIds, anchors, waypointNodes, segments }` |

`edgeIds` is the **path**; `blockEdgeIds` is the union of every edge of every
block the path touches — that union is what highlights on hover and what a vote
is cast on. `waypointNodes` is what goes into the URL.

## Pseudocode

### The pipeline, per route-kind vote type

```
step(voteType):
    nets      = net(up - down) per edge, for this type only
    typeAdj   = arcs where net >= MIN_NET                # the net-positive subgraph
    for each connected component of typeAdj:
        if total component weight < minRouteScore: skip  # cheap early exit —
                                                         # no corridor can
                                                         # outscore its component
        for each corridor peeled out of the component:   # peelCorridors
            gate: score >= minRouteScore
                  edges >= MIN_ROUTE_EDGES
                  blocks >= MIN_ROUTE_BLOCKS             # shorter reads as a
                                                         # point, and its votes
                                                         # still surface as a PBTP
            project the path onto blocks (groupBlocks)
            emit a RouteProposal
    return dedupeRoutes(proposals)                       # same-type Jaccard

finish(perType):
    rank by score, admit at most MAX_PER_TYPE per vote type,
    backfill leftover slots by pure score, cap at DEFAULT_LIMIT
```

`peelCorridors` grows a corridor, removes its edges, and grows again — up to
`PEEL_MAX_PATHS` times, stopping when a corridor falls below `PEEL_DOMINANCE ×`
the component's first. Components *localize* the search; peeling separates the
genuinely parallel corridors inside one component from weak dead-end residue.

### How the map is explored — `growCorridor`

This is the core. A corridor grows outward from the heaviest arc, at **both
ends**, taking the heaviest available extension each time:

```
growCorridor(adj, lengthOf, isSegmentShortest, recoversCorridor):

    seed = the heaviest arc in the subgraph          # ties: lowest node id,
                                                     # then ascending edge id
    path = [seed]; ghosts = {}

    loop:
      ── extendOnce ──────────────────────────────────────────────
      candidates = arcs off BOTH tips, excluding nodes already on the
                   path and arcs previously rejected
      sort heaviest first (tie: lowest edge id)

      for each candidate:
          if totalLength + candLength > budgetOf(weight + candWeight):
              skip        # not rejected — retried next round, when more
                          # accumulated support has bought more budget

          # Is the corridor still routing-consistent if we take this?
          consistent = isSegmentShortest(openSegment.bound, cand.node,
                                         openSegmentLength + candLength)
          if not consistent:
              # Shortest-ness is a PESSIMISTIC proxy: an alternative two
              # metres shorter along the same blocks fails it. Ask a second,
              # honest oracle before spending a pin.
              consistent = recoversCorridor(...)   # route it, compare BLOCKS

          if not consistent and ghosts is full:
              reject this arc; try the next candidate

          take the extension
          if not consistent:
              pin the PREVIOUS tip as a ghost waypoint
              if ghosts now full: growth is "spent"
          break

      ── when growth stalls or spends its last pin ───────────────
      pruneGhosts():
          re-test every ghost against the stretch between its NEIGHBOURING
          waypoints, outward-in, so each test sees the survivors on its left.
          A ghost pinned early often turns unnecessary once the far end has
          moved on — routing to the more distant target no longer takes the
          shortcut that forced the pin.
          Every ghost dropped = one waypoint off the shared URL, and one pin
          handed back to growth, so the corridor reaches further on the same
          budget. Dropping any also un-rejects the arcs that were refused for
          want of a pin.
      if nothing was dropped: stop. Otherwise resume growth.

    final prune pass on a RESERVED allowance the growth loop could not spend
    split the path at its ghosts into `segments`; return path + waypoints
```

Three ideas do all the work here:

1. **Routing-consistent extension.** An extension is only accepted if the open
   segment (the stretch between the tip and the nearest inner waypoint) remains
   what the router would return. Where it wouldn't, growth *spends a ghost pin*
   to force it. This replaced an older scheme of straightness-splitting plus
   budget-window trimming, and does that job as a side effect: a corridor can
   only get so roundabout before it runs out of pins.
2. **Two oracles, not one.** `makeSegmentShortestCheck` (a bounded A\*) is cheap
   and pessimistic. `makeSegmentRecoveryCheck` actually routes the stretch and
   compares *blocks*, which is the grain that matters for display and voting.
   The expensive one is rationed by `MAX_RECOVERY_CHECKS`.
3. **Pins are reclaimable.** A ghost is a cost — a URL waypoint and a lost unit
   of growth budget — so growth keeps re-litigating them. This is why the loop is
   grow → stall → prune → grow rather than a single pass.

### Length budget

```
budget(score) = min(ROUTE_LENGTH_MAX_M,
                    ROUTE_LENGTH_BASE_M + ROUTE_LENGTH_PER_SQRT_SCORE_M * √score)
```

A corridor **earns** length with votes, sublinearly: 4× the support buys 2× the
reach. Without this, greedy extension keeps going while any net-positive arc
exists and produces proposals that snake for miles.

## Tuning knobs

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `MIN_NET` | `1` | `routeProposals.ts` | Minimum net for an edge to enter a type's subgraph at all. Below 1 the subgraph includes contested edges and corridors wander through opposition. |
| `PEEL_MAX_PATHS` | `8` | `routeProposals.ts` | Corridors peeled from one component. Raise it and dense components dominate the ranked list. |
| `PEEL_DOMINANCE` | `0.25` | `routeProposals.ts` | A peeled corridor survives only at ≥ this fraction of its component's first. Lower it and weak dead-end residue surfaces as proposals. |
| `MAX_GHOST_WAYPOINTS` | `3` | `routeProposals.ts` | Pin budget — so a proposal carries at most 2 anchors + 3 ghosts = 5 URL waypoints. The 3rd pin ends growth: "three path modifications and we're done". Raise it and share links get long and fragile. |
| `RECOVERY_MIN_BLOCK_COVERAGE` | `0.85` | `routeProposals.ts` | Fraction of a stretch's blocks plain routing must hand back for the stretch to count as recovered. Not 1.0 — a shortcut clipping a corner off one block in twenty still reproduces the corridor for every purpose a proposal has. |
| `MAX_RECOVERY_CHECKS` | `16` | `routeProposals.ts` | Recovery-check budget per corridor (an A\* apiece, path reconstruction included). Exhausted, growth falls back to the strict check. Raise it and recompute time grows. |
| `MAX_PRUNE_PASSES` | `3` | `routeProposals.ts` | How often one corridor may re-examine its ghosts, so it can't ping-pong between pinning and pruning forever. |
| `ROUTE_CHECK_MAX_POPS` | `30000` | `routeProposals.ts` | A\* pop cap. On pathological geometry the check **fails open** (treats the corridor as shortest) to keep recompute bounded — deterministic either way. |
| `MIN_ROUTE_SCORE` | `3` | `routeProposals.ts` | Default activity gate. In the app this is overridden by `topProposalMinNet + 1` so both proposal families share one bar. |
| `MIN_ROUTE_EDGES` | `2` | `routeProposals.ts` | Minimum path edges. |
| `MIN_ROUTE_BLOCKS` | `5` | `routeProposals.ts` | Minimum blocks spanned. Anything shorter reads as a point, not a route — and nothing is lost, since its votes still surface as a PBTP. |
| `DEFAULT_LIMIT` | `20` | `routeProposals.ts` | Global cap on the ranked list. |
| `MAX_PER_TYPE` | `4` | `routeProposals.ts` | Type-diversity quota. Without it, bulk-imported types (scores in the tens of thousands) fill every slot: Broadway's net-strongest organic corridor ("Add sharrow", score 237) once ranked #87 behind 86 imported-type corridors. |
| `DEFAULT_JACCARD` | `0.5` | `routeProposals.ts` | Same-type edge-set Jaccard at/above which two routes are duplicates. Containment ≥ 0.999 also counts, so a subset never survives alongside its superset. |
| `ROUTE_LENGTH_BASE_M` | `2700` | `routeProposals.ts` | Every corridor may span at least this far, whatever its score. |
| `ROUTE_LENGTH_PER_SQRT_SCORE_M` | `660` | `routeProposals.ts` | Metres earned per √score. The √ is the point: support buys reach sublinearly. |
| `ROUTE_LENGTH_MAX_M` | `10500` | `routeProposals.ts` | Ceiling regardless of support. |

## Invariants

Enforced by `routeProposals.test.ts` (988 lines — read it, it is the real spec)
and `routeProposals.perf.test.ts`:

- **Determinism.** No randomness, no clock. Every iteration order and tiebreak is
  by ascending edge/node id; the A\* breaks heap ties by node id. Same
  (topology, vote state) → byte-identical proposals, ids and order, on every
  client. This is load-bearing: proposal ids are shared in URLs.
- **Simple paths.** A corridor never revisits a node.
- **Routing reproducibility.** For every consecutive waypoint pair, routing
  between them returns the corridor's stretch — either because it was shortest,
  or because a ghost pins it.
- **≤ 5 URL waypoints.** 2 anchors + `MAX_GHOST_WAYPOINTS`.
- **Slicing changes nothing.** `createRouteProposalJob` run one type per idle
  slice is byte-identical to `computeRouteProposals` run in one pass — the latter
  is the reference entry point for tests.
- **Family disjointness.** Point-kind types never reach this pipeline; same-type
  PBTPs covered by an RBTP's blocks are dropped (`dropPointsCoveredByRoutes`).

## Performance

The clustering walks the whole vote graph — hundreds of milliseconds on the NYC
bike map even after the sparse-nets rework — so in the app it runs as a **sliced,
minute-batched job**: one vote type per idle slice, yielding to input between
slices. Votes only dirty-mark; a new recompute cancels any in-flight job, since a
half-built result would mix two vote states. The component-weight early exit
prunes almost everything before growth (and its A\* checks) ever starts.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| Selecting a proposal produced a route that didn't follow it | Growth followed votes; the router didn't agree | Routing-consistent extension + ghost waypoints ([2026-07-30](https://github.com/edbltn/city-edit/blob/main/changelog/index.html)) |
| Corridors snaked for miles | Greedy extension with no stopping rule | `routeLengthBudgetM` — support-earned reach |
| Corridors looped back on themselves | Shape-blind growth | Superseded: `splitLoopyPath`/`capPath` were **deleted** when routing-consistency subsumed them |
| Broadway's real corridor ranked #87 | Bulk-imported types outscore organic ones by orders of magnitude | `MAX_PER_TYPE` diversity quota ([2026-07-15](https://github.com/edbltn/city-edit/blob/main/changelog/index.html)) |
| Recompute froze the UI on every vote | Whole-graph walk, synchronously | Minute-batched, idle-sliced job ([2026-07-09](https://github.com/edbltn/city-edit/blob/main/changelog/2026-07-09-vote-zoom-batching.html)) |
| A 7.75 km hairpin across the East River | A geocode stranded an anchor on the ESCR-severed esplanade, so the shortest path went the long way | `DETOUR_RATIO_MAX` demotion at import time — a data problem, not a growth problem |

## Extension points

- **Weights other than net.** `buildTypeAdj` turns nets into arc weights, and it
  is the only place that mapping lives. Distinct-voter weighting or recency decay
  would slot in there.
- **Directed corridors.** The subgraph is currently undirected. One-way
  proposals would need `buildTypeAdj` and the block projection to carry direction.
- **A different oracle.** `makeSegmentRecoveryCheck` is injected, and tests pass
  fakes. Swapping in a real routing profile (see
  [dossier 04](04-route-finding.md)) is a constructor argument, not a rewrite.
- **Cross-type corridors.** Types are clustered independently by design. Merging
  "protected bike lane" and "add sharrow" into one corridor would be a change to
  `step`, and would need a rule for what the merged label says.
