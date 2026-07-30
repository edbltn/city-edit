# The Three-Layer Model — graph, blocks, route proposals

This is the **source of truth** for how City Edit separates map functionality into
three layers, and for the block-scoped voting semantics that bind them. It
supersedes the 2026-06-18 "propagate + dedup" design (now in
[`archive/`](archive/README.md)): **votes are never fanned out across a block** —
they are recorded only on the edges the user actually selected. Blocks are the
*aggregation, display, and interaction* grain; edges/nodes remain the *storage*
grain.

The Layer-1 mechanics (vote identity, Postgres/Redis storage, migration,
reconciliation) live in [voting-architecture.md](voting-architecture.md) and are
unchanged. This doc defines the two layers above it and the write semantics.

```
Layer 3  Route proposals   dynamic · client-computed · deterministic clustering of
         (derived)         vote state → corridors → displayed as their blocks
                                       │ many-to-one (edge → proposal), minute-batched
Layer 2  Blocks            precomputed · procedural per-city polygons · aggregate +
         (interaction)     display votes deduped per (voter, type, direction) ·
                           define unvote/flip behavior · highlight target
                                       │ many-to-one (edge/node → block), baked
Layer 1  Edge/node graph   OSRM pathfinding + votable topology · votes stored
         (source of truth) per (map, edge, vote_type, device) → direction ±1
```

> **Terminology — PBTP / RBTP.** The map surfaces two kinds of *top proposal*,
> and the code, changelogs, and discussions use these abbreviations throughout:
>
> - **PBTP** — **point-based top proposal**: one hot edge, rendered as a
>   **square** pin at the edge midpoint. Selected by
>   `topProposals.selectTopProposals` (client) — at most **one pin per block**
>   across all vote types, and same-type pins at least
>   `TOP_PROPOSAL_MIN_SPACING_M` (600 m) apart — drawn by GraphLayer's
>   `indicatorMarkers`.
> - **RBTP** — **route-based top proposal**: a hot **corridor** (Layer 3 below),
>   rendered as a **diamond** pin at its middle path edge. Computed by
>   `routeProposals.computeRouteProposals` (client), drawn by GraphLayer's
>   `routeIndicatorMarkers`.
>
> Same-type PBTPs subsumed by an RBTP's blocks are dropped
> (`dropPointsCoveredByRoutes`); both kinds share one icon system
> (`makeVoteTypeIcon`: square vs `diamond`, same tint/heat/selected states).
>
> **Kind split.** Every vote type carries a **route/point kind**
> (`pointType`), declared where the type is authored (preset lists, a map's
> custom list) or recorded by the cast that creates a free-text suggestion
> (`vote_types.point_type` in Postgres; served per map via `voteTypes` +
> `searchVoteTypes`). The families are disjoint by kind: **point**-kind types
> surface only as PBTPs, **route**-kind types only as RBTPs
> (`pointTypeForLabel` resolves label → kind; unknown kind — a legacy
> suggestion never flagged — stays eligible for both). Station networks skip
> the split (every vote there is a point). `backfill_vote_type_kinds()`
> repairs legacy rows from the authored lists on every startup.

---

## 1. Layer 1 — the edge/node graph

- **Routing**: OSRM (self-hosted, foot profile). **Topology / snapping /
  display**: `CityGraph` (`server/graph_registry.py`), built from the same PBF +
  foot filter, shipped to the client as a typed-array binary blob
  (`/api/graph-topology?format=bin`, decoded by
  `client-react/src/components/GraphLayer/graphTopology.ts`).
- **Votes** are rows in Postgres `edge_votes`, unique per
  `(map_slug, edge_id, vote_type_id, device_id)` with a mutable
  `direction ∈ {+1, −1}` — see voting-architecture.md §1–2. Redis `ev:<slug>`
  holds packed aggregate counts for serving. Node votes are always **derived**
  from adjacent edges; nothing is ever stored per node.
- A selection (a tapped point or a start→end route) always resolves to a set of
  **edge ids** — the route's path edges, or the tapped point's snapped edge.
  These selection edges are the **only** edges a cast writes to (§4).

## 2. Layer 2 — blocks

### 2.1 What a block is

