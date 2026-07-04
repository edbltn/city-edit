# Unify voting — delegation waves (copyable agent prompts)

Decisions (2026-06-18): **propagate + dedup-display** · modal grain `(block,type,device)`
net=up−down · **full shared `Cluster` abstraction** FE+BE · issue-4 route ghost uses
**verbatim** stored path. Specs: `docs/cluster-model.md`, `docs/vote-system-design.md`.

**Dependency graph**
```
Wave 1 (parallel):   W1-A edge_block_id      W1-B FE Cluster types     W1-C BE Cluster types
Wave 2 (parallel):   W2-A block_votes+propagate(←A,C)  W2-B backfill(←A)  W2-C FE block-select(←A,B)
Wave 3 (parallel):   W3-A route UX parity(←2C)  W3-B modal summary(←2A,2C)  W3-C issue-4(←1B,1C)  W3-D verify(←all)
```
File ownership (avoid merge conflicts): W1-A = graph build/load + new `server/blocks.py`,
does NOT touch `route_proposals.py`. W1-C owns `route_proposals.py`. W2-A owns `block_votes.py`
+ `/api/vote` write loop. W2-B owns a new migrate script. Run each in its own worktree.

---

## SHARED CONTEXT (every prompt repeats a short version of this)

Repo `city-edit`, branch `fix/unify-voting`. React+Leaflet client in `client-react/`,
Flask backend in `server/`. Read **first**: `docs/cluster-model.md` (the abstraction),
`docs/vote-system-design.md` (storage/propagation/display), and root `CLAUDE.md`
(uv-only Python, changelog rules, local dev on :3000/:5001/:6379). Pure-logic modules
have unit tests next to them — add/extend tests. Don't deploy. End by writing a 5-line
summary of what changed + how to verify.

---

## WAVE 1

### W1-A — Bake & load `edge_block_id` (BACKEND foundation)
```
You are working in the city-edit repo (branch fix/unify-voting). FIRST read
docs/vote-system-design.md §2.2 and docs/cluster-model.md §5, plus server/streetscape_blocks/
(block geometry), server/osm_graph_builder.py + server/refresh_osm.py (graph build/bake),
and how route_proposals.py / app.py reference `getattr(rmap.graph, "edge_block_id", None)`.

TASK: build, bake, and load the edge→block mapping so block_of(edge) is O(1) everywhere.
1. Compute `edge_block_id: int32[n_edges]` (edge_id -> block_id, or -1 if no covering block)
   from the streetscape block geometry + the city graph edges. Put the builder in a NEW
   module server/blocks.py (do NOT edit route_proposals.py — another agent owns it).
2. Bake it next to walk_graph.pkl, cache-keyed by topology_etag + a blocks_hash, so it only
   rebuilds when the graph or blocks change. Wire it into the graph load so
   rmap.graph.edge_block_id is populated, plus a block_to_edges map (block_id -> [edge_ids])
   and helpers block_of(edge) / blocks_version().
3. Expose blocks_version/blocks_hash where app.py can stamp it onto /api/graph-votes (the
   stale-cache guard already stamps topology_version — mirror that).

CONSTRAINTS: uv pip only. Maps with no block artifacts must fall back to edge-as-singleton
(edge_block_id absent/-1) and keep working. Don't touch route_proposals.py, block_votes.py,
or the /api/vote write loop (other agents). Add unit tests: round-trip bake/load, unmapped
edge -> -1, singleton fallback, blocks_hash changes on geometry change. Run them.
Summarize what changed + how another agent gets block_of(edge).
```

