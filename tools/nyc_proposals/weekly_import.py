#!/usr/bin/env python3
"""
Weekly NYC DOT proposals → City Edit import (Cloud Run job entrypoint).

Fetches nycdotprojects.info project pages changed in the last WINDOW_DAYS
(overlapping window — casts are idempotent, so re-processing a page is safe:
voter_id derives from the project URL and /api/vote is clear-then-cast per
voter+type), plans them (geocode + classify through the app's own API), and
casts one vote per new/changed proposal on the target map.

Every proposal it casts is also registered in the map's source registry (see
set_vote_sources.py), so the citation links on its votes appear alongside the
votes themselves rather than only for whatever bulk import ran last. The
registration merges, since this job only knows the week's proposals.

Config via env:
    BASE_URL   app to geocode/route/cast against (default https://cityedit.org)
    MAP_SLUG   target map                        (default nyc-proposals)
    WINDOW_DAYS  sitemap lastmod lookback        (default 8)
    ADMIN_TOKEN  gates the source registration; without it the job still casts
                 and warns, rather than dropping the week's votes

Run locally:  BASE_URL=http://localhost:5001 python3 weekly_import.py
"""
import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_latest import fetch_dotprojects
from import_to_map import cast_entry, plan_project, voter_id

BASE_URL = os.environ.get("BASE_URL", "https://cityedit.org")
MAP_SLUG = os.environ.get("MAP_SLUG", "nyc-proposals")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "8"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def register_sources(sources: list[dict]) -> None:
    """Cite this run's proposals, merging them into the map's registry.

    Best-effort: a failure here costs citation links on the week's new votes,
    which isn't worth failing an otherwise-successful import over.
    """
    if not sources:
        return
    if not ADMIN_TOKEN:
        print(f"[WEEKLY] WARNING no ADMIN_TOKEN — {len(sources)} proposals cast "
              f"without source citations", flush=True)
        return
    req = urllib.request.Request(
        f"{BASE_URL}/api/admin/maps/{MAP_SLUG}/vote-sources",
        data=json.dumps({"sources": sources, "merge": True}).encode(),
        headers={"Content-Type": "application/json",
                 "X-Admin-Token": ADMIN_TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[WEEKLY] sources {resp.status} {resp.read().decode()}",
                  flush=True)
    except Exception as e:
        print(f"[WEEKLY] WARNING source registration failed: {e}", flush=True)


def main() -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
             ).strftime("%Y-%m-%d")
    print(f"[WEEKLY] fetching projects changed since {since}", flush=True)
    changed = fetch_dotprojects(since, limit=500, fetch_pages=True)
    projects = [p for p in changed if p["type"] in ("project", "project-home")]
    print(f"[WEEKLY] {len(changed)} changed pages, {len(projects)} projects",
          flush=True)

    stats: dict[str, int] = {}
    seen: set[str] = set()
    sources: list[dict] = []
    for proj in projects:
        title = (proj.get("title") or "").strip().lower()
        if not title or title in seen:
            continue
        seen.add(title)
        entry = plan_project(BASE_URL, MAP_SLUG, proj)
        if not entry:
            stats["filtered"] = stats.get("filtered", 0) + 1
            continue
        result = cast_entry(BASE_URL, MAP_SLUG, entry)
        status = f"{entry['cast']}:{result['status']}"
        stats[status] = stats.get(status, 0) + 1
        # Only casts that landed have rows to attribute (same rule the bulk
        # set_vote_sources.py applies to a plan file).
        if result["status"].startswith("ok"):
            sources.append({"voter_id": voter_id(entry["url"]),
                            "url": entry["url"], "title": entry["title"]})
        print(f"[WEEKLY] {status:>20}  {entry['title'][:60]}"
              f"  → {entry['vote_type']}", flush=True)

    register_sources(sources)
    print(f"[WEEKLY] DONE {stats}", flush=True)
    # Only infra failures should flip the job red; geocode misses are normal.
    errors = sum(v for k, v in stats.items() if k.endswith(":error"))
    return 1 if errors and errors == sum(
        v for k, v in stats.items() if ":" in k) else 0


if __name__ == "__main__":
    sys.exit(main())
