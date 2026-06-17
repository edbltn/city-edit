#!/usr/bin/env python3
"""Generate the DC + Philly expansion-groundwork changelog report.

Run from repo root: python changelog/build_dc_philly_report.py
Reads changelog/changes.diff, writes changelog/2026-06-17-dc-philly-groundwork.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-06-17-dc-philly-groundwork.html")

DATE = "2026-06-17"
TITLE = "Expansion groundwork — Philadelphia (and finishing D.C.)"


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
        "id": "registry",
        "tag": "Backend · config",
        "title": "1 · Register Philadelphia as a city",
        "symptom": (
            "We want to launch in D.C. and Philadelphia. D.C. was already wired into the city "
            "registry; Philadelphia was entirely absent, so it couldn’t be selected when proposing "
            "a map, OSRM didn’t route it, and no graph existed for it."
        ),
        "cause": [
            "A <em>city</em> in City Edit is data-driven from <code>server/cities.py</code> "
            "(bbox + center + zooms + the source PBF + the OSRM service). The client city dropdown "
            "auto-populates from <code>/api/cities</code>, so adding a city is purely a backend/data step "
            "— no client changes.",
            "Two collaborating definitions must agree on the bbox: <code>cities.py</code> "
            "(<code>south,west,north,east</code>) drives the votable walk-graph build and the map view, "
            "while <code>osrm/build-merged.sh</code> (<code>west,south,east,north</code> for osmium) clips "
            "the city into the single merged OSRM extract that serves every city from one instance.",
        ],
        "fixes": [
            "<strong>Added Philadelphia to <code>CITIES</code></strong> in <code>server/cities.py</code> — "
            "bbox <code>(39.867, -75.280, 40.138, -74.956)</code> enclosing the elongated NE–SW city "
            "limits (Navy Yard up to Somerton), center on the bbox midpoint so the map opens centered, "
            "zooms matching the other mid-size cities, the bbbike Philadelphia PBF, and "
            "<code>osrm_service=\"osrm-philly\"</code>.",
            "<strong>Added the matching <code>philly</code> line to <code>osrm/build-merged.sh</code></strong> "
            "(osmium <code>west,south,east,north</code> order) with the bbbike primary + geofabrik "
            "<code>pennsylvania-latest</code> fallback URL, so the next merged-OSRM build routes Philadelphia.",
            "<strong>Built the Philadelphia walk graph</strong> locally with "
            "<code>refresh_osm.py --city philly --force</code> → <code>osm_data/philly/walk_graph.pkl</code> "
            "(504,998 nodes / 1,177,436 edges, 107&nbsp;MB — between SF and Chicago in size).",
            "<strong>Derived <code>GraphRegistry(max_loaded=…)</code> from <code>len(CITIES)</code></strong> "
            "instead of the hard-coded <code>4</code>, so the resident-graph budget tracks the registry "
            "automatically and never silently under-provisions when the next city is added.",
        ],
        "files": [
            "server/cities.py — add the philly City to CITIES",
            "osrm/build-merged.sh — add the philly clip line (+ refresh the stale city-list comment)",
            "server/app.py — max_loaded = len(CITIES) + len(STATION_NETWORKS) (was 4 + …)",
        ],
    },
    {
        "id": "presets",
        "tag": "Backend · seed data",
        "title": "2 · Make D.C. and Philly reachable out-of-the-box",
        "symptom": (
            "Registering a city only makes it <em>selectable</em> in Propose-a-Map. A city has no "
            "actual maps until a <code>maps</code> row exists, so D.C. and Philly had nowhere to land — "
            "you couldn’t open <code>/m/&lt;slug&gt;</code> for either."
        ),
        "cause": [
            "Maps are DB rows. <code>seed_presets()</code> runs at startup (idempotent upsert) and seeds "
            "the canonical preset vote-type lists plus the preset maps from "
            "<code>server/presets.py</code> <code>PRESET_MAPS</code> — which until now held only the three "
            "NYC maps (Bikes / Trees / Walkways).",
        ],
        "fixes": [
            "<strong>Added six preset maps</strong> to <code>PRESET_MAPS</code> — "
            "<code>dc-bikes / dc-trees / dc-walkways</code> and "
            "<code>philly-bikes / philly-trees / philly-walkways</code> — reusing the existing "
            "<code>bikes / trees / walkways</code> vote-type lists, for parity with NYC.",
            "<strong>Launched subdomain-less</strong> (<code>subdomain: None</code>): the NYC presets keep "
            "their vanity <code>bikepaths./trees./walkways.</code> hosts, but the new cities are reached at "
            "<code>/m/&lt;slug&gt;</code> until DNS/TLS is added — the <code>subdomain</code> column is "
            "nullable and the seed upsert handles <code>None</code> cleanly.",
            "<strong>Refreshed the stale “Preset NYC maps” comment</strong> to describe the general case.",
        ],
        "files": [
            "server/presets.py — six dc-*/philly-* PRESET_MAPS entries (+ comment)",
        ],
    },
]

VERIFY = [
    "<code>python -c 'import cities, presets'</code> — both import; <code>CITIES</code> is "
    "<code>['nyc','sf','chicago','dc','philly']</code>; every <code>PRESET_MAPS</code> entry’s "
    "<code>city_id</code> and <code>list_key</code> resolves.",
    "<code>bash -n osrm/build-merged.sh</code> — syntax clean.",
    "<code>refresh_osm.py --city philly --force</code> — built "
    "<code>osm_data/philly/walk_graph.pkl</code> (504,998 nodes / 1,177,436 edges).",
    "Booted host Flask and hit <code>GET /api/cities</code> — returns all five cities including "
    "<code>dc</code> and <code>philly</code> with correct bounds/center/tilesPath.",
]

CHECKLIST = [
    "Bring up local Postgres (Docker daemon was down this session: <code>docker compose up -d postgres</code>), "
    "restart Flask, and confirm the boot log shows <code>seed_presets</code> succeed (no “Failed to seed presets”).",
    "Open <code>http://localhost:3000/m/philly-walkways</code> and <code>/m/dc-walkways</code> — the map should "
    "center on the right city and the votable street network should render.",
    "Curl <code>localhost:5001/api/graph-votes?map=philly-walkways&amp;mode=walkways</code> and confirm it "
    "returns vote arrays sized to the Philly topology (no length mismatch).",
    "Cast a vote on a Philly street and confirm it persists + paints (exercises OSRM node-id → edge mapping "
    "for the new city).",
    "For prod: run <code>gcloud builds submit</code> so the merged OSRM image picks up Philadelphia, then "
    "let the boot warmup build the <code>philly</code> graph on-demand (see the resnap-on-deploy runbook).",
    "Decide whether D.C./Philly want vanity subdomains; if so, add DNS/TLS and set the <code>subdomain</code> "
    "via the admin endpoint.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Context</h3>
      <p>{s['symptom']}</p>
      <h3>How it works</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


# ── Hierarchical "where does this block sit" context ──────────────────────────
SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/cities.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/", "the curated city registry: bbox, default view, OSRM endpoint, on-disk graph/tiles paths"),
        "file": ("cities.py", "~150 LOC — the City dataclass + the CITIES registry every map references by id"),
        "outline": [
            ("Per-city OSRM host helper", "_env_osrm_host — OSRM_HOST_<CITY> override", False),
            ("City dataclass", "bbox/center/zooms + data_dir, osrm_host, geocode_bbox, to_public", False),
            ("CITIES registry — nyc / sf / chicago / dc", "the existing four cities", False),
            ("CITIES registry — philly", "NEW — Philadelphia bbox + center + bbbike PBF + osrm-philly", True),
            ("Lookups", "get_city, all_cities", False),
        ],
        "blocks": [
            "philly City entry — bbox (39.867,-75.280,40.138,-74.956), center (40.003,-75.118), bbbike Philadelphia PBF, osrm-philly",
        ],
    },
    "osrm/build-merged.sh": {
        "on": ["OSRM"],
        "module": ("OSRM build · osrm/", "produces the single merged .osm.pbf that one OSRM instance uses to route every city"),
        "file": ("build-merged.sh", "~73 LOC — download → clip each city to its bbox → osmium-merge into combined.osm.pbf"),
        "outline": [
            ("Header / rationale comment", "why one merged extract serves all cities", True),
            ("CITIES array — nyc / sf / chicago / dc", "name|primary|fallback|bbox lines", False),
            ("CITIES array — philly", "NEW — bbbike + geofabrik-PA fallback, osmium bbox order", True),
            ("download_pbf — retries across primary/fallback", "resilient wget loop", False),
            ("clip loop + osmium merge", "extract per bbox, merge to combined.osm.pbf", False),
        ],
        "blocks": [
            "philly clip line — bbbike primary + geofabrik pennsylvania-latest fallback, bbox -75.280,39.867,-74.956,40.138",
            "header comment — \"NYC, SF, and Chicago\" → \"every supported city (see the CITIES list below)\"",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WebSocket routes, the per-map vote cache, graph + OSRM registries, startup warmup"),
        "file": ("app.py", "~1640 LOC — every API/WS route plus the registries and the boot warmup"),
        "outline": [
            ("Imports + setup", "cities, registries, redis client", False),
            ("GraphRegistry construction", "max_loaded budget — now derived from len(CITIES)", True),
            ("Map resolution / passcode / vote cache", "resolve_map + bounded LRU vote cache", False),
            ("Startup warmup", "preload every city's graph + vote body; docstring no longer says \"four\"", True),
            ("Vote / graph / admin APIs", "/api/vote, /api/graph-*, admin", False),
        ],
        "blocks": [
            "GraphRegistry(max_loaded=len(CITIES) + len(STATION_NETWORKS)) — was 4 + len(STATION_NETWORKS)",
            "warmup docstring — \"All four cities\" / \"max_loaded=4\" → len(CITIES)-relative wording",
        ],
    },
    "server/presets.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "canonical preset vote-type lists + the preset maps seeded at startup"),
        "file": ("presets.py", "~117 LOC — PRESET_LISTS (bikes/trees/walkways) + PRESET_MAPS seed rows"),
        "outline": [
            ("PRESET_LISTS", "the three canonical vote-type lists", False),
            ("PRESET_MAPS — header comment", "now describes the general (non-NYC) case", True),
            ("PRESET_MAPS — nyc-*", "the three existing NYC maps (keep vanity subdomains)", False),
            ("PRESET_MAPS — dc-* / philly-*", "NEW — six subdomain-less maps for the new cities", True),
        ],
        "blocks": [
            "PRESET_MAPS header comment generalized beyond NYC",
            "dc-bikes / dc-trees / dc-walkways — subdomain: None",
            "philly-bikes / philly-trees / philly-walkways — subdomain: None",
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Groundwork for launching in Washington, D.C. and Philadelphia. D.C. was already
  registered; this pass adds Philadelphia end-to-end (city registry, merged-OSRM clip, built walk graph)
  and seeds preset maps so both cities are reachable out-of-the-box. Purely additive backend/data —
  deliberately disjoint from the in-flight block-level voting pipeline (<code>server/streetscape_blocks/</code>).</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_dc_philly_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_dc_philly_report.py</code>.
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
