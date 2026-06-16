# Streetscape blocks

Generates **block-level streetscape polygons** for NYC: one merged polygon per street
segment (between intersections), built from NYC planimetric roadbed + sidewalk geometry.
These are a simplified, votable view of the city; the OSM routing graph stays the
source of truth for route-finding.

## Output (written to `./output/`, ~regenerable, git-ignored)
- `blocks_nyc.geojson` — EPSG:4326, ~82k blocks. Attrs: `block_id, seg_id, road_class, road_name, area_m2`
- `blocks_nyc.pmtiles` — vector tiles (layer `blocks`) for MapLibre, same as `graph.pmtiles`
- `overview_by_class.png`, `window_check.png` — verification renders

## Run
```bash
pip install osmnx geopandas shapely matplotlib   # geo deps
brew install tippecanoe                           # for PMTiles (optional)
./run_all.sh                                       # pull -> build -> pmtiles -> pngs
```
Re-running is safe: the data pull is checkpointed per page and every stage is cached,
so an interruption resumes where it stopped. Takes ~15 min on a fresh run (Socrata is slow).

## How it works
1. **pull_nyc.py** — paginated, resumable pull of NYC roadbed (`i36f-5ih7`) + sidewalk
   (`52n9-sdep`) over the CityEdit nyc bbox, plus the osmnx drive graph (consolidated).
   Note: use the `52n9-sdep` sidewalk dataset — the `vfx9-tbb6` copy has broken geometry.
2. **build_nyc_blocks.py** — union roadbed + sidewalk into the full right-of-way, then
   partition it by **nearest centerline segment** (segment-Voronoi, tiled for memory).
   Width-agnostic; cuts cleanly at intersections with no radius to tune.
3. **plot_blocks.py** — verification PNGs.

## Known edge cases
Airports / big parkways / highway interchanges produce a few oversized blocks
(JFK terminal roads are the largest). Find them via `area_m2`; spot-fix manually.
