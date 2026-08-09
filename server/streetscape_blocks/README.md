# Streetscape blocks — Layer 2 of the three-layer model

Generates **block-level streetscape polygons**: one merged polygon per street
segment (between intersections) covering its right-of-way, plus foot-path blocks
for everything the street network doesn't reach. Blocks are the aggregation /
display / interaction grain of the vote system — see
[docs/three-layer-model.md](../../docs/three-layer-model.md) §2. The OSM routing
graph stays the source of truth for route-finding and vote storage.

Two generators produce the same schema; the procedural one is the default:

| Generator | ROW surface | Use |
|---|---|---|
| `build_blocks_generic.py` | OSM centerlines buffered per road class | **Any city** (OSM-only, no municipal data) |
| `build_nyc_blocks.py` | NYC planimetric roadbed + sidewalk polygons | NYC ground truth / evaluation reference |

Both partition the surface by nearest centerline segment (segment-Voronoi over
the **same** consolidated osmnx drive graph, so `seg_id` matches 1:1 between
them), then dissolve per segment.

## Adding a city (one command)

```bash
# once: the geo venv (osmnx/geopandas are too heavy for the server venv)
python3 -m venv env && ./env/bin/pip install uv && ./env/bin/uv pip install osmnx geopandas shapely matplotlib
brew install tippecanoe

# after the city's walk graph exists (refresh_osm.py --city <id>):
./build_city_blocks.sh <city-id>            # or: refresh_osm.py --city <id> --blocks
```

The script runs: procedural street blocks → `build_node_blocks.py` (a 9 m disc
per walk-graph junction, overlapping discs merged into one multi-node block per
junction cluster, punched out of the street blocks so a route never selects a
perpendicular street's block at an intersection; also writes the
junction→block sidecar `node_clusters_<network>.npz`) → edge→block bake
(pass 1) → `build_foot_blocks.py` for the uncovered edges (park paths, plazas —
severed at the junction blocks so each path segment is its own block; makes the
mapping **total**) → final bake → `blocks.pmtiles`. Node blocks get their own
MAPPING rule in the bake, separate from the polygons: any edge whose midpoint
is within `NODE_CAPTURE_M` (12 m) of a junction maps to that junction's block
by graph distance — stub midpoints run past the 9 m drawn rim, and capture is
what holds the ladder at zero. Artifacts land
next to the graph (`osm_data/<city>/edge_blocks_<network>.npy/.json`,
`blocks.pmtiles`); the bake stamps the graph's `topology_etag`, so rebuilding
the graph invalidates the mapping and the script must re-run.

## The tile bake carries EVERY block to EVERY served zoom

`blocks.pmtiles` is not a decorative layer — it is the heatmap's geometry,
addressed by feature id (`--use-attribute-for-id=block_id`; the client sets
`heat`/`selected` feature-state per id and reads no properties at all). So a
block tippecanoe thins out of a tile simply cannot be lit, and tippecanoe
thins in proportion to **density** — it eats downtown first, which is exactly
where the votes are. Until 2026-08-09 the bake shipped that: Manhattan carried
61% of its blocks at z12, 24% at z11 and 17% at z10 (leaflet 13 / 12 / 11), so
zooming out to the city view blanked the heat over the core while the sparse
outskirts stayed lit.

What keeps it honest now:

- `--include=block_id` — the six display attributes (`seg_id`, `road_class`,
  `road_name`, `area_m2`, `n_edges`, `n_nodes`/`node_id`) never reach a tile.
  Nothing reads them, and they were most of every low-zoom tile's bytes.
- `--no-tiny-polygon-reduction` — sub-pixel blocks stay themselves instead of
  being accumulated into a shared "tiny polygon" square, which silently merges
  away their ids.
- `--maximum-tile-bytes=1500000` — only the handful of city-wide z9–z11 tiles
  come near it; z12 and up stay under 450KB.
- **no** `--coalesce-densest-as-needed` — coalescing merges same-attribute
  features and keeps ONE id. With the attributes gone every feature looks
  identical to it, so it would eat the layer.
- `verify_blocks_tiles.py` runs on the temp archive before the atomic rename
  and fails the bake if any zoom from z10 up carries under 95% of the blocks.

Net effect on NYC: z10–z13 all ≥99.9% complete, and the archive got ~20%
*smaller* — the dead attributes cost more than the recovered geometry.

## Evaluation against ground truth

`compare_blocks.py` scores procedural output against Brook's NYC planimetric
blocks — the reference this algorithm is tuned to mimic (`COMPARISON.md`,
`eval/RESULTS.md` + `eval/results_*.json`):

- median **IoU 0.84**, median area ratio **1.00**, median centroid offset ~1 m
  over ~82k shared segments;
- per-class half-widths (`HALF_WIDTH` in `build_blocks_generic.py`) are the
  tuned knob — a pure area/(2·length) calibration undershoots because Voronoi
  assigns intersection flare to segments;
- `eval/eval_mapping.py` justified the bake rule (midpoint containment + 30 m
  nearest-block snap): containment alone leaves ~20% of foot edges unmapped.

Run the comparison for a new city whenever a planimetric/ground-truth dataset
exists; otherwise sanity-check with `plot_blocks.py` renders.

## NYC ground-truth pipeline

```bash
./run_all.sh        # pull (resumable) -> planimetric build -> pmtiles -> pngs
```

`pull_nyc.py` pulls roadbed (`i36f-5ih7`) + sidewalk (`52n9-sdep` — the
`vfx9-tbb6` copy has broken geometry) paged + checkpointed; ~15 min fresh.

## Output (`./output/`, regenerable, git-ignored)

- `blocks_generic_<city>.geojson` / `blocks_nyc.geojson` — EPSG:4326. Attrs:
  `block_id, seg_id, road_class, road_name, area_m2` (foot blocks:
  `road_class="foot", seg_id=-1`, `block_id`s continuing).
- `blocks_compare_<city>.json` — comparison metrics.
- `overview_by_class.png`, `window_check.png` — verification renders.

## Known edge cases

Airports / big parkways / highway interchanges produce a few oversized blocks
(JFK terminal roads are the largest). Find them via `area_m2`; spot-fix manually.
