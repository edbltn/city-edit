#!/usr/bin/env python3
"""
The one file to send the printer.

    ./env/bin/python build_order.py

Writes out/order/usatees-order.pdf — a spec page followed by one page per
print, each page exactly the physical size of the artwork on it and containing
nothing else. Vector throughout, so it is a few hundred KB and the RIP places it
at the size intended rather than at whatever it infers from a pixel count.

USA Tees publish no artwork spec (their site says contact them), so this follows
what every DTF/DTG shop asks for: true size, transparent ground, no white plate,
nothing on the page but the art.

## The one thing the printer must be told

Every shirt's code is DIFFERENT. That rules out screen printing, which is USA
Tees' headline service — a screen is one image pulled twenty times, and twenty
identical codes would mean twenty shirts all pointing at the same proposal. This
run has to be DTF or DTG. It says so on the spec page in as many words.

Also emits seed_tees.sql beside it. That one is NOT for the printer — it is the
row per code the API needs before a scan can resolve, and an unseeded code is a
404 to whoever scans it.
"""

import sys
from pathlib import Path

import cairosvg
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent))

import back  # noqa: E402
import iso  # noqa: E402
import qr_tee  # noqa: E402
import tee_codes  # noqa: E402
from palette import (  # noqa: E402
    ACCENT, GARMENT_BLACK, GARMENT_WHITE, INK_ON_BLACK, INK_ON_WHITE, svg,
)
from typo import text  # noqa: E402

OUT = Path(__file__).parent / "out" / "order"
DPI = 300

# 20 shirts: two designs, ten each, split evenly across the two blanks.
PER_DESIGN = 10
WAYS = ("black", "white")

INK = {"black": INK_ON_BLACK, "white": INK_ON_WHITE}
GARMENT = {"black": GARMENT_BLACK, "white": GARMENT_WHITE}

SCAN_MAP = "nyc-proposals"
SCAN_VOTE_TYPE = "Fix dangerous intersection"

DESIGNS = [
    {"key": "one-note", "label": "I LOVE THIS CITY (BUT I HAVE ONE NOTE)"},
    {"key": "isogrid", "label": "ISOMETRIC GRID"},
]


def art_for(design: str, face: str, way: str, url: str | None):
    """(markup, width_px, height_px) for one print."""
    ink, ground = INK[way], GARMENT[way]
    if face == "back":
        return back.tee_back_isogrid(ink=ink, ground=ground, url=url)
    if design == "one-note":
        return qr_tee.tee_one_note(ink=ink, ground=ground, url=url)
    extra = {"letter_ink": ACCENT} if way == "black" else {}
    return iso.tee_isogrid(ink=ink, ground=ground, **extra)


def art_page(markup: str, w: int, h: int, path: Path) -> None:
    """One print, alone, on a page its own physical size.

    Nothing else goes on it — no caption, no crop marks, no filename. A note in
    the margin is a note a printer can mistake for artwork, and the spec page
    already says which page is which.
    """
    page = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w / DPI:.3f}in" '
            f'height="{h / DPI:.3f}in" viewBox="0 0 {w} {h}">{markup}</svg>')
    cairosvg.svg2pdf(bytestring=page.encode(), write_to=str(path))


