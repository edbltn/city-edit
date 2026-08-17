#!/usr/bin/env python3
"""
Two files, one per garment colour. Twenty pages each.

    ./env/bin/python build_simple.py

    out/simple/city-edit-black.pdf   10 shirts, 20 pages
    out/simple/city-edit-white.pdf   10 shirts, 20 pages

Ten shirts per colour, each shirt two consecutive pages — front, then its own
back. Twenty distinct codes across the two files, one per shirt: the front is
CITY EDIT and carries none, the back is I LOVE THIS CITY and carries the shirt's
own. No two shirts share a code, so no two shirts can ever point at the same
proposal by accident.

Every page is exactly the physical size of the print on it and holds nothing
else — no caption, no crop marks, no order details. A note in the margin is a
note a printer can mistake for artwork, and the covering email says which page
is which.

## Why PDF and not PNG

Pages. A PNG is one image; twenty prints in one PNG is a contact sheet somebody
has to cut up, and the whole point of a page per print is that the printer never
has to decide where one ends. PDF keeps the transparency that made PNG
attractive — there is still no white plate anywhere in these files, so what you
see is exactly the ink that lands.
"""

import sys
from pathlib import Path

import cairosvg
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent))

import iso  # noqa: E402
import qr_tee  # noqa: E402
import tee_codes  # noqa: E402
from typo import text  # noqa: E402
from palette import (  # noqa: E402
    ACCENT, GARMENT_BLACK, GARMENT_WHITE, INK_ON_BLACK, INK_ON_WHITE, svg,
)

OUT = Path(__file__).parent / "out" / "simple"
DPI = 300
PER_COLOUR = 10

WAYS = {
    "black": {"ink": INK_ON_BLACK, "ground": GARMENT_BLACK, "accent": True},
    "white": {"ink": INK_ON_WHITE, "ground": GARMENT_WHITE, "accent": False},
}


def art_page(markup: str, w: int, h: int, path: Path) -> None:
    page = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w / DPI:.3f}in" '
            f'height="{h / DPI:.3f}in" viewBox="0 0 {w} {h}">{markup}</svg>')
    cairosvg.svg2pdf(bytestring=page.encode(), write_to=str(path))


def build(way: str, tmp: Path) -> tuple[Path, list[str], tuple[float, float]]:
    spec = WAYS[way]
    extra = {"letter_ink": ACCENT} if spec["accent"] else {}
    codes = tee_codes.mint_run(f"simple--{way}", PER_COLOUR)

    pages = []
    for i, code in enumerate(codes, start=1):
        fw, fh, front = iso.tee_isogrid(ink=spec["ink"], ground=spec["ground"], **extra)
        bw, bh, back = qr_tee.tee_one_note(ink=spec["ink"], ground=spec["ground"],
                                           url=tee_codes.url_for(code))
        for markup, w, h, face in ((front, fw, fh, "front"), (back, bw, bh, "back")):
            p = tmp / f"{way}-{i:02d}-{face}.pdf"
            art_page(svg(w, h, markup), w, h, p)
            pages.append(p)

    writer = PdfWriter()
    for p in pages:
        writer.append(str(p))
    out = OUT / f"city-edit-{way}.pdf"
    with out.open("wb") as f:
        writer.write(f)
    for p in pages:
        p.unlink()
    return out, codes, (fw / DPI, fh / DPI)


def proof(paths: dict[str, Path]) -> Path:
    """
    A visual proof, one page per colour, art shown on the garment it prints on.

    The print files are transparent, which is correct and also unreadable: open
    city-edit-black.pdf in any viewer and the viewer paints white behind the
    page, so light ink on a light ground all but disappears — the QR looks
    MISSING. That is normal for white-ink artwork and still alarming to receive.
    This page is what the printer checks their output against, and it is marked
    so nobody prints it by mistake.
    """
    W, H = 17 * DPI, 11 * DPI
    grounds = {"black": GARMENT_BLACK, "white": GARMENT_WHITE}
    pages = []
    tmp = OUT / "_proof"
    tmp.mkdir(exist_ok=True)

    for way, spec in WAYS.items():
        extra = {"letter_ink": ACCENT} if spec["accent"] else {}
        fw, fh, front = iso.tee_isogrid(ink=spec["ink"], ground=spec["ground"], **extra)
        code = tee_codes.mint_run(f"simple--{way}", PER_COLOUR)[0]
        bw, bh, back = qr_tee.tee_one_note(ink=spec["ink"], ground=spec["ground"],
                                           url=tee_codes.url_for(code))
        scale = (H - 900) / fh
        gap = 200
        span = (fw + bw) * scale + gap
        x0 = (W - span) / 2
        body = [f'<rect width="{W}" height="{H}" fill="#f2f2f0"/>',
                text(f"VISUAL PROOF — {way.upper()} — NOT FOR PRINT", 200, 240, 54,
                     weight=600, fill="#000"),
                text("shown on the garment; the print files are transparent and "
                     "will look faint on a white screen", 200, 330, 30, fill="#555")]
        for markup, w, h, x in ((front, fw, fh, x0),
                                (back, bw, bh, x0 + fw * scale + gap)):
            body.append(
                f'<g transform="translate({x:.1f},520) scale({scale:.4f})">'
                f'<rect width="{w}" height="{h}" fill="{grounds[way]}"/>{markup}</g>')
        page = (f'<svg xmlns="http://www.w3.org/2000/svg" width="17in" height="11in" '
                f'viewBox="0 0 {W} {H}">{"".join(body)}</svg>')
        pp = tmp / f"{way}.pdf"
        cairosvg.svg2pdf(bytestring=page.encode(), write_to=str(pp))
        pages.append(pp)

    writer = PdfWriter()
    for pp in pages:
        writer.append(str(pp))
    out = OUT / "city-edit-PROOF-not-for-print.pdf"
    with out.open("wb") as f:
        writer.write(f)
    for pp in pages:
        pp.unlink()
    tmp.rmdir()
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_pages"
    tmp.mkdir(exist_ok=True)

    seed, all_codes = [], []
    for way in WAYS:
        out, codes, size = build(way, tmp)
        all_codes += codes
        print(f"  {out}")
        print(f"     {PER_COLOUR} shirts · {PER_COLOUR * 2} pages · "
              f"{size[0]:.0f} x {size[1]:.0f} in per page · "
              f"{out.stat().st_size / 1024:.0f} KB")
        print(f"     codes: {' '.join(codes)}")
        seed += [{"code": c.lower(), "map_slug": "nyc-proposals",
                  "vote_type": "Fix dangerous intersection",
                  "headline": "I LOVE THIS CITY (BUT I HAVE ONE NOTE)",
                  "src_tag": f"tee-{way}"} for c in codes]

    tmp.rmdir()
    pf = proof({})
    print(f"  {pf}   (visual proof — NOT for print)")
    assert len(set(all_codes)) == len(all_codes) == PER_COLOUR * 2, "code collision"
    (OUT / "seed_tees.sql").write_text(tee_codes.seed_sql(seed))
    print(f"  {OUT / 'seed_tees.sql'}   ({len(seed)} codes — for us, not the printer)")


if __name__ == "__main__":
    main()