One **corridor block** per path/street segment between intersections, plus one
**junction block per junction cluster** (a physical intersection is several OSM
junction nodes — centerline node, crossing ends, sidewalk corners — so nearby
junctions merge into one multi-node block). Junction blocks exist because
corridor polygons would otherwise extend across intersections: the short edges
crossing an intersection would land in a PERPENDICULAR street's block, so a
route down an avenue would select — and cast onto — every cross street it
passes (the "ladder" bug). Blocks partition the votable network: **every edge
belongs to exactly one block** (strict many-to-one, total — coverage and
edge∩polygon overlap are 100% by construction, audited at build time), and a
node belongs to the block of its **shortest** incident edge (deterministic,
matching the client's `adjShortest` node→edge upgrade rule).

### 2.2 Generation — procedural, per city, at graph-build time

Blocks are generated **graph-first from the walk graph alone** so any city can
be added without city-specific open data — one pass,
`server/streetscape_blocks/build_blocks_graph_first.py` (runs in the server
venv; no osmnx/geopandas geo venv). Membership is decided FIRST, topologically,
for every edge; each block's polygon is then generated **from its own member
edges**, so coverage and edge∩polygon overlap hold by construction (the
previous five-script pipeline generated polygons from drive centerlines and
mapped edges into them geometrically, which left unmapped edges — heatmap gaps
— and nearest-snapped edges assigned to polygons they didn't intersect):

1. **Junction clusters** — walk-graph junctions (unique-neighbour degree ≥ 3),
   union-found within 18 m, oversized clusters bisected along their principal
   axis (> 40 m extent).
2. **Edge grouping (total)** — an edge whose endpoints share a cluster belongs
   to that junction; a short (≤ 30 m) edge between two clusters is a crosswalk
   stub and joins the nearer one; every other edge joins a **corridor** —
   connected components linked through non-junction endpoints, i.e. one
   component per segment between junctions (the same grain for streets and
   park paths).
3. **Degeneracy + equivalence fixpoint** — four rules iterate to convergence:
   corridors with the SAME two endpoint clusters merge (both sidewalks + the
   roadway of one street segment become one block); driveway-class stubs
   (extent ≤ 25 m, one junction) melt into their junction; a junction left
   touching ≤ 1 corridor isn't a junction and dissolves into that corridor;
   clusters with the SAME incident-corridor set (≥ 2 corridors) merge (an
   over-split intersection becomes one junction). Each rule strictly shrinks
   a count, so the loop terminates.
4. **Geometry from membership** — a junction cell is the convex hull of its
   member nodes (⊕ 8 m pad) unioned with its captured edges' tubes, then cut
   at the perpendicular bisector against overlapping neighbour cells
   (Voronoi-style shared boundaries; a cut that would evict a member is
   skipped). A cluster that captured no edges gets no cell — the corridors'
   own tubes cover the fork. A corridor is the union of its member edges
   buffered by per-road-class half-width, minus the junction cells (junctions
   win where they meet). A corridor edge left entirely inside junction cells
   is reassigned to the cell containing its midpoint — membership follows the
   final geometry.
5. Bakes `edge_block_id: int32[n_edges]`
   (`osm_data/<city>/edge_blocks_<network>.npy` + a meta JSON stamping
   `topology_etag` + `blocks_sha256`) and writes
   `blocks_final_<city>.geojson` → tippecanoe → `blocks.pmtiles`. `block_id`s
   are **1-based**: MVT can't represent a native feature id of 0 (tippecanoe
   drops it), which would detach block 0's heat/selection feature-state.

The old pipeline (`build_blocks_generic.py` → `build_node_blocks.py` →
`build_foot_blocks.py` → `build_edge_blocks.py` → `merge_degenerate_blocks.py`)
and its planimetric evaluation harness (`compare_blocks.py`, `COMPARISON.md`)
remain in the repo for reference but are no longer run by
`build_city_blocks.sh`.

Generation runs **when a city's graph is built** (alongside
`refresh_osm.py` / the graph-builder image) and the artifacts are baked next to
`walk_graph.pkl`. A map whose city has no block artifacts falls back to
**edge-as-singleton blocks** everywhere below — every rule still holds with
`block(e) = {e}`.

### 2.3 Loading and shipping

