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

The script runs: procedural street blocks → `build_node_blocks.py` (one 12 m
disc block per walk-graph junction, punched out of the street blocks so a route
never selects a perpendicular street's block at an intersection) → edge→block
bake (pass 1) → `build_foot_blocks.py` for the uncovered edges (park paths,
plazas — severed at the junction discs so each path segment is its own block;
makes the mapping **total**) → final bake → `blocks.pmtiles`. Artifacts land
next to the graph (`osm_data/<city>/edge_blocks_<network>.npy/.json`,
`blocks.pmtiles`); the bake stamps the graph's `topology_etag`, so rebuilding
the graph invalidates the mapping and the script must re-run.

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
