#!/bin/bash
# Produce a SINGLE merged .osm.pbf covering every supported city.
#
# OSRM doesn't care that the cities are geographically far apart — coordinates
# are global — so one routed instance can serve NYC, SF, and Chicago from one
# combined extract. We clip each source PBF to its city bbox first (NYC ships as
# the whole NY-state extract; clipping keeps the merged file small), then
# osmium-merge into one combined.osm.pbf. The OSRM extract/partition/customize
# runs in the next Docker stage (the osrm-backend image already ships those tools
# but is too old to apt-install osmium, so the merge happens here on bookworm).
#
# Keep the bboxes here in sync with server/cities.py (bbox is south,west,north,east
# there; osmium wants west,south,east,north).
set -euo pipefail

WORK="${WORK_DIR:-/build}"
OUT="${OUT_DIR:-/merged}"
mkdir -p "$WORK" "$OUT"

# name|pbf_url|west,south,east,north  (osmium bbox order)
CITIES=(
  "nyc|https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf|-74.2591,40.4774,-73.7004,40.9176"
  "sf|https://download.bbbike.org/osm/bbbike/SanFrancisco/SanFrancisco.osm.pbf|-122.516,37.700,-122.354,37.832"
  "chicago|https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf|-87.75,41.78,-87.58,42.02"
  "dc|https://download.bbbike.org/osm/bbbike/WashingtonDC/WashingtonDC.osm.pbf|-77.12,38.79,-76.91,39.00"
)

clipped_files=()
for entry in "${CITIES[@]}"; do
  IFS='|' read -r name url bbox <<< "$entry"
  src="$WORK/${name}-source.osm.pbf"
  clipped="$WORK/${name}.osm.pbf"

  echo "[build] Downloading $name from $url"
  wget -q -O "$src" "$url"
  echo "[build] $name source size: $(du -h "$src" | cut -f1)"

  echo "[build] Clipping $name to bbox $bbox"
  osmium extract -b "$bbox" "$src" -o "$clipped" --overwrite
  rm -f "$src"
  echo "[build] $name clipped size: $(du -h "$clipped" | cut -f1)"
  clipped_files+=("$clipped")
done

MERGED="$OUT/combined.osm.pbf"
echo "[build] Merging ${#clipped_files[@]} city extracts -> $MERGED"
osmium merge "${clipped_files[@]}" -o "$MERGED" --overwrite
rm -f "${clipped_files[@]}"
echo "[build] Done. Merged PBF: $(du -h "$MERGED" | cut -f1)"
