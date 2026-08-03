#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-07-29-slug-redirects-src-tracking.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-08-03-sacramento-city-and-proposals.html")

DATE = "2026-08-03"
TITLE = "Sacramento: a new city, and its official proposals on the map"


def split_by_file(diff_text: str):
    """Split a unified diff into (filename, hunk_text) chunks."""
    files = []
    current_name = None
    current_lines: list[str] = []
    for line in diff_text.splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m:
            if current_name is not None:
                files.append((current_name, "\n".join(current_lines)))
            current_name = m.group(2)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_name is not None:
        files.append((current_name, "\n".join(current_lines)))
    return files


def colorize(diff_chunk: str) -> str:
    out = []
    for raw in diff_chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith("diff ") or raw.startswith("index ") or raw.startswith("new file") or raw.startswith("similarity"):
            cls = "d-meta"
        elif raw.startswith("+"):
            cls = "d-add"
        elif raw.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        out.append(f'<span class="{cls}">{esc or "&nbsp;"}</span>')
    return "\n".join(out)


SECTIONS = [
    {
        "id": "city",
        "tag": "CITIES",
        "title": "Sacramento, registered end to end",
        "symptom": "Sacramento was not a City Edit city: not in the Propose-a-Map picker, not routable, no graph, no blocks.",
        "cause": [
            "A city is data-driven from <code>server/cities.py</code> plus a matching line in <code>osrm/build-merged.sh</code>; nothing else in the client or server hard-codes the list, and <code>GraphRegistry.max_loaded</code> already derives from <code>len(CITIES)</code>",
            "The votable graph and the OSRM routing dataset are built from the SAME PBF + foot filter, so both entries have to name the same extract and the same bbox (in the two different orderings the two files use)",
        ],
        "fixes": [
            "bbox is the city limits (Nominatim: 38.4377–38.6855 N, -121.5601–-121.3627 W) padded on each side. The padding is load-bearing twice over: osmnx's <code>truncate_by_edge</code> spills nodes just past the boundary and they must stay inside the votable area, and the west edge has to reach the Sacramento River crossings — the Tower and I Street bridges are the subject of active city projects",
            "Source extract is BBBike's pre-clipped Sacramento PBF (28 MB, fresh), with the Geofabrik California extract as the OSRM build's fallback",
            "Built artifacts: <strong>398K nodes / 901K edges</strong> (between Chicago and NYC in size), full-resolution graph.pmtiles, and a Layer-2 block bake of <strong>102,387 blocks with 0 overlapping pairs</strong>",
        ],
        "files": ["server/cities.py", "osrm/build-merged.sh"],
    },
    {
        "id": "source",
        "tag": "DATA SOURCE",
        "title": "A curated index instead of a sitemap firehose",
        "symptom": "The NYC Proposals map is fed by scraping nycdotprojects.info. Sacramento has no equivalent site, and its open-data portal carries no project/CIP dataset — only 311 calls, permits, crash data and boundaries.",
        "cause": [
            "The city's Public Works section publishes each project as its own page under <code>/public-works/engineering/projects/&lt;slug&gt;</code>, and every one of those pages carries the full index in its site navigation — so one fetch enumerates all 33",
            "Unlike the NYC source this index is CURATED: it is capital and safety projects, not projects mixed with blog posts and outreach events, so the two-sided junk filter the NYC importer needs has no job to do here",
            "Better still, each page's <code>&lt;meta name=\"description\"&gt;</code> states the project's limits in plain prose — &quot;located on Folsom Boulevard, between 47th Street and 67th Street&quot; — rather than requiring the limits to be parsed out of a title",
        ],
        "fixes": [
            "<code>fetch_projects.py</code> pulls the index, then each project page, extracting the &lt;h1&gt;, the meta description and the body prose (sliced between the title component and the global footer, which is what separates the content from the repeated nav)",
            "One non-street project on the index (broadband internet expansion) is dropped by a small blacklist",
            "Output is JSONL, stdlib only, 1 req/s — the same politeness contract as the NYC fetcher",
        ],
        "files": ["tools/sac_proposals/fetch_projects.py"],
    },
    {
        "id": "classify",
        "tag": "CORRECTNESS",
        "title": "Three ways the import wanted to put proposals in the wrong place",
        "symptom": "Early plan runs read plausibly and were wrong: half the corridors classified as school-crossing pins, Vision Zero corridors collapsed onto one vote type, and one pin landed 9km from its project.",
        "cause": [
            "<strong>Incidental nouns.</strong> Classifying on the whole page sent projects to whichever keyword appeared first in the prose. Every Vision Zero page mentions schools, bus stops and intersections in passing, so &quot;Folsom Boulevard Safety Improvements&quot; became a school crossing",
            "<strong>Unverifiable geocodes.</strong> Photon answers an intersection it does not recognise with its best single-street guess anywhere in the bbox. <code>Broadway &amp; Franklin Boulevard</code> came back as Franklin at Cosumnes River Boulevard, 9km south — inside the city, so neither the bbox check nor the distance guard could see anything wrong",
            "<strong>Limits read as a crossing.</strong> When a corridor could not be resolved, the fallback pin used the prose naming its endpoints: &quot;on Marysville Boulevard between Arcade Boulevard and North Avenue&quot; parsed as a crossing of Arcade and North — a corner nowhere near Marysville Boulevard",
            "A fourth, smaller one: the guard rejecting a capture trimmed to a bare suffix (&quot;Boulevard&quot;) also rejected <strong>Broadway</strong>, which is a complete street name — silently dropping the corridor for both Broadway projects",
        ],
        "fixes": [
            "<strong>Rules are scoped.</strong> A rule keyed on a noun (school, bus stop, bridge, interchange, intersection, plaza) may read only the title and description; a rule keyed on a treatment (bike lane, trail, sidewalk, repave) may fall through to the body. A title-scoped complete-street / Vision Zero rule sits after the treatment rules so a corridor safety project reads as traffic calming rather than as whichever bike lane its prose mentions",
            "<strong>A corridor endpoint must verify against its own street.</strong> The geocode result's display_name has to name the street the point claims to be on, matched on tokens long enough to discriminate (&quot;T Street&quot; and &quot;9th Street&quot; carry no usable key, so those go unverified rather than matching on a stray letter)",
            "<strong>The fallback pin uses the corridor's street</strong>, never the limits prose — so a corridor that fails to verify still pins somewhere true",
            "Endpoint captures are trimmed at their street-type word, and a period only ends a capture when it is not an abbreviation in front of one — without which &quot;Martin Luther King Jr. Boulevard&quot; ended at &quot;Jr&quot;",
            "With the street check carrying the load the distance ceiling could rise 8km → 10km, which is what let Stockton Boulevard's genuine ~8km corridor through",
        ],
        "files": ["tools/sac_proposals/import_to_map.py"],
    },
    {
        "id": "map",
        "tag": "THE MAP",
        "title": "Sacramento Proposals",
        "symptom": "—",
        "cause": [
            "Mirrors nyc-proposals: <code>symbol=safety</code>, <code>style=terracotta</code>, the same subtitle, and <code>top_proposal_min_net = 0</code> — without that override the client's default &gt;100-net floor hides every net-1 imported proposal",
            "A 14-entry vote-type list tuned to Sacramento's actual project mix (bikeways, complete streets, trails and parkways, river bridges, transit, school safety). Every label the classifier can emit is in the list, so icons and route/point kind resolve from the map's own types",
        ],
        "fixes": [
            "<strong>29 votes cast</strong> from 31 candidate projects: 12 corridors + 17 pins, across 10 vote types",
            "The two skips are deliberate. An 11-school safety program and a systemwide bus-stop consolidation have no single location, and a pin would assert one falsely",
            "Each project votes as its own synthetic voter (<code>sacproj:&lt;sha1(url)&gt;</code> with <code>ip_from_voter</code>), so re-running the import is idempotent — /api/vote is clear-then-cast per voter and type",
        ],
        "files": ["tools/sac_proposals/import_to_map.py"],
    },
]


