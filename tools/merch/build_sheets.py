#!/usr/bin/env python3
"""
Print run: ten shirts of each SKU, each with its own code.

    ./env/bin/python build_sheets.py --run 10

Writes to out/run/:
  shirts/<print>--<way>--<code>.png   one 300 DPI file per physical shirt
  sheets/<print>--<way>.pdf           22 in gang sheet, vector, true size
  sheets/<print>--<way>.proof.png     the same sheet, small, to eyeball
  manifest.csv                        every shirt, its code and its URL
  seed_tees.sql                       the rows the API needs to answer a scan

Every code is decoded back out of its own finished artwork before the run is
called good, with zxing-cpp — the decoder phone scanners are built on, and the
gate tools/stickers uses for the same reason. A code that does not read is a
shirt that does nothing.

## Why the gang sheets are one-across

A DTF sheet is 22 in wide and these are 11–12 in prints, so two will not fit
beside each other once you leave the 0.25 in the shop needs to trim between
them. That wastes about half of every sheet, which is why the per-shirt files
are the better thing to send if the printer will take them — they let the shop
nest the run itself. The sheets are here for shops that only accept a sheet.

They are PDF rather than PNG on purpose. Ten 11 in prints down a 22 in sheet is
113 in long: at 300 DPI that is a 6600 × 33900 raster, which is past Cairo's
32767 px surface limit and about 900 MB in memory before it is compressed. The
vector PDF is exact, is a few hundred KB, and every DTF shop's RIP reads it. The
proof PNG beside it is for looking at, not for printing.
"""

import argparse
import csv
import sys
from pathlib import Path

import cairosvg
import zxingcpp
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

import back  # noqa: E402
import iso  # noqa: E402
import qr_tee  # noqa: E402
import tee_codes  # noqa: E402
from palette import (  # noqa: E402
    ACCENT, GARMENT_BLACK, GARMENT_WHITE, INK_ON_BLACK, INK_ON_WHITE, svg,
)

OUT = Path(__file__).parent / "out" / "run"
DPI = 300
SHEET_W_IN = 22.0
MARGIN_IN = 0.25
GUTTER_IN = 0.25

# What every shirt in the run says. The map is where a FRESH scan lands; once
# somebody votes, the binding replaces it (server/database.resolve_sticker).
SCAN_MAP = "nyc-proposals"
SCAN_VOTE_TYPE = "Fix dangerous intersection"

WAYS = {
    "black": {"ink": INK_ON_BLACK, "ground": GARMENT_BLACK},
    "white": {"ink": INK_ON_WHITE, "ground": GARMENT_WHITE},
}


def one_note_front(way, url):
    return qr_tee.tee_one_note(url=url, **WAYS[way])


def pls_fix_back(way, url):
    return back.tee_back_isogrid(url=url, **WAYS[way])


def iso_front(way, _url):
    extra = {"letter_ink": ACCENT} if way == "black" else {}
    return iso.tee_isogrid(**WAYS[way], **extra)


# One code per SHIRT, not per print.
#
# The One Note shirt carries a code on both faces, and they must be the SAME
# code: two codes on one garment would bind to two different proposals, so the
# wearer's front and back would disagree about what they are asking for. So the
# code is minted for the shirt and every coded print on it renders that one.
#
# `coded` marks the prints that carry it. The isometric FRONT does not — it is
# identical on every shirt, which also makes it the one print in the line worth
# screen printing instead at this quantity.
SKUS = [
    {"key": "isogrid--black", "way": "black",
     "headline": "PLS FIX",
     "prints": [("iso-front", iso_front, False), ("pls-fix-back", pls_fix_back, True)]},
    {"key": "isogrid--white", "way": "white",
     "headline": "PLS FIX",
     "prints": [("iso-front", iso_front, False), ("pls-fix-back", pls_fix_back, True)]},
    {"key": "one-note--black", "way": "black",
     "headline": "I LOVE THIS CITY BUT I HAVE ONE NOTE",
     "prints": [("one-note-front", one_note_front, True),
                ("pls-fix-back", pls_fix_back, True)]},
    {"key": "one-note--white", "way": "white",
     "headline": "I LOVE THIS CITY BUT I HAVE ONE NOTE",
     "prints": [("one-note-front", one_note_front, True),
                ("pls-fix-back", pls_fix_back, True)]},
]


def render_png(markup, w, h, path, scale=1.0):
    cairosvg.svg2png(bytestring=markup.encode(), write_to=str(path),
                     output_width=round(w * scale), output_height=round(h * scale))
    with Image.open(path) as im:
        im.save(path, dpi=(DPI, DPI))