def spec_page(rows: list[dict], path: Path) -> None:
    """Letter portrait: what to print, on what, how many, and how."""
    W, H = int(8.5 * DPI), int(11 * DPI)
    ink = "#000000"
    y = 300
    body = [f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    def line(s, size=30, weight=400, dy=44, indent=0, gap=0):
        nonlocal y
        y += gap
        if s:
            body.append(text(s, 220 + indent, y, size, weight=weight, fill=ink))
        y += dy

    line("CITY EDIT — PRINT ORDER", 46, 600, 70)
    line("20 shirts · 2 designs · 10 each · 5 per colourway", 26, 400, 60)

    line("PRINT METHOD", 26, 600, 46, gap=30)
    line("DTF or DTG. NOT screen print.", 30, 600, 42)
    line("Every shirt's QR code is different — see the code column below.", 26)
    line("A screen would put the same code on all 20, which breaks them.", 26)

    line("GARMENTS", 26, 600, 46, gap=34)
    line("10 x black tee   (blank as close to #17171A as stocked)", 26)
    line("10 x white tee   (#FFFFFF)", 26)
    line("Sizes: to be confirmed — please quote on a standard S–XXL spread.", 26)

    line("ARTWORK", 26, 600, 46, gap=34)
    line("One print per page, from page 2 on. Each page is exactly the", 26)
    line("physical size of the print on it. Place at 100% — do not scale,", 26)
    line("re-crop, mirror, or add a white background plate.", 26)
    line("Artwork is transparent: the garment is the background.", 26)

    line("PLACEMENT", 26, 600, 46, gap=34)
    line("Front — centre chest, standard drop from collar.", 26)
    line("Back  — centre, standard drop from collar.", 26)

    line("PAGES", 26, 600, 46, gap=34)
    hdr = f"{'PG':<5}{'SHIRT':<9}{'COLOUR':<8}{'FACE':<7}{'SIZE (IN)':<12}{'CODE'}"
    line(hdr, 22, 600, 34)
    for r in rows:
        line(f"{r['page']:<5}{r['shirt']:<9}{r['way']:<8}{r['face']:<7}"
             f"{r['size']:<12}{r['code'] or '—'}", 22, 400, 30)

    page = (f'<svg xmlns="http://www.w3.org/2000/svg" width="8.5in" height="11in" '
            f'viewBox="0 0 {W} {H}">{"".join(body)}</svg>')
    cairosvg.svg2pdf(bytestring=page.encode(), write_to=str(path))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_pages"
    tmp.mkdir(exist_ok=True)

    rows, seed_rows, pages = [], [], []
    page_no = 2

    for design in DESIGNS:
        codes = tee_codes.mint_run(design["key"], PER_DESIGN)
        for i, code in enumerate(codes):
            way = WAYS[i % len(WAYS)]
            url = tee_codes.url_for(code)
            shirt = f"{design['key'][:3].upper()}{i + 1:02d}"

            faces = ["front", "back"] if design["key"] == "one-note" else ["back"]
            # The isometric front carries no code, so it is one page printed ten
            # times rather than ten pages.
            for face in faces:
                w, h, markup = art_for(design["key"], face, way, url)
                path = tmp / f"p{page_no:03d}.pdf"
                art_page(svg(w, h, markup), w, h, path)
                pages.append(path)
                rows.append({"page": page_no, "shirt": shirt, "way": way,
                             "face": face, "size": f"{w / DPI:.0f}x{h / DPI:.0f}",
                             "code": code})
                page_no += 1

            seed_rows.append({"code": code.lower(), "map_slug": SCAN_MAP,
                              "vote_type": SCAN_VOTE_TYPE,
                              "headline": design["label"],
                              "src_tag": f"tee-{design['key']}"})

    # The uncoded isometric front: one page, printed on all ten of that design.
    for way in WAYS:
        w, h, markup = art_for("isogrid", "front", way, None)
        path = tmp / f"p{page_no:03d}.pdf"
        art_page(svg(w, h, markup), w, h, path)
        pages.append(path)
        rows.append({"page": page_no, "shirt": "ISO x5", "way": way,
                     "face": "front", "size": f"{w / DPI:.0f}x{h / DPI:.0f}",
                     "code": ""})
        page_no += 1

    spec = tmp / "p001.pdf"
    spec_page(rows, spec)

    writer = PdfWriter()
    for p in [spec] + pages:
        writer.append(str(p))
    order = OUT / "usatees-order.pdf"
    with order.open("wb") as f:
        writer.write(f)

    (OUT / "seed_tees.sql").write_text(tee_codes.seed_sql(seed_rows))
    for p in tmp.glob("*.pdf"):
        p.unlink()
    tmp.rmdir()

    print(f"  {order}  ({order.stat().st_size / 1024:.0f} KB, "
          f"{len(pages) + 1} pages)")
    print(f"  {OUT / 'seed_tees.sql'}  ({len(seed_rows)} codes — for us, not the printer)")


if __name__ == "__main__":
    main()
