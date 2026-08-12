#!/usr/bin/env python3
"""
Build the City Edit sticker sheets.

    ./env/bin/python build_stickers.py                    # the three starters
    ./env/bin/python build_stickers.py --all              # every message
    ./env/bin/python build_stickers.py --stock 3 --proof  # 3" + registration proof
    ./env/bin/python build_stickers.py --art "PROMPT" --art-dry-run  # qrart, costed

Writes, into out/<stock>/:

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


def plan(stock, keys: list[str], copies: int, style: str = "flat",
         way: str = "dark") -> list[dict]:
    """Mint every sticker in the run and assign it a sheet + grid cell.

    One message per sheet: a sheet is a stack of the same complaint, which is
    how they get used — you go out to fix one thing at a time. The index a code
    is minted at is global per message and starts at 0, so re-running with a
    larger --copies extends the run instead of reshuffling it.
    """
    rows = []
    for key in keys:
        s = campaign.BY_KEY[key]
        run = codes_mod.mint_run(key, copies, stock.key)
        for i, code in enumerate(run):
            rows.append({
                "stock": stock.key,
                "message": key,
                "line": s.display,
                "vote_type": s.vote_type,
                "kind": s.kind,
                "src": s.src,
                "code": code,
                "url": codes_mod.url_for(code),
                "sheet": i // stock.per_sheet,
                "col": (i % stock.per_sheet) % stock.across,
                "row": (i % stock.per_sheet) // stock.across,
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
    written = []
    by_sheet = {}
    for r in rows:
        by_sheet.setdefault((r["message"], r["sheet"]), []).append(r)

    for (message, n), group in sorted(by_sheet.items()):
        placements = [(r["col"], r["row"], r["line"], r["url"],
                       Path(r["art"]) if r.get("art") else None,
                       r.get("style") or "flat",
                       r.get("colourway") or "dark") for r in group]
        stem = f'{message}-{n + 1}'

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

        if proof:
            pv = sheet_svg(stock, placements, proof=True)
            cairosvg.svg2png(bytestring=pv.encode(),
                             write_to=str(out / f"{stem}-proof.png"),
                             output_width=int(sheet_mod.PAGE_W * DPI),
                             output_height=int(sheet_mod.PAGE_H * DPI))
            written.append(out / f"{stem}-proof.png")

    return written


def write_manifest(rows, out: Path) -> Path:
    path = out / "manifest.csv"
    cols = ["stock", "message", "line", "vote_type", "kind", "src", "code",
            "url", "sheet", "col", "row", "art", "style", "colourway"]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) or "" for c in cols})
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


def contact_sheet(stock, keys: list[str], rows, out: Path) -> Path:
    """A page that proofs every design at its real size and carries the runbook,
    so the print decisions live next to the thing they apply to."""
    cards = []
    for key in keys:
        s = campaign.BY_KEY[key]
        url = codes_mod.url_for(codes_mod.mint(key, 0, stock.key))
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

    sheets = len({(r["message"], r["sheet"]) for r in rows})
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
    ap.add_argument("--copies", type=int, default=None,
                    help="stickers per message (default: one full sheet)")
    ap.add_argument("--proof", action="store_true",
                    help="also write registration proofs with die outlines")
    ap.add_argument("--clean", action="store_true", help="wipe out/ first")
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

    copies = args.copies or stock.per_sheet

    out = OUT / stock.key
    if args.clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    # Anything loose at the root of out/ is left over from an experiment. The
    # build owns this directory, so it tidies it rather than letting stale
    # renders accumulate beside the sheets that are about to be printed.
    for stray in OUT.iterdir():
        if stray.is_file():
            stray.unlink()

    rows = plan(stock, keys, copies, args.style, args.colourway)

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

    write_sheets(stock, rows, out, proof=args.proof)
    write_manifest(rows, out)
    write_seed(rows, out)
    contact_sheet(stock, keys, rows, out)

    (out / "run.json").write_text(json.dumps({
        "stock": stock.key, "asin": stock.asin, "campaign": codes_mod.CAMPAIGN,
        "messages": keys, "copies": copies, "stickers": len(rows),
        "sheets": len({(r["message"], r["sheet"]) for r in rows}),
    }, indent=2) + "\n")

    print(f'{stock.die}" — {len(rows)} stickers, '
          f'{len({(r["message"], r["sheet"]) for r in rows})} sheet(s)')
    for key in keys:
        s = campaign.BY_KEY[key]
        url = codes_mod.url_for(codes_mod.mint(key, 0, stock.key))
        m = art.metrics(s.display, url, stock.die, stock.bleed, args.style)
        print(f'  {s.display:26s} {" / ".join(m["lines"]):28s} '
              f'{m["type_pt"]:5.1f}pt  {m["qr_modules"]}mod '
              f'{m["module_mm"]:.2f}mm  → {s.vote_type} ({s.kind})')
    print(f"\n  → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
