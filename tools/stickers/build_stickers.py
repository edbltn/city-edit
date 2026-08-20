#!/usr/bin/env python3
"""
Build the City Edit sticker sheets.

    ./env/bin/python build_stickers.py                    # the three starters
    ./env/bin/python build_stickers.py --all              # every message
    ./env/bin/python build_stickers.py --stock 3 --proof  # 3" + registration proof
    ./env/bin/python build_stickers.py --art "PROMPT" --art-dry-run  # qrart, costed

Writes, into out/<stock>/:

    cityedit-stickers-<stock>in.pdf THE PACKET — every sheet, one Letter PDF
    <message>-<sheet>.svg           vector master, text already outlined
    <message>-<sheet>.png           the file you actually print, 300 DPI
    <message>-<sheet>-proof.png     with --proof: die outlines, on plain paper
    manifest.csv                    one row per printed sticker
    seed_stickers.sql               the rows the API needs to answer a scan
    contact-sheet.html              every design proofed at size, plus the runbook

Each stock gets its own directory because each gets its own codes: a 2.5" and a
3" sticker are different physical objects that end up on different poles, so
they must never share an identity.

Deterministic: same arguments, same codes, same bytes. That matters more here
than on a tee, because half the run is already stuck to lamp posts by the time
anyone reruns it.

## Print it right

Print at 100% / "actual size". Any "fit to page" or "shrink oversized pages"
setting rescales the sheet by a percent or two, which is invisible on a photo
and fatal on a die-cut grid — every sticker creeps further off its circle
towards the edges of the page. Run one --proof sheet on plain paper first and
hold it against a label sheet against a window.
"""

import argparse
import base64
import csv
import json
import shutil
import sys
from pathlib import Path

import cairosvg
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent))

import campaign  # noqa: E402
import codes as codes_mod  # noqa: E402
import qrart_bridge  # noqa: E402
import sheet as sheet_mod  # noqa: E402
import sticker as art  # noqa: E402

#: Everything the build writes lives under out/<stock>/ and nowhere else.
#: Ad-hoc renders — proofs, experiments, one-off previews — go to the scratchpad
#: instead, because they used to pile up here and there is no way to tell a
#: stale experiment from a sheet you are about to print.
OUT = Path(__file__).parent / "out"
DPI = art.DPI
MAP_SLUG = "nyc-proposals"

# The die outline drawn by --proof. Never appears on a real print file.
PROOF_RULE = "#00C4D4"
PROOF_PAPER = "#ffffff"


def sheet_svg(stock, placements, *, proof: bool) -> str:
    """One Letter sheet. `placements` is a list of (col, row, line, url, art)."""
    w, h = sheet_mod.PAGE_W * DPI, sheet_mod.PAGE_H * DPI
    body = [f'<rect width="{w}" height="{h}" fill="{PROOF_PAPER}"/>'] if proof else []

    for col, row, line, url, art_path, style, way in placements:
        cx_in, cy_in = stock.centre(col, row)
        canvas, svg = art.disc(line, url, stock.die, stock.bleed, art=art_path,
                               style=style, colourway=way)
        x = cx_in * DPI - canvas / 2
        y = cy_in * DPI - canvas / 2
        body.append(f'<g transform="translate({x:.2f},{y:.2f})">{svg}</g>')
        if proof:
            body.append(
                f'<circle cx="{cx_in * DPI:.2f}" cy="{cy_in * DPI:.2f}" '
                f'r="{stock.die / 2 * DPI:.2f}" fill="none" '
                f'stroke="{PROOF_RULE}" stroke-width="2"/>'
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">{"".join(body)}</svg>'
    )


