#!/usr/bin/env bash
# One-command Layer-2 block build for a city (docs/three-layer-model.md §2.2).
#
#   ./build_city_blocks.sh <city-id> [network]      # e.g. ./build_city_blocks.sh philly
#
# Pipeline (graph-first — see build_blocks_graph_first.py, which replaced the
# old five-script generate→map→patch→merge chain):
#   1. build_blocks_graph_first.py  edges grouped topologically (junction
#                                   clusters + corridors), polygons generated
#                                   FROM the groups — coverage and edge↔polygon
#                                   overlap hold by construction. Writes
#                                   blocks_final_<city>.geojson + the baked
#                                   edge_blocks_<network>.npy/.json mapping.
#   2. tippecanoe                   blocks.pmtiles next to the graph (display).
#
# Run AFTER the city's walk graph exists (refresh_osm.py --city <id>): the bake
# stamps the graph's topology_etag, so a graph rebuild invalidates the mapping
# and this script must be re-run (refresh_osm.py --blocks does both).
set -euo pipefail

CITY="${1:?usage: build_city_blocks.sh <city-id> [network]}"
NETWORK="${2:-streets}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$(cd "$HERE/.." && pwd)"
OUT="${BLOCKS_OUT:-$HERE/output}"
FINAL_FILE="$OUT/blocks_final_${CITY}.geojson"

[ -x "$SERVER/env/bin/python" ] || { echo "missing server venv: $SERVER/env"; exit 1; }

srv_py() { (cd "$SERVER" && ./env/bin/python "$@"); }

DATA_DIR="$SERVER/$(srv_py -c "
from cities import CITIES
print(CITIES['$CITY'].data_dir)
")"

echo "== [1/2] graph-first blocks (grouping → polygons → baked mapping)"
CITY="$CITY" NETWORK="$NETWORK" BLOCKS_OUT="$OUT" \
  srv_py streetscape_blocks/build_blocks_graph_first.py

echo "== [2/2] blocks.pmtiles"
if command -v tippecanoe >/dev/null 2>&1; then
  # --use-attribute-for-id: block_id becomes the NATIVE feature id (required by
  # the client's setFeatureState heat/selection; MapLibre reads native ids, no
  # promoteId). Matches how the existing nyc blocks.pmtiles was built.
  # Bake to a temp file + atomic rename: the archive is read IN PLACE by the
  # running server (mmap'd pmtiles reader + /api/tile/), and tippecanoe -o
  # writes a SQLite journal at the target path mid-bake — readers must never
  # observe a half-written archive.
  # The temp name MUST end in .pmtiles: tippecanoe picks its output format
  # from the extension, so "blocks.pmtiles.tmp" silently wrote an mbtiles
  # (SQLite) archive that the server's PMTiles reader can't open.
  tippecanoe -o "$DATA_DIR/blocks.tmp.pmtiles" -zg --drop-densest-as-needed \
    --extend-zooms-if-still-dropping --coalesce-densest-as-needed \
    --use-attribute-for-id=block_id \
    -l blocks --force "$FINAL_FILE"
  mv -f "$DATA_DIR/blocks.tmp.pmtiles" "$DATA_DIR/blocks.pmtiles"
else
  echo "tippecanoe not found — skipping PMTiles (install: brew install tippecanoe)"
fi

echo "DONE — artifacts:"
ls -la "$DATA_DIR"/edge_blocks_"$NETWORK".* "$DATA_DIR"/blocks.pmtiles 2>/dev/null || true
