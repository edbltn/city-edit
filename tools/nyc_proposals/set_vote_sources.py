#!/usr/bin/env python3
"""
Register where a map's imported votes came from, so the app can cite them.

Reads the plan produced by `import_to_map.py plan` and registers, for every
proposal it cast, the page it was scraped from (its nycdotprojects.info project
URL) against the SAME synthetic voter the cast used. The app then resolves a
selection's edges → the proposals whose votes are actually on them
(/api/vote-sources), and each vote-type row in a proposal card links to its own
source page — not to every proposal of that type elsewhere in the city.

    python3 set_vote_sources.py --plan plan.jsonl \
        --base http://localhost:5001 --map nyc-proposals \
        --token $ADMIN_TOKEN [--dry-run]

Idempotent: the map's registry is replaced wholesale on every run. Keyed by
voter id (not edge ids), so a graph rebuild + resnap can't strand attribution.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_to_map import voter_id  # noqa: E402  (the same hash the casts used)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--base", default="http://localhost:5001")
    ap.add_argument("--map", dest="slug", default="nyc-proposals")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sources, seen = [], set()
    with open(args.plan) as f:
        for line in f:
            entry = json.loads(line)
            # Only entries that actually cast have rows to attribute.
            if entry.get("cast") not in ("route", "point") or entry["url"] in seen:
                continue
            seen.add(entry["url"])
            sources.append({"voter_id": voter_id(entry["url"]),
                            "url": entry["url"], "title": entry["title"]})
            print(f"  {entry['vote_type']:<28} {entry['title'][:44]:<44} {entry['url']}")

    print(f"{len(sources)} proposals to register on '{args.slug}'")
    if args.dry_run:
        return
    if not args.token:
        sys.exit("--token (or ADMIN_TOKEN) required")

    req = urllib.request.Request(
        f"{args.base}/api/admin/maps/{args.slug}/vote-sources",
        data=json.dumps({"sources": sources}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": args.token},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode())


if __name__ == "__main__":
    main()
