#!/usr/bin/env python3
"""
Install per-vote-type proposal links on a map from a plan JSONL.

Reads the plan produced by `import_to_map.py plan` and builds, for each vote
type, the list of real proposals of that type — each linking to its SOURCE
document (the project's page on nycdotprojects.info, the same pages the votes
were imported from) and carrying the proposal's coordinates. Installs the whole
mapping via the admin API (maps.vote_type_links); the client shows the three
NEAREST proposals to whatever the card is anchored on, as [a] [b] [c] beside
the vote-type label.

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


def link_for(entry: dict) -> dict | None:
    """{url, title, lat, lng} for a cast plan entry, or None if it wasn't cast.

    Corridors are anchored at their midpoint so proximity ranking doesn't favor
    whichever end happened to be geocoded first.
    """
    if entry.get("cast") == "route":
        (slat, slng), (elat, elng) = entry["start"], entry["end"]
        lat, lng = (slat + elat) / 2, (slng + elng) / 2
    elif entry.get("cast") == "point":
        lat, lng = entry["point"]
    else:
        return None
    return {"url": entry["url"], "title": entry["title"],
            "lat": round(lat, 6), "lng": round(lng, 6)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--base", default="http://localhost:5001")
    ap.add_argument("--map", dest="slug", default="nyc-proposals")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    links: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    with open(args.plan) as f:
        for line in f:
            entry = json.loads(line)
            lnk = link_for(entry)
            if not lnk or (entry["vote_type"], lnk["url"]) in seen:
                continue
            seen.add((entry["vote_type"], lnk["url"]))
            links.setdefault(entry["vote_type"], []).append(lnk)

    total = sum(len(v) for v in links.values())
    for label, ls in sorted(links.items(), key=lambda kv: -len(kv[1])):
        print(f"  {label}: {len(ls)}")
        for lnk in ls:
            print(f"    {lnk['lat']:.4f},{lnk['lng']:.4f}  {lnk['title'][:52]:<52} {lnk['url']}")
    print(f"{total} proposals across {len(links)} vote types "
          f"(the client shows the 3 nearest per card)")
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
