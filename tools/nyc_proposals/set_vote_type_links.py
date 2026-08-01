#!/usr/bin/env python3
"""
Install per-vote-type location links on a map from a plan JSONL.

Reads the plan produced by `import_to_map.py plan` and builds, for each vote
type, the ordered list of proposal locations as in-app deep links
(/m/<slug>?w=…&vt=…). Installs the whole mapping via the admin API
(maps.vote_type_links); the client renders each list as [#1] [#2] anchors
beside the vote-type label in proposal cards.

    python3 set_vote_type_links.py --plan plan.jsonl \
        --base http://localhost:5001 --map nyc-proposals \
        --token $ADMIN_TOKEN [--dry-run]

Idempotent: the mapping is replaced wholesale on every run.
"""
import argparse
import json
import os
import sys
import urllib.request
from urllib.parse import urlencode


def link_for(slug: str, entry: dict) -> dict | None:
    if entry.get("cast") == "route":
        (slat, slng), (elat, elng) = entry["start"], entry["end"]
        w = f"{slat:.6f},{slng:.6f};{elat:.6f},{elng:.6f}"
    elif entry.get("cast") == "point":
        plat, plng = entry["point"]
        w = f"{plat:.6f},{plng:.6f}"
    else:
        return None
    qs = urlencode({"w": w, "vt": entry["vote_type"]})
    return {"url": f"/m/{slug}?{qs}", "title": entry["title"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--base", default="http://localhost:5001")
    ap.add_argument("--map", dest="slug", default="nyc-proposals")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    links: dict[str, list[dict]] = {}
    with open(args.plan) as f:
        for line in f:
            entry = json.loads(line)
            lnk = link_for(args.slug, entry)
            if lnk:
                links.setdefault(entry["vote_type"], []).append(lnk)

    total = sum(len(v) for v in links.values())
    for label, ls in sorted(links.items(), key=lambda kv: -len(kv[1])):
        print(f"  {label}: {len(ls)}")
        for i, lnk in enumerate(ls):
            print(f"    [#{i + 1}] {lnk['title'][:60]}  {lnk['url'][:90]}")
    print(f"{total} links across {len(links)} vote types")
    if args.dry_run:
        return
    if not args.token:
        sys.exit("--token (or ADMIN_TOKEN) required")

    req = urllib.request.Request(
        f"{args.base}/api/admin/maps/{args.slug}/vote-type-links",
        data=json.dumps({"links": links}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": args.token},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        print(resp.status, resp.read().decode())


if __name__ == "__main__":
    main()
