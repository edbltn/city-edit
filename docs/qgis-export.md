# Exporting a map's artifacts for QGIS

One command turns everything a City Edit map serves — blocks + votes, top
proposals, the votable route graph, and sample OSRM routes — into a single
GeoPackage you can drag into QGIS and play with.

```bash
# stack must be up: Redis :6379, Flask :5001 (SKIP_PREWARM=1 is fine — the
# export's first request loads the graph lazily)
server/streetscape_blocks/env/bin/python scripts/export_qgis_artifacts.py nyc-walkways
```

Output: `exports/qgis/<slug>/city-edit-<slug>.gpkg` (+ `manifest.json`
recording vote revision, topology/blocks versions, seed, and per-layer feature
counts). Open QGIS → drag the `.gpkg` in → pick the layers you want.

## Prerequisites

- **The local dev stack** (the export reads the same API a browser does):
  Redis on `:6379` and Flask on `:5001`. Vite is not needed.
- **The geo venv** — `server/streetscape_blocks/env` (geopandas/shapely/
  pyogrio). If missing, see `server/streetscape_blocks/README.md` for the
  one-time setup.
- **Block artifacts for the city** — `server/osm_data/<city>/edge_blocks_
  <network>.npy/.json` and `server/streetscape_blocks/output/blocks_generic_
  <city>.geojson` (built by `build_city_blocks.sh <city>`). Maps without them
  just skip the blocks layer.
- **vite-node** — already in `client-react/node_modules` (proposals are
  computed by the *actual client TS modules*, not a Python re-implementation).
- **OSRM** for the sample routes (however you normally run it for local dev);
  failed routes are skipped with a warning, everything else still exports.

## The layers

| Layer | Geometry | What it is |
|---|---|---|
| `blocks` | polygons | Layer-2 streetscape blocks joined with their aggregated votes: `net_votes`, `up_votes`, `down_votes`, `top_label`, and a `vote_types` JSON breakdown per block |
| `graph_edges` | lines | The full Layer-1 votable graph: `edge_id`, `from_node`/`to_node`, `block_id`, `net_votes`, `top_label` |
| `graph_nodes` | points | Voted nodes only by default (`--all-nodes` for every node — ~1.3M for NYC) |
| `proposals_point` | points | PBTPs (square pins): pin at the winning edge's midpoint; `covered_by_route` marks ones a same-type route proposal subsumes |
| `proposals_point_edges` | lines | The winning edge of each PBTP |
| `proposals_route` | lines | RBTPs (diamond pins): the ordered corridor path, with `score`, `n_blocks`, and the `block_ids` it covers |
| `proposals_route_blocks` | multilines | Each RBTP's full block-edge union — the set hovering highlights and voting casts on |
| `proposals_route_anchors` | points | The two terminal intersections of each RBTP |
| `sample_routes` | lines | OSRM geometry for sample A→B requests: 3 between top-RBTP anchors (`kind=proposal:<id>`) + N seeded-random pairs 0.8–6 km apart (`kind=random`) |
| `sample_route_edges` | multilines | The graph edges each sample route's `edge_ids` resolved to — overlay on `sample_routes` to check the OSRM→graph snap |

All layers are EPSG:4326.

## Options

```bash
… export_qgis_artifacts.py <slug> \
    [--api http://localhost:5001]        # point at another instance
    [--out DIR]                          # default exports/qgis/<slug>/
    [--routes 6] [--seed 42]             # sample-route count / determinism
    [--bbox=west,south,east,north]       # clip blocks + graph (proposals/routes stay whole)
                                         # (use the = form — a leading "-74…" otherwise trips argparse)
    [--all-nodes]                        # every graph node, not just voted ones
    [--skip blocks,graph,proposals,routes]  # rerun just one piece faster
```

For a laptop-friendly file, clip to a borough, e.g. Manhattan below Central
Park: `--bbox=-74.03,40.70,-73.93,40.77`.

## QGIS styling tips

- **blocks**: Graduated fill on `net_votes` (filter `net_votes != 0` to see
  just the voted ones), or Categorized on `top_label` to color by proposal
  type. `vote_types` holds the full per-type up/down JSON for the info panel.
- **graph_edges**: at city scale render as a simple 0.3pt line; add a
  rule-based override `net_votes > 0` with width/color by `net_votes` to get
  the heatmap look.
- **proposals**: Categorized on `label`; label routes with `score`. Put
  `proposals_route_blocks` under `proposals_route` in a muted color to see
  corridor vs. vote-set.
- **sample checks**: style `sample_routes` solid and `sample_route_edges`
  dashed in a contrasting color — anywhere they diverge is an OSRM↔graph
  mapping discrepancy worth a look.

## Fidelity notes

- Proposals are computed by `client-react/scripts/export-proposals.ts`, which
  imports `topProposals.ts` / `routeProposals.ts` / `graphTopology.ts` — the
  same code paths the app runs, against the same `/api/graph-topology` +
  `/api/graph-votes` responses. One deliberate difference: the PBTP tiebreak
  salt is fixed to 0 (the app randomizes it per page load), so exports are
  deterministic for a given vote state.
- The blocks layer joins votes by the `block_id` property of
  `blocks_generic_<city>.geojson` — the same file the edge→block bake read.
  The script sha-checks it against the bake metadata and warns if the
  polygons on disk no longer match the block ids being served.
- Votes come from Redis via the API at a specific `rev` (recorded in
  `manifest.json`); re-run the export after casting votes to refresh.
