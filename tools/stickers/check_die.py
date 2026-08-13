#!/usr/bin/env python3
"""
Check a stock's grid against a photograph of the real sheet.

    ./env/bin/python check_die.py --stock 2.5 --photo ../../data/sticker.jpg
    ./env/bin/python check_die.py --stock 2.5 --photo … --overlay /tmp/align.png

The 2.5" grid shipped wrong once. The numbers came from a generic "12-up 2.5
circle" template because the manufacturer publishes Avery compatibility rather
than a dimensioned drawing, and every axis was out by about 0.03" — which
`validate()` could not catch, because a wrong grid closes on the sheet just as
neatly as a right one. It took a photo of a printed sheet to see it.

So this measures the die itself. Give it a straight-on photo of a blank sheet
and it finds the circles, converts to inches, and compares against `sheet.py`.

## Why the measurement can be trusted

Two independent checks fall out of the same picture, and both have to pass:

  * the sheet's aspect ratio must match US Letter, which says the photo is not
    cropped or stretched; and
  * the measured die must match the stock's nominal diameter, which says the
    pixels-to-inches conversion is right.

If a photo passes both, a disagreement with `sheet.py` is a real one. If it
fails either, this refuses to draw a conclusion rather than "correcting" a good
grid with a bad photograph.

## What it cannot tell you

Printer drift. This checks the ARTWORK against the DIE; it says nothing about
whether a given printer lays that artwork down where it claims to. That is what
the `--proof` sheets are for, and they are still worth running on real stock
before a long print.
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import sheet as sheet_mod  # noqa: E402

#: Two tolerances, because the two kinds of error are not equally dangerous.
#:
#: A GAP error is a pitch error: it accumulates across the sheet, so the first
#: sticker can look fine while the last is a sixteenth out, and no printer offset
#: can fix it because the sheet is stretched rather than shifted. That is what
#: was wrong before, and it is held tight.
#:
#: A MARGIN error shifts every sticker by the same amount. It is correctable in
#: the print dialog, and it is also the quantity a photograph measures worst:
#: the sheet edge in a product shot is a drawn line a pixel or two wide, and
#: being out by two pixels here is 0.02". So margins are advisory — reported,
#: with the residual named, for a proof sheet to settle.
GAP_TOLERANCE_IN = 0.010
MARGIN_TOLERANCE_IN = 0.030


def find_sheet(grey: np.ndarray) -> tuple[int, int, int, int]:
    """The sheet's rectangle in the photo: (x0, y0, w, h)."""
    thr = int(grey.min()) + (int(grey.max()) - int(grey.min())) // 2
    xs = np.where(grey.mean(axis=0) > thr)[0]
    ys = np.where(grey.mean(axis=1) > thr)[0]
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


#: Fraction of the sheet trimmed off each edge before measuring. The die lines
#: are a very light grey on white and the contrast stretch below is what makes
#: them visible at all — leave any of the photo's dark border in frame and the
#: stretch anchors on it instead, crushing the lines to nothing. Detection found
#: exactly zero circles until this trim went in.
EDGE_TRIM = 0.015


def find_circles(grey: np.ndarray, sheet_w: int) -> list[tuple[float, float, float]]:
    """Every die circle in the photo, as (x, y, r) in pixels.

    Contours off a contrast-stretched Canny rather than HoughCircles: a die line
    is a very light grey on white and Hough simply does not see it.
    """
    t = int(sheet_w * EDGE_TRIM)
    inner = grey[t:grey.shape[0] - t, t:grey.shape[1] - t]
    norm = cv2.normalize(inner, None, 0, 255, cv2.NORM_MINMAX)
    edges = cv2.Canny(cv2.GaussianBlur(norm, (5, 5), 0), 15, 45)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found = []
    for c in contours:
        (x, y), r = cv2.minEnclosingCircle(c)
        if not (sheet_w * 0.08 < r < sheet_w * 0.28):
            continue
        if cv2.contourArea(c) < 0.55 * np.pi * r * r:   # roughly circular
            continue
        found.append((x + t, y + t, r))

    # A drawn line has an inner and an outer edge; keep one circle per die.
    merged: list[tuple[float, float, float]] = []
    for x, y, r in sorted(found, key=lambda t: -t[2]):
        if all((x - mx) ** 2 + (y - my) ** 2 > (0.5 * r) ** 2 for mx, my, _ in merged):
            merged.append((x, y, r))
    return merged