def half_split(stock) -> tuple[str, int]:
    """How to cut a sheet in two: ("rows"|"cols", how many of them per half).

    Derived rather than configured, because only one axis of each grid divides
    evenly. The 12-up is 3 across by 4 down, so it splits into a top half and a
    bottom half of two rows each. The 6-up is 2 across by 3 down — three rows
    will not halve — so it splits down the middle into a left and a right
    column. Either way each half is exactly half the sheet, and the boundary
    falls on a straight line the person peeling them can see.
    """
    if stock.down % 2 == 0:
        return "rows", stock.down // 2
    if stock.across % 2 == 0:
        return "cols", stock.across // 2
    raise ValueError(
        f'{stock.key}": a {stock.across}x{stock.down} grid has no even axis, '
        f"so it cannot carry two messages in equal halves"
    )


def pair_up(keys: list[str]) -> list[tuple[str, str | None]]:
    """Two messages to a sheet, in the order given.

    An odd message out gets a sheet to itself rather than half a sheet and a
    gap: a half-blank sheet wastes the labels, which are the expensive part.
    """
    return [(keys[i], keys[i + 1] if i + 1 < len(keys) else None)
            for i in range(0, len(keys), 2)]


def plan(stock, keys: list[str], sheets: int, style: str = "flat",
         way: str = "dark", base: int = 0) -> list[dict]:
    """Mint every sticker in the run and assign it a sheet + grid cell.

    TWO messages to a sheet, split down the middle (see half_split). A sheet of
    twelve identical stickers was the wrong unit: you do not go out to fix one
    thing twelve times, you go out with a couple of complaints and whichever
    poles you pass. Half a sheet of each means one sheet in a pocket covers two
    kinds of problem.

    The index a code is minted at is per message and starts at 0, so asking for
    more sheets EXTENDS the run rather than reshuffling it — codes already
    printed keep their identity.

    `base` slides that whole window further along the same sequence, which is
    how a LATER print run gets codes of its own. It is deliberately not a new
    campaign string: bumping the campaign would re-mint the codes already on
    poles, so re-running an old build would stop reproducing the stickers it
    printed — the one guarantee codes_mod promises. Extending the index keeps
    one ordered namespace per (campaign, stock, message), so "no code is ever
    printed twice" is checkable by looking at the ranges.
    """
    axis, span = half_split(stock)
    per_half = stock.per_sheet // 2

    rows = []
    for pair_no, (a, b) in enumerate(pair_up(keys)):
        # An unpaired message fills the whole sheet rather than half of one.
        halves = [(a, 0), (b, 1)] if b else [(a, 0), (a, 1)]
        for key, half in halves:
            s = campaign.BY_KEY[key]
            # Where this half's codes start in the message's own mint sequence.
            # Zero for a paired message, which owns one half. For the unpaired
            # case the SAME message fills both halves, so the second half has to
            # start after the first or the two would be the same twelve codes —
            # and two stickers with one identity share a pole.
            start = base + (0 if b else half * per_half * sheets)
            run = codes_mod.mint_run(key, per_half * sheets, stock.key, start=start)
            for i, code in enumerate(run):
                sheet_no, slot = divmod(i, per_half)
                if axis == "rows":
                    row = half * span + slot // stock.across
                    col = slot % stock.across
                else:
                    col = half * span + slot % span
                    row = slot // span
                rows.append({
                    "stock": stock.key,
                    "message": key,
                    "line": s.display,
                    "vote_type": s.vote_type,
                    "kind": s.kind,
                    "src": s.src,
                    "code": code,
                    "url": codes_mod.url_for(code),
                    #: Sheets are numbered across the whole run, and named after
                    #: the pair they carry, so a printed sheet says what is on it.
                    "sheet": pair_no * sheets + sheet_no,
                    "pair": f"{a}+{b}" if b else a,
                    "col": col,
                    "row": row,
                    # Filled in by paint() when --art is on; empty means the plain
                    # vector code, which is what every sticker gets by default.
                    "art": None,
                    #: "flat" (the drawn module grid) or "iso" (the isometric city).
                    #: Recorded per sticker so verify_scan re-renders exactly what
                    #: was printed rather than assuming the default.
                    "style": style,
                    #: "light" (dark ink on bare stock) or "dark" (the app's
                    #: terminal palette, printed). Recorded per sticker so
                    #: verify_scan re-renders what was printed.
                    "colourway": way,
                })
    return rows