VERIFY = [
    "Graph built and loaded: <strong>398,472 nodes / 901,030 edges</strong>, and /api/cities lists <code>sacramento</code> after the Flask restart.",
    "Blocks baked clean: <strong>102,387 blocks, 0 overlapping pairs</strong> in the final ship-frame audit (CC/CJ/JJ all zero).",
    "Local OSRM rebuilt and re-verified BOTH ways: a Broadway MLK→Stockton foot route returns <strong>1,097 m</strong> (matching the ~1 km crow distance), and an existing NYC route still returns 702 m — the merged dataset gained a city without losing one.",
    "Every classification and corridor parse was reviewed offline against all 33 pages before a single vote was cast, and the three failure modes above were each found that way rather than after the fact.",
    "The wrong-street geocode was confirmed by hand, not inferred: <code>Broadway &amp; Franklin Boulevard</code> resolves to &quot;Franklin, Cosumnes River Boulevard, Valley Hi / North Laguna&quot;. The verification rejects it and the two corridors it legitimately cannot confirm (Marysville, La Mancha Way) now pin on their own street.",
    "Cast result on local: <strong>29 ok, 0 route-failed, 0 detour demotions</strong>, giving 1,057 voted edges over 8 vote types and 601 blocks with heat.",
    "Rendered and driven in the browser: topology 891,764 edges / 102,388 blocks loaded, <strong>11 route proposals</strong> computed client-side, and clicking a pin opens a TOP PROPOSAL card (&quot;Street redesign — 1 proposal&quot;) with working ±1 controls.",
    "Deploy discipline: prod DB backed up BEFORE any build (<code>~/city-edit-prod-backups/20260803T180348Z/votes.dump</code>, 28 MB), client type-checked with <code>npx tsc -b</code> (clean) since Vite dev never type-checks, and the overlay submitted from a tree whose only diff from HEAD is this work.",
    "Sacramento is added to the app image via the <strong>arrays overlay</strong>, not a full rebuild — the other cities keep the image's own baked graphs, so their edge ids do not shift and no vote resnap or block re-bake is needed. Only <code>sacramento</code> was staged; philly / test-cp / test-mid were moved OUT of <code>.arrays-staging/</code> first, because their stale local pickles predate the 07-29 full rebuild and copying them in would have shifted philly's edge ids.",
]

