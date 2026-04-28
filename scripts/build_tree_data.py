#!/usr/bin/env python3
"""
Download the NYC 2015 Street Tree Census from NYC Open Data and bake it
into a compact binary file the frontend can load once and render with canvas.

Source:
  - NYC Open Data dataset uvpi-gqnh
    https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh
    (fields: latitude, longitude, tree_dbh)

Output format: Float32 little-endian, triples of [lat, lng, dbh].
Written to client-react/public/trees.bin.
"""

import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://data.cityofnewyork.us/resource/uvpi-gqnh.json"
PAGE_SIZE = 50000
OUT = Path(__file__).resolve().parent.parent / "client-react" / "public" / "trees.bin"


def fetch_page(offset: int) -> list[dict]:
    params = {
        "$select": "latitude,longitude,tree_dbh",
        "$where": "latitude IS NOT NULL AND longitude IS NOT NULL",
        "$limit": PAGE_SIZE,
        "$offset": offset,
        "$order": "tree_id",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        import json
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out = OUT.open("wb")
    count = 0
    offset = 0
    start = time.time()

    while True:
        rows = fetch_page(offset)
        if not rows:
            break

        buf = bytearray()
        for r in rows:
            try:
                lat = float(r["latitude"])
                lng = float(r["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            try:
                dbh = float(r.get("tree_dbh") or 0.0)
            except ValueError:
                dbh = 0.0
            buf += struct.pack("<fff", lat, lng, dbh)
            count += 1

        out.write(buf)
        elapsed = time.time() - start
        print(f"  fetched offset={offset} rows={len(rows)} total={count} ({elapsed:.1f}s)", flush=True)

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    out.close()
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"done: {count} trees, {size_mb:.1f} MB at {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
