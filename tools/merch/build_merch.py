#!/usr/bin/env python3
"""
Build the City Edit merch print files.

    ./env/bin/python build_merch.py --lookbook --clean

Two designs, two colourways each. Per design and colourway:
  out/<slug>.svg   the vector master (text already outlined — no font needed)
  out/<slug>.png   the upload file, at 300 DPI in real print pixels

and, with --lookbook, out/lookbook.html — one self-contained page proofing
every shirt on the garment it prints on.

Tee art is transparent: the garment is the background, so a design puts down
ink and nothing else.
"""

import argparse
import base64
import shutil
import sys
from pathlib import Path

import cairosvg
from fontTools.ttLib import TTFont
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

import back  # noqa: E402
import iso  # noqa: E402
import qr_tee  # noqa: E402
from typo import text  # noqa: E402
from palette import (  # noqa: E402
    ACCENT, GARMENT_BLACK, GARMENT_WHITE, INK_ON_BLACK, INK_ON_WHITE, svg,
)

OUT = Path(__file__).parent / "out"
FONT_TTF = Path(__file__).parent / "fonts" / "RedHatMono[wght].ttf"
FONT_WOFF2 = FONT_TTF.with_suffix(".woff2")
DPI = 300

BLACK = {"ink": INK_ON_BLACK, "ground": GARMENT_BLACK}
WHITE = {"ink": INK_ON_WHITE, "ground": GARMENT_WHITE}

PRODUCTS = [
    {
        "name": "tee-isogrid",
        "title": "Isometric Grid",
        "size_in": "11 × 13 in",
        "fn": iso.tee_isogrid,
        # Amber only on black: the accent is tuned for a dark surface and goes
        # muddy on white, where the greyscale ladder already carries the design.
        "ways": [("black", BLACK | {"letter_ink": ACCENT}), ("white", WHITE)],
    },
    {
        "name": "tee-isoback",
        "title": "PLS FIX — back, both shirts",
        "size_in": "12 × 15 in",
        "fn": back.tee_back_isogrid,
        "ways": [("black", BLACK), ("white", WHITE)],
    },
    {
        "name": "tee-one-note",
        "title": "One Note",
        "size_in": "11 × 11 in",
        "fn": qr_tee.tee_one_note,
        "ways": [("black", BLACK), ("white", WHITE)],
    },
]


def render(product, suffix, spec, previews):
    w, h, body = product["fn"](**spec)
    slug = f"{product['name']}--{suffix}"

    markup = svg(w, h, body)
    (OUT / f"{slug}.svg").write_text(markup, encoding="utf-8")

    png_path = OUT / f"{slug}.png"
    cairosvg.svg2png(bytestring=markup.encode(), write_to=str(png_path),
                     output_width=w, output_height=h)

    # Print-on-demand uploaders read the pHYs chunk to sanity-check physical
    # size; cairosvg does not write one, so stamp it.
    with Image.open(png_path) as im:
        im.save(png_path, dpi=(DPI, DPI))

    if previews:
        preview_dir = OUT / "preview"
        preview_dir.mkdir(exist_ok=True)
        with Image.open(png_path) as im:
            im.convert("RGBA").resize(
                (760, round(760 * im.height / im.width)), Image.LANCZOS
            ).save(preview_dir / f"{slug}.png")

    return slug, w, h, png_path.stat().st_size