- Server: `CityGraph` loads `edge_block_id` only when the baked
  `topology_etag` matches the live graph (else the map serves without blocks);
  exposes `edge_block_id`, `n_blocks`, `blocks_version`, and a lazily-built
  `block_to_edges` index.
- Client: the binary topology blob carries an optional trailing
  `edge_block_id: int32[n_edges]` section (blob version bumped so stale caches
  refetch). The client builds a `blockIndex` (block → edge ids, CSR layout like
  `buildNodeAdj`) once per topology load. No boxed arrays — the mobile
  typed-array memory model applies.

### 2.4 Blocks are the aggregation + display grain

Votes **display** at block grain everywhere — heatmap, modals, legends — deduped
so each user counts once:

> **count(block, type, direction) = # distinct devices with ≥1 edge-vote of that
> (type, direction) inside the block.**

- Served from the Redis structures in `server/block_votes.py`
  (`bd:<slug>:<mode>:<block>:<vt>:<dir>` device-multiplicity hashes +
  `bagg:<slug>:<mode>` aggregate; rebuilt from Postgres on cold start / resnap)
  as `block_votes[]` (total deduped activity per block) and `block_vote_types[]`
  (`[legendIdx, up, down]` per block) on `/api/graph-votes`.
- **Heat display** is the **signed top-proposal differential** per block: the
  vote differential (up − down) of the block's best-ranked proposal, ranked by
  differential across the block's vote types (`client-react/src/components/GraphLayer/voteApply.ts`
  `topProposalDiffs`). Positive differentials ride the warm ramp (existing hues),
  negative descend into the theme's cold arm (almost blue at floor), zero is
  invisible (cancelled signal carries no heat). Per-block differentials are
  normalized via two-armed log scaling: positives against HEAT_FULL_SCALE (50),
  negatives against their own tighter floor NEG_HEAT_FULL_SCALE (10), so
  organic net-against blocks reach genuine cold instead of washing out next to
  bulk-import positives. Feature-state heat ∈ [−1, 1]. The per-edge canvas heat
  is the fallback for maps without blocks.
- The **modal** for any selection sums the *deduped block counts* over the
  selection's blocks (a device present in two blocks of a corridor counts once
  per block).
- **Highlight**: selecting or hovering any node, edge, or edge set highlights the
  covering **block polygons** (feature-state `selected`), with the resolved
  edge/route still emphasized on the highlight canvas.

### 2.5 THE invariant

> **A block can never hold both a `+` and a `−` from the same device for the same
> vote type.**

Enforced server-side by the block-scoped write semantics (§4): every cast first
clears the caster's same-type votes across all touched blocks, inside the
per-voter lock. `block_votes.rebuild_from_db` asserts it when rebuilding, and
unit tests cover the flip/partial/concurrent paths.

## 3. Layer 3 — route-based top proposals

### 3.1 What they are

For each vote type with enough support, the top **corridors** — simple paths
through the intersection graph where that type has strong net support. They are
**derived state**: a pure, deterministic function of (topology, current vote
state), recomputed **on the client**
(`client-react/src/components/GraphLayer/routeProposals.ts`). No server
round-trip, no persistence, no randomness. Recomputation is **batched off the
vote path**: casts and deltas only mark the lists dirty; a minute-cadence sweep
(GraphLayer `PROPOSALS_REFRESH_INTERVAL_MS`) reruns the pipeline in idle-time
slices — one vote type per slice — so proposals may lag votes by up to a minute
while casting stays instant even on the 3.3M-edge NYC bike graph. The heatmap
and per-proposal counts do NOT go through this path and stay live.

### 3.2 The clustering pipeline (deterministic)

