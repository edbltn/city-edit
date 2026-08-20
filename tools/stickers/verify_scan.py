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
run, because it cannot read two things this design uses on purpose:

  * an INVERTED code (light modules on a dark field), which is what the default
    dark colourway prints — measured, OpenCV reads zero of those at any size,
    including a plain segno code before our artwork touches it;
  * the isometric city, likewise zero.

So a `0/N` opencv column is the EXPECTED reading for the default build and not a
regression. It stays in the output because it is still a useful tripwire on the
light colourway's flat code, where it should read ~100% — a drop there would
mean something happened to the art.

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
import pymupdf
import zxingcpp

sys.path.insert(0, str(Path(__file__).parent))

import codes as codes_mod  # noqa: E402
import sheet as sheet_mod  # noqa: E402
import sticker as art  # noqa: E402

OUT = Path(__file__).parent / "out"


def render_disc(line: str, url: str, die: float, bleed: float, px: int,
                art_path: Path | None = None, style: str = "flat",
                colourway: str = "dark") -> np.ndarray:
    """The finished artwork, rasterised to a greyscale capture `px` wide.

    `art_path` re-applies whatever qrart painted for this code. It matters more
    here than anywhere else: a diffusion-painted code trades error-correction
    headroom for looks, so this check stops being a formality and becomes the
    thing that decides whether a painted run is printable at all.
    """
    canvas, body = art.disc(line, url, die, bleed, art=art_path, style=style,
                            colourway=colourway)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{canvas}" '
           f'height="{canvas}" viewBox="0 0 {canvas} {canvas}">{body}</svg>')
    # A mid grey behind the disc, not white: on the dark colourway a white
    # ground would fuse with nothing and on the light one it would hide a
    # missing field. Grey is what a pole looks like anyway.
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=px,
                           output_height=px, background_color="#808080")
    return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)


def read_zxing(img: np.ndarray) -> str | None:
    res = zxingcpp.read_barcode(img)
    return res.text if res else None


def read_opencv(img: np.ndarray) -> str | None:
    text, _pts, _straight = cv2.QRCodeDetector().detectAndDecode(img)
    return text or None


def check_pdf(rows: list[dict]) -> list[str]:
    """Decode every sticker out of the finished PDF, in its own grid cell.

    This is the last artefact in the chain and the only one anybody sends to a
    printer, so it gets checked as a printed page rather than as a drawing: each
    page is rasterised at 300 DPI — exactly what the RIP receives — and every
    grid cell is cropped and decoded on its own.

    Checking the cell, not just the page, is the point. A first pass had every
    sticker decoding perfectly and every page in the wrong order, because the
    PDF was assembled from a filename sort ("press+cross-3" sorts before
    "wait+fix-1"). Sheet-level checks cannot see that; cell-level ones cannot
    miss it.
    """
    problems = []
    for run_key in sorted({r["_run"] for r in rows}):
        stock_key = next(r["stock"] for r in rows if r["_run"] == run_key)
        stock = sheet_mod.STOCKS[stock_key]
        suffix = run_key[len(stock_key):]
        pdf = OUT / run_key / f"cityedit-stickers-{stock_key}in{suffix}.pdf"
        if not pdf.exists():
            problems.append(f"{pdf.name}: missing")
            continue

        want: dict[int, dict[tuple[int, int], str]] = {}
        for r in rows:
            if r["_run"] == run_key:
                want.setdefault(int(r["sheet"]), {})[
                    (int(r["col"]), int(r["row"]))] = r["url"]

        doc = pymupdf.open(pdf)
        if doc.page_count != len(want):
            problems.append(
                f"{pdf.name}: {doc.page_count} pages, manifest has {len(want)} sheets")
        for pno, page in enumerate(doc):
            box = page.rect
            if abs(box.width - 612) > 0.5 or abs(box.height - 792) > 0.5:
                problems.append(
                    f"{pdf.name} p{pno + 1}: {box.width:.0f}x{box.height:.0f} pt, "
                    f"not US Letter — it would print scaled")
            pix = page.get_pixmap(dpi=300)
            img = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n)
            grey = cv2.cvtColor(
                img, cv2.COLOR_RGB2GRAY if pix.n == 3 else cv2.COLOR_RGBA2GRAY)
            half = int(stock.die / 2 * 300 * 1.06)
            for (col, row), url in sorted(want.get(pno, {}).items()):
                cx, cy = stock.centre(col, row)
                x, y = int(cx * 300), int(cy * 300)
                crop = grey[max(0, y - half):y + half, max(0, x - half):x + half]
                res = zxingcpp.read_barcode(crop)
                got = res.text if res else None
                if got != url:
                    problems.append(
                        f"{pdf.name} p{pno + 1} cell ({col},{row}): expected "
                        f"{url}, got {got}")
    return problems


def load_run() -> list[dict]:
    rows = []
    for manifest in sorted(OUT.glob("*/manifest.csv")):
        for r in csv.DictReader(manifest.open()):
            # The run directory, not the stock, is what identifies a print run:
            # `out/3` and `out/3-2026-08-20` are both stock "3" but are two
            # different sets of poles. Keying the PDF lookup on the stock alone
            # checked a dated run's manifest against the ORIGINAL run's PDF and
            # reported all twelve as mismatched.
            r["_run"] = manifest.parent.name
            rows.append(r)
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
            img = render_disc(r["line"], r["url"], stock.die, stock.bleed, px,
                              Path(r["art"]) if r.get("art") else None,
                              r.get("style") or "flat",
                              r.get("colourway") or "dark")
            if read_zxing(img) != r["url"]:
                bad.append(r)
            if read_opencv(img) == r["url"]:
                cv_ok += 1
        ppm = px * art.QR_SIZE / modules
        status = "ok  " if not bad else "FAIL"
        print(f"  {status} {px:>4} px across the disc ({ppm:.1f} px/module)  "
              f"zxing {len(rows) - len(bad)}/{len(rows)}  "
              f"opencv {cv_ok}/{len(rows)} (advisory)"
              + ("  ← expected: OpenCV cannot read inverted/iso codes"
                 if cv_ok == 0 else ""))
        for r in bad[:6]:
            print(f'        {r["stock"]}" {r["message"]} {r["code"]}')
        if len(bad) > 6:
            print(f"        … and {len(bad) - 6} more")
        if bad:
            failed += 1

    pdf_problems = check_pdf(rows)
    print(f"  {'FAIL' if pdf_problems else 'ok  '} finished PDF: every sticker "
          f"decodes at 300 DPI from its own grid cell, pages US Letter")
    for msg in pdf_problems[:8]:
        print(f"        {msg}")
    if len(pdf_problems) > 8:
        print(f"        … and {len(pdf_problems) - 8} more")

    print()
    if failed or pdf_problems:
        if failed:
            print(f"{failed} capture size(s) failed", file=sys.stderr)
        if pdf_problems:
            print(f"{len(pdf_problems)} problem(s) in the finished PDF", file=sys.stderr)
        return 1
    painted = sum(1 for r in rows if r.get("art"))
    isos = sum(1 for r in rows if (r.get("style") or "flat") == "iso")
    print(f"all {len(rows)} stickers decode to the URL the manifest expects")
    print(f"{codes_mod.CODE_LEN}-char codes, {modules} modules, "
          f"error correction H, {len({r['stock'] for r in rows})} stock(s), "
          f"{painted} qrart-painted, {isos} isometric")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
