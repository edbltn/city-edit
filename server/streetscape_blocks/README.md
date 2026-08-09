# Streetscape blocks — Layer 2 of the three-layer model

Generates the **block polygons** you vote on: one shape per place a person would
name, plus the edge→block mapping that binds them to the votable graph. Blocks
are the aggregation / display / interaction grain of the vote system — see
[docs/three-layer-model.md](../../docs/three-layer-model.md) §2. The OSM routing
graph stays the source of truth for route-finding and vote storage.

> **How the algorithm works — including every tuning knob and why it is where it
> is — lives in [docs/algorithms/01-block-identification.md](../../docs/algorithms/01-block-identification.md).**
> That dossier is bound to `build_blocks_graph_first.py` by `make docs-check`, so
> it cannot drift. This README is the *operator's* view: how to run a bake and
> what the tile step guarantees.

`build_blocks_graph_first.py` is the only generator. It decides membership
**topologically** (junction clusters → corridors → a merge fixpoint) and derives
geometry from that membership, which is the reverse of the earlier
centreline-buffer-then-Voronoi generators it replaced.

## Adding a city (one command)

```bash
# once: the geo venv (osmnx/geopandas are too heavy for the server venv)
python3 -m venv env && ./env/bin/pip install uv && ./env/bin/uv pip install osmnx geopandas shapely matplotlib
brew install tippecanoe

# after the city's walk graph exists (refresh_osm.py --city <id>):
./build_city_blocks.sh <city-id>            # or: refresh_osm.py --city <id> --blocks
```

Artifacts land next to the graph: `osm_data/<city>/edge_blocks_<network>.npy`
(+ `.json` meta) and `blocks.pmtiles`. The bake stamps the graph's
`topology_etag`, so **rebuilding the graph invalidates the mapping** and this
script must re-run — Flask refuses a mapping stamped for a different topology and
serves a zero-block map instead.

Two standalone auditors re-check a shipped bake independently of the build's own
audit: `scan_overlaps.py` (no two block polygons overlap) and
`scan_contiguity.py` (each block is one connected piece).

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

## Historical: evaluation against planimetric ground truth

The earlier centreline-buffer generator was calibrated against NYC's planimetric
roadbed + sidewalk open data, scoring median **IoU 0.84**, median area ratio
**1.00**, and median centroid offset ~1 m over ~82k shared segments. That is the
evidence behind the per-class `HALF_WIDTH` values the current builder still uses
for its corridor tubes.

The comparison harness and its NYC reference generator have since been removed;
the numbers above are recorded here because the knobs they justified are still
live. See the dossier's "Extension points" for how a city with its own
planimetric data could supply real right-of-way polygons without touching the
membership rules.