def build_proof_pdf() -> Path:
    """
    The proof sheet as a page you can print or attach.

    One landscape page, four shirts across, front over back, each on the garment
    it prints on. Same previews the HTML sheet uses, so the two cannot disagree.
    """
    W, H = 17 * DPI, 11 * DPI
    cols = [("tee-one-note", "black"), ("tee-one-note", "white"),
            ("tee-isogrid", "black"), ("tee-isogrid", "white")]
    grounds = {"black": GARMENT_BLACK, "white": GARMENT_WHITE}
    labels = {"tee-one-note": "I LOVE THIS CITY", "tee-isogrid": "CITY EDIT"}

    pad, top = 90, 560
    cell_w = (W - pad * (len(cols) + 1)) / len(cols)
    cell_h = (H - top - pad * 2) / 2
    body = [f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
            text("CITY EDIT — MERCH PROOF", pad, 200, 76, weight=600, fill="#000"),
            text("4 shirts · front over back · shown on the garment each prints on",
                 pad, 300, 34, fill="#555")]

    for i, (design, way) in enumerate(cols):
        x = pad + i * (cell_w + pad)
        body.append(text(f"{labels[design]} — {way}", x, top - 110, 30,
                         weight=600, fill="#000"))
        for j, slug in enumerate((f"{design}--{way}", f"tee-isoback--{way}")):
            png = OUT / "preview" / f"{slug}.png"
            y = top + j * (cell_h + pad)
            body.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{cell_w:.0f}" '
                        f'height="{cell_h:.0f}" fill="{grounds[way]}"/>')
            with Image.open(png) as im:
                ar = im.width / im.height
            iw = min(cell_w - 40, (cell_h - 40) * ar)
            ih = iw / ar
            body.append(
                f'<image x="{x + (cell_w - iw) / 2:.0f}" y="{y + (cell_h - ih) / 2:.0f}" '
                f'width="{iw:.0f}" height="{ih:.0f}" href="{data_uri(png, "image/png")}"/>')

    page = (f'<svg xmlns="http://www.w3.org/2000/svg" width="17in" height="11in" '
            f'viewBox="0 0 {W} {H}">{"".join(body)}</svg>')
    path = OUT / "proof-sheet.pdf"
    cairosvg.svg2pdf(bytestring=page.encode(), write_to=str(path))
    return path


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def build_lookbook() -> Path:
    """
    Inline every asset into the proof sheet.

    A published page can't reach an external host, so the font, the mark and all
    four proofs travel inside the file. Previews rather than the print PNGs: the
    artwork only has to be judged on screen here.
    """
    template = (Path(__file__).parent / "lookbook.template.html").read_text(encoding="utf-8")
    preview_dir = OUT / "preview"

    # Derived from the vendored TTF rather than committed beside it — woff2 is
    # less than half the bytes, and a generated binary in git is a second copy
    # that can silently fall out of step with its source.
    if not FONT_WOFF2.exists():
        font = TTFont(FONT_TTF)
        font.flavor = "woff2"
        font.save(FONT_WOFF2)

    # The masthead mark is a corner of the design itself — the sheet and the
    # thing it documents cannot drift apart if they come out of one function.
    # A fragment, not the whole plan: the full city collapses to a blob at
    # badge size.
    mark_body, (mx0, my0, mx1, my1) = iso.city(
        iso.tones_for(GARMENT_BLACK, INK_ON_BLACK), grid=["X.", "C."]
    )
    mw, mh = mx1 - mx0, my1 - my0
    mark = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{mw * 40:.0f}" '
        f'height="{mh * 40:.0f}" viewBox="{mx0:.3f} {my0:.3f} {mw:.3f} {mh:.3f}">'
        f"{mark_body}</svg>"
    )

    html = template.replace("{{FONT}}", data_uri(FONT_WOFF2, "font/woff2"))
    html = html.replace("{{MARK}}", mark)
    for png in sorted(preview_dir.glob("*.png")):
        html = html.replace(f"{{{{IMG:{png.stem}}}}}", data_uri(png, "image/png"))

    assert "{{" not in html, "unsubstituted placeholder left in the lookbook"

    path = OUT / "lookbook.html"
    path.write_text(html, encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--previews", action="store_true",
                    help="also write 760px-wide web previews to out/preview/")
    ap.add_argument("--lookbook", action="store_true",
                    help="also build the proof sheet (implies --previews)")
    ap.add_argument("--qr-url", default=qr_tee.DEFAULT_URL,
                    help="what the One Note code points at")
    ap.add_argument("--clean", action="store_true", help="wipe out/ first")
    args = ap.parse_args()

    qr_tee.DEFAULT_URL = args.qr_url

    if args.clean and OUT.exists():
        # Only what this script owns. out/ is also home to the print run and the
        # printer's order file, which take minutes to rebuild and which --clean
        # used to delete without mentioning it.
        for path in OUT.glob("*.png"):
            path.unlink()
        for path in OUT.glob("*.svg"):
            path.unlink()
        for path in (OUT / "lookbook.html", OUT / "proof-sheet.pdf"):
            path.unlink(missing_ok=True)
        shutil.rmtree(OUT / "preview", ignore_errors=True)
    OUT.mkdir(exist_ok=True)

    previews = args.previews or args.lookbook
    for product in PRODUCTS:
        for suffix, spec in product["ways"]:
            slug, w, h, nbytes = render(product, suffix, spec, previews)
            print(f"  {slug:<26} {w}×{h}px @ {DPI}dpi   {nbytes / 1024:7.0f} KB")

    print(f"\n{len(PRODUCTS)} designs, 4 shirts → {OUT}")

    if args.lookbook:
        path = build_lookbook()
        print(f"proof sheet        → {path}  ({path.stat().st_size / 1024:.0f} KB)")
        pdf = build_proof_pdf()
        print(f"proof sheet (pdf)  → {pdf}  ({pdf.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
