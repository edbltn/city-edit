#!/usr/bin/env python3
"""Generate the HTML changelog report for the ferry-removal workstream.

Run from repo root: python changelog/build_ferry_report.py
Reads changelog/changes.diff, writes changelog/2026-06-17-remove-ferry-edges.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-06-17-remove-ferry-edges.html")

DATE = "2026-06-17"
TITLE = "Removing ferry edges from routing and the display graph"


def split_by_file(diff_text: str):
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
        "id": "leak",
        "tag": "OSRM + Backend",
        "title": "1 · The ferry leak — water-crossing edges in both graphs",
        "symptom": (
            "Ferry routes still showed up as long straight lines over open water in the hover/topology "
            "graph (most visibly the Staten Island Ferry across New York Harbor), and OSRM would happily "
            "route a pedestrian <em>across</em> the water along them."
        ),
        "cause": [
            "Both <code>osrm/foot.lua</code> and its Python mirror <code>server/foot_profile.py</code> "
            "already had <strong>empty ferry tables</strong> (<code>route_speeds = {}</code> / "
            "<code>_ROUTABLE_ROUTE = frozenset()</code>) and <em>claimed</em> ferries were disabled.",
            "But an empty ferry table only stops the dedicated ferry handler from assigning a <em>ferry</em> "
            "speed. A <code>route=ferry</code> way that is also tagged <code>foot=yes</code> "
            "(the Staten Island Ferry, Governors Island, every NYC Ferry line…) then falls through to the "
            "generic speed step, whose <strong>access fallback</strong> gives any access-whitelisted way "
            "<code>default_speed</code>. So OSRM routed it as a normal walkway, and the Python mirror admitted "
            "it via the identical <code>return access in _ACCESS_WHITELIST</code> fallback.",
            "Measured against the real NYC extract: <strong>134 of 145</strong> <code>route=ferry</code> ways "
            "were being accepted into the topology graph; OSRM routed Whitehall→St George (open water) as "
            "<strong>8848&nbsp;m at 1.39&nbsp;m/s</strong> — normal walking speed straight across the harbor.",
        ],
        "fixes": [
            "<strong>Explicit ferry cutoff in OSRM.</strong> <code>process_way</code> now aborts on "
            "<code>data.route == 'ferry'</code> <em>before</em> the handler sequence runs, so the access "
            "fallback never gets a chance to assign a speed. This drops <em>all</em> ferries regardless of "
            "their access tags — not just the ones the empty <code>route_speeds</code> already covered.",
            "<strong>Matching cutoff in the Python mirror.</strong> <code>way_is_foot_routable</code> rejects "
            "<code>route=ferry</code> (new <code>_ROUTE_BLACKLIST</code>) before the access whitelist "
            "fallback, so the votable topology graph — and the PMTiles background and hover graph built from "
            "it — carry exactly what OSRM routes.",
            "Rebuilt artifacts: the per-city walk graphs (<code>walk_graph.pkl</code>), the merged OSRM "
            "dataset (re-extracted with the new profile), and the per-city <code>graph.pmtiles</code>. NYC "
            "dropped from 3,309,478 → 3,307,316 directed edges; longest surviving edge is 1439&nbsp;m (no "
            "over-water hops).",
        ],
        "files": [
            "osrm/foot.lua — route=ferry abort in process_way (+ corrected route_speeds note)",
            "server/foot_profile.py — _ROUTE_BLACKLIST + route=ferry reject in way_is_foot_routable",
        ],
    },
    {
        "id": "consistency",
        "tag": "Backend · tests",
        "title": "2 · Keeping OSRM and the display graph consistent",
        "symptom": (
            "The deeper question: is the hover graph guaranteed to be a consistent copy of the graph OSRM "
            "routes on? The ferry bug was precisely a case where the two independent implementations of the "
            "routability rule (Lua + Python) drifted — they were <em>consistently wrong</em> together."
        ),
        "cause": [
            "By design the topology graph is built by a Python <em>mirror</em> of <code>foot.lua</code> and is "
            "a deliberate <strong>superset</strong> of OSRM's foot network (extra topology edges are harmless; "
            "every OSRM node must resolve to a topology edge so votes map). <code>validate_osrm_topology.py</code> "
            "verified that <em>forward</em> direction (topology ⊇ OSRM) — but nothing checked the other "
            "direction, so a topology edge that OSRM <em>refuses</em> to route (a ferry) went unnoticed.",
        ],
        "fixes": [
            "<strong>New reverse-coverage check</strong> in <code>validate_osrm_topology.py</code> "
            "(<code>--reverse-edges N</code>): sample N topology edges and confirm OSRM will actually traverse "
            "each at roughly its own length. An edge OSRM refuses or only reaches via a huge detour is flagged "
            "as an <em>orphan</em> — exactly the ferry class of bug. On the rebuilt NYC graph: "
            "<strong>0 / 382 orphans</strong>.",
            "<strong>Unit regression test</strong> <code>tests/unit/test_foot_profile.py</code> locks the "
            "Python predicate: ferries with <code>foot=yes</code>/<code>access=yes</code>/<code>designated</code> "
            "are all rejected, while ordinary footways and the access-fallback path (e.g. a "
            "<code>cycleway</code> tagged <code>foot=designated</code>) still route.",
            "Both files keep the <code>KEEP IN SYNC</code> contract explicit: the Lua abort and the Python "
            "reject reference each other so the next profile edit updates both.",
        ],
        "files": [
            "server/tests/validate_osrm_topology.py — run_reverse_check + --reverse-edges flag",
            "server/tests/unit/test_foot_profile.py — NEW predicate regression tests",
        ],
    },
]

VERIFY = [
    "Predicate scan over the real NYC PBF: of 145 <code>route=ferry</code> ways, the old rule accepted 134, the new rule accepts <strong>0</strong>.",
    "OSRM (rebuilt dataset): Staten Island Ferry crossing Whitehall→St George now returns <code>NoRoute</code> (was <code>Ok</code>, 8848&nbsp;m); on-land NYC + SF walks still route.",
    "Rebuilt NYC topology: 0 edges longer than 1500&nbsp;m (longest 1439&nbsp;m) — no over-water hops survive.",
    "<code>validate_osrm_topology.py --city nyc -n 300 --reverse-edges 400</code> against the rebuilt OSRM: forward node coverage 99.95% / edge 99.997%; reverse <strong>0/382 orphan edges</strong>.",
    "<code>pytest tests/unit</code> — 24 passed (9 new in test_foot_profile.py).",
    "All five city walk graphs and their <code>graph.pmtiles</code> rebuilt from the same source PBFs.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-walkways</code> and look at New York Harbor / the East River — the long straight ferry lines (Staten Island Ferry, Governors Island, NYC Ferry routes) should be gone from both the background network and the hover graph.",
    "Try routing on foot from Manhattan to Staten Island — it should fail to cross the water rather than drawing a straight line over the harbor.",
    "Spot-check other cities (SF Bay, DC/Potomac) for stray over-water lines.",
    "Re-run <code>OSRM_URL=http://localhost:5005 python tests/validate_osrm_topology.py --city sf -n 300 --reverse-edges 400</code> for a non-NYC city; reverse orphans should be ~0.",
    "Confirm <code>server/.env</code> points <code>OSRM_URL</code> at the rebuilt dataset (local: <code>http://localhost:5005</code>, the <code>osrm-local</code> container).",
    "On the next prod build, the OSRM image re-extracts from <code>osrm/foot.lua</code> automatically; the per-city graph + PMTiles rebuild picks up the new <code>foot_profile.py</code>.",
]


# ── Hierarchical "where does this block sit" context ──────────────────────────
SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "osrm/foot.lua": {
        "on": ["OSRM"],
        "module": ("OSRM · osrm/", "the pinned foot profile that decides which OSM ways OSRM routes (extract-time rules)"),
        "file": ("foot.lua", "~280 LOC — osrm-backend v5.25.0 foot profile, kept in sync with server/foot_profile.py"),
        "outline": [
            ("setup() — properties & speed tables", "walking_speed, access lists, speeds, route_speeds (ferries)", True),
            ("process_node", "barriers + traffic signals", False),
            ("process_way — prefetch + early aborts", "empty-tag abort; NEW route=ferry abort", True),
            ("process_way — handler sequence", "access, oneway, ferries, speed, names, weights", False),
            ("process_turn", "u-turn + traffic-light penalties", False),
        ],
        "blocks": [
            "route_speeds note corrected — empty table is not enough; the abort below is the real cutoff",
            "process_way: `if data.route == 'ferry' then return end` before the handler sequence",
        ],
    },
    "server/foot_profile.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/", "Python mirror of foot.lua — decides which ways enter the votable topology graph"),
        "file": ("foot_profile.py", "~120 LOC — routable category/access sets + way_is_foot_routable / node_blocks_foot"),
        "outline": [
            ("Routable category sets", "_ROUTABLE_HIGHWAY/RAILWAY/AMENITY/MAN_MADE/LEISURE", False),
            ("_ROUTABLE_ROUTE + _ROUTE_BLACKLIST", "ferries: empty routable set + NEW explicit blacklist", True),
            ("Access tables + prefetch keys", "whitelist/blacklist, hierarchy, prefetch", False),
            ("way_is_foot_routable", "prefetch → NEW ferry reject → access gate → category/whitelist", True),
            ("node_blocks_foot", "barrier parity (auditing only)", False),
        ],
        "blocks": [
            "_ROUTE_BLACKLIST = frozenset({'ferry'}) + corrected route_speeds note",
            "way_is_foot_routable: reject tags['route'] in _ROUTE_BLACKLIST before the access fallback",
        ],
    },
    "server/tests/unit/test_foot_profile.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/tests/unit", "NEW — unit coverage for the routability predicate"),
        "file": ("test_foot_profile.py", "NEW FILE — asserts ferry rejection + that ordinary/fallback ways still route"),
        "outline": [
            ("routable cases", "footway, residential, access-fallback cycleway", True),
            ("ferry rejection cases", "route=ferry with foot=yes / access=yes / designated / bare", True),
            ("negative cases", "access-blacklisted footway, untagged way", True),
        ],
        "blocks": ["Whole file is new — the regression net for the ferry cutoff."],
    },
    "server/tests/validate_osrm_topology.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/tests", "empirical OSRM↔topology consistency validator (manual / CI)"),
        "file": ("validate_osrm_topology.py", "~180 LOC — routes coordinate pairs through OSRM and checks node/edge coverage"),
        "outline": [
            ("arg parsing", "city, num, seed, osrm url; NEW --reverse-edges", True),
            ("forward check", "topology ⊇ OSRM: node + edge-pair coverage, fallback rate", False),
            ("results print + worst routes", "coverage summary", True),
            ("run_reverse_check / _edge_len_m", "NEW — OSRM must traverse each topology edge (catches ferries)", True),
        ],
        "blocks": [
            "--reverse-edges N flag",
            "run_reverse_check() + _edge_len_m() — flag topology edges OSRM won't route",
            "call run_reverse_check at end of main when --reverse-edges is set",
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

  .ctx {{ padding: 14px 16px; background: #f7f5ef; border-top: 1px solid var(--hairline); }}
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; position: relative; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
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

  <p class="lede">Ferry ways tagged <code>foot=yes</code> were slipping past the "ferries disabled" profile via
  an access-fallback that both OSRM and its Python mirror share — so routes jumped open water and ferry lines
  haunted the hover graph. One explicit <code>route=ferry</code> cutoff on each side removes them everywhere,
  and a new reverse-coverage check keeps the two graphs honest.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_ferry_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_ferry_report.py</code>.
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