### W1-B — Shared `Cluster` TS model (FRONTEND foundation)
```
You are in the city-edit repo (branch fix/unify-voting). FIRST read docs/cluster-model.md
(esp. §1), then client-react/src/components/GraphLayer/topProposals.ts and routeProposals.ts
(existing VoteTypeWinner + RouteProposal shapes) and their .test.ts files.

TASK: introduce the unified Cluster model as PURE LOGIC only (no GraphLayer/React wiring yet).
1. New module client-react/src/components/GraphLayer/cluster.ts with the §1.1 contract:
   a Cluster type (discriminated by kind: "point"|"route"|"block") exposing id, kind, label,
   legendIdx, anchorEdgeId, anchorCoords, blocks:number[][], blockEdgeIds:number[], score,
   and (route only) pathEdgeIds:number[].
2. Adapters: pointClusterFromWinner(VoteTypeWinner, blockEdges), routeClusterFromProposal(
   RouteProposal), blockClusterFromBlock(blockId, edgeIds, anchorEdgeId, coords).
3. Shared helpers: clusterHighlightEdges(c)=blockEdgeIds, clusterShapeClass(kind) (reuse
   routeProposals.proposalShapeClass: diamond for route, square otherwise), isClusterCovered
   (generalize routeProposals.isRouteCovered), clusterNodeSet(c, topology).
   Keep topProposals.ts / routeProposals.ts producing their current types; just add adapters.

CONSTRAINTS: do NOT modify GraphLayer.tsx, MapView.tsx, or selection/* (later waves).
Enforce invariant blockEdgeIds == dedup(flatten(blocks)). Add cluster.test.ts covering the
adapters + invariant + the PointCluster≡BlockCluster-over-same-edge equivalence
(docs/cluster-model.md §6). Run the test suite. Summarize the exported API surface.
```

### W1-C — Shared `Cluster` Python model (BACKEND foundation)
```
You are in the city-edit repo (branch fix/unify-voting). FIRST read docs/cluster-model.md
(§1, §5) and server/route_proposals.py (RouteProposal dataclass + to_dict + the engine) and
server/tests/unit/test_route_proposals.py.

TASK: refactor RouteProposal into the shared Cluster hierarchy (you OWN route_proposals.py).
1. Add a base Cluster dataclass with the §1.1 fields (id, kind, label, legend_idx,
   anchor_edge_id, anchor_coords, blocks, block_edge_ids, score) and a to_dict() emitting the
   §1.3 wire shape including "kind" and (route only) "path_edge_ids".
2. Make RouteProposal a RouteCluster(Cluster) (kind="route", path_edge_ids = ordered edge_ids,
   two anchors). Add PointCluster (kind="point", single block = block_of(anchor), one anchor)
   and BlockCluster (kind="block", single block). Keep ALL existing wire keys present so the
   current client keeps parsing (additive: add kind + anchor_edge_id + path_edge_ids).
3. Keep the RouteProposalEngine behavior identical; only the dataclass type changes.

CONSTRAINTS: uv pip only. Pure logic — no Flask/Redis/Postgres imports in this module. Do NOT
touch app.py, block_votes.py, or graph loading (other agents). Update test_route_proposals.py
for the new fields and add tests for PointCluster/BlockCluster to_dict + the §6 invariants.
Run the suite. Summarize the new wire shape so FE/W3 agents can rely on it.
```

---

## WAVE 2 (start after W1 lands)

### W2-A — `block_votes.py` dedup + write-path propagation (BACKEND)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W1-A (edge_block_id) and
W1-C (Cluster.to_dict). FIRST read docs/vote-system-design.md §2.1–2.6 carefully, then
server/vote_store.py (apply_directional, build_arrays, redis_field), server/app.py
(/api/vote write loop ~app.py:926-1072, /api/graph-votes ~app.py:1485, _hydrate_map_redis),
and use rmap.graph.edge_block_id (from W1-A) + block_of.

TASK (two halves, both yours):
1. NEW server/block_votes.py implementing §2.3–2.6: bd:<slug>:<mode>:<block>:<vt>:<dir> device
   multiplicity hashes + bagg:<slug>:<mode> aggregate; apply_block_delta(redis, ...) (O(1) per
   changed edge, moves bagg only on the 0↔1 device boundary); rebuild_from_db(slug, mode,
   edge_block_id); and a serve projection block_votes[] + block_vote_types[] ([legendIdx,up,down]
   per block) built from one HGETALL bagg. Stamp n_blocks + blocks_version onto /api/graph-votes.