Per vote type `T`, over edges with net(T) ≥ `MIN_NET` (high-activity gate #1):

1. **Type subgraph** — nodes = intersections touched, edge weight = net.
2. **Localize** — connected components (a deterministic replacement for the
   server-side Leiden step: components localize naturally, and corridor
   peeling separates parallel corridors inside one component). Components
   whose TOTAL weight can't reach `minRouteScore` are skipped outright — with
   the top-proposal support floor (`TOP_PROPOSAL_MIN_NET`, net > 100, the same
   bar PBTP winners clear) as the in-app score gate, this prunes nearly
   everything before any routing work.
3. **Routing-consistent growth** (`growCorridor`) — grow one corridor from the
   component's heaviest edge, repeatedly taking the heaviest net-positive arc
   off either tip (ties: lowest edge id) that fits the length budget. An
   extension is accepted outright only if the **open segment stays a shortest
   path** through the full graph (`makeSegmentShortestCheck` — a bounded,
   deterministic A* that tolerates ties and sub-eps shortcuts); otherwise the
   previous tip is pinned as a **ghost waypoint**. At most
   `MAX_GHOST_WAYPOINTS` (3) pins — the 3rd ends growth — so a proposal is
   always reproducible as ≤ 5 route waypoints (`waypointNodes`, with
   per-segment `segments` edge slices), which is what keeps a shared
   top-proposal URL routing back into the corridor after the proposal
   retires, and what bounds how roundabout a corridor can get (this replaces
   the old straightness-splitting + budget-window trimming). Peel the grown
   corridor's edges out and repeat up to `PEEL_MAX_PATHS`, keeping corridors
   scoring ≥ `PEEL_DOMINANCE` × the first.
4. **Length budget** — growth may not extend past a meter budget that grows
   with support: `min(ROUTE_LENGTH_MAX_M, ROUTE_LENGTH_BASE_M +
   ROUTE_LENGTH_PER_SQRT_SCORE_M · √score)`. A corridor *earns* length with
   votes; extension can no longer snake for miles on chains of net-1 edges.
5. **High-activity gate #2** — the grown corridor survives only with score ≥
   `MIN_ROUTE_SCORE` and ≥ `MIN_ROUTE_EDGES` edges.
6. **Blocks projection** — each path edge expands to its block:
   `blocks: number[][]` (ordered distinct blocks along the path),
   `blockEdgeIds` = their union. **Proposals are displayed as their blocks**
   (block highlight + diamond marker at the path midpoint); `pathEdgeIds` stays
   the verbatim corridor for routing (ghost-waypoint insertion).
7. **Dedupe + rank** — same-type Jaccard ≥ 0.5 collapses near-duplicates;
   rank by score; global cap.

Edge → proposal is many-to-one *within a type* (peeling removes used edges), so
proposal → blocks is a well-defined projection.

### 3.3 Selection behavior

- An RBTP renders **selected** when either:
  1. **Coverage** — the current selection covers **all of its blocks** (≥ 1
     selected edge in every block), exactly like PBTPs render selected when
     their block is covered. `isRouteCovered` is the shared predicate, with the
     selection **expanded to direction twins** first
     (`expandSelectionToUndirected`) — a routed path often traverses the twin
     of the edge a corridor's block recorded.
  2. **Explicit tap** — it is the RBTP the user tapped (GraphLayer's
     `selectedRbtpId`) and **both of its anchors are still waypoints** of the
     current route. Editing an anchor away or clearing the route deselects it.
- Tapping an RBTP inserts its two anchors as waypoints (`chooseAnchorOrder`
  picks the faster order) so the route runs anchor-to-anchor. The A→B leg is
  routed by **OSRM**, which need not re-trace the corridor's exact path — that
  is *why* rule 2 exists: coverage alone almost never fires for a tapped RBTP,
  especially on maps without block artifacts (singleton blocks). Routing the
  leg through the proposal's stored `edgeIds` verbatim remains an open
  follow-up.

## 4. Vote semantics — block-scoped clear-then-cast

The write path binds the layers together. Let:

- `S` — the selection's edge ids (route path edges, or the tapped point's edge).
- `T` — the vote type; `D` — the device; `d ∈ {+1, −1}` — the pressed direction.
- `B(S) = { block(e) : e ∈ S }` — the **touched blocks**.
- `coverage(d)` — of the blocks in `B(S)`, how many contain ≥ 1 edge with `D`'s
  existing `(T, d)` vote: `all`, `some`, or `none`.

### 4.1 Button state (per direction, client-derived from local my-votes)

| `coverage(d)` | button `d` renders | note |
|---|---|---|
| `all` | **active** (pressing = unvote) | at most one direction can be `all` — the invariant forbids the other direction from coexisting in any of those blocks |
| `some` | neutral | no unvote affordance; pressing still clears first (below) |
| `none` | neutral | fresh cast |