def decodes_to(path, expected, ground):
    """Read the code back out of the finished art, over its real garment."""
    with Image.open(path) as im:
        art = im.convert("RGBA")
        plate = Image.new("RGBA", art.size, ground)
        plate.alpha_composite(art)
        res = zxingcpp.read_barcode(plate.convert("RGB"))
    return bool(res) and res.text == expected


def ink_box(path):
    """The artwork's actual ink, as (x0, y0, w, h) in px.

    A design's canvas carries its own margins — they position it on the chest,
    and on a sheet priced by the inch they are a third of the bill. Placing the
    trimmed box instead is the same print and a much shorter sheet; the shop
    cuts to the ink either way.
    """
    with Image.open(path) as im:
        box = im.convert("RGBA").getbbox()
    x0, y0, x1, y1 = box
    return x0, y0, x1 - x0, y1 - y0


def gang_sheet(items, path_pdf, path_proof):
    """One-across down a 22 in sheet, in true physical units."""
    sheet_w = SHEET_W_IN * DPI
    gutter, margin = GUTTER_IN * DPI, MARGIN_IN * DPI
    y = margin
    parts = []
    for markup, (bx, by, bw, bh) in items:
        # Shift the art so its ink box starts at the row origin, then clip to
        # that box so nothing outside it can bleed into the neighbouring row.
        parts.append(
            f'<g transform="translate({(sheet_w - bw) / 2 - bx:.1f},{y - by:.1f})">'
            f"{markup}</g>"
        )
        y += bh + gutter
    sheet_h = y - gutter + margin
    body = "".join(parts)
    # Physical size in inches on the root element, so the RIP places it at the
    # size intended rather than at whatever it infers from a pixel count.
    markup = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_W_IN}in" '
        f'height="{sheet_h / DPI:.3f}in" viewBox="0 0 {sheet_w:.0f} {sheet_h:.0f}">'
        f"{body}</svg>"
    )
    cairosvg.svg2pdf(bytestring=markup.encode(), write_to=str(path_pdf))
    render_png(markup, sheet_w, sheet_h, path_proof, scale=900 / sheet_w)
    return sheet_h / DPI


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=10, help="shirts per SKU")
    args = ap.parse_args()

    shirts_dir, sheets_dir = OUT / "shirts", OUT / "sheets"
    for d in (shirts_dir, sheets_dir):
        d.mkdir(parents=True, exist_ok=True)

    manifest, seed_rows, failures = [], [], []
    sheets: dict[str, list] = {}

    for sku in SKUS:
        way = sku["way"]
        ground = WAYS[way]["ground"]
        rgba = tuple(int(ground[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
        codes = tee_codes.mint_run(sku["key"], args.run)

        for shirt_no, code in enumerate(codes, start=1):
            url = tee_codes.url_for(code)
            for print_key, fn, coded in sku["prints"]:
                # An uncoded print is the same file on every shirt — render it
                # once for the SKU, not ten identical times.
                if not coded and shirt_no > 1:
                    continue
                w, h, body = fn(way, url if coded else None)
                markup = svg(w, h, body)
                name = (f"{sku['key']}--{print_key}"
                        + (f"--{code.lower()}" if coded else ""))
                path = shirts_dir / f"{name}.png"
                render_png(markup, w, h, path)

                if coded:
                    if not decodes_to(path, url, rgba):
                        failures.append(name)
                    sheets.setdefault(f"{sku['key']}--{print_key}", []).append(
                        (markup, ink_box(path)))
                manifest.append({
                    "sku": sku["key"], "shirt": shirt_no, "print": print_key,
                    "code": code if coded else "", "url": url if coded else "",
                    "size_in": f"{w / DPI:.1f}x{h / DPI:.1f}",
                    "file": f"shirts/{name}.png",
                })

            seed_rows.append({
                "code": code.lower(), "map_slug": SCAN_MAP,
                "vote_type": SCAN_VOTE_TYPE, "headline": sku["headline"],
                "src_tag": f"tee-{sku['key'].split('--')[0]}",
            })

    for name, items in sorted(sheets.items()):
        length = gang_sheet(items, sheets_dir / f"{name}.pdf",
                            sheets_dir / f"{name}.proof.png")
        print(f"  {name:<32} {len(items):>3} up   sheet {SHEET_W_IN:.0f}×{length:.1f} in")

    with (OUT / "manifest.csv").open("w", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        wtr.writeheader()
        wtr.writerows(manifest)
    (OUT / "seed_tees.sql").write_text(tee_codes.seed_sql(seed_rows))

    coded = len(seed_rows)
    print(f"\n{coded} shirts, {coded} unique codes, every coded print decoded "
          f"back from its own artwork: "
          f"{'OK' if not failures else 'FAILED ' + ', '.join(failures)}")
    print(f"→ {OUT}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