def paint(rows, spec: qrart_bridge.ArtSpec, *, dry_run: bool) -> int:
    """Give every sticker in the run a qrart-painted code. Returns the count
    that got one.

    Art is per CODE, and every sticker has a different code — that is the whole
    premise of the scan flow — so this is one diffusion run per sticker, not one
    per design. At the defaults that is roughly an hour per twelve-up sheet, so
    the estimate is printed first and `--art-dry-run` stops here.

    A code that yields nothing scannable falls back to its plain vector version
    rather than failing the sheet: a sticker that scans is worth more than a
    sticker that is pretty, and one stubborn code should not throw away an
    hour of finished work. The count of fallbacks is reported at the end.
    """
    ok, reason = qrart_bridge.availability()
    print(f"  qrart: {reason}")
    if not ok:
        raise SystemExit(1)
    print(f"  {qrart_bridge.estimate(len(rows), spec)}")

    cached = sum(1 for r in rows if qrart_bridge.cached_art(r["url"], spec))
    print(f"  {cached}/{len(rows)} already in art-cache/")
    if dry_run:
        print("  --art-dry-run: stopping before any generation")
        raise SystemExit(0)

    painted = 0
    for i, r in enumerate(rows, 1):
        print(f"  [{i}/{len(rows)}] {r['message']} {r['code']}")
        got = qrart_bridge.generate_art(r["url"], spec)
        if got:
            r["art"] = str(got)
            painted += 1
    if painted < len(rows):
        print(f"  {len(rows) - painted} sticker(s) fell back to the plain code")
    return painted


def write_sheets(stock, rows, out: Path, *, proof: bool) -> list[Path]:
    """Render every sheet. Returns the SVG masters IN SHEET ORDER — the PDF is
    assembled from this list, and sorting it by filename instead would page the
    packet alphabetically ("press+cross-3" before "wait+fix-1"), so the manifest
    would name a sheet number the PDF does not have at that page."""
    written = []
    svgs: list[tuple[int, Path]] = []
    by_sheet = {}
    for r in rows:
        by_sheet.setdefault((r.get("pair") or r["message"], r["sheet"]), []).append(r)

    for (pair, n), group in sorted(by_sheet.items()):
        placements = [(r["col"], r["row"], r["line"], r["url"],
                       Path(r["art"]) if r.get("art") else None,
                       r.get("style") or "flat",
                       r.get("colourway") or "dark") for r in group]
        stem = f'{pair}-{n + 1}'

        svg = sheet_svg(stock, placements, proof=False)
        (out / f"{stem}.svg").write_text(svg)
        # The SVG master keeps a transparent field, because on paper the field
        # IS the label stock and nothing should be printed there. The PNG does
        # not get that luxury: it is the file that gets opened, previewed and
        # handed to a print dialog, and transparency composites against whatever
        # is behind it — which in Preview and Finder is a mid grey, turning
        # near-black type into something you can barely read. So the PNG is
        # flattened onto white. It prints identically (a consumer printer lays
        # no ink for white on white paper) and it can no longer be previewed,
        # or composited, onto the wrong colour.
        cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out / f"{stem}.png"),
                         output_width=int(sheet_mod.PAGE_W * DPI),
                         output_height=int(sheet_mod.PAGE_H * DPI),
                         background_color=PROOF_PAPER)
        written += [out / f"{stem}.svg", out / f"{stem}.png"]
        svgs.append((n, out / f"{stem}.svg"))

        if proof:
            pv = sheet_svg(stock, placements, proof=True)
            cairosvg.svg2png(bytestring=pv.encode(),
                             write_to=str(out / f"{stem}-proof.png"),
                             output_width=int(sheet_mod.PAGE_W * DPI),
                             output_height=int(sheet_mod.PAGE_H * DPI))
            written.append(out / f"{stem}-proof.png")

    return [path for _n, path in sorted(svgs)]


