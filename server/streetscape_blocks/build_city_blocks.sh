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
  #
  # EVERY BLOCK MUST SURVIVE TO EVERY ZOOM. This layer is a heatmap keyed by
  # feature id: a block tippecanoe thins out of a tile can't be lit, and the
  # thinning is density-proportional, so it eats the densest neighbourhoods —
  # exactly where the votes are — while sparse outskirts stay lit. Manhattan
  # used to lose 45% of its blocks at z12 and 88% at z10 (leaflet 13 / 11), so
  # zooming out to the city view blanked the heat over the core. Hence:
  #   --include=block_id       drop the six display attributes (seg_id,
  #                            road_class, road_name, area_m2, n_edges,
  #                            n_nodes/node_id) — the client reads NOTHING but
  #                            the feature id and its feature-state, and those
  #                            strings were most of every low-zoom tile.
  #   --no-tiny-polygon-reduction  keep sub-pixel blocks as themselves instead
  #                            of accumulating them into a shared "tiny
  #                            polygon" square, which silently merges away
  #                            their ids at z≤10.
  #   --maximum-tile-bytes     1.5MB, up from the 500KB default: only the
  #                            handful of city-wide z9–z11 tiles come near it
  #                            (z12+ stay under 450KB), and dropping features
  #                            there is precisely the bug.
  #   (no --coalesce-densest-as-needed) — coalescing merges same-attribute
  #                            features and keeps ONE id, destroying the rest.
  #                            It never fired before; with the attributes gone
  #                            every feature would look identical to it.
  # --drop-densest-as-needed stays as the last-resort backstop so a
  # pathological tile can't blow the archive up unboundedly.
  #
  # Bake to a temp file + atomic rename: the archive is read IN PLACE by the
  # running server (mmap'd pmtiles reader + /api/tile/), and tippecanoe -o
  # writes a SQLite journal at the target path mid-bake — readers must never
  # observe a half-written archive.
  # The temp name MUST end in .pmtiles: tippecanoe picks its output format
  # from the extension, so "blocks.pmtiles.tmp" silently wrote an mbtiles
  # (SQLite) archive that the server's PMTiles reader can't open.
  # -Z8: the client never asks below it. Leaflet zoom = MapLibre zoom + 1 and
  # the lowest min_zoom any city declares (cities.py) is 10, so ml z9 is the
  # widest tile ever requested — z8 leaves a level of margin. Everything below
  # was ~3MB of world-view tiles per city that nothing has ever fetched.
  # LOWERING A CITY'S min_zoom BELOW 9 MEANS LOWERING THIS TOO, or the blocks
  # vanish at that zoom (MapLibre draws nothing below a source's minzoom).
  tippecanoe -o "$DATA_DIR/blocks.tmp.pmtiles" -zg -Z8 --drop-densest-as-needed \
    --extend-zooms-if-still-dropping \
    --use-attribute-for-id=block_id --include=block_id \
    --no-tiny-polygon-reduction --maximum-tile-bytes=1500000 \
    -l blocks --force "$FINAL_FILE"

  # Gate the fresh archive BEFORE it goes live: a thinned low zoom is a
  # heatmap with holes, and the old bake shipped one for months because
  # nothing ever counted. Verified on the temp file so a bad bake never
  # replaces a good archive.
  N_BLOCKS="$(srv_py -c "
import json
print(json.load(open('$DATA_DIR/edge_blocks_${NETWORK}.json'))['n_blocks'])
")"
  srv_py streetscape_blocks/verify_blocks_tiles.py \
    "$DATA_DIR/blocks.tmp.pmtiles" --expect "$N_BLOCKS"

  mv -f "$DATA_DIR/blocks.tmp.pmtiles" "$DATA_DIR/blocks.pmtiles"
else
  echo "tippecanoe not found — skipping PMTiles (install: brew install tippecanoe)"
fi

echo "DONE — artifacts:"
ls -la "$DATA_DIR"/edge_blocks_"$NETWORK".* "$DATA_DIR"/blocks.pmtiles 2>/dev/null || true
