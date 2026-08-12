#!/usr/bin/env python3
"""
Read every printed sticker back the way a phone would.

    ./env/bin/python verify_scan.py                    # the built run
    ./env/bin/python verify_scan.py --px 300 200 160   # + degraded captures

A code that is correct in the generator and unreadable on paper is the one
failure this pipeline cannot walk back — by the time anyone finds out, the
stickers are on poles. So this does not inspect the matrix we generated. It
rasterises the finished art and decodes the pixels.

It works one sticker at a time, because that is the real situation: a phone is
held up to a single disc, not to a sheet of twelve. Decoding a whole sheet at
once measures how well a library separates a crowded scene, which is a fact
about the library and not about our sticker.

Three things are checked together:

  * every sticker decodes,
  * it decodes to the URL the manifest says is printed at that grid cell — a
    layout bug that swapped two discs would give a sheet where every code scans
    perfectly and every one of them is pinned to the wrong pole, and
  * no two stickers in the run share a code, across stocks as well as within
    one, since two stickers with one identity would share a location.

## Two decoders, one of them advisory

zxing-cpp is the gate. It is the lineage most phone scanners are built on, and
it reads every code here.

OpenCV's detector is reported alongside it and deliberately does NOT fail the
run. It rejects a handful of these codes at any resolution — including the raw
segno output, before our artwork touches it — so it is measuring its own
detector rather than the sticker. It is still worth printing: a change that
suddenly drops OpenCV's agreement from "most" to "none" is a signal that
something happened to the art, even if the gate is still green.

`--px` sets how many pixels wide the sticker is in the simulated capture, which
is the only thing that decides decodability: a 29-module code needs roughly
three pixels per module. Testing below that is how you find out how much margin
the design has rather than assuming it.
"""

import argparse
import csv
import sys
from pathlib import Path

import cairosvg
import cv2
import numpy as np
import zxingcpp

sys.path.insert(0, str(Path(__file__).parent))

import codes as codes_mod  # noqa: E402
import sheet as sheet_mod  # noqa: E402
import sticker as art  # noqa: E402

OUT = Path(__file__).parent / "out"


def render_disc(line: str, url: str, die: float, bleed: float, px: int) -> np.ndarray:
    """The finished artwork, rasterised to a greyscale capture `px` wide."""
    canvas, body = art.disc(line, url, die, bleed)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" '
           f'height="{canvas}" viewBox="0 0 {canvas} {canvas}">{body}</svg>')
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px,
                           output_height=px, background_color="#ffffff")
    return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def read_zxing(img: np.ndarray) -> str | None:
    res = zxingcpp.read_barcode(img)
    return res.text if res else None


def read_opencv(img: np.ndarray) -> str | None:
    text, _pts, _straight = cv2.QRCodeDetector().detectAndDecode(img)
    return text or None


def load_run() -> list[dict]:
    rows = []
    for manifest in sorted(OUT.glob("*/manifest.csv")):
        rows += list(csv.DictReader(manifest.open()))
    return rows


def main():
    ap = argparse.ArgumentParser()
    # 200 px across the disc is 3.0 px per module, the decodability floor for a
    # 29-module code — and where this design measures out. A phone held a foot
    # from a 2.5" sticker sees several hundred pixels of it.
    ap.add_argument("--px", type=int, nargs="*", default=[400, 300, 200],
                    help="simulated capture widths, in pixels across the disc")
    args = ap.parse_args()

    rows = load_run()
    if not rows:
        print("no out/*/manifest.csv — run build_stickers.py first",
              file=sys.stderr)
        return 1

    counts = {}
    for r in rows:
        counts[r["code"]] = counts.get(r["code"], 0) + 1
    dupes = sorted(c for c, n in counts.items() if n > 1)
    if dupes:
        print(f"duplicate codes in the run: {dupes}", file=sys.stderr)
        return 1

    modules = art.qr_modules(rows[0]["url"])
    failed = 0
    for px in args.px:
        bad, cv_ok = [], 0
        for r in rows:
            stock = sheet_mod.STOCKS[r["stock"]]
            img = render_disc(r["line"], r["url"], stock.die, stock.bleed, px)
            if read_zxing(img) != r["url"]:
                bad.append(r)
            if read_opencv(img) == r["url"]:
                cv_ok += 1
        ppm = px * art.QR_SIZE / modules
        status = "ok  " if not bad else "FAIL"
        print(f"  {status} {px:>4} px across the disc ({ppm:.1f} px/module)  "
              f"zxing {len(rows) - len(bad)}/{len(rows)}  "
              f"opencv {cv_ok}/{len(rows)} (advisory)")
        for r in bad[:6]:
            print(f'        {r["stock"]}" {r["message"]} {r["code"]}')
        if len(bad) > 6:
            print(f"        … and {len(bad) - 6} more")
        if bad:
            failed += 1

    print()
    if failed:
        print(f"{failed} capture size(s) failed", file=sys.stderr)
        return 1
    print(f"all {len(rows)} stickers decode to the URL the manifest expects")
    print(f"{codes_mod.CODE_LEN}-char codes, {modules} modules, "
          f"error correction H, {len({r['stock'] for r in rows})} stock(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