#: US Letter in POINTS. The PDF has to be exactly this or the printer scales it,
#: and a die-cut grid does not survive being scaled — every sticker creeps
#: further off its circle towards the edges of the page.
PAGE_PT = (8.5 * 72, 11 * 72)

#: cairosvg measures `output_width` in CSS pixels and writes PDF in points,
#: converting at its `dpi` (96 by default). Asking for 612 "px" therefore
#: produced a 459 pt page — a 75% sheet, which every print dialog would then
#: happily "fit to page" and nobody would notice until the die missed. Ask in
#: px, and let the assertion below check the points we actually got.
PX_PER_PT = 96 / 72


def write_pdf(stock, sheet_svgs: list[Path], out: Path, suffix: str = "") -> Path:
    """Every sheet of a stock, joined into one print-ready PDF.

    Vector all the way through — the pages are the SVG masters, not the PNGs —
    so the type stays outlines and the code stays hard-edged at any zoom the
    print shop's RIP happens to use.

    The page box is asserted rather than assumed. A PDF that is a hair off
    Letter gets silently scaled to fit by every print dialog there is, and
    scaling is the one thing this artwork cannot take.
    """
    writer = PdfWriter()
    for svg_path in sheet_svgs:
        pdf_bytes = cairosvg.svg2pdf(
            bytestring=svg_path.read_bytes(),
            output_width=PAGE_PT[0] * PX_PER_PT,
            output_height=PAGE_PT[1] * PX_PER_PT,
        )
        tmp = out / f".{svg_path.stem}.pdf"
        tmp.write_bytes(pdf_bytes)
        writer.append(str(tmp))
        tmp.unlink()

    path = out / f'cityedit-stickers-{stock.key}in{suffix}.pdf'
    with path.open("wb") as fh:
        writer.write(fh)

    # Verify the page box on the file we actually wrote.
    from pypdf import PdfReader
    for i, page in enumerate(PdfReader(str(path)).pages):
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        if abs(w - PAGE_PT[0]) > 0.5 or abs(h - PAGE_PT[1]) > 0.5:
            raise ValueError(
                f"{path.name} page {i + 1} is {w:.1f}x{h:.1f} pt, not "
                f"{PAGE_PT[0]:.0f}x{PAGE_PT[1]:.0f} — it would print scaled"
            )
    return path


def write_manifest(rows, out: Path) -> Path:
    path = out / "manifest.csv"
    cols = ["stock", "message", "line", "vote_type", "kind", "src", "code",
            "url", "sheet", "pair", "col", "row", "art", "style", "colourway"]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            # `is None`, not `or ""` — sheet 0, column 0 and row 0 are all
            # falsy, and blanking them turned the top-left sticker of every
            # first sheet into a row with no grid position. The manifest exists
            # to say which disc is in which cell; a hole there is the bug it is
            # supposed to catch.
            w.writerow({c: ("" if r.get(c) is None else r[c]) for c in cols})
    return path


def write_seed(rows, out: Path) -> Path:
    """The DB rows a scan needs. Idempotent: re-running the build and re-running
    this SQL must not disturb a code that is already stuck to a pole and already
    resolved to a location, so every insert is ON CONFLICT DO NOTHING and the
    resolved columns are never written here."""
    path = out / "seed_stickers.sql"
    seen = {}
    lines = [
        "-- City Edit sticker codes. Generated by tools/stickers/build_stickers.py.",
        "-- Safe to re-run: existing codes (and any location they have already",
        "-- resolved to) are left untouched.",
        "BEGIN;",
    ]
    for r in rows:
        if r["code"] in seen:
            continue
        seen[r["code"]] = True
        lines.append(
            "INSERT INTO sticker_codes "
            "(code, map_slug, vote_type, headline, src_tag, campaign) VALUES ("
            f"'{r['code'].lower()}', '{MAP_SLUG}', "
            f"'{r['vote_type'].replace(chr(39), chr(39) * 2)}', "
            f"'{r['line'].replace(chr(39), chr(39) * 2)}', "
            f"'{r['src']}', '{codes_mod.CAMPAIGN}') "
            "ON CONFLICT (code) DO NOTHING;"
        )
    lines.append("COMMIT;")
    path.write_text("\n".join(lines) + "\n")
    return path


