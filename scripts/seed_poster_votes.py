#!/usr/bin/env python3
"""
Seed one vote per poster intersection on the nyc-intersections map, so every
QR scan lands on a proposal that already shows campaign support instead of
"No votes yet".

Reads data/posters/out/manifest.csv, dedupes to unique (intersection, type)
pairs — a fitting point-kind vote type per campaign — and casts each through
the app's own /api/vote (point fallback: the server snaps to the graph).

All casts use ONE deterministic voter id (uuid5 of "cityedit-poster-seed"),
so re-runs are idempotent (clear-then-cast per block+type) and the seed
votes are identifiable in the DB by that device hash if they ever need to
be removed.

Usage:
  python scripts/seed_poster_votes.py                  # dry-run (prints plan)
  python scripts/seed_poster_votes.py --cast           # cast on prod
  python scripts/seed_poster_votes.py --cast --base-url http://localhost:5001
"""

import argparse
import csv
import json
import time
import uuid
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "data" / "posters" / "out" / "manifest.csv"

SEED_VOTER_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "cityedit-poster-seed"))

# Fitting point-kind vote type per campaign (route kinds need a corridor, so
# point casts always use point types).
CAMPAIGN_VOTE_TYPE = {
    "ped_no1": "Add pedestrian signal",
    "after_dark": "Add intersection lighting",
    "cyc_no1": "Fix dangerous intersection",
    "overall": "Fix dangerous intersection",
    "dark": "Fix dangerous intersection",
    "nabe_no1": "Fix dangerous intersection",
    "heating_up": "Fix dangerous intersection",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://cityedit.org")
    ap.add_argument("--map", default="nyc-crossings")
    ap.add_argument("--cast", action="store_true", help="actually cast (default: dry-run)")
    args = ap.parse_args()

    # Unique (lat, lon, vote_type); keep the first campaign/code for logging.
    plan = {}
    for m in csv.DictReader(open(MANIFEST)):
        key = (m["lat"], m["lon"], CAMPAIGN_VOTE_TYPE[m["campaign"]])
        plan.setdefault(key, m)

    print(f"{len(plan)} unique (intersection, type) casts from "
          f"{sum(1 for _ in csv.DictReader(open(MANIFEST)))} posters; "
          f"voter_id {SEED_VOTER_ID}")
    ok = fail = 0
    for (lat, lon, vt), m in sorted(plan.items(), key=lambda kv: kv[1]["code"]):
        label = f"{m['code']:7s} {vt:28s} {m['name']}"
        if not args.cast:
            print(f"DRY  {label}")
            continue
        body = json.dumps({
            "map": args.map, "mode": "walk", "vote_type": vt,
            "voter_id": SEED_VOTER_ID, "point": [float(lat), float(lon)],
            "direction": 1,
        }).encode()
        req = urllib.request.Request(
            f"{args.base_url}/api/vote", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                json.load(resp)
            ok += 1
            print(f"CAST {label}")
        except Exception as e:
            fail += 1
            print(f"FAIL {label} — {e}")
        time.sleep(0.3)
    if args.cast:
        print(f"\n{ok} cast, {fail} failed")


if __name__ == "__main__":
    main()
