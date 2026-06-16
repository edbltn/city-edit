#!/bin/bash
# Self-contained + resumable: pull (checkpointed) -> tiled Voronoi build -> PMTiles -> PNGs.
# Re-run safely after any interruption; cached pages/stages are skipped.
set -e
cd "$(dirname "$0")"
python3 pull_nyc.py
python3 build_nyc_blocks.py
OUT="${BLOCKS_OUT:-$(pwd)/output}"
if command -v tippecanoe >/dev/null 2>&1; then
  tippecanoe -o "$OUT/blocks_nyc.pmtiles" -zg --drop-densest-as-needed \
    --extend-zooms-if-still-dropping --coalesce-densest-as-needed \
    -l blocks --force "$OUT/blocks_nyc.geojson"
else
  echo "tippecanoe not found — skipping PMTiles (install: brew install tippecanoe)"
fi
python3 plot_blocks.py
echo "DONE -> $OUT"
ls -la "$OUT"/blocks_nyc.* "$OUT"/*.png 2>/dev/null
