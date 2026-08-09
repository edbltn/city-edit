#!/usr/bin/env python3
"""
Download the open datasets behind the longest-wait intersection ranking.

Everything lands in data/raw/wait/ and is cached — re-running only fetches
what's missing (pass --force to refresh).

Sources (all public, no keys):
  ped_demand.geojson   NYC DOT Pedestrian Mobility Plan "Pedestrian Demand"
                       (fwpa-qxaf) — every street segment citywide scored 1-5
                       for pedestrian need. Our citywide volume backbone.
  ped_counts.json      NYC DOT Bi-Annual Pedestrian Counts (cqsj-cfgu) — real
                       screenline counts at 114 locations. Calibration truth.
  barnes_dance.json    Exclusive Pedestrian Signal locations (8kuj-2n3u) — an
                       all-stop ped phase means a longer wait for a shorter,
                       safer crossing.
  lpi.json             VZV Leading Pedestrian Interval signals (xc4v-ntf4).
  subway_ridership.json  MTA subway entries per station complex, summed over a
                       recent month (5wq4-mkjj) — the strongest citywide proxy
                       for where pedestrians actually are.
"""

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "raw" / "wait"

NYC = "https://data.cityofnewyork.us/resource"
NYS = "https://data.ny.gov/resource"

# Month summed for the subway proxy. A single recent month smooths weekday /
# weekend and one-off service changes without dragging in seasonal drift.
RIDERSHIP_START = "2026-05-01T00:00:00"
RIDERSHIP_END = "2026-06-01T00:00:00"
RIDERSHIP_DAYS = 31

SIMPLE = {
    "ped_counts.json": f"{NYC}/cqsj-cfgu.json?$limit=5000",
    "barnes_dance.json": f"{NYC}/8kuj-2n3u.json?$limit=5000",
    "lpi.json": f"{NYC}/xc4v-ntf4.json?$limit=50000",
    "ped_demand.geojson": f"{NYC}/fwpa-qxaf.geojson?$limit=200000",
}


def fetch(url: str, dest: Path) -> None:
    print(f"  GET {url[:110]}…")
    with urllib.request.urlopen(url, timeout=600) as r:
        dest.write_bytes(r.read())
    print(f"  -> {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def subway_url() -> str:
    q = {
        "$select": "station_complex_id,station_complex,borough,latitude,longitude,"
                   "sum(ridership) as riders",
        "$where": f"transit_timestamp >= '{RIDERSHIP_START}' "
                  f"AND transit_timestamp < '{RIDERSHIP_END}'",
        "$group": "station_complex_id,station_complex,borough,latitude,longitude",
        "$limit": "5000",
    }
    return f"{NYS}/5wq4-mkjj.json?" + urllib.parse.urlencode(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download cached files")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    targets = dict(SIMPLE)
    targets["subway_ridership.json"] = subway_url()

    for name, url in targets.items():
        dest = OUT / name
        if dest.exists() and not args.force:
            print(f"cached {name} ({dest.stat().st_size / 1e6:.1f} MB)")
            continue
        print(f"fetching {name}")
        fetch(url, dest)

    # Normalise the subway month into daily entries so downstream code never
    # has to remember the window.
    raw = json.loads((OUT / "subway_ridership.json").read_text())
    daily = [
        {
            "station": r["station_complex"],
            "borough": r.get("borough", ""),
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "daily_entries": float(r["riders"]) / RIDERSHIP_DAYS,
        }
        for r in raw
        if r.get("latitude") and r.get("longitude")
    ]
    (OUT / "subway_daily.json").write_text(json.dumps(daily, indent=1))
    total = sum(d["daily_entries"] for d in daily)
    print(f"\n{len(daily)} station complexes, {total:,.0f} entries/day citywide")


if __name__ == "__main__":
    main()
