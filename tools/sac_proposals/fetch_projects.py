#!/usr/bin/env python3
"""
Fetch the City of Sacramento's official transportation projects.

Source: the Public Works "Transportation Projects" index
(cityofsacramento.gov/public-works/engineering/projects), an Adobe Experience
Manager site whose left-hand site-navigation lists every project page. Unlike
nycdotprojects.info (a Drupal firehose of projects mixed with blog posts and
outreach events — see tools/nyc_proposals/), this index is *curated*: every
entry is a real capital/safety project, so no junk filter is needed here.

Each project page gives us three useful fields:
  - the <h1> title,
  - the <meta name="description">, which on this site is almost always a plain
    statement of the project's limits ("This project is located on Broadway in
    Oak Park, between Martin Luther King Jr. Boulevard and Stockton Boulevard"),
  - the main body text, used to classify the project into a vote type.

Usage:
    python3 fetch_projects.py                    # every project on the index
    python3 fetch_projects.py -o projects.jsonl
    python3 fetch_projects.py --limit 5

Output: JSONL (one project per line) to stdout or --out. Stdlib only.
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://www.cityofsacramento.gov"
INDEX_URL = f"{BASE}/public-works/engineering/projects"
USER_AGENT = "city-edit-research/1.0 (contact: eric.didier.bolton@gmail.com)"
REQUEST_DELAY_S = 1.0  # be polite to a city-run site

# Project pages live one level under the index. `_jcr_content` is AEM's
# component path, not a page.
PROJECT_HREF_RE = re.compile(
    r"/public-works/engineering/projects/([A-Za-z0-9][A-Za-z0-9_-]*)")
NOT_A_PROJECT = {"_jcr_content"}

# Content markers in the AEM page. The site navigation (which repeats every
# project title on every page) sits *before* the <h1> component, and the global
# footer after the content, so slicing between the two isolates the body.
BODY_START = 'cmp-internal-title__container-title-text'
BODY_END = 'cmp-portal-footer'

TITLE_RE = re.compile(
    r'cmp-internal-title__container-title-text"[^>]*>(.*?)</h1>', re.S)
META_DESC_RE = re.compile(
    r'<meta name="description" content="(.*?)"', re.S)


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|svg).*?</\1>", " ", markup)
    text = re.sub(r"(?s)<[^>]+>", " ", markup)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def project_urls(index_html: str) -> list[str]:
    """Every distinct project page linked from the index (and its site nav)."""
    slugs = []
    for m in PROJECT_HREF_RE.finditer(index_html):
        slug = m.group(1)
        if slug in NOT_A_PROJECT or slug in slugs:
            continue
        slugs.append(slug)
    return [f"{INDEX_URL}/{s}" for s in sorted(slugs)]


def parse_project(url: str, page: str) -> dict:
    title_m = TITLE_RE.search(page)
    desc_m = META_DESC_RE.search(page)

    # Slice past the END of the <h1> tag, not the start of its class attribute —
    # otherwise the attribute's own text survives strip_tags into the body.
    start = page.find(BODY_START)
    if start >= 0:
        start = page.find(">", start) + 1
    body_markup = page[start:] if start > 0 else page
    end = body_markup.find(BODY_END)
    if end > 0:
        body_markup = body_markup[:end]
    body = strip_tags(body_markup)
    # The h1 text leads the sliced body; drop it so `body` is prose only.
    title = strip_tags(title_m.group(1)) if title_m else ""
    if title and body.startswith(title):
        body = body[len(title):].strip()
    # AEM renders Material icon ligatures as bare words ("open_in_full") that
    # would otherwise land in the classifier's input.
    body = re.sub(r"\b(open_in_full|arrow_\w+|expand_\w+|chevron_\w+)\b", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    return {
        "url": url,
        "title": title,
        "description": html.unescape(desc_m.group(1)).strip() if desc_m else "",
        "body": body,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("-o", "--out", help="output JSONL path (default: stdout)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N projects")
    args = ap.parse_args()

    index = http_get(INDEX_URL)
    urls = project_urls(index)
    if args.limit:
        urls = urls[:args.limit]
    print(f"[fetch] {len(urls)} project pages on the index", file=sys.stderr)

    out = open(args.out, "w") if args.out else sys.stdout
    try:
        for i, url in enumerate(urls):
            time.sleep(REQUEST_DELAY_S)
            try:
                proj = parse_project(url, http_get(url))
            except Exception as e:  # a single dead page must not kill the run
                proj = {"url": url, "error": str(e)}
            out.write(json.dumps(proj) + "\n")
            out.flush()
            print(f"[{i+1}/{len(urls)}] {proj.get('title') or proj.get('error')}",
                  file=sys.stderr)
    finally:
        if args.out:
            out.close()


if __name__ == "__main__":
    main()
