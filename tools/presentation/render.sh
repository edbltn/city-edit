#!/bin/bash
# Rebuild the story panels, then render 2x PNGs + the PDF deck.
# Usage: tools/presentation/render.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../../docs/presentation/story"
SHELL_BIN="/Users/ericbolton/.cache/puppeteer/chrome-headless-shell/mac_arm-148.0.7778.97/chrome-headless-shell-mac-arm64/chrome-headless-shell"

python3 "$HERE/build.py"

# A raw & or < in slide copy silently produces an SVG the browser refuses to
# render (the PDF page comes out blank), so parse every panel before shipping.
python3 - "$OUT" <<'PY'
import glob, sys, xml.dom.minidom
bad = []
for f in sorted(glob.glob(f"{sys.argv[1]}/*.svg")):
    try:
        xml.dom.minidom.parse(f)
    except Exception as e:
        bad.append(f"{f}: {e}")
if bad:
    sys.exit("invalid SVG:\n  " + "\n  ".join(bad))
PY

mkdir -p "$OUT/png"
for f in "$OUT"/*.svg; do
  n="$(basename "$f" .svg)"
  "$SHELL_BIN" --disable-gpu --virtual-time-budget=8000 --window-size=1600,900 \
    --force-device-scale-factor=2 --hide-scrollbars \
    --screenshot="$OUT/png/$n.png" "file://$f" 2>/dev/null
done

"$SHELL_BIN" --disable-gpu --virtual-time-budget=25000 --no-pdf-header-footer \
  --print-to-pdf="$OUT/city-edit-story.pdf" "file://$OUT/deck.html" 2>/dev/null

echo "panels + png/ + city-edit-story.pdf → $OUT"
