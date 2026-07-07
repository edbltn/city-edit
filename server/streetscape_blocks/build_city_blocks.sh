#!/usr/bin/env bash
# One-command Layer-2 block build for a city (docs/three-layer-model.md §2.2).
#
#   ./build_city_blocks.sh <city-id> [network]      # e.g. ./build_city_blocks.sh philly
#
# Pipeline:
#   1. build_blocks_generic.py   procedural street blocks from OSM alone (geo venv)
#   2. build_node_blocks.py      junction-node discs, punched out of street blocks
#   3. build_edge_blocks.py      bake edge→block (server venv; marks gaps as −1)
#   4. build_foot_blocks.py      blocks for the −1 edges (park paths, plazas),
#                                severed at the junction discs
#   5. build_edge_blocks.py      re-bake against the completed block set
#   6. tippecanoe                blocks.pmtiles next to the graph (display layer)
#
# Run AFTER the city's walk graph exists (refresh_osm.py --city <id>): the bake
# stamps the graph's topology_etag, so a graph rebuild invalidates the mapping
# and this script must be re-run (refresh_osm.py --blocks does both).
set -euo pipefail

CITY="${1:?usage: build_city_blocks.sh <city-id> [network]}"
NETWORK="${2:-streets}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$(cd "$HERE/.." && pwd)"
GEO_PY="$HERE/env/bin/python"       # osmnx/geopandas/shapely venv (heavy geo deps)
OUT="${BLOCKS_OUT:-$HERE/output}"
BLOCKS_FILE="$OUT/blocks_generic_${CITY}.geojson"

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

echo "== [1/6] procedural street blocks ($CITY, bbox $BBOX)"
CITY="$CITY" BBOX="$BBOX" BLOCKS_OUT="$OUT" "$GEO_PY" "$HERE/build_blocks_generic.py"

echo "== [2/6] junction-node discs (each junction is its own block)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_OUT="$OUT" \
  srv_py streetscape_blocks/build_node_blocks.py

echo "== [3/6] edge→block bake (pass 1: find uncovered edges)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_FILE="$BLOCKS_FILE" \
  srv_py streetscape_blocks/build_edge_blocks.py

echo "== [4/6] foot blocks for uncovered edges"
CITY="$CITY" NETWORK="$NETWORK" srv_py streetscape_blocks/build_foot_blocks.py

echo "== [5/6] edge→block bake (final, against completed block set)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_FILE="$BLOCKS_FILE" \
  srv_py streetscape_blocks/build_edge_blocks.py

echo "== [6/6] blocks.pmtiles"
if command -v tippecanoe >/dev/null 2>&1; then
  # --use-attribute-for-id: block_id becomes the NATIVE feature id (required by
  # the client's setFeatureState heat/selection; MapLibre reads native ids, no
  # promoteId). Matches how the existing nyc blocks.pmtiles was built.
  tippecanoe -o "$DATA_DIR/blocks.pmtiles" -zg --drop-densest-as-needed \
    --extend-zooms-if-still-dropping --coalesce-densest-as-needed \
    --use-attribute-for-id=block_id \
    -l blocks --force "$BLOCKS_FILE"
else
  echo "tippecanoe not found — skipping PMTiles (install: brew install tippecanoe)"
fi

echo "DONE — artifacts:"
ls -la "$DATA_DIR"/edge_blocks_"$NETWORK".* "$DATA_DIR"/blocks.pmtiles 2>/dev/null || true
