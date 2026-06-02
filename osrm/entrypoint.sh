#!/bin/sh
set -e

PROFILE="${OSRM_PROFILE:-foot}"
PBF_URL="${OSRM_PBF_URL:-https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf}"
# Optional bbox to clip a large (e.g. whole-state) extract before processing.
# Format: "west,south,east,north". Leave unset when PBF_URL is already a
# city-sized extract (e.g. the BBBike per-city files).
BBOX="${OSRM_BBOX:-}"
DATA_DIR="/data"
PBF_FILE="$DATA_DIR/region.osm.pbf"
OSRM_FILE="$DATA_DIR/region.osrm"

if [ ! -f "$OSRM_FILE.cell_metrics" ]; then
  echo "[OSRM] Processed data not found, building from scratch..."

  if [ ! -f "$PBF_FILE" ]; then
    echo "[OSRM] Downloading PBF from $PBF_URL ..."
    wget -q --show-progress -O "$DATA_DIR/source.osm.pbf" "$PBF_URL"
    echo "[OSRM] Download complete ($(du -h "$DATA_DIR/source.osm.pbf" | cut -f1))"

    if [ -n "$BBOX" ] && command -v osmium >/dev/null 2>&1; then
      echo "[OSRM] Clipping to bbox $BBOX with osmium..."
      osmium extract -b "$BBOX" "$DATA_DIR/source.osm.pbf" -o "$PBF_FILE" --overwrite
      rm -f "$DATA_DIR/source.osm.pbf"
      echo "[OSRM] Clipped extract size: $(du -h "$PBF_FILE" | cut -f1)"
    else
      if [ -n "$BBOX" ]; then
        echo "[OSRM] WARNING: OSRM_BBOX set but osmium not available — using full extract"
      fi
      mv "$DATA_DIR/source.osm.pbf" "$PBF_FILE"
    fi
  fi

  echo "[OSRM] Extracting with $PROFILE profile..."
  osrm-extract -p /opt/$PROFILE.lua "$PBF_FILE"

  echo "[OSRM] Partitioning..."
  osrm-partition "$OSRM_FILE"

  echo "[OSRM] Customizing..."
  osrm-customize "$OSRM_FILE"

  rm -f "$PBF_FILE"
  echo "[OSRM] Build complete"
else
  echo "[OSRM] Using existing processed data"
fi

echo "[OSRM] Starting routed (MLD algorithm)..."
exec osrm-routed --algorithm mld --port 5000 "$OSRM_FILE"
