#!/usr/bin/env python3
"""
Fetch the latest NYC street-level change proposals.

Two sources (see docs/nyc-proposal-data-sources.md for the full writeup):

1. nycdotprojects.info  — DOT's "Projects & Initiatives" Drupal site: proposed
   street redesigns, community-board presentations, feedback maps. Freshest
   signal. Scraped by diffing sitemap.xml <lastmod> stamps against a local
   state file, then fetching only new/changed pages.
2. NYC Open Data (Socrata) — VZV Street Improvement Projects (SIPs): geocoded
   corridors/intersections of *implemented* safety projects. Queried via SODA
   against the underlying data views (the public map UIDs return empty rows).

Usage:
    python3 fetch_latest.py                 # everything changed since last run
    python3 fetch_latest.py --since 2026-07-01
    python3 fetch_latest.py --source sips   # only the Socrata datasets
    python3 fetch_latest.py --source dotprojects --limit 20

Output: JSON to stdout. State (last successful run) is kept in
tools/nyc_proposals/state.json next to this script. Stdlib only — no deps.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SITEMAP_URL = "https://nycdotprojects.info/sitemap.xml"
USER_AGENT = "city-edit-research/1.0 (contact: eric.didier.bolton@gmail.com)"
REQUEST_DELAY_S = 1.0  # be polite; this is a small city-run Drupal site

# Underlying Socrata data views (NOT the map asset UIDs — those return empty).
SODA_BASE = "https://data.cityofnewyork.us/resource"
SIPS_CORRIDOR_UID = "if4c-w48d"       # map asset: wqhs-q6wd
SIPS_INTERSECTION_UID = "shr7-eqdc"   # map asset: 79sh-heg3

STATE_PATH = Path(__file__).parent / "state.json"

# Path prefix → what kind of content the page is
CONTENT_TYPES = {
    "/project/": "project",
    "/project-updates/": "update",
    "/project-event/": "event",
    "/project-feedback-map/": "feedback-map",
    "/content/": "content",
}


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def classify(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    for prefix, kind in CONTENT_TYPES.items():
        if path.startswith(prefix):
            return kind
    return "project-home"  # bare slugs like /grand-concourse are project homepages


def project_state(html: str) -> str:
    """DOT's own status for a project page, or "" when the page doesn't say.

    Each page ships a Drupal analytics blob naming the project's state in its
    taxonomy — "Planning", "Data Collection & Feedback", "Past Project",
    "Complete", "Installation". It's the only machine-readable statement of
    whether a project is still a proposal or already built, which the prose
    only implies ("Construction crews wrapped up work"). Callers treat an
    unreadable state as unknown rather than active; see is_active_project.
    """
    m = re.search(r"window\.dataLayer\.push\((\{.*?\})\);", html, re.S)
    if not m:
        return ""
    try:
        taxonomy = (json.loads(m.group(1)).get("entityTaxonomy") or {})
    except (json.JSONDecodeError, AttributeError):
        return ""
    return " / ".join((taxonomy.get("state") or {}).values())


class PageMeta(HTMLParser):
    """Pull <title>, meta description, and PDF links out of a project page."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self.pdfs = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and a.get("name") == "description":
            self.description = a.get("content", "")
        elif tag == "a" and ".pdf" in (a.get("href") or "").lower():
            self.pdfs.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def fetch_dotprojects(since: str, limit: int, fetch_pages: bool) -> list[dict]:
    xml = http_get(SITEMAP_URL).decode("utf-8", errors="replace")
    entries = re.findall(r"<url><loc>(.*?)</loc>(?:<lastmod>(.*?)</lastmod>)?", xml)
    changed = sorted(
        (
            {"url": u, "lastmod": m, "type": classify(u)}
            for u, m in entries
            if m and m >= since
        ),
        key=lambda e: e["lastmod"],
        reverse=True,
    )[:limit]

    if fetch_pages:
        for entry in changed:
            time.sleep(REQUEST_DELAY_S)
            try:
                html = http_get(entry["url"]).decode("utf-8", errors="replace")
            except Exception as e:  # keep going; record the failure
                entry["error"] = str(e)
                continue
            meta = PageMeta()
            meta.feed(html)
            entry["title"] = re.sub(r"\s*\|\s*Projects.*$", "", meta.title.strip())
            entry["description"] = meta.description
            entry["pdfs"] = sorted(set(meta.pdfs))
            entry["state"] = project_state(html)
    return changed


def fetch_sips(since: str) -> dict:
    out = {}
    for label, uid in (
        ("corridors", SIPS_CORRIDOR_UID),
        ("intersections", SIPS_INTERSECTION_UID),
    ):
        query = f"$where=end_date >= '{since}'&$order=end_date DESC&$limit=1000"
        url = f"{SODA_BASE}/{uid}.json?{query.replace(' ', '%20')}"
        rows = json.loads(http_get(url))
        for row in rows:
            row.pop("the_geom", None)  # bulky; fetch .geojson if geometry needed
        out[label] = rows
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--since", help="ISO date; default = last run (or 30 days ago)")
    ap.add_argument("--source", choices=["all", "dotprojects", "sips"], default="all")
    ap.add_argument("--limit", type=int, default=50, help="max changed pages to report")
    ap.add_argument(
        "--no-pages",
        action="store_true",
        help="list changed URLs only, skip fetching each page for title/PDFs",
    )
    args = ap.parse_args()

    state = load_state()
    now = datetime.now(timezone.utc)
    since = args.since or state.get("last_run", "")[:10]
    if not since:
        since = datetime.fromtimestamp(now.timestamp() - 30 * 86400, timezone.utc
                                       ).strftime("%Y-%m-%d")

    result = {"since": since, "fetched_at": now.isoformat()}
    if args.source in ("all", "dotprojects"):
        result["dotprojects"] = fetch_dotprojects(
            since, args.limit, fetch_pages=not args.no_pages
        )
    if args.source in ("all", "sips"):
        result["sips"] = fetch_sips(since)

    json.dump(result, sys.stdout, indent=2)
    print()

    state["last_run"] = now.isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