2. PROPAGATION in /api/vote: before the per-edge write loop, expand incoming edge_ids to the
   union of block_edge_ids (block_of each edge -> all edges in that block); record the vote on
   ALL of them (same vote_type/direction/device, each row's lat/lon = its own edge midpoint),
   and call apply_block_delta for each changed edge inside the existing voter_lock + pipeline.
   Maps without edge_block_id skip propagation (singleton blocks) — unchanged behavior.

CONSTRAINTS: uv pip only. Hook rebuild_from_db into _hydrate_map_redis / _populate_redis and the
graph_reload/resnap path (§2.6). Keep ev:<slug> + edge_votes[] shapes unchanged. Add tests for
the §2.7 invariants (bagg==HLEN(bd); rebuild==incremental; unmapped edge never touches bd/bagg;
one user voting N block edges -> bagg stays 1). Restart Flask, curl /api/graph-votes?map=<slug>
and confirm block_votes present. Summarize the new response fields + propagation behavior.
```

### W2-B — Backfill script: make historical votes block-complete (BACKEND)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W1-A (edge_block_id).
FIRST read docs/vote-system-design.md §2.8, server/database.py (edge_votes schema + record_/
delete_edge_votes), and existing CLIs server/migrate_votes.py / server/vote_migration.py for
style (argparse, prod-tunnel awareness per CLAUDE.md — local :5432, prod via 5433 tunnel).

TASK: NEW server/migrate_block_votes.py implementing block_backfill(slug, edge_block_id):
for every (block, vote_type, direction, device) group present on >=1 edge, INSERT the row on
every other edge in that block lacking it, ON CONFLICT DO NOTHING (idempotent; the unique key
is (map_slug, edge_id, vote_type_id, device_id)). Synthesized rows use the TARGET edge's own
midpoint for lat/lon (resnap-safe). After backfill, trigger the Redis ev: + bd:/bagg: rebuild
(reuse W2-A's rebuild path / publish graph_reload). Provide a --dry-run that reports counts.

CONSTRAINTS: uv pip only. Idempotent + re-runnable. Default to LOCAL db; require an explicit
flag to point at prod and follow the CLAUDE.md backup-before-touching-prod rule (don't run
against prod yourself). Add a unit test on a synthetic edge_block_id + fake edge_votes proving
idempotency (running twice == once) and direction-awareness. Summarize how to run it locally.
```

### W2-C — Resolve clicks to a BlockCluster + highlight the block (FRONTEND, issue 3)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W1-A (baked edge_block_id)
and W1-B (cluster.ts). FIRST read docs/cluster-model.md §1–2 + issue 3, then
client-react/src/components/GraphLayer/GraphLayer.tsx (resolveSelection ~L794, hover state,
edge/node highlight, indicator rendering) and graphTopology.ts (typed-array topology +
decodeTopologyBin).

TASK: make selecting/hovering ANY edge or node resolve to its BlockCluster and highlight the
whole block, with the clicked edge shown as the routing anchor.
1. Ship edge_block_id to the client: extend the binary topology blob (decodeTopologyBin /
   the server encoder) to carry an optional int32[n_edges] edge_block_id, plus a client-side
   block_to_edges index built once on load (mirror buildNodeAdj). Gate on its presence.
2. In resolveSelection/hover, build a BlockCluster (via W1-B blockClusterFromBlock) for the
   resolved edge/node and highlight clusterHighlightEdges(c) (all block edges) + emphasize the
   anchor edge. No kind-specific branches beyond marker shape (docs §6).
3. Maps without edge_block_id keep today's single-edge highlight (fallback).

CONSTRAINTS: keep the mobile typed-array memory model (no boxed arrays). Don't change the modal
contents yet (W3-B) or route-proposal UX (W3-A). Add/extend tests for block_to_edges build +
cluster resolution. Run the app locally (:3000), confirm hovering an edge lights its block.
Summarize the wire change to the topology blob + the new highlight behavior.
```

---

## WAVE 3 (start after W2 lands)

### W3-A — Route proposals share point-proposal UX (FRONTEND, issue 1)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W2-C (unified cluster
selection/highlight) and W1-B. FIRST read docs/cluster-model.md §2 + issue 1, then
GraphLayer.tsx (indicator rendering, hover/select, cluster spread/explode, mid-drag) and
how point top-proposals vs route proposals are currently rendered differently.

TASK: route the rendering + hover + select + drag of BOTH point and route proposals through the
single Cluster path so they behave identically — same highlight, same select persistence, same
drag affordance, same modal trigger — differing ONLY in marker shape (diamond vs square) and
anchor count. Remove the now-duplicate route-specific interaction branches. A route clicked or
dragged onto must highlight its blocks exactly like a point highlights its block.

CONSTRAINTS: do NOT implement issue-4 routing here (that's W3-C) or change modal counts (W3-B) —
just the selection/hover/drag parity. Keep spread/explode working for both kinds. Run locally,
verify a route proposal highlights + selects identically to a point proposal. Summarize which
branches were unified/removed.
```

### W3-B — Block-aware modal vote summary (FRONTEND, issue 2)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W2-A (server block_vote_types)
and W2-C (BlockCluster selection). FIRST read docs/cluster-model.md §3 and docs/vote-system-design.md
§2.1 (modal grain (block,type,device), net = #up-devices − #down-devices), then the modal
component opened on selection and how it currently summarizes the single edge.

TASK: when any Cluster is selected, the modal summarizes votes over ALL edges in ALL its blocks,
deduplicated per (block, vote_type, device): net(type) = distinct-up-devices − distinct-down-devices,
read from block_vote_types served by /api/graph-votes (W2-A). For a multi-block RouteCluster, sum
the per-block deduped counts. Show the per-type breakdown for the block(s), not the lone edge.

CONSTRAINTS: don't change vote CASTING (still casts blockEdgeIds; server propagates). Fall back to
the edge summary when block data is absent. Add a unit test for the per-block-sum aggregation. Run
locally, select an edge in a multi-edge block, confirm the modal shows the deduped block totals.
Summarize the new modal data source + aggregation.
```

### W3-C — Route ghost waypoint uses verbatim path (FRONTEND, issue 4)
```
You are in the city-edit repo (branch fix/unify-voting). DEPENDS ON W1-B (RouteCluster.pathEdgeIds)
and W1-C (path_edge_ids on the wire). FIRST read docs/cluster-model.md §4 + issue 4, then
MapView.tsx (handleRouteProposalClick, waypoint insertion), routeProposals.ts (chooseAnchorOrder),
and how route legs are computed/rendered (OSRM routing integration + the selection model).

TASK: dropping a route proposal as a ghost waypoint must insert its two anchors (ordered via
chooseAnchorOrder to minimize total duration) but set the A→B leg to the proposal's
path_edge_ids VERBATIM — NOT an OSRM/shortest-path recompute. All other legs route normally. The
displayed + voted route then follows the proposal's (deliberately non-fastest) corridor. Carry
path_edge_ids through the Selection/route model so the verbatim leg survives a re-render.

CONSTRAINTS: only the A→B leg is verbatim; everything else uses normal routing. (Optional, only if
cheap: dragging an anchor afterward reverts that leg to normal routing.) Add a test asserting the
A→B leg edge sequence equals path_edge_ids (docs §6). Run locally, drop a route proposal, confirm
the route follows the corridor not the shortcut. Summarize how the verbatim leg flows through state.
```

### W3-D — End-to-end verification + changelog report (VERIFY)
```
You are in the city-edit repo (branch fix/unify-voting). Runs AFTER W3-A/B/C. FIRST read
root CLAUDE.md (changelog rules + FILE_CONTEXT in changelog/build_report.py) and docs/cluster-model.md.

TASK: 1) Run the full FE + BE test suites and report failures. 2) Bring up local dev (Redis :6379,
Flask :5001, Vite :3000 per CLAUDE.md) and verify end-to-end: hover edge -> block highlights;
select -> modal shows deduped block totals; cast a vote -> all block edges update + survive reload
(propagation); route proposal selects/hovers like a point; drop route proposal -> route follows the
verbatim corridor. Use the browser-AI verification style (describe what to check on screen).
3) Write changelog/2026-06-18-unify-voting.html via the Python builder with the per-file
"where does this block sit" context diagrams (add FILE_CONTEXT entries for every changed file),
link it from changelog/index.html.

CONSTRAINTS: don't deploy. Report a concrete pass/fail checklist. Summarize residual risks +
anything still edge-level that should be block-level.
```