def cluster(values: list[float], tol: float) -> list[float]:
    """Collapse near-equal coordinates into one per row/column."""
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", choices=sorted(sheet_mod.STOCKS), required=True)
    ap.add_argument("--photo", type=Path, required=True,
                    help="straight-on photo of a BLANK sheet of this stock")
    ap.add_argument("--overlay", type=Path,
                    help="write the photo with the computed grid drawn on it")
    args = ap.parse_args()

    stock = sheet_mod.STOCKS[args.stock]
    img = cv2.imread(str(args.photo))
    if img is None:
        print(f"cannot read {args.photo}", file=sys.stderr)
        return 1
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    x0, y0, sw, sh = find_sheet(grey)
    aspect = sw / sh
    letter = sheet_mod.PAGE_W / sheet_mod.PAGE_H
    print(f"sheet in photo: {sw}x{sh}px  aspect {aspect:.4f} vs Letter {letter:.4f} "
          f"({abs(aspect - letter) / letter * 100:.2f}% off)")
    if abs(aspect - letter) / letter > 0.02:
        print("  photo is cropped or not straight on — refusing to measure from it",
              file=sys.stderr)
        return 1

    sx, sy = sheet_mod.PAGE_W / sw, sheet_mod.PAGE_H / sh
    circles = find_circles(grey, sw)
    if len(circles) != stock.per_sheet:
        print(f"  found {len(circles)} circles, expected {stock.per_sheet}",
              file=sys.stderr)
        return 1

    cols = cluster([x for x, _, _ in circles], sw * 0.02)
    rows = cluster([y for _, y, _ in circles], sh * 0.02)
    r_px = float(np.mean([r for _, _, r in circles]))
    die = r_px * 2 * sx

    print(f"measured die: {die:.4f}\" vs nominal {stock.die}\" "
          f"({abs(die - stock.die) * 1000:.1f} thou)")
    if abs(die - stock.die) > 0.03:
        print("  die does not match the stock — wrong photo, or wrong scale",
              file=sys.stderr)
        return 1

    measured = {
        "left_margin": (cols[0] - x0 - r_px) * sx,
        "top_margin": (rows[0] - y0 - r_px) * sy,
        "h_gap": (np.mean(np.diff(cols)) * sx - die) if len(cols) > 1 else stock.h_gap,
        "v_gap": (np.mean(np.diff(rows)) * sy - die) if len(rows) > 1 else stock.v_gap,
    }

    print(f"\n{'':14s} {'sheet.py':>10s} {'measured':>10s} {'delta':>9s}")
    bad, soft = [], []
    for name, got in measured.items():
        want = getattr(stock, name)
        delta = want - got
        is_gap = name.endswith("_gap")
        tol = GAP_TOLERANCE_IN if is_gap else MARGIN_TOLERANCE_IN
        flag = ""
        if abs(delta) > tol:
            flag = "  <-- OFF" if is_gap else "  <-- off (advisory)"
            (bad if is_gap else soft).append(name)
        print(f"  {name:12s} {want:10.4f} {got:10.4f} {delta:+9.4f}{flag}")

    if args.overlay:
        over = img.copy()
        for r in range(stock.down):
            for c in range(stock.across):
                cx, cy = stock.centre(c, r)
                px, py = int(x0 + cx / sx), int(y0 + cy / sy)
                cv2.circle(over, (px, py), int(stock.die / 2 / sx), (0, 0, 255), 3)
                cv2.drawMarker(over, (px, py), (0, 0, 255), cv2.MARKER_CROSS, 24, 2)
        cv2.imwrite(str(args.overlay), over)
        print(f"\noverlay: {args.overlay}")

    print()
    if bad:
        print(f"PITCH disagrees with the die on: {', '.join(bad)} — this "
              f"accumulates across the sheet and cannot be dialled out",
              file=sys.stderr)
        return 1
    print(f"pitch matches the die to within {GAP_TOLERANCE_IN}\" — the error that "
          f"compounds is not present")
    if soft:
        print(f"margins differ by more than {MARGIN_TOLERANCE_IN}\" "
              f"({', '.join(soft)}); that is a uniform shift, correctable in the "
              f"print dialog. Run a --proof sheet to settle it.")
    else:
        print(f"margins agree within {MARGIN_TOLERANCE_IN}\", which is about the "
              f"best a photograph can resolve — proof on real stock to confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
