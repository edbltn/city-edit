#!/usr/bin/env python3
"""
Check every generated poster for the two failures a rendered page can hide:
content pushed off the bottom of the sheet, and a missing footer.

The poster is a fixed 850x1134 with `overflow: hidden`, so an overlong layout
does not error or reflow — it silently amputates the wordmark. This caught a
whole class of posters (every 2-, 3- and 4-stage crossing) after a row was
added to the template and only the one-stage proof was re-rendered.

Usage: python scripts/check_wait_posters.py [--dir data/posters/wait]
"""

import argparse
import csv
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent

# Template geometry in CSS pixels; the renderer's scale factor is inferred.
PAGE_W, PAGE_H = 850, 1134
BOTTOM_PAD = 38        # .page padding-bottom — must stay clear of ink
FOOTER_BAND = (86, 44)  # from the bottom: where the wordmark row lives
REG_TICK = (35, 20)     # registration ticks live here; ignore them


def dark_pixels(im, y0, y1, x0=0.0, x1=1.0, thresh=140):
    W, H = im.size
    box = (int(W * x0), H - y1, int(W * x1), H - y0)
    return sum(1 for p in im.crop(box).convert("L").getdata() if p < thresh)


def check(png, scale):
    im = Image.open(png)
    s = scale
    # The wordmark sits bottom-right and is the last thing on the page.
    footer = dark_pixels(im, FOOTER_BAND[1] * s, FOOTER_BAND[0] * s, 0.60, 1.0)
    # Below the footer there is only white paper and the two corner ticks.
    bleed = dark_pixels(im, 0, (BOTTOM_PAD - 20) * s, 0.10, 0.90)
    return footer, bleed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=REPO / "data" / "posters" / "wait")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.dir / "manifest.csv")))
    if not rows:
        sys.exit("no manifest")
    first = Image.open(REPO / rows[0]["png"])
    scale = first.size[0] // PAGE_W
    print(f"{len(rows)} posters at {first.size[0]}x{first.size[1]} (scale {scale})")

    bad = []
    for m in rows:
        footer, bleed = check(REPO / m["png"], scale)
        why = []
        if footer < 400:
            why.append(f"footer missing ({footer} dark px)")
        if bleed > 30:
            why.append(f"content past the bottom margin ({bleed} dark px)")
        if why:
            bad.append((m["code"], m["name"], m["stages"], "; ".join(why)))

    for code, name, stages, why in bad:
        print(f"  FAIL {code} {name} ({stages}-stage) — {why}")
    if bad:
        sys.exit(f"\n{len(bad)}/{len(rows)} posters are clipped")
    print("  all footers present, nothing past the bottom margin")


if __name__ == "__main__":
    main()