### 4.2 Press behavior

| state | press | server does (inside the per-voter lock) |
|---|---|---|
| `coverage(d) = all` | `d` | **Unvote**: delete every `(D, T)` vote on every edge of every block in `B(S)`. Cast nothing. (Wire: `direction: 0`.) |
| anything else | `d` | **Clear-then-cast**: delete every `(D, T)` vote — *either direction* — on every edge of every block in `B(S)`, then insert `d` on exactly the edges of `S`. (Wire: `direction: d`.) |

Consequences, matching the product spec:

- **Fresh** (`none`/`none`): votes land on the selection edges only — never on
  the rest of the block.
- **Flip** (`+` active, press `−`): all my `+` votes across the touched blocks
  are removed, then `−` is cast along the current selection edges.
- **Partial** (`some`): no active state shown, but pressing reconciles — my
  scattered prior votes in those blocks are cleared and replaced by this
  selection's votes.
- **Idempotent per block**: after any cast, `D`'s `T`-votes inside every touched
  block are exactly `{e ∈ S ∩ block : direction d}` — one direction per block,
  which is the §2.5 invariant.
- Blocks **outside** `B(S)` are never touched.

### 4.3 Wire contract

`POST /api/vote` — request unchanged:
`{ map, mode, vote_type, voter_id, edge_ids: S, direction: d | 0 }`.
The server expands `S → B(S)` itself (`edge_block_id`), reads `D`'s existing
`T`-rows for the map, clears the ones whose edge falls in `B(S)`, then applies
`d` to `S`. Response gains `cleared: number[]` (edges the server unvoted beyond
`S`) so the client reconciles its local store. Redis edge counts
(`apply_directional`) and block dedup state (`apply_block_delta`) move for every
cleared/cast edge inside the same lock; `publish_delta` broadcasts authoritative
`[up, down]` for **all** changed edges.

The client mirrors the same plan optimistically (it knows its own votes + the
block index), then reconciles from the response / delta.

## 5. File map

| Concern | File |
|---|---|
| Layer-1 storage, identity, migration | `server/database.py`, `server/vote_store.py`, [voting-architecture.md](voting-architecture.md) |
| Block generation + eval | `server/streetscape_blocks/` (`build_blocks_generic.py`, `build_foot_blocks.py`, `build_edge_blocks.py`, `compare_blocks.py`, `eval/`) |
| Block load + mapping | `server/graph_registry.py` (`edge_block_id`, `block_to_edges`) |
| Block vote dedup + serving | `server/block_votes.py`, `/api/graph-votes` in `server/app.py` |
| Block-scoped write semantics | `/api/vote` in `server/app.py` |
| Client block index + topology blob | `client-react/src/components/GraphLayer/graphTopology.ts` |
| Client vote plan / button state | `client-react/src/utils/voteStore.ts`, `client-react/src/utils/castVote.ts` |
| Client route-proposal clustering | `client-react/src/components/GraphLayer/routeProposals.ts` |
| Selection / highlight / modal wiring | `client-react/src/components/GraphLayer/GraphLayer.tsx`, `MapView.tsx`, `MapLibreBackground.tsx` |

## 6. Invariants (tests assert these)

1. `edge_block_id` is total after foot blocks (every edge ≥ 0), and stable under
   bake/load round-trip; maps without artifacts behave as singleton blocks.
2. No sequence of casts can leave a block holding `+` and `−` from the same
   `(device, vote type)` — including flips, partial coverage, and concurrent
   casts under the voter lock.
3. After a cast of `d` on `S`, the device's `T`-votes inside every touched block
   equal exactly `S ∩ block`, all with direction `d`; blocks outside `B(S)` are
   untouched.
4. `bagg` count == `HLEN(bd)` == distinct devices; a device voting N edges of a
   block counts once; rebuild-from-Postgres reproduces incremental state.
5. Clustering is deterministic: same (topology, vote state) ⇒ identical proposal
   list (ids, order) on every client; proposals appear only above the activity
   thresholds.
6. A route proposal is selected iff every one of its blocks contains a selected
   edge; point and route proposals share the predicate.