CHECKLIST = [
    "Open <code>https://cityedit.org/m/sac-proposals</code> — expect the Sacramento basemap with terracotta corridors along Broadway, Franklin, Stockton and Marysville, plus proposal pins.",
    "Click one of the corridor lines and confirm the proposal card names a real vote type (e.g. <strong>Add traffic calming</strong> on Broadway) rather than &quot;No votes yet&quot;.",
    "Open Propose-a-Map on any map and confirm <strong>Sacramento</strong> now appears in the city dropdown.",
    "Spot-check that an existing city still routes: draw a route on <code>nyc-walkways</code> — the merged OSRM dataset was rebuilt, so this is the regression that matters most.",
    "<code>curl -s https://cityedit.org/api/maps | grep sac-proposals</code> and <code>curl -s 'https://cityedit.org/api/graph-votes?map=sac-proposals&amp;mode=walk'</code> — remember the SWR trap: the first hit after a bulk cast serves the stale snapshot, so poll until the count settles before concluding votes are missing.",
    "Compare a couple of pins against the source pages at <code>cityofsacramento.gov/public-works/engineering/projects</code> — the plan JSONL records the exact geocode query used for each one.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Symptom</h3>
      <p>{s['symptom']}</p>
      <h3>Root cause</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


# ── Hierarchical "where does this block sit" context ──────────────────────────
# For every diff we render a recursively-summarized map: the SYSTEM (which of the
# app's components this file belongs to) → the MODULE (subsystem + one-line
# summary) → the FILE (summary + an outline of its top-level sections) → the
# changed BLOCKS. The file outline is a focus+context minimap: changed sections
# are highlighted, the rest are dimmed, so you see where the diff lands among its
# peers at every zoom level.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

# label, summary, changed?
FILE_CONTEXT = {
    "server/cities.py": {
        "on": ["Flask API", "OSRM", "React / Leaflet client"],
        "module": ("Server \u00b7 city registry", "The curated list of supported cities: bbox, default view, OSRM endpoint, where each city's graph/tiles/blocks live on disk"),
        "file": ("cities.py", "~260 LOC \u2014 blocks-header cache, the City dataclass, the CITIES registry, lookups"),
        "outline": [
            ("_blocks_header_info / _env_osrm_host", "unchanged \u2014 pmtiles header cache + per-city OSRM host override", False),
            ("@dataclass City", "unchanged \u2014 data_dir, osrm_host, geocode_bbox, to_public()", False),
            ("CITIES: nyc / sf / chicago / dc / philly", "unchanged", False),
            ("CITIES: sacramento (new)", "+17 lines \u2014 bbox, center, zooms, PBF source, OSRM service", True),
            ("CITIES: test-cp / test-mid", "unchanged \u2014 block-pipeline playground areas", False),
            ("get_city / all_cities", "unchanged", False),
        ],
        "blocks": [
            "bbox=(38.430, -121.580, 38.690, -121.355) \u2014 city limits padded: osmnx truncate_by_edge spills past the boundary, and the west edge must reach the Sacramento River bridge projects",
            "center = the bbox midpoint, so the votable area opens centered (same convention as sf/chicago/dc/philly)",
            "default_zoom=12, min_zoom=10 \u2014 Sacramento sprawls wider than SF, so it opens one step out",
            "pbf_url = BBBike's pre-clipped Sacramento extract (28 MB) \u2014 same PBF the OSRM build clips from",
            "osrm_service='osrm-sacramento' \u2014 the per-city default host; prod routes every city through the one merged service",
            "No max_loaded bump needed: app.py derives it from len(CITIES) + len(STATION_NETWORKS)",
        ],
    },
    "osrm/build-merged.sh": {
        "on": ["OSRM"],
        "module": ("OSRM \u00b7 merged dataset build", "Clips each city's PBF to its bbox and osmium-merges them into ONE routable extract; osrm-extract/partition/customize runs in the next Docker stage"),
        "file": ("build-merged.sh", "~90 lines \u2014 the CITIES array, a retrying downloader, the clip+merge loop"),
        "outline": [
            ("header / WORK+OUT dirs", "unchanged", False),
            ("CITIES array", "+1 line \u2014 the sacramento entry", True),
            ("download_pbf", "unchanged \u2014 retries across primary then fallback URL", False),
            ("clip loop / osmium merge", "unchanged \u2014 picks up the new entry automatically", False),
        ],
        "blocks": [
            "sacramento|bbbike Sacramento PBF|geofabrik california (fallback)|-121.580,38.430,-121.355,38.690",
            "NOTE the bbox ordering differs from cities.py: osmium wants west,south,east,north; cities.py is south,west,north,east",
            "The fallback URL matters here \u2014 a single flaky bbbike download under `set -e` has killed ~2h builds before",
            "Adding a city here is what makes it routable at all: one merged instance serves every city, since OSRM coordinates are global",
        ],
    },
    "tools/sac_proposals/fetch_projects.py": {
        "on": ["Flask API"],
        "module": ("Tools \u00b7 sac_proposals", "Sacramento twin of tools/nyc_proposals: scrape the city's official project pages, then plan and cast them as votes"),
        "file": ("fetch_projects.py", "~145 LOC \u2014 index enumeration, per-page extraction, JSONL out"),
        "outline": [
            ("constants / content markers", "new \u2014 index URL, politeness delay, the AEM body markers", True),
            ("http_get / strip_tags", "new \u2014 stdlib fetch + tag stripping", True),
            ("project_urls", "new \u2014 enumerates all 33 project slugs from one page", True),
            ("parse_project", "new \u2014 h1, meta description, body prose", True),
            ("main", "new \u2014 CLI, JSONL output, per-page error containment", True),
        ],
        "blocks": [
            "The site nav repeats every project title on every page, so ONE fetch of the index enumerates the whole set",
            "BODY_START/BODY_END slice between the title component and the global footer \u2014 what separates content from the repeated nav",
            "Slicing past the END of the <h1> tag, not the start of its class attribute, or the attribute text survives into the body",
            "AEM renders Material icon ligatures as bare words ('open_in_full') \u2014 stripped so they never reach the classifier",
            "A single dead page is captured as {'error': ...} rather than killing a 33-page run",
        ],
    },
    "tools/sac_proposals/import_to_map.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Tools \u00b7 sac_proposals", "Two-phase plan\u2192cast import over the public HTTP API, so the server does all snapping and one plan casts identically against local and prod"),
        "file": ("import_to_map.py", "~470 LOC \u2014 vocabulary, classification, corridor parsing, geocode verification, cast"),
        "outline": [
            ("guards (MAX_CORRIDOR_KM / EDGES / DETOUR_RATIO_MAX)", "8km \u2192 10km, edges + detour inherited from the NYC importer", True),
            ("VOTE_TYPES", "new \u2014 the map's 14-entry vocabulary", True),
            ("CLASSIFY_RULES + SCOPE_TITLE/SCOPE_ANY", "new \u2014 scoped rules, first match wins", True),
            ("corridor regexes (CORRIDOR_ON / NAMED / LIMITS)", "new \u2014 abbreviation-safe terminator", True),
            ("geocode / street_keys", "new \u2014 in-bbox + verified-against-street", True),
            ("classify", "new \u2014 two passes, different rule scopes", True),
            ("street_name / clean_endpoint / street_variants", "new \u2014 capture trimming", True),
            ("corridor_candidates", "new \u2014 best-evidence-first guesses", True),
            ("plan_project / point_queries / is_placeable", "new \u2014 corridor then pin, with fallbacks", True),
            ("cast_entry / voter_id", "new \u2014 routes \u2192 /api/vote, idempotent per project", True),
            ("main", "new \u2014 plan / cast / vote-types phases", True),
        ],
        "blocks": [
            "SCOPE_TITLE vs SCOPE_ANY \u2014 noun rules (school, bus stop, bridge) read only title+description; treatment rules (bike lane, trail, repave) may read the body",
            "The complete-street / Vision Zero rule is title-scoped and sits AFTER the treatment rules, so corridor safety projects read as traffic calming instead of collapsing onto 'Add protected bike lane'",
            "geocode(expect=street): the hit's display_name must name the street the point claims to be on \u2014 Photon answers an unknown intersection with a single-street guess anywhere in the bbox",
            "street_keys drops suffixes and tokens under 4 chars, so 'T Street' goes unverified rather than matching a stray letter",
            "SUFFIX_ONLY excludes Broadway/Trail/Expressway \u2014 rejecting a bare 'Broadway' had silently dropped both Broadway corridors",
            "END terminator: a period only ends an endpoint capture when it is NOT an abbreviation before a street word ('Martin Luther King Jr. Boulevard')",
            "point_queries(prefer_street) yields the corridor's own street FIRST \u2014 limits prose ('between Arcade and North') otherwise reads as a crossing far from the project",
            "is_placeable rejects bare suffixes, mid-sentence fragments and single generic nouns ('School') \u2014 a wrong pin is worse than no pin",
            "DETOUR_RATIO_MAX demotion and the sacproj: synthetic voter are carried over unchanged from the NYC importer",
        ],
    },
}