def contact_sheet(stock, keys: list[str], rows, out: Path,
                  base: int = 0) -> Path:
    """A page that proofs every design at its real size and carries the runbook,
    so the print decisions live next to the thing they apply to."""
    cards = []
    for key in keys:
        s = campaign.BY_KEY[key]
        url = codes_mod.url_for(codes_mod.mint(key, base, stock.key))
        first = next((r for r in rows if r["message"] == key), None)
        art_path = Path(first["art"]) if first and first.get("art") else None
        style = first["style"] if first and first.get("style") else "flat"
        way = first["colourway"] if first and first.get("colourway") else "light"
        _canvas, svg = art.disc(s.display, url, stock.die, stock.bleed,
                                art=art_path, style=style, colourway=way)
        m = art.metrics(s.display, url, stock.die, stock.bleed, style)
        px = (stock.die + 2 * stock.bleed) * DPI
        one = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'viewBox="0 0 {px} {px}" '
               f'width="{px}" height="{px}">{svg}</svg>')
        b64 = base64.b64encode(one.encode()).decode()
        cards.append(f"""
        <figure class="card">
          <img src="data:image/svg+xml;base64,{b64}" alt="{s.display}"
               style="width:{stock.die + 2 * stock.bleed}in">
          <figcaption>
            <b>{s.display}</b>
            <span>casts <code>{s.vote_type}</code> ({s.kind})</span>
            <span>tag <code>{s.src}</code></span>
            <span>{m['qr_modules']}-module code, {m['module_mm']:.2f} mm per module</span>
            <span>type {m['type_pt']:.1f} pt, cap {m['cap_mm']:.1f} mm, {len(m['lines'])} line(s)</span>
            <p>{s.note}</p>
          </figcaption>
        </figure>""")

    sheets = len({(r.get("pair"), r["sheet"]) for r in rows})
    path = out / "contact-sheet.html"
    path.write_text(f"""<!doctype html>
<meta charset="utf-8"><title>City Edit stickers — {stock.die}"</title>
<style>
  :root {{ --ink:#141414; --paper:#f4f5f0; --accent:#9A6410; --rule:rgba(0,0,0,.14); }}
  body {{ margin:0; padding:48px; background:var(--paper); color:var(--ink);
         font:15px/1.55 "Red Hat Mono", ui-monospace, monospace; }}
  h1 {{ font-size:22px; letter-spacing:.06em; text-transform:uppercase; margin:0 0 4px; }}
  .lede {{ max-width:62ch; opacity:.72; margin:0 0 40px; }}
  .grid {{ display:flex; flex-wrap:wrap; gap:40px; align-items:flex-start; }}
  .card {{ margin:0; width:{stock.die + 2 * stock.bleed}in; }}
  .card img {{ display:block; background:#fff;
               box-shadow:0 1px 0 var(--rule), 0 12px 28px rgba(0,0,0,.10);
               border-radius:50%; }}
  figcaption {{ display:flex; flex-direction:column; gap:2px; margin-top:14px;
                font-size:12px; }}
  figcaption b {{ letter-spacing:.05em; }}
  figcaption span {{ opacity:.66; }}
  figcaption p {{ opacity:.55; margin:8px 0 0; }}
  code {{ color:var(--accent); }}
  .run {{ margin:48px 0 0; padding-top:24px; border-top:1px solid var(--rule);
          max-width:70ch; }}
  .run h2 {{ font-size:13px; letter-spacing:.12em; text-transform:uppercase;
             opacity:.55; margin:28px 0 8px; }}
  .warn {{ color:var(--accent); font-weight:600; }}
</style>
<h1>Stickers — {stock.die}" round</h1>
<p class="lede">{stock.title} · ASIN {stock.asin} · {stock.per_sheet} per sheet,
{stock.across}&times;{stock.down} on US Letter · bleed {stock.bleed}" ·
{sheets} sheet(s) in this run.</p>
<div class="grid">{"".join(cards)}</div>
<div class="run">
  <h2>Printing</h2>
  <p><span class="warn">Print at 100% — never "fit to page".</span> Any rescale
  creeps the grid off the die, worst at the sheet edges. Run a
  <code>-proof.png</code> on plain paper first and hold it against a label sheet
  against a window.</p>
  <p>Printer: {stock.printer}. Finish: {stock.finish}.</p>
  <p>The field is unprinted on purpose — the label stock is the paper. Do not
  flatten the art onto a white rectangle; you would be spending a full pass of
  ink to print white on white, and you would lose the drift tolerance the bleed
  band buys.</p>
  <h2>Before the first sticker goes up</h2>
  <p>Load <code>seed_stickers.sql</code> into the database, or every scan is a
  404. Scan one sticker off a printed sheet — not off a screen — before you put
  the rest in your bag.</p>
  <h2>What a scan does</h2>
  <p>First scan of a given code asks for location, then opens the map at that
  spot with the vote type preselected. Once a vote has been cast, the code is
  pinned to that location and every later scan goes straight there.</p>
</div>
""")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stock", choices=sorted(sheet_mod.STOCKS), default="2.5",
                    help='which label stock (default: 2.5")')
    ap.add_argument("--messages", help="comma-separated message keys")
    ap.add_argument("--all", action="store_true", help="every message in the line")
    ap.add_argument("--sheets", type=int, default=1,
                    help="sheets per PAIR of messages (default: 1). Each sheet "
                         "carries two messages, half the sheet each")
    ap.add_argument("--proof", action="store_true",
                    help="also write registration proofs with die outlines")
    ap.add_argument("--clean", action="store_true", help="wipe out/ first")
    ap.add_argument("--start-index", type=int, default=0, metavar="N",
                    help="mint from index N instead of 0, so a later print run "
                         "gets codes of its own without re-minting the ones "
                         "already on poles (see plan())")
    ap.add_argument("--suffix", default="", metavar="S",
                    help="tag the output dir and PDF, e.g. -2026-08-20, so a "
                         "new run never overwrites a printed one")
    ap.add_argument("--colourway", choices=sorted(art.COLOURWAYS), default="dark",
                    help='"dark" (default) prints the app\'s terminal palette: '
                         'white on black with the amber band. "light" is dark '
                         'ink on bare stock — far less ink, and the safer '
                         'choice on the 2.5" matte inkjet paper')
    ap.add_argument("--style", choices=["flat", "iso"], default="flat",
                    help='code style: "flat" (default) is the plain module '
                         'grid — a 29%% bigger module and readable by every '
                         'decoder tried. "iso" is the isometric city, which '
                         'zxing reads perfectly but OpenCV cannot read at all; '
                         'see the README before printing a run of it')

    art_g = ap.add_argument_group(
        "qrart", "Paint the codes with qrart instead of drawing plain modules. "
                 "Off by default. One diffusion run PER STICKER — see --art-dry-run."
    )
    art_g.add_argument("--art", metavar="PROMPT",
                       help="prompt for the painted code, e.g. "
                            "\"aerial view of a dense city at dusk, ink and gold\"")
    art_g.add_argument("--art-dry-run", action="store_true",
                       help="report qrart readiness, cache hits and the time "
                            "estimate, then stop before generating anything")
    art_g.add_argument("--art-ref", type=Path,
                       help="reference image (IP-Adapter): steers palette and mood")
    art_g.add_argument("--art-ref-scale", type=float, default=0.6,
                       help="reference influence 0-1; >0.8 fights the QR")
    art_g.add_argument("--art-strength", type=float, default=1.1,
                       help="ControlNet scale — the art vs scannability dial. "
                            "0.9 fragile, 1.1 balanced, 1.3+ obviously a QR")
    art_g.add_argument("--art-candidates", type=int, default=4,
                       help="images per code; the first that decodes to that "
                            "code's own URL wins")
    art_g.add_argument("--art-steps", type=int, default=30, help="diffusion steps")
    art_g.add_argument("--art-cfg", type=float, default=7.0, help="guidance scale")
    art_g.add_argument("--art-size", type=int, default=768,
                       help="square resolution of the painted code")
    art_g.add_argument("--art-seed", type=int, help="base seed (random if unset)")
    art_g.add_argument("--art-negative", default=qrart_bridge.DEFAULT_NEGATIVE)
    args = ap.parse_args()

    problems = campaign.validate()
    stock = sheet_mod.STOCKS[args.stock]
    problems += sheet_mod.validate(stock)
    if problems:
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    if args.all:
        keys = [s.key for s in campaign.STICKERS]
    elif args.messages:
        keys = [k.strip() for k in args.messages.split(",") if k.strip()]
        unknown = [k for k in keys if k not in campaign.BY_KEY]
        if unknown:
            print(f"unknown message(s): {', '.join(unknown)}", file=sys.stderr)
            return 1
    else:
        keys = campaign.STARTERS

    sheets = max(1, args.sheets)

    out = OUT / f"{stock.key}{args.suffix}"
    if args.clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    # Anything loose at the root of out/ is left over from an experiment. The
    # build owns this directory, so it tidies it rather than letting stale
    # renders accumulate beside the sheets that are about to be printed.
    for stray in OUT.iterdir():
        if stray.is_file():
            stray.unlink()

    rows = plan(stock, keys, sheets, args.style, args.colourway,
                base=args.start_index)

    if args.art or args.art_dry_run:
        if not args.art:
            print("--art-dry-run needs --art \"<prompt>\"", file=sys.stderr)
            return 1
        paint(rows, qrart_bridge.ArtSpec(
            args.art, negative=args.art_negative, ref=args.art_ref,
            ref_scale=args.art_ref_scale, qr_strength=args.art_strength,
            steps=args.art_steps, cfg=args.art_cfg, size=args.art_size,
            candidates=args.art_candidates, seed=args.art_seed,
        ), dry_run=args.art_dry_run)

    sheet_svgs = write_sheets(stock, rows, out, proof=args.proof)
    pdf = write_pdf(stock, sheet_svgs, out, args.suffix)
    write_manifest(rows, out)
    write_seed(rows, out)
    contact_sheet(stock, keys, rows, out, args.start_index)

    (out / "run.json").write_text(json.dumps({
        "stock": stock.key, "asin": stock.asin, "campaign": codes_mod.CAMPAIGN,
        "messages": keys, "sheetsPerPair": sheets, "stickers": len(rows),
        "startIndex": args.start_index, "suffix": args.suffix,
        "sheets": len({(r.get("pair"), r["sheet"]) for r in rows}),
    }, indent=2) + "\n")

    print(f'{stock.die}" — {len(rows)} stickers, '
          f'{len({(r.get("pair"), r["sheet"]) for r in rows})} sheet(s), '
          f'two messages per sheet')
    for key in keys:
        s = campaign.BY_KEY[key]
        url = codes_mod.url_for(codes_mod.mint(key, args.start_index, stock.key))
        m = art.metrics(s.display, url, stock.die, stock.bleed, args.style)
        print(f'  {s.display:26s} {" / ".join(m["lines"]):28s} '
              f'{m["type_pt"]:5.1f}pt  {m["qr_modules"]}mod '
              f'{m["module_mm"]:.2f}mm  → {s.vote_type} ({s.kind})')
    print(f"\n  packet: {pdf}")
    print(f"  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
