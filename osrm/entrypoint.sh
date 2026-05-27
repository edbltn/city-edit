#!/bin/sh
set -e

PROFILE="${OSRM_PROFILE:-foot}"
PBF_URL="${OSRM_PBF_URL:-https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf}"
DATA_DIR="/data"
PBF_FILE="$DATA_DIR/region.osm.pbf"
OSRM_FILE="$DATA_DIR/region.osrm"

if [ ! -f "$OSRM_FILE.cell_metrics" ]; then
  echo "[OSRM] Processed data not found, building from scratch..."

  if [ ! -f "$PBF_FILE" ]; then
    echo "[OSRM] Downloading PBF from $PBF_URL ..."
    wget -q --show-progress -O "$PBF_FILE" "$PBF_URL"
    echo "[OSRM] Download complete ($(du -h "$PBF_FILE" | cut -f1))"
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
