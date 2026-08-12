#!/usr/bin/env python3
"""
Build the City Edit merch print files.

    ./env/bin/python build_merch.py            # everything, into out/
    ./env/bin/python build_merch.py --previews # also write small web previews
    ./env/bin/python build_merch.py --lookbook # also build the proof sheet

Emits, per design and per garment colourway:
  out/<slug>.svg   the vector master (text already outlined — no font needed)
  out/<slug>.png   the upload file, at 300 DPI in real print pixels

and, with --lookbook, out/lookbook.html — a single self-contained page proofing
every design on the ground it prints on, with the fulfilment runbook.

Tee art is transparent: the garment is the background, so a dark colourway
prints light ink and nothing else. Mug art is full-bleed, since the blank is
white ceramic.
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

import designs as d  # noqa: E402
import iso  # noqa: E402
import qr_tee  # noqa: E402

OUT = Path(__file__).parent / "out"
FONT_TTF = Path(__file__).parent / "fonts" / "RedHatMono[wght].ttf"
FONT_WOFF2 = FONT_TTF.with_suffix(".woff2")
DPI = 300

# Ink for each colourway. Dark garments take the terminal's light grey; light
# garments take a near-black that still reads as ink rather than as pure black.
INK_ON_DARK = d.INK
INK_ON_LIGHT = "#141414"
PAPER_LIGHT = "#F4F5F0"     # --paper for light basemaps

# Actual blank colours. Designs that pre-composite their fills (the isometric
# tee) resolve every tone against these, so a colourway is tied to the garment
# it was built for — printing the black files on navy or heather will read off.
GARMENT_BLACK = "#17171a"
GARMENT_NATURAL = "#e6e0d2"


def tee(name, fn, title, placement, size_in, ground=False):
    return dict(kind="tee", name=name, fn=fn, title=title, placement=placement,
                size_in=size_in, ground=ground)


def mug(name, fn, title):
    return dict(kind="mug", name=name, fn=fn, title=title, ground=False,
                placement="Full wrap, 11oz", size_in='9.25" × 3.8"')


PRODUCTS = [
    tee("tee-isogrid", iso.tee_isogrid, "Isometric Grid",
        "Centre chest, front", '11" × 13"', ground=True),
    tee("tee-one-note", qr_tee.tee_one_note, "One Note",
        "Centre chest, front", '11" × 11"', ground=True),
    tee("tee-desire-path", d.tee_desire_path, "Desire Path",
        "Full front", '11" × 14"'),
    tee("tee-heat", d.tee_heat, "Heat",
        "Full back", '13" × 17"'),
    mug("mug-heat-ramp", d.mug_heat_ramp, "Heat Ramp"),
    mug("mug-clues", d.mug_clues, "Clues"),
]

# Per colourway: the ink, the painted background (mugs only — tee art is
# transparent), and the blank it will be printed on.
TEE_WAYS = [
    ("black", INK_ON_DARK, None, GARMENT_BLACK),
    ("natural", INK_ON_LIGHT, None, GARMENT_NATURAL),
]
MUG_WAYS = [
    ("black", INK_ON_DARK, d.PAPER, d.PAPER),
    ("bone", INK_ON_LIGHT, PAPER_LIGHT, PAPER_LIGHT),
]


def render(product, suffix, ink, background, garment, previews):
    accent = d.ACCENT if suffix in ("black",) else d.ACCENT_ON_LIGHT
    if product["ground"]:
        w, h, body = product["fn"](ink, accent, garment)
    elif product["kind"] == "tee":
        w, h, body = product["fn"](ink, accent)
    else:
        w, h, body = product["fn"](ink, background, accent)

    markup = d.svg(w, h, body, background=background)
    slug = f"{product['name']}--{suffix}"

    svg_path = OUT / f"{slug}.svg"
    svg_path.write_text(markup)

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
            scale = 760 / im.width
            im.convert("RGBA").resize(
                (760, round(im.height * scale)), Image.LANCZOS
            ).save(preview_dir / f"{slug}.png")

    return slug, w, h, png_path.stat().st_size


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def build_lookbook() -> Path:
    """
    Inline every asset into the proof sheet.

    A published artifact can't reach an external host, so the font, the logo
    mark and all five proofs have to travel inside the file. Previews (760px)
    rather than the print PNGs: the artwork only has to be judged on screen
    here, and the full-size files would push the page past 20MB.
    """
    template = (Path(__file__).parent / "lookbook.template.html").read_text(encoding="utf-8")
    preview_dir = OUT / "preview"

    # Derived from the vendored TTF rather than committed beside it — woff2 is
    # less than half the bytes over the wire, and a generated binary in git is
    # a second copy that can silently fall out of step with its source.
    if not FONT_WOFF2.exists():
        font = TTFont(FONT_TTF)
        font.flavor = "woff2"
        font.save(FONT_WOFF2)

    # The masthead mark is the design itself, at badge size — the sheet and the
    # thing it documents cannot drift apart if they come out of one function.
    mark_body, (mx0, my0, mx1, my1) = iso.city(
        iso.tones_for(GARMENT_BLACK, INK_ON_DARK), grid=["X.", "C."]
    )
    mw, mh = mx1 - mx0, my1 - my0
    mark = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{mw * 40:.0f}" '
        f'height="{mh * 40:.0f}" viewBox="{mx0:.3f} {my0:.3f} {mw:.3f} {mh:.3f}">'
        f'{mark_body}</svg>'
    )

    # Variants rendered only for the sheet: options still to be decided on, not
    # colourways anyone has asked to print.
    def sheet_variant(name, w, h, markup):
        cairosvg.svg2png(bytestring=d.svg(w, h, markup).encode(),
                         write_to=str(preview_dir / f"{name}.png"),
                         output_width=760, output_height=round(760 * h / w))

    sheet_variant("tee-isogrid--accent",
                  *iso.tee_isogrid(INK_ON_DARK, d.ACCENT, GARMENT_BLACK,
                                   letter_ink=d.ACCENT))
    sheet_variant("tee-one-note--line",
                  *qr_tee.tee_one_note(INK_ON_DARK, d.ACCENT, GARMENT_BLACK,
                                       layout="line"))

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
                    help="also build the self-contained proof sheet (implies --previews)")
    ap.add_argument("--qr-url", default=qr_tee.DEFAULT_URL,
                    help="what the One Note code points at")
    ap.add_argument("--clean", action="store_true", help="wipe out/ first")
    args = ap.parse_args()

    qr_tee.DEFAULT_URL = args.qr_url

    if args.clean and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(exist_ok=True)

    previews = args.previews or args.lookbook

    for product in PRODUCTS:
        ways = TEE_WAYS if product["kind"] == "tee" else MUG_WAYS
        for suffix, ink, background, garment in ways:
            slug, w, h, nbytes = render(product, suffix, ink, background,
                                        garment, previews)
            print(f"  {slug:<28} {w}×{h}px @ {DPI}dpi   {nbytes / 1024:7.0f} KB")

    print(f"\n{len(PRODUCTS)} designs → {OUT}")

    if args.lookbook:
        path = build_lookbook()
        print(f"proof sheet          → {path}  ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