def context_html(path: str) -> str:
    ctx = FILE_CONTEXT.get(path)
    if not ctx:
        return ""
    pills = "".join(
        f'<span class="pill {"on" if c in ctx["on"] else ""}">{html.escape(c)}</span>'
        for c in SYSTEM_COMPONENTS
    )
    mod_label, mod_sum = ctx["module"]
    file_label, file_sum = ctx["file"]
    outline = "".join(
        f'<li class="{"changed" if changed else "dim"}">'
        f'<span class="ol-label">{html.escape(label)}</span>'
        f'<span class="ol-sum">{html.escape(summary)}</span>'
        f'{"<span class=\"ol-tag\">changed</span>" if changed else ""}</li>'
        for (label, summary, changed) in ctx["outline"]
    )
    blocks = "".join(f"<li>{html.escape(b)}</li>" for b in ctx["blocks"])
    return f"""
    <div class="ctx">
      <div class="ctx-tier ctx-sys">
        <span class="ctx-k">System</span>
        <span class="ctx-v">{SYSTEM_NAME}</span>
        <span class="pills">{pills}</span>
      </div>
      <div class="ctx-tier ctx-mod">
        <span class="ctx-k">Module</span>
        <span class="ctx-v">{html.escape(mod_label)}</span>
        <span class="ctx-sum">{html.escape(mod_sum)}</span>
      </div>
      <div class="ctx-tier ctx-file">
        <span class="ctx-k">File</span>
        <span class="ctx-v">{html.escape(file_label)}</span>
        <span class="ctx-sum">{html.escape(file_sum)}</span>
      </div>
      <div class="ctx-map">
        <div class="ctx-map-title">File map — where this diff sits <span class="legend"><span class="sw-ch"></span>changed&nbsp;&nbsp;<span class="sw-dim"></span>context</span></div>
        <ul class="ctx-outline">{outline}</ul>
      </div>
      <div class="ctx-map">
        <div class="ctx-map-title">Changed blocks (top → bottom)</div>
        <ol class="ctx-blocks">{blocks}</ol>
      </div>
    </div>
    """


