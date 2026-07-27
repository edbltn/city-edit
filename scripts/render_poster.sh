#!/bin/bash
# Render a poster HTML file to PNG via chrome-headless-shell.
# Usage: render_poster.sh <html-file> <out.png> [width] [height] [scale]
set -euo pipefail
HTML="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
OUT="$2"
W="${3:-850}"
H="${4:-1134}"
SCALE="${5:-1}"
SHELL_BIN="/Users/ericbolton/.cache/puppeteer/chrome-headless-shell/mac_arm-148.0.7778.97/chrome-headless-shell-mac-arm64/chrome-headless-shell"
"$SHELL_BIN" --disable-gpu --virtual-time-budget=10000 \
  --window-size="$W,$H" --force-device-scale-factor="$SCALE" --hide-scrollbars \
  --screenshot="$OUT" "file://$HTML" 2>/dev/null
echo "rendered $OUT"
