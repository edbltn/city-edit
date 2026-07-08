#!/usr/bin/env bash
# One-command Layer-2 block build for a city (docs/three-layer-model.md §2.2).
#
#   ./build_city_blocks.sh <city-id> [network]      # e.g. ./build_city_blocks.sh philly
#
# Pipeline:
#   1. build_blocks_generic.py    procedural street blocks from OSM alone (geo venv)
#   2. build_node_blocks.py       junction Voronoi cells (max-radius clipped),
#                                 punched out of street blocks
#   3. build_edge_blocks.py       edge→block bake pass 1 (server venv; topological
#                                 junction capture; marks gaps as −1)
#   4. build_foot_blocks.py       graph-component foot blocks for the −1 edges
#                                 (park paths, plazas), membership by construction
#   5. build_edge_blocks.py       final bake (applies the foot sidecar)
#   6. merge_degenerate_blocks.py stubs → node blocks; fake junctions → corridors
#                                 (writes blocks_final_<city>.geojson + remapped npy)
#   7. tippecanoe                 blocks.pmtiles next to the graph (display layer)
#
# Run AFTER the city's walk graph exists (refresh_osm.py --city <id>): the bake
# stamps the graph's topology_etag, so a graph rebuild invalidates the mapping
# and this script must be re-run (refresh_osm.py --blocks does both). Always
# start from step 1: steps 2+ mutate blocks_generic_<city>.geojson in place
# (node cells are punched out of street polygons).
set -euo pipefail

CITY="${1:?usage: build_city_blocks.sh <city-id> [network]}"
NETWORK="${2:-streets}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$(cd "$HERE/.." && pwd)"
GEO_PY="$HERE/env/bin/python"       # osmnx/geopandas/shapely venv (heavy geo deps)
OUT="${BLOCKS_OUT:-$HERE/output}"
BLOCKS_FILE="$OUT/blocks_generic_${CITY}.geojson"
FINAL_FILE="$OUT/blocks_final_${CITY}.geojson"

[ -x "$GEO_PY" ] || { echo "missing geo venv: $HERE/env (see README.md)"; exit 1; }
[ -x "$SERVER/env/bin/python" ] || { echo "missing server venv: $SERVER/env"; exit 1; }

srv_py() { (cd "$SERVER" && ./env/bin/python "$@"); }

# cities.py bbox is (south, west, north, east); the generator wants "W,S,E,N".
BBOX="$(srv_py -c "
from cities import CITIES
s, w, n, e = CITIES['$CITY'].bbox
print(f'{w},{s},{e},{n}')
")"
DATA_DIR="$SERVER/$(srv_py -c "
from cities import CITIES
print(CITIES['$CITY'].data_dir)
")"

echo "== [1/7] procedural street blocks ($CITY, bbox $BBOX)"
CITY="$CITY" BBOX="$BBOX" BLOCKS_OUT="$OUT" "$GEO_PY" "$HERE/build_blocks_generic.py"

echo "== [2/7] junction Voronoi cells (each junction cluster is its own block)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_OUT="$OUT" \
  srv_py streetscape_blocks/build_node_blocks.py

echo "== [3/7] edge→block bake (pass 1: find uncovered edges)"
# A stale foot sidecar from a previous run carries dead block ids — never let
# pass 1 apply it; build_foot_blocks.py rewrites it fresh in step 4.
rm -f "$DATA_DIR/foot_clusters_${NETWORK}.npz"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_FILE="$BLOCKS_FILE" \
  srv_py streetscape_blocks/build_edge_blocks.py

echo "== [4/7] foot blocks for uncovered edges (graph components)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_OUT="$OUT" \
  srv_py streetscape_blocks/build_foot_blocks.py

echo "== [5/7] edge→block bake (final, against completed block set)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_FILE="$BLOCKS_FILE" \
  srv_py streetscape_blocks/build_edge_blocks.py

echo "== [6/7] merge degenerate blocks (stubs + fake junctions)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_OUT="$OUT" \
  srv_py streetscape_blocks/merge_degenerate_blocks.py

echo "== [7/7] blocks.pmtiles"
if command -v tippecanoe >/dev/null 2>&1; then
  # --use-attribute-for-id: block_id becomes the NATIVE feature id (required by
  # the client's setFeatureState heat/selection; MapLibre reads native ids, no
  # promoteId). Matches how the existing nyc blocks.pmtiles was built.
  tippecanoe -o "$DATA_DIR/blocks.pmtiles" -zg --drop-densest-as-needed \
    --extend-zooms-if-still-dropping --coalesce-densest-as-needed \
    --use-attribute-for-id=block_id \
    -l blocks --force "$FINAL_FILE"
else
  echo "tippecanoe not found — skipping PMTiles (install: brew install tippecanoe)"
fi

echo "DONE — artifacts:"
ls -la "$DATA_DIR"/edge_blocks_"$NETWORK".* "$DATA_DIR"/blocks.pmtiles 2>/dev/null || true
