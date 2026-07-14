#!/bin/bash
# Build (and optionally serve) a per-city BICYCLE-profile OSRM dataset.
#
# Purpose: bulk-import corrections. Citibike trips are ingested as votes by
# routing them through the FOOT profile ("pedestrianized"); to subtract the
# stretches a legal bike route would have used anyway (import_lyft counterpart:
# server/counter_lyft.py), we need a bike-based routing graph over the same OSM
# snapshot. Building from the city's own server/osm_data/<city>/source.osm.pbf
# guarantees OSM node ids align with the votable graph, so bike-route
# annotations resolve through the same osm_nodes_to_edge_ids mapping.
#
# The profile is osrm/bicycle-flat.lua: the image's stock bicycle.lua (same
# pinned v5.25.0 as the foot build) patched to route by shortest LEGAL path —
# distance-weighted, no pushing-the-bike fallback onto foot-only ways, no
# ferries. One-way restrictions and foot-only exclusions are exactly the
# divergences the counter-vote pass must leave unscathed; see the header of
# bicycle-flat.lua for the full delta list.
#
# Usage:
#   scripts/build_bike_osrm.sh <city> [--serve [port]]
#
# Output: server/osm_data/<city>/osrm-bike/bike.osrm* (dir is gitignored via
# server/osm_data/). --serve runs a detached container named
# city-edit-osrm-bike-<city> on the given host port (default 5006; NOT 5000 —
# macOS AirPlay squats it — and not 5005, the merged foot instance).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OSRM_IMAGE="osrm/osrm-backend:v5.25.0"

CITY="${1:?usage: build_bike_osrm.sh <city> [--serve [port]]}"
shift
SERVE=0
PORT=5006
if [ "${1:-}" = "--serve" ]; then
  SERVE=1
  PORT="${2:-5006}"
fi

# Keep in sync with server/cities.py (bbox there is south,west,north,east;
# osmium wants west,south,east,north) — same duplication as osrm/build-merged.sh.
case "$CITY" in
  nyc)     BBOX="-74.2591,40.4774,-73.7004,40.9176" ;;
  sf)      BBOX="-122.516,37.700,-122.354,37.832" ;;
  chicago) BBOX="-87.75,41.78,-87.58,42.02" ;;
  dc)      BBOX="-77.12,38.79,-76.91,39.00" ;;
  philly)  BBOX="-75.280,39.867,-74.956,40.138" ;;
  *) echo "ERROR: unknown city '$CITY'" >&2; exit 1 ;;
esac

SRC_PBF="$REPO_DIR/server/osm_data/$CITY/source.osm.pbf"
OUT_DIR="$REPO_DIR/server/osm_data/$CITY/osrm-bike"
[ -f "$SRC_PBF" ] || { echo "ERROR: $SRC_PBF missing (run refresh_osm.py first)" >&2; exit 1; }
mkdir -p "$OUT_DIR"

if [ ! -f "$OUT_DIR/bike.osrm.cell_metrics" ]; then
  # Clip first: nyc's source.osm.pbf is the whole-state extract; the clipped
  # city file keeps osrm-extract inside Docker Desktop's RAM budget.
  if [ ! -f "$OUT_DIR/bike.osm.pbf" ]; then
    echo "[bike-osrm] Clipping $CITY to $BBOX"
    osmium extract -b "$BBOX" "$SRC_PBF" -o "$OUT_DIR/bike.osm.pbf" --overwrite
  fi
  echo "[bike-osrm] osrm-extract (bicycle-flat profile) ..."
  docker run --rm -v "$OUT_DIR":/data -v "$REPO_DIR/osrm/bicycle-flat.lua":/profiles/bicycle-flat.lua:ro \
    "$OSRM_IMAGE" osrm-extract -p /profiles/bicycle-flat.lua /data/bike.osm.pbf
  echo "[bike-osrm] osrm-partition ..."
  docker run --rm -v "$OUT_DIR":/data "$OSRM_IMAGE" osrm-partition /data/bike.osrm
  echo "[bike-osrm] osrm-customize ..."
  docker run --rm -v "$OUT_DIR":/data "$OSRM_IMAGE" osrm-customize /data/bike.osrm
  echo "[bike-osrm] Build complete: $OUT_DIR/bike.osrm*"
else
  echo "[bike-osrm] Using existing dataset in $OUT_DIR"
fi

if [ "$SERVE" = 1 ]; then
  NAME="city-edit-osrm-bike-$CITY"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  echo "[bike-osrm] Serving on localhost:$PORT (container $NAME)"
  docker run -d --name "$NAME" -p "$PORT:5000" -v "$OUT_DIR":/data "$OSRM_IMAGE" \
    osrm-routed --algorithm mld --ip 0.0.0.0 --port 5000 /data/bike.osrm
fi
