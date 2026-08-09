---
title: Route finding
description: What decides the path between two dropped waypoints, and how that path becomes votable edge ids.
sources:
  - path: server/osrm_router.py
    anchors: [OsrmRouter, OSRM_MODE_MAP, calculate_route, extract_all_segments]
  - path: server/router_interface.py
    anchors: [RouterInterface, calculate_route]
  - path: server/python_router.py
    anchors: [PythonRouter, nearest_node_coords, reverse_geocode]
  - path: server/foot_profile.py
    anchors: [way_is_foot_routable, node_blocks_foot, BARRIER_BLACKLIST]
  - path: server/vote_store.py
    anchors: [osm_nodes_to_edge_ids]
---

# Route finding

## Why it exists

When you drop a start and an end, something has to decide which streets lie
between them — and, crucially, **which votable edges that path corresponds to**.
Drawing a line is the easy half. The hard half is that the line must resolve to
edge ids in *our* graph, exactly, or the vote lands somewhere else.

Today there is exactly one routing profile in the product: **pedestrian**. That
is a product decision, not an architectural one — the plumbing is mode-aware
throughout, and this dossier marks where a second profile would attach.

## Inputs and outputs

| | |
|---|---|
| **In** | `POST /api/routes` — `{ map, start, end, waypoints[] }` |
| **Out** | `route.geometry` (GeoJSON), `desire_path_segments` (the drawn polyline), **`edge_ids`** (the votable set), `vote_mode` |

## Pseudocode

```
POST /api/routes:
    if the map's network is a STATION network:
        400 routing_disabled       # you vote on points, not paths;
                                   # the client gates this too

    route = OSRM /route/v1/foot/{lon,lat;...}
                ?overview=full&geometries=geojson
                &steps=false&annotations=nodes        # <- the load-bearing bit

    if OSRM failed:
        404, no fallback           # deliberately: see below

    segments = extract_all_segments(route.geometry)
    edge_ids = osm_nodes_to_edge_ids(route.osm_node_ids,
                                     graph.osm_to_graph_idx,
                                     graph.node_pair_to_edge)
    if edge_ids is empty:
        log a warning — a route that maps to nothing is a real defect
```

### Why `annotations=nodes` is the whole design

The votable graph is built from **the same OSM PBF and the same foot filter as
OSRM** (`server/foot_profile.py` mirrors `osrm/foot.lua` v5.25.0 — keep them in
sync). That makes our topology a superset of OSRM's foot network, and it means
OSRM's returned OSM node ids resolve **directly** into our edge ids. No
coordinate matching, no tolerance, no nearest-segment snapping.

There used to be a coordinate-rounding fallback for when node mapping came up
empty. It masked genuine mapping failures, so it was removed. An empty `edge_ids`
now surfaces the problem instead of hiding it.

### Why there is no Python fallback

`server/python_router.py` still contains a Dijkstra router, but `/api/routes`
**does not call it**. A failed OSRM route returns 404 and the client draws a
straight connector for that one segment. Silently substituting a second engine
with different snapping and different topology is exactly what hid the
proposal-mid-waypoint routing failures. One router, one topology, visible
failures.

`PythonRouter` is not dead code, though — it is the **graph provider**, and owns
the two jobs OSRM cannot do:

- `nearest_node_coords` — snapping a click or a geocode to a graph node;
- `reverse_geocode` — naming an intersection by BFS-ing up to 5 hops from the
  nearest node (pedestrian plazas can be wide) and collecting street names.

### Where else routing happens

Corridor growth in [dossier 03](03-route-proposals.md) runs its **own** bounded
A\* on the client (`boundedAStar`) rather than calling OSRM. That is not
duplication for its own sake: it needs thousands of "would routing return this
stretch?" answers per recompute, at zero latency, with a hard determinism
guarantee. It shares the topology, not the engine.

## Tuning knobs

**There are none worth tabulating**, and that is the interesting fact about this
algorithm. Routing has no local constants to tune: its entire behaviour lives in
two files that are not knobs but *definitions* —

- `osrm/foot.lua` — the pinned OSRM foot profile (v5.25.0), which decides what
  OSRM will route over;
- `server/foot_profile.py` — its mirror (`way_is_foot_routable`,
  `node_blocks_foot`, `BARRIER_BLACKLIST`), which decides what our votable graph
  contains.

**These two must change together.** Divergence means OSRM routes over an edge our
graph does not have, and the vote silently lands nowhere.
`server/tests/validate_osrm_topology.py` is the check that they still agree, and
it is the test to run after touching either.

`OSRM_MODE_MAP` (`server/osrm_router.py`) maps `walk/bike/drive` → `foot/bicycle/car`.
Only `walk` is reachable today: `/api/routes` hardcodes `mode="walk"`.

## Invariants

- **One PBF, one filter.** The votable graph and the OSRM dataset are built from
  the same source with equivalent filters. `validate_osrm_topology.py` confirms
  OSRM node ids resolve to topology edges.
- **Node ids, not coordinates.** Route → edge mapping is by OSM node id only.
- **Failures are visible.** No engine substitution, no coordinate fallback; a
  route that maps to zero edges logs a warning.
- **Station networks never route.** Gated on both sides.
- **Edge ids shift when the graph is rebuilt.** A graph deploy requires a vote
  resnap (`graph_reload`) and a block rebake — see
  [dossier 01](01-block-identification.md) and the deployment runbook.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| Votes landed on edges the user never selected | A coordinate-rounding fallback matched the wrong nearby edge when node mapping failed | Fallback deleted; empty `edge_ids` now warns |
| Proposal mid-waypoint routes silently disagreed with the drawn path | The Python router answered when OSRM failed, with different snapping | No fallback; 404 + straight connector |
| Route votes didn't map at all | The votable graph was built from a different extract than OSRM's | Rebuilt from the same PBF + foot filter (~2× the NYC graph size, worth it) |
| A geocode stranded an anchor on a severed esplanade → a 7.75 km hairpin | Routing was correct; the *input* was wrong | `DETOUR_RATIO_MAX` demotion at import time |

## Extension points — a second profile

This is the placeholder the product will eventually need. Everything below
already exists or is a small, contained change:

1. **The engine is already multi-profile.** `OSRM_MODE_MAP` maps `walk/bike/drive`
   to `foot/bicycle/car`, and `OsrmRouter.calculate_route` takes `mode`. A
   separate bicycle-legality OSRM (`bicycle-flat.lua` on :5006) has already been
   run for the Citibike counter-vote import, so the pattern is proven.
2. **`/api/routes` hardcodes `mode="walk"`.** That is the one line that decides
   the product's answer today. Threading a mode through would mean: accept it in
   the request, validate it against the map's network, and return it as
   `vote_mode` (the field already exists in the response for exactly this).
3. **The votable graph would have to match.** This is the real cost, and the
   reason it hasn't been done casually. A bicycle profile routes over edges the
   foot graph may not contain. Either the graph becomes a superset of *both*
   profiles (`foot_profile.py` grows a mode dimension), or each network gets its
   own graph and its own edge-id space.
4. **Per-map, not global.** Maps already carry a `network` (`streets`, `ebikes`).
   A routing profile belongs on the same object, so a bike map routes as a bike
   and a walking map doesn't change under existing voters' feet.
5. **Cost functions beyond distance.** OSRM profiles support arbitrary weights —
   comfort, elevation, safety, crash exposure. City Edit already computes crash
   rankings (`scripts/build_intersection_rankings.py`); feeding those into a
   "safest route" profile is the most interesting version of this, and would make
   the *routing itself* an argument the map is making.
