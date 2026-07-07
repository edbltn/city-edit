#!/usr/bin/env python3
"""Generate the QGIS artifact-export changelog report.

Run from repo root: python changelog/build_qgis_export_report.py
Reads changelog/changes-qgis-export.diff
(captured with: git add -N <new files> && git diff -- scripts/export_qgis_artifacts.py
 client-react/scripts/export-proposals.ts docs/qgis-export.md .gitignore
 > changelog/changes-qgis-export.diff),
writes changelog/2026-07-07-qgis-export.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-qgis-export.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-qgis-export.html")

DATE = "2026-07-07"
TITLE = "QGIS export — one command turns a map slug into a GeoPackage of blocks, proposals, graph, and sample routes"


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
        elif raw.startswith("diff ") or raw.startswith("index "):
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
        "id": "exporter",
        "tag": "Tooling · GIS export",
        "title": "1 · scripts/export_qgis_artifacts.py — the one-command exporter",
        "ask": (
            "A repeatable way to pull everything a City Edit map serves — blocks with their votes, top "
            "proposals (both kinds), the votable route graph, and sample A→B routes — into QGIS to play with."
        ),
        "design": [
            "One <strong>GeoPackage per slug</strong> (<code>exports/qgis/&lt;slug&gt;/city-edit-&lt;slug&gt;.gpkg</code>, "
            "EPSG:4326) with 10 layers, so the whole export drags into QGIS as a single file; a "
            "<code>manifest.json</code> records vote revision, topology/blocks versions, seed, and per-layer counts.",
            "Reads the <strong>same live API a browser does</strong> (Flask :5001): the GTB2 binary topology "
            "(decoded with numpy straight off the wire format), <code>/api/graph-votes</code>, and "
            "<code>POST /api/routes</code> — so the export always reflects the currently-served state, "
            "and aborts if the vote arrays don't match the topology dimensions.",
            "Block polygons come from <code>blocks_generic_&lt;city&gt;.geojson</code> — the exact file the "
            "edge→block bake read — joined to the served per-block vote aggregates by <code>block_id</code>; "
            "the file is sha-checked against the bake metadata and a mismatch warns loudly.",
            "The full NYC graph (3.3M edges) is built with vectorized shapely (<code>shapely.linestrings</code> "
            "over the raw coord arrays) and written via pyogrio — the whole export runs in ~53s.",
            "Sample routes mix two kinds: anchor-to-anchor requests for the top route proposals (does OSRM "
            "re-trace the hot corridor?) plus seeded-random node pairs 0.8–6 km apart; each also exports the "
            "graph edges its <code>edge_ids</code> resolved to, so OSRM-geometry vs graph-snap divergence is "
            "visible as two overlaid layers.",
            "Escape hatches: <code>--bbox=w,s,e,n</code> clips blocks+graph for laptop-friendly files, "
            "<code>--skip</code> reruns one piece, <code>--all-nodes</code> exports all 1.3M nodes instead of "
            "just voted ones, <code>--seed</code>/<code>--routes</code> control the samples.",
        ],
        "files": ["scripts/export_qgis_artifacts.py", ".gitignore"],
    },
    {
        "id": "proposals",
        "tag": "Client · proposals fidelity",
        "title": "2 · export-proposals.ts — proposals computed by the real client modules",
        "ask": (
            "Top proposals are computed CLIENT-side (docs/three-layer-model.md §3) — a Python "
            "re-implementation of the PBTP pipeline and the RBTP corridor clustering would drift the first "
            "time either module changed."
        ),
        "design": [
            "A small vite-node script inside <code>client-react/</code> imports the actual "
            "<code>graphTopology.ts</code> / <code>topProposals.ts</code> / <code>routeProposals.ts</code> "
            "modules, fetches the same two API responses the app does, and runs "
            "<code>selectTopProposals</code> + <code>computeRouteProposals</code> verbatim — the Python side "
            "just turns the resulting JSON into geometries.",
            "One deliberate divergence, documented in both places: the PBTP tiebreak salt is fixed to 0 "
            "(the app randomizes per page load to rotate equal-net pins), so exports are deterministic "
            "for a given (topology, vote state).",
            "Points a same-type route subsumes are <em>flagged</em> (<code>covered_by_route</code>) rather "
            "than dropped — in QGIS you filter interactively instead of losing the data.",
            "RBTP path geometry is recovered by walking the ordered edge ids from <code>anchors[0]</code> "
            "(the modules only expose edge ids + anchors); the block-edge union and the two anchors export "
            "as their own layers, mirroring the highlight/vote set vs the pin's corridor line.",
        ],
        "files": ["client-react/scripts/export-proposals.ts"],
    },
    {
        "id": "guide",
        "tag": "Docs",
        "title": "3 · docs/qgis-export.md — the guide",
        "ask": "A quick guide for generating and using the artifacts.",
        "design": [
            "Covers prerequisites (dev stack, the streetscape geo venv, block artifacts, vite-node), the "
            "one-command run, a table of all 10 layers with their attributes, every CLI option, QGIS styling "
            "recipes per layer (graduated <code>net_votes</code> fill for blocks, rule-based heatmap override "
            "for graph edges, solid-vs-dashed overlay for the OSRM↔graph snap check), and the fidelity notes "
            "(fixed salt, sha check, vote revision in the manifest).",
        ],
        "files": ["docs/qgis-export.md"],
    },
]

VERIFY = [
    "Tested end-to-end on NYC (<code>nyc-walkways</code>): 53s total → 853MB GeoPackage with all 10 layers — "
    "blocks 563,812 · graph_edges 3,299,152 · graph_nodes 87 (voted) · proposals 4 points + 2 routes "
    "(+ edges/blocks/anchors layers) · sample_routes 8 + their matched-edge twins.",
    "Cross-checked the vote join: 634 blocks with non-zero <code>net_votes</code> in the GeoPackage — exactly "
    "the number of non-zero entries in the served <code>block_votes</code> array.",
    "Proposal fidelity: the vite-node helper returned the same 4 PBTPs + 2 RBTPs the client computes "
    "(<code>Add bike lane</code> corridor, score 69, 46 blocks), and <code>covered_by_route</code> correctly "
    "flags the one bike-lane point sitting on that corridor.",
    "Corridor sanity: the sample route between the top RBTP's anchors resolved to 69 <code>edge_ids</code> — "
    "the same count as the proposal's path edges (OSRM re-traced the corridor).",
    "All 8 sample routes returned geometry and non-empty matched-edge multilines; "
    "<code>--bbox=-74.03,40.70,-73.93,40.77 --skip proposals,routes</code> exercised the clip path "
    "(50,805 blocks / 331,138 edges, 16s).",
]

CHECKLIST = [
    "Run <code>server/streetscape_blocks/env/bin/python scripts/export_qgis_artifacts.py nyc-walkways</code> "
    "with the dev stack up (Redis + Flask :5001) and confirm it finishes with “done in ~60s”.",
    "Drag <code>exports/qgis/nyc-walkways/city-edit-nyc-walkways.gpkg</code> into QGIS and confirm all 10 "
    "layers appear and land on New York.",
    "Style <code>blocks</code> graduated on <code>net_votes</code> with filter <code>net_votes != 0</code>: "
    "the voted blocks should match the heat you see at <a href='http://localhost:3000/m/nyc-walkways'>"
    "localhost:3000/m/nyc-walkways</a>.",
    "Overlay <code>sample_route_edges</code> (dashed) on <code>sample_routes</code> (solid): the pairs "
    "should trace the same streets; divergence = an OSRM↔graph mapping discrepancy worth a look.",
    "Open <code>proposals_route</code>'s attribute table: 2 corridors with scores 69 and 8; select one and "
    "confirm <code>proposals_route_blocks</code> highlights its wider vote set around the path.",
    "Cast a vote in the app, re-run the export, and confirm <code>manifest.json</code>'s "
    "<code>vote_revision</code> bumped and the new block shows in QGIS after a layer reload.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>The ask</h3>
      <p>{s['ask']}</p>
      <h3>What was built &amp; why</h3>
      <ul>{li(s['design'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client", "Tooling / scripts"]

FILE_CONTEXT = {
    "scripts/export_qgis_artifacts.py": {
        "on": ["Tooling / scripts", "Flask API"],
        "module": ("scripts/", "repo-level developer tooling (worktree helpers, cloud tests — now GIS export)"),
        "file": ("export_qgis_artifacts.py", "NEW ~380 LOC — slug → 10-layer GeoPackage via the live local API"),
        "outline": [
            ("API helpers", "stdlib urllib GET/POST (the geo venv has no requests)", True),
            ("Topology", "GTB1/GTB2 binary decode + vectorized edge-LineString builders", True),
            ("vt_summary", "one [legendIdx,up,down] list → top label / totals / JSON breakdown", True),
            ("Layer builders", "blocks join · graph edges/nodes · proposal layers · sample routes", True),
            ("run_proposals_helper", "shells out to vite-node → the client TS modules", True),
            ("sample_route_pairs", "top-RBTP anchors + seeded random 0.8–6km node pairs", True),
            ("main", "arg parsing, consistency checks, per-layer writes, manifest", True),
        ],
        "blocks": [
            "Topology class — numpy views over the GTB2 wire format (coords 1e7-scaled i32, ends u32, edge_block_id i32)",
            "build_blocks_layer — sha-checks blocks_generic_<city>.geojson against the bake metadata, joins by block_id",
            "build_graph_edge_layer — 3.3M vectorized linestrings; net_votes + top_label + block_id per edge",
            "build_proposal_layers — point/point_edges + route/route_blocks/route_anchors GeoDataFrames",
            "build_sample_route_layers — OSRM geometry + the edge_ids resolved back to graph multilines",
            "main — fresh-gpkg unlink, --bbox .cx clip on blocks/graph layers, --skip, manifest.json",
        ],
    },
    "client-react/scripts/export-proposals.ts": {
        "on": ["React / Leaflet client", "Tooling / scripts"],
        "module": ("React client · scripts/", "NEW dir — node-side helpers that reuse src/ modules verbatim"),
        "file": ("export-proposals.ts", "NEW ~130 LOC — PBTPs + RBTPs via the real client pipeline, dumped as JSON"),
        "outline": [
            ("Imports", "graphTopology / topProposals / routeProposals — the app's own modules", True),
            ("chainPathNodes", "ordered edge ids + start anchor → node sequence (path geometry)", True),
            ("main", "fetch bin topology + votes → selectTopProposals + computeRouteProposals → JSON", True),
        ],
        "blocks": [
            "TIEBREAK_SALT = 0 — deterministic exports (app randomizes per load); documented divergence",
            "dimension guard — refuses to run votes against a mismatched topology (the stale-cache trap)",
            "covered_by_route flag via dropPointsCoveredByRoutes — flag, don't drop, for QGIS filtering",
        ],
    },
    "docs/qgis-export.md": {
        "on": ["Tooling / scripts"],
        "module": ("docs", "developer guides"),
        "file": ("qgis-export.md", "NEW — the how-to: prerequisites, layer table, options, QGIS styling recipes"),
        "outline": [
            ("Quick start", "one command, where output lands", True),
            ("Prerequisites", "dev stack · geo venv · block artifacts · vite-node · OSRM", True),
            ("Layer table", "all 10 layers with their attributes", True),
            ("Options + styling tips + fidelity notes", "bbox/skip/seed; per-layer QGIS recipes", True),
        ],
        "blocks": ["entire file is new"],
    },
    ".gitignore": {
        "on": ["Tooling / scripts"],
        "module": ("repo root", "ignore rules"),
        "file": (".gitignore", "+1 rule"),
        "outline": [("exports/", "NEW — regenerable QGIS exports stay out of git", True)],
        "blocks": ["exports/ — regenerable via scripts/export_qgis_artifacts.py"],
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
        <details open>
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
  @media (max-width: 680px) {{ h1 {{ font-size: 27px; }} }}
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

  <p class="lede">New tooling:
  <code>scripts/export_qgis_artifacts.py &lt;slug&gt;</code> exports everything a map serves into a single
  QGIS-ready GeoPackage — Layer-2 blocks joined with their vote aggregates, both kinds of top proposals
  (PBTPs and RBTPs, computed by the <em>actual client TS modules</em> via vite-node, so nothing drifts),
  the full Layer-1 votable graph with per-edge votes, and sample OSRM routes paired with the graph edges
  they mapped to. Tested end-to-end on NYC: 10 layers, 853MB, 53 seconds. Guide:
  <code>docs/qgis-export.md</code>.</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Green is added, red removed.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes-qgis-export.diff</code> by <code>changelog/build_qgis_export_report.py</code>.
    Regenerate after further edits with
    <code>git diff -- &lt;files&gt; &gt; changelog/changes-qgis-export.diff &amp;&amp; python changelog/build_qgis_export_report.py</code>.
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
