#!/usr/bin/env python3
"""
Weekly NYC DOT proposals → City Edit import (Cloud Run job entrypoint).

Fetches nycdotprojects.info project pages changed in the last WINDOW_DAYS
(overlapping window — casts are idempotent, so re-processing a page is safe:
voter_id derives from the project URL and /api/vote is clear-then-cast per
voter+type), plans them (geocode + classify through the app's own API), and
casts one vote per new/changed proposal on the target map.

Config via env:
    BASE_URL   app to geocode/route/cast against (default https://cityedit.org)
    MAP_SLUG   target map                        (default nyc-proposals)
    WINDOW_DAYS  sitemap lastmod lookback        (default 8)

Run locally:  BASE_URL=http://localhost:5001 python3 weekly_import.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_latest import fetch_dotprojects
from import_to_map import cast_entry, plan_project

BASE_URL = os.environ.get("BASE_URL", "https://cityedit.org")
MAP_SLUG = os.environ.get("MAP_SLUG", "nyc-proposals")
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "8"))


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
        print(f"[WEEKLY] {status:>20}  {entry['title'][:60]}"
              f"  → {entry['vote_type']}", flush=True)

    print(f"[WEEKLY] DONE {stats}", flush=True)
    # Only infra failures should flip the job red; geocode misses are normal.
    errors = sum(v for k, v in stats.items() if k.endswith(":error"))
    return 1 if errors and errors == sum(
        v for k, v in stats.items() if ":" in k) else 0


if __name__ == "__main__":
    sys.exit(main())
