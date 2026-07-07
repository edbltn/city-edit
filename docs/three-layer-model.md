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
                                       │ many-to-one (edge → proposal), real time
Layer 2  Blocks            precomputed · procedural per-city polygons · aggregate +
         (interaction)     display votes deduped per (voter, type, direction) ·
                           define unvote/flip behavior · highlight target
                                       │ many-to-one (edge/node → block), baked
Layer 1  Edge/node graph   OSRM pathfinding + votable topology · votes stored
         (source of truth) per (map, edge, vote_type, device) → direction ±1
```

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

One polygon per street segment between intersections (a "streetscape block"),
covering the roadbed + sidewalk right-of-way — plus one small **disc block per
junction node** (12 m, `build_node_blocks.py`), punched out of the street/foot
polygons so blocks never overlap. Junction discs exist because street polygons
otherwise extend across intersections (Voronoi flare): the short edges crossing
an intersection would bake into a PERPENDICULAR street's block, so a route down
an avenue would select — and cast onto — every cross street it passes (the
"ladder" bug). Blocks partition the votable network: **every edge belongs to
exactly one block** (strict many-to-one), and a node belongs to the block of its
**shortest** incident edge (deterministic, matching the client's `adjShortest`
node→edge upgrade rule — the shortest incident edge is the one whose midpoint
falls inside the junction's own disc).

### 2.2 Generation — procedural, per city, at graph-build time

Blocks are generated **procedurally from the street graph alone** so any city can
be added without city-specific open data (`server/streetscape_blocks/`):

1. `build_blocks_generic.py` — consolidated drive-centerline graph (osmnx,
   intersections merged at 12 m) → buffer each segment by a per-road-class
   half-width → seed points every 6 m → Voronoi partition by nearest segment →
   dissolve + clip. One polygon per street segment.
2. `build_node_blocks.py` — every walk-graph junction (unique-neighbour degree
   ≥ 3) becomes its own 12 m disc block (`road_class="node"`), subtracted from
   every street block it touches so blocks stay disjoint.
3. `build_foot_blocks.py` — edges not covered by any street block (park paths,
   plazas, boardwalks — ~18% in NYC) are buffered (6 m), merged, and **severed
   at the junction discs** (12 m > 6 m, so the mesh disconnects there): one
   block per path segment between junctions — the same grain as streets, not
   one giant block per connected park network. Appended with continuing
   `block_id`s; this is what makes the edge→block mapping **total**.
4. `build_edge_blocks.py` — bakes `edge_block_id: int32[n_edges]`
   (`osm_data/<city>/edge_blocks_<network>.npy` + a meta JSON stamping
   `topology_etag` + `blocks_sha256`). Assignment: polygon containment of the
   edge midpoint, else nearest block within 30 m (see `eval/RESULTS.md` — nearest
   -polygon is exact where containment leaves ~20% unmapped).

**Evaluation against ground truth**: `compare_blocks.py` scores the procedural
output against Brook's NYC planimetric blocks (`build_nyc_blocks.py`, from NYC
open-data roadbed+sidewalk polygons — the reference this algorithm mimics).
Current numbers (see `COMPARISON.md`): median IoU **0.84**, median area ratio
**1.00**, median centroid offset ~1 m over ~82k shared segments. **Every city —
NYC included — serves the procedural output** (`edge_blocks_streets.json` stamps
`blocks_file: blocks_generic_nyc.geojson`); Brook's planimetric blocks are the
evaluation reference only. New cities should be spot-checked with the same
comparison harness when planimetric data exists.

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
  as `block_votes[]` (net = up − down) and `block_vote_types[]`
  (`[legendIdx, up, down]` per block) on `/api/graph-votes`.
- The **heat display is the block fill** (MapLibre feature-state on the blocks
  PMTiles); the per-edge canvas heat is the fallback for maps without blocks.
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
state), recomputed **on the client in real time** as vote deltas arrive
(`client-react/src/components/GraphLayer/routeProposals.ts`). No server
round-trip, no persistence, no randomness.

### 3.2 The clustering pipeline (deterministic)

Per vote type `T`, over edges with net(T) ≥ `MIN_NET` (high-activity gate #1):

1. **Type subgraph** — nodes = intersections touched, edge weight = net.
2. **Localize** — connected components (a deterministic replacement for the
   server-side Leiden step: components localize naturally, and path peeling
   separates parallel corridors inside one component).
3. **Peel heaviest simple paths** — exact DFS for components ≤ 12 vertices,
   greedy two-way extension above; peel up to `PEEL_MAX_PATHS`, keeping paths
   scoring ≥ `PEEL_DOMINANCE` × the first. All iteration orders and tie-breaks
   are by ascending edge/node id — same vote state ⇒ same proposals on every
   client.
4. **High-activity gate #2** — a path survives only with score ≥
   `MIN_ROUTE_SCORE` and ≥ `MIN_ROUTE_EDGES` edges.
5. **Blocks projection** — each path edge expands to its block:
   `blocks: number[][]` (ordered distinct blocks along the path),
   `blockEdgeIds` = their union. **Proposals are displayed as their blocks**
   (block highlight + diamond marker at the path midpoint); `pathEdgeIds` stays
   the verbatim corridor for routing (ghost-waypoint insertion).
6. **Dedupe + rank** — same-type Jaccard ≥ 0.5 collapses near-duplicates;
   rank by score; global cap.

Edge → proposal is many-to-one *within a type* (peeling removes used edges), so
proposal → blocks is a well-defined projection.

### 3.3 Selection behavior

- A route proposal renders **selected** when the current selection covers **all
  of its blocks** (≥ 1 selected edge in every block) — exactly like point
  proposals render selected when their block is covered. `isRouteCovered` is the
  shared predicate.
- Tapping a route proposal inserts its two anchors as waypoints
  (`chooseAnchorOrder` picks the faster order) so the route traverses the
  corridor; the A→B leg uses the proposal's stored path verbatim.

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
