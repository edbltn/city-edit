#!/usr/bin/env python3
"""
Check a built run against the live site, before any of it is printed.

    ./env/bin/python verify_prod.py
    ./env/bin/python verify_prod.py --base http://localhost:3000

`verify_scan.py` proves a sticker decodes to the URL the manifest intends. This
proves that URL is a real page on a real server, that the campaign's vocabulary
exists there, and that the codes have been seeded. Those are different failures
and only the first one is caught locally.

The chain checked is **pixels → URL → live page**, in that order, because that
is the chain a person with a phone actually walks. Comparing the manifest to
prod would only prove the manifest agrees with itself.

## What each step catches

1. **Decode.** Renders every sticker and reads it back. A code that renders
   wrong is the expensive failure — it is on paper before anyone finds out.
2. **Resolve.** Fetches each decoded URL. nginx serves the SPA shell for any
   path, so a 200 here means the address is real and the app will boot on it.
3. **Seeded.** Asks the API about each code. A code that is not in the database
   is a 404 at the kerb: the scanner gets the "that code isn't one of ours"
   screen. This is data, not code — see the README's pre-flight.
4. **Vocabulary.** Checks every vote type the campaign asks for still exists on
   the map. A missing one does not 404; it silently falls back to a different
   proposal, which is worse.

Steps 1 and 2 gate the exit code, because they are the ones that would make a
PRINTED sticker wrong. Steps 3 and 4 are reported loudly but do not fail the
run: they are fixed by loading seed SQL and adding a vote type, both of which
can happen after the sheets come off the printer and neither of which requires
a reprint.

## Why this is not affected by URL-format changes

A sticker encodes `HTTPS://CITYEDIT.ORG/S/<CODE>` and nothing else — no map, no
coordinates, no vote type, no selection params. Everything about where a scan
lands is resolved server-side at scan time. That is deliberate: the app's
address format can be refactored freely, and has been, without invalidating a
single sticker already on a pole. Run this after such a change to confirm it,
rather than reprinting.
"""

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cairosvg
import cv2
import numpy as np
import zxingcpp

sys.path.insert(0, str(Path(__file__).parent))

import campaign  # noqa: E402
import sheet as sheet_mod  # noqa: E402
import sticker as art  # noqa: E402

OUT = Path(__file__).parent / "out"
MAP_SLUG = "nyc-proposals"
UA = {"User-Agent": "cityedit-sticker-verify"}

#: Wide enough that a decode failure means the ART is wrong rather than the
#: capture being too small — verify_scan.py is where the resolution margin is
#: measured.
CAPTURE_PX = 420


def load_run() -> list[dict]:
    rows = []
    for manifest in sorted(OUT.glob("*/manifest.csv")):
        rows += list(csv.DictReader(manifest.open()))
    return rows


def decode(row: dict) -> str | None:
    """Read a sticker back off its own rendered pixels."""
    stock = sheet_mod.STOCKS[row["stock"]]
    canvas, body = art.disc(
        row["line"], row["url"], stock.die, stock.bleed,
        style=row.get("style") or "flat",
        colourway=row.get("colourway") or "dark",
    )
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{canvas}" '
           f'height="{canvas}" viewBox="0 0 {canvas} {canvas}">{body}</svg>')
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=CAPTURE_PX,
                           output_height=CAPTURE_PX, background_color="#808080")
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    res = zxingcpp.read_barcode(img)
    return res.text if res else None


def fetch(url: str) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20) as r:
            return r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                       # network, DNS, timeout
        return None, str(e)[:60]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://cityedit.org",
                    help="site to check against (default: prod)")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent requests; keep it neighbourly")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    rows = load_run()
    if not rows:
        print("no out/*/manifest.csv — run build_stickers.py first", file=sys.stderr)
        return 1
    print(f"{len(rows)} stickers, against {base}\n")

    pool = ThreadPoolExecutor(args.workers)
    failed = False

    # 1. pixels → URL
    decoded = list(pool.map(decode, rows))
    bad = [(r, d) for r, d in zip(rows, decoded) if d != r["url"]]
    print(f"  {'FAIL' if bad else 'ok  '} decoded off the art, matches manifest: "
          f"{len(rows) - len(bad)}/{len(rows)}")
    for r, d in bad[:6]:
        print(f"         {r['stock']}\" {r['code']}: got {d!r}")
    failed |= bool(bad)

    # 2. URL → live page
    urls = [d for d in decoded if d]
    got = list(pool.map(fetch, urls))
    unresolved = [(u, s, c) for u, (s, c) in zip(urls, got)
                  if not (s == 200 and "text/html" in c)]
    print(f"  {'FAIL' if unresolved else 'ok  '} resolves (200 text/html): "
          f"{len(urls) - len(unresolved)}/{len(urls)}")
    for u, s, c in unresolved[:6]:
        print(f"         {u} -> {s} {c}")
    failed |= bool(unresolved)

    # 3. seeded — advisory: fixable without reprinting
    api = list(pool.map(
        lambda u: fetch(f"{base}/api/stickers/{u.rsplit('/', 1)[1]}"), urls))
    seeded = sum(1 for s, _ in api if s == 200)
    print(f"  {'ok  ' if seeded == len(urls) else 'note'} seeded in the database: "
          f"{seeded}/{len(urls)}")
    if seeded < len(urls):
        print("         unseeded codes hit the unknown-code screen — load "
              "out/<stock>/seed_stickers.sql (safe to re-run)")

    # 4. vocabulary — advisory, and the quiet one: a missing type does not
    #    error, it silently casts something else.
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"{base}/api/maps/{MAP_SLUG}", headers=UA),
                timeout=20) as r:
            have = {v["label"] for v in json.loads(r.read())["voteTypes"]}
    except Exception as e:
        print(f"  note could not read {MAP_SLUG}'s vote types: {e}")
        have = None

    if have is not None:
        missing = [s for s in campaign.STICKERS if s.vote_type not in have]
        print(f"  {'ok  ' if not missing else 'note'} vote types present on "
              f"{MAP_SLUG}: {len(campaign.STICKERS) - len(missing)}/"
              f"{len(campaign.STICKERS)}")
        for s in missing:
            print(f"         {s.line!r} wants {s.vote_type!r} — add it with "
                  f"server/manage_vote_types.py, or that sticker casts "
                  f"something else")

    print()
    if failed:
        print("a printed sticker would be WRONG — do not print", file=sys.stderr)
        return 1
    print("every code decodes and resolves; safe to print")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