def main():
    with open(DIFF_PATH) as f:
        diff_text = f.read()
    files = split_by_file(diff_text)

    diff_blocks = []
    for name, chunk in files:
        added = sum(1 for ln in chunk.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in chunk.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        diff_blocks.append(f"""
        <details>
          <summary><span class="fname">{html.escape(name)}</span>
            <span class="stat"><span class="d-add">+{added}</span> / <span class="d-del">-{removed}</span></span>
          </summary>
          {context_html(name)}
          <pre class="diff">{colorize(chunk)}</pre>
        </details>
        """)

    sections_html = "\n".join(section_html(s) for s in SECTIONS)
    nav = "\n".join(f'<a href="#{s["id"]}">{s["title"].split("·")[0].strip()}</a>' for s in SECTIONS)

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE} — {DATE}</title>
<style>
  :root {{
    --paper: #fbfbf8; --ink: #15140f; --muted: #6b6760; --hairline: rgba(0,0,0,0.10);
    --accent: #b4541e; --add-bg: #e6ffec; --add-fg: #06402b; --del-bg: #ffeef0; --del-fg: #82071e;
    --code-bg: #f1efe9; --font-ui: "Source Sans 3", system-ui, -apple-system, sans-serif;
    --font-ed: "Source Serif 4", Georgia, serif; --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-ui); line-height: 1.6; }}
  .wrap {{ max-width: 920px; margin: 0 auto; padding: 48px 24px 96px; }}
  header.masthead {{ border-bottom: 2px solid var(--ink); padding-bottom: 20px; margin-bottom: 8px; }}
  .kicker {{ font-size: 13px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--accent); font-weight: 600; }}
  h1 {{ font-family: var(--font-ed); font-size: 34px; line-height: 1.15; margin: 8px 0 6px; }}
  .dateline {{ color: var(--muted); font-size: 14px; }}
  nav.toc {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0 8px; }}
  nav.toc a {{ font-size: 13px; text-decoration: none; color: var(--ink); border: 1px solid var(--hairline);
    border-radius: 999px; padding: 5px 12px; background: #fff; }}
  nav.toc a:hover {{ border-color: var(--accent); color: var(--accent); }}
  .lede {{ font-family: var(--font-ed); font-size: 18px; color: #2c2a24; margin: 20px 0 28px; }}
  .card {{ background: #fff; border: 1px solid var(--hairline); border-radius: 14px; padding: 24px 26px;
    margin: 20px 0; box-shadow: 0 1px 0 rgba(0,0,0,0.02); }}
  .tag {{ display: inline-block; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); border: 1px solid var(--hairline); border-radius: 6px; padding: 2px 8px; margin-bottom: 10px; }}
  h2 {{ font-family: var(--font-ed); font-size: 24px; margin: 4px 0 14px; }}
  h3 {{ font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent);
    margin: 20px 0 6px; }}
  ul {{ margin: 6px 0 0; padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  ul.files li {{ font-family: var(--font-mono); font-size: 12.5px; color: #33312b; }}
  code {{ font-family: var(--font-mono); font-size: 0.88em; background: var(--code-bg);
    padding: 1px 5px; border-radius: 4px; }}
  p {{ margin: 6px 0; }}
  .twocol {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 680px) {{ .twocol {{ grid-template-columns: 1fr; }} h1 {{ font-size: 27px; }} }}
  details {{ border: 1px solid var(--hairline); border-radius: 10px; margin: 10px 0; background: #fff; overflow: hidden; }}
  summary {{ cursor: pointer; padding: 10px 14px; font-family: var(--font-mono); font-size: 13px;
    display: flex; justify-content: space-between; align-items: center; gap: 12px; user-select: none; }}
  summary:hover {{ background: #faf8f3; }}
  .fname {{ color: var(--ink); }} .stat {{ font-size: 12px; color: var(--muted); }}
  pre.diff {{ margin: 0; padding: 14px 16px; overflow-x: auto; background: #fcfbf7;
    border-top: 1px solid var(--hairline); font-family: var(--font-mono); font-size: 12px; line-height: 1.5; }}
  pre.diff span {{ display: block; white-space: pre; }}

  /* Hierarchical context map (System ▸ Module ▸ File ▸ blocks) */
  .ctx {{ padding: 14px 16px; background: #f7f5ef; border-top: 1px solid var(--hairline); }}
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
  .ctx-tier {{ position: relative; }}
  .ctx-mod::before, .ctx-file::before {{ content: "└"; position: absolute; left: 4px; color: #bdb8ac; }}
  .ctx-file::before {{ left: 22px; }}
  .ctx-k {{ font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #fff;
    background: var(--accent); border-radius: 4px; padding: 2px 6px; }}
  .ctx-mod .ctx-k, .ctx-file .ctx-k {{ background: #8a857a; }}
  .ctx-v {{ font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--ink); }}
  .ctx-sum {{ font-size: 12.5px; color: var(--muted); }}
  .pills {{ display: inline-flex; flex-wrap: wrap; gap: 5px; }}
  .pill {{ font-size: 11px; color: #8a857a; background: #fff; border: 1px solid var(--hairline);
    border-radius: 999px; padding: 1px 9px; }}
  .pill.on {{ color: #fff; background: var(--accent); border-color: var(--accent); font-weight: 600; }}
  .ctx-map {{ margin-top: 12px; padding-left: 36px; }}
  .ctx-map-title {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
    margin-bottom: 6px; display: flex; gap: 12px; align-items: center; }}
  .legend {{ font-size: 11px; text-transform: none; letter-spacing: 0; color: var(--muted); display: inline-flex; align-items: center; }}
  .sw-ch, .sw-dim {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .sw-ch {{ background: var(--accent); }} .sw-dim {{ background: #d6d1c5; }}
  ul.ctx-outline {{ list-style: none; margin: 0; padding: 0; border-left: 2px solid #e3ded2; }}
  ul.ctx-outline li {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px;
    margin: 0; padding: 4px 0 4px 12px; position: relative; }}
  ul.ctx-outline li.changed {{ border-left: 3px solid var(--accent); margin-left: -2px; padding-left: 11px; background: #fff6ef; }}
  ul.ctx-outline li.dim {{ opacity: 0.62; }}
  .ol-label {{ font-family: var(--font-mono); font-size: 12.5px; color: var(--ink); }}
  li.changed .ol-label {{ font-weight: 700; color: var(--accent); }}
  .ol-sum {{ font-size: 12px; color: var(--muted); }}
  .ol-tag {{ font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: #fff;
    background: var(--accent); border-radius: 4px; padding: 1px 6px; }}
  ol.ctx-blocks {{ margin: 0; padding-left: 20px; }}
  ol.ctx-blocks li {{ font-family: var(--font-mono); font-size: 12px; color: #33312b; margin: 4px 0; }}

  .d-add {{ background: var(--add-bg); color: var(--add-fg); }}
  .d-del {{ background: var(--del-bg); color: var(--del-fg); }}
  .d-hunk {{ color: #5a3ec8; }} .d-meta {{ color: var(--muted); }} .d-ctx {{ color: #33312b; }}
  .checklist li, .verify li {{ margin: 8px 0; }}
  footer {{ margin-top: 40px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--hairline); padding-top: 16px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="kicker">City Edit · Change log</div>
    <h1>{TITLE}</h1>
    <div class="dateline">{DATE} · branch <code>main</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Three fixes to Top Proposals: a pin now takes real support (&gt;100 net votes, both point and route families); every modal badges the vote types that are CURRENT top proposals for what it shows (square = point, diamond = route fully inside the selection); and route corridors are now grown ROUTING-CONSISTENTLY — an extension either keeps the segment a shortest path or pins a ghost waypoint (max 3), and the whole waypoint chain lands in the URL, so a shared top-proposal link keeps routing into its corridor long after the proposal itself has churned away.</p>

  {sections_html}

  <section class="card verify" id="verify">
    <div class="tag">Verification</div>
    <h2>What was run</h2>
    <ul>{li(VERIFY)}</ul>
  </section>

  <section class="card checklist" id="checklist">
    <div class="tag">For you</div>
    <h2>Manual checklist</h2>
    <ul>{li(CHECKLIST)}</ul>
  </section>

  <section id="diff">
    <h2 style="font-family:var(--font-ed);font-size:24px;margin:32px 0 10px;">Full diff</h2>
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_report.py</code>.
  </footer>
</div>
</body>
</html>
"""
    with open(OUT_PATH, "w") as f:
        f.write(doc)
    print(f"Wrote {OUT_PATH} ({len(files)} files in diff)")


if __name__ == "__main__":
    main()
