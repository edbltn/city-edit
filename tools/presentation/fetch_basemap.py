"""Fetch the app's real basemap tiles (CARTO dark, nolabels + only_labels)
and stitch a 1600x900 crop for the opening slide.

Same tile URLs the client uses (mapStyles.ts TILE_DARK / TILE_DARK_LABELS),
same split: label-free raster underneath, labels re-added on top.
"""

import io
import math
import urllib.request
from pathlib import Path

from PIL import Image

OUT = Path(__file__).with_name("basemap.png")
LAT, LON = 40.7415, -73.9895
ZOOM = 16              # leaflet zoom; @2x tiles → 512 px each
W, H = 1600, 900
NOLABELS = "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png"
LABELS = "https://a.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}@2x.png"
TILE = 512


def world_px(lat, lon, z):
    n = 2 ** z * TILE
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "city-edit-presentation/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGBA")


def mosaic(template):
    cx, cy = world_px(LAT, LON, ZOOM)
    left, top = cx - W / 2, cy - H / 2
    tx0, ty0 = int(left // TILE), int(top // TILE)
    tx1, ty1 = int((left + W) // TILE), int((top + H) // TILE)
    canvas = Image.new("RGBA", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            url = template.format(z=ZOOM, x=tx, y=ty)
            canvas.paste(fetch(url), ((tx - tx0) * TILE, (ty - ty0) * TILE))
    ox, oy = int(left - tx0 * TILE), int(top - ty0 * TILE)
    return canvas.crop((ox, oy, ox + W, oy + H))


base = mosaic(NOLABELS)
base.alpha_composite(mosaic(LABELS))     # labels at full strength (LABEL_OPACITY_DARK = 1.0)
base.convert("RGB").save(OUT, optimize=True)
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
