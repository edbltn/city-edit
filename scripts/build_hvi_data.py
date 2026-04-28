#!/usr/bin/env python3
"""
Fetch NYC Heat Vulnerability Index (HVI) scores and ZCTA polygons, join them,
and write a single GeoJSON FeatureCollection to client-react/public/hvi.geojson.

Sources:
  - HVI scores:  NYC Open Data dataset 4mhf-duep   (fields: zcta20, hvi)
  - ZCTA polys:  NYC Open Data dataset pri4-ifjk   (field: zcta, the_geom)
"""

import json
import sys
import urllib.request
from pathlib import Path

HVI_URL = "https://data.cityofnewyork.us/resource/4mhf-duep.json?$limit=5000"
ZCTA_URL = "https://data.cityofnewyork.us/resource/pri4-ifjk.geojson?$limit=5000"
OUT = Path(__file__).resolve().parent.parent / "client-react" / "public" / "hvi.geojson"


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    print("fetching HVI scores...")
    hvi_rows = fetch_json(HVI_URL)
    hvi_by_zip = {}
    for row in hvi_rows:
        zcta = str(row.get("zcta20") or "").strip()
        if not zcta:
            continue
        try:
            score = int(row["hvi"])
        except (KeyError, TypeError, ValueError):
            continue
        hvi_by_zip[zcta] = score
    print(f"  got {len(hvi_by_zip)} HVI entries")

    print("fetching ZCTA polygons...")
    zcta_fc = fetch_json(ZCTA_URL)
    features = zcta_fc.get("features", [])
    print(f"  got {len(features)} polygons")

    joined = []
    missed_zips: list[str] = []
    for feat in features:
        props = feat.get("properties") or {}
        zcta = str(props.get("zcta") or props.get("modzcta") or "").strip()
        score = hvi_by_zip.get(zcta)
        if score is None:
            missed_zips.append(zcta)
            continue
        joined.append({
            "type": "Feature",
            "properties": {"zcta": zcta, "hvi": score, "label": props.get("label")},
            "geometry": feat.get("geometry"),
        })

    unmatched_hvi = [z for z in hvi_by_zip if not any(
        (f["properties"]["zcta"] == z) for f in joined
    )]

    out_fc = {"type": "FeatureCollection", "features": joined}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out_fc))
    print(f"done: {len(joined)} HVI-scored polygons at {OUT}")
    if missed_zips:
        print(f"  {len(missed_zips)} polygons had no HVI score (skipped)")
    if unmatched_hvi:
        print(f"  {len(unmatched_hvi)} HVI entries had no matching polygon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
