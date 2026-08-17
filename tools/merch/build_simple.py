#!/usr/bin/env python3
"""
Two files, one per garment colour. Nothing else.

    ./env/bin/python build_simple.py

    out/simple/city-edit-black.png
    out/simple/city-edit-white.png

Each holds both prints for that colour — front on the left, back on the right,
separated by two clear inches — on one transparent canvas at 300 DPI. The order
details live in the covering email, not in the file, so the artwork is only ever
artwork.

Front is CITY EDIT (no code). Back is I LOVE THIS CITY, which carries the QR.

## One code per FILE, and what that costs

A file printed ten times puts the SAME code on ten shirts. The binding is
write-once, so the first person to scan one of those ten and cast a vote fixes
what all ten point at, for everyone. That is a real change from a unique code
per shirt and it is a consequence of asking for two files rather than a design
decision — splitting the run back out is a loop away.
"""

import sys
from pathlib import Path

import cairosvg
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

import iso  # noqa: E402
import qr_tee  # noqa: E402
import tee_codes  # noqa: E402
from palette import (  # noqa: E402
    ACCENT, GARMENT_BLACK, GARMENT_WHITE, INK_ON_BLACK, INK_ON_WHITE, svg,
)

OUT = Path(__file__).parent / "out" / "simple"
DPI = 300
GAP = 2 * DPI          # clear inches between the two prints

WAYS = {
    "black": {"ink": INK_ON_BLACK, "ground": GARMENT_BLACK, "accent": True},
    "white": {"ink": INK_ON_WHITE, "ground": GARMENT_WHITE, "accent": False},
}


def build(way: str) -> tuple[Path, str, dict]:
    spec = WAYS[way]
    code = tee_codes.mint(f"simple--{way}", 0)
    url = tee_codes.url_for(code)

    extra = {"letter_ink": ACCENT} if spec["accent"] else {}
    fw, fh, front = iso.tee_isogrid(ink=spec["ink"], ground=spec["ground"], **extra)
    bw, bh, back = qr_tee.tee_one_note(ink=spec["ink"], ground=spec["ground"], url=url)

    W = fw + GAP + bw
    H = max(fh, bh)
    body = (
        f'<g transform="translate(0,{(H - fh) / 2:.1f})">{front}</g>'
        f'<g transform="translate({fw + GAP},{(H - bh) / 2:.1f})">{back}</g>'
    )
    path = OUT / f"city-edit-{way}.png"
    cairosvg.svg2png(bytestring=svg(W, H, body).encode(), write_to=str(path),
                     output_width=W, output_height=H)
    with Image.open(path) as im:
        im.save(path, dpi=(DPI, DPI))

    return path, code, {
        "front": (fw / DPI, fh / DPI), "back": (bw / DPI, bh / DPI),
        "canvas": (W / DPI, H / DPI), "url": url,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for way in WAYS:
        path, code, info = build(way)
        rows.append((way, path, code, info))
        print(f"  {path}")
        print(f"     {info['canvas'][0]:.0f} x {info['canvas'][1]:.0f} in canvas · "
              f"front {info['front'][0]:.0f}x{info['front'][1]:.0f} in · "
              f"back {info['back'][0]:.0f}x{info['back'][1]:.0f} in · code {code}")

    seed = [{"code": c.lower(), "map_slug": "nyc-proposals",
             "vote_type": "Fix dangerous intersection",
             "headline": "I LOVE THIS CITY (BUT I HAVE ONE NOTE)",
             "src_tag": f"tee-{w}"} for w, _, c, _ in rows]
    (OUT / "seed_tees.sql").write_text(tee_codes.seed_sql(seed))
    print(f"  {OUT / 'seed_tees.sql'}   (2 codes — for us, not the printer)")


if __name__ == "__main__":
    main()
