#!/usr/bin/env python3
"""Generate the HTML changelog report for the mobile graph-memory refactor.

Run from repo root: python changelog/build_report_mobile.py
Reads changelog/changes.diff, writes changelog/2026-06-17-mobile-graph-memory.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-06-17-mobile-graph-memory.html")

DATE = "2026-06-17"
TITLE = "The mobile-Safari heatmap crash was the graph's own memory"
BRANCH = "fix/unify-voting"
LEDE = ("“A problem repeatedly occurred” on phones wasn’t the server, the subdomain routing, or "
        "vote-sync — it was the doubled NYC graph decoding to ~500&nbsp;MB of boxed JS on the device. "
        "Backing the topology with flat typed arrays drops that to ~71&nbsp;MB of off-heap buffers.")

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]


def split_by_file(diff_text):
    files, current_name, current_lines = [], None, []
    for line in diff_text.splitlines():
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if not m:
            m = re.match(r"^diff --git a/(\S+) b/(\S+)$", line)
        mg = re.match(r"^diff --git ", line)
        if mg:
            # robustly grab the b/ path (handles /dev/null new-file form)
            parts = line.split(" b/")
            name = parts[-1].strip() if len(parts) > 1 else line
            if current_name is not None:
                files.append((current_name, "\n".join(current_lines)))
            current_name = name
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_name is not None:
        files.append((current_name, "\n".join(current_lines)))
    return files


def colorize(diff_chunk):
    out = []
    for raw in diff_chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith(("diff ", "index ", "new file", "similarity")):
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
        "id": "diagnosis",
        "tag": "Investigation",
        "title": "1 · What it actually was (and three things it wasn't)",
        "symptom": (
            "On mobile, <code>bikepaths.cityedit.org</code> (and the other NYC street maps) showed "
            "<em>“a problem repeatedly occurred”</em> on load. <code>ebikes.cityedit.org</code> looked "
            "fine. Desktop looked fine."
        ),
        "cause": [
            "<strong>Not the subdomain.</strong> The same map fails identically on the apex "
            "(<code>cityedit.org?map=nyc-bikes</code>) and the subdomain; <code>subdomainRedirectUrl()</code> "
            "guards the canonical case, so there is no redirect loop. <code>ebikes</code> only looks healthy "
            "because <code>nyc-ebike-charging</code> is passcode-locked and never fetches topology.",
            "<strong>Not the server.</strong> Real browsers send <code>Accept-Encoding</code>, so nginx "
            "brotli-compresses the topology/votes under Cloud Run’s 32&nbsp;MiB response cap and returns 200. "
            "(The 500s seen while probing were a <code>curl</code> artifact — no <code>Accept-Encoding</code> "
            "→ uncompressed 34&nbsp;MB → over the cap.) Server-side OOM in the logs was load-induced by those "
            "probes, not the cause — and would hit desktop equally anyway.",
            "<strong>It was client-side memory.</strong> The foot-aligned rebuild ~doubled the NYC graph to "
            "3.3M edges / 1.3M nodes. The client decoded the compact 35&nbsp;MB binary blob into boxed JS "
            "tuples — <code>[lat,lon][]</code> + <code>[from,to,name][]</code> + a <code>number[][]</code> "
            "adjacency — which measures <strong>~497&nbsp;MB on the V8 object heap</strong> (more on Safari’s "
            "JSC). That exceeds mobile Safari’s per-tab budget → the WebContent process is jetsam-killed → "
            "reload → re-decode → killed again = the crash loop. Desktop has the RAM; <code>ebikes</code>’ "
            "station graph is tiny.",
            "The earlier defense-in-depth fix (<code>0c3ebec</code>) targeted stale-cache + concurrency, "
            "<em>not</em> the decoded-memory size — so it never addressed this, and its error boundary "
            "actually made the loop worse by reloading into a deterministic OOM.",
        ],
        "fixes": [
            "Measured the boxed decode at <strong>497&nbsp;MB</strong> of object heap (Node/V8) against the "
            "real <code>nyc-bikes</code> topology blob, then re-measured the typed-array version at "
            "<strong>~71&nbsp;MB of ArrayBuffer-backed external memory</strong> and ~0 on the object heap.",
            "Confirmed end-to-end in a headless browser: the full NYC graph loads, decodes, builds indices + "
            "adjacency, fetches votes, and <strong>paints the heatmap with zero console errors</strong>.",
        ],
        "files": [
            "(investigation — no code in this section)",
        ],
    },
    {
        "id": "refactor",
        "tag": "Frontend",
        "title": "2 · Typed-array topology — ~497 MB → ~71 MB",
        "symptom": (
            "The in-memory graph representation, not the wire format, was the memory hog: the 35&nbsp;MB "
            "binary blob was expanded into hundreds of MB of boxed JS objects on every load."
        ),
        "cause": [
            "<code>GraphData.nodes</code> / <code>.edges</code> were arrays of small JS tuples — each one a "
            "heap object with per-object overhead, ×4.6M of them. <code>buildNodeAdj</code> added a "
            "<code>number[][]</code> of 1.3M sub-arrays (~150&nbsp;MB more). All of it lived on the GC’d "
            "object heap.",
        ],
        "fixes": [
            "<strong>New <code>graphTopology.ts</code>:</strong> a flat <code>GraphTopology</code> "
            "(<code>coords: Int32Array</code>, <code>ends: Uint32Array</code>, optional <code>edgeNames</code> "
            "for tiny station nets) with module-level accessors (<code>nodeLat/nodeLon/edgeFrom/edgeTo/"
            "edgeName</code>). The binary decoder now returns these as <strong>zero-copy views onto the blob</strong> "
            "instead of boxing — so the topology stays near its wire size.",
            "<strong>CSR adjacency.</strong> <code>buildNodeAdj</code> returns <code>{start, edges}</code> "
            "(two <code>Uint32Array</code>s, ~31&nbsp;MB) replacing the <code>number[][]</code>; "
            "<code>adjEdgesOf</code> hands callers a cheap subarray view and <code>adjFirst</code> the first "
            "incident edge.",
            "<strong>Threaded through every consumer</strong> (compiler-driven — ~95 sites in "
            "<code>GraphLayer.tsx</code> plus <code>voteApply.ts</code>, <code>topProposals.ts</code>): "
            "hit-testing, snapping, the canvas redraw hot loop (reads the typed arrays directly), tooltips, "
            "proposal markers, and node-vote rederivation. Station networks (JSON topology) normalize through "
            "<code>topologyFromJson</code> into the same shape, keeping names.",
            "<strong>Bumped the IndexedDB cache to v3</strong> so a pre-refactor (boxed-shape) cached station "
            "topology can’t survive into the accessor code and crash it.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/graphTopology.ts — NEW: typed-array topology + accessors + CSR adjacency",
            "client-react/src/components/GraphLayer/GraphLayer.tsx — decode returns views; all node/edge/adj reads via accessors; redraw uses typed arrays directly",
            "client-react/src/components/GraphLayer/voteApply.ts — NodeAdj (CSR) + edgeFrom/edgeTo",
            "client-react/src/components/GraphLayer/topProposals.ts — edgeMidpointResolver / selectTopProposals on GraphTopology",
            "client-react/src/types/index.ts — GraphData topology fields are now typed arrays",
            "client-react/src/utils/graphCache.ts — DB_VERSION 2 → 3",
            "client-react/src/components/GraphLayer/{voteApply,topProposals}.test.ts — fixtures build typed-array topologies",
        ],
    },
]

VERIFY = [
    "Diagnosis from prod: <code>gcloud logging</code> + header-varied <code>curl</code> proved the 500s were a no-Accept-Encoding artifact; real-browser headers return 200.",
    "Memory measured against the real <code>nyc-bikes</code> blob: boxed decode = <strong>497 MB</strong> object heap; typed-array decode + CSR = <strong>~71 MB external, ~0 object heap</strong>.",
    "Frontend: <code>tsc -b --noEmit</code> — clean (compiler enumerated every consumer site).",
    "Frontend: <code>vitest run</code> — 101 passed.",
    "Frontend: <code>npm run build</code> — clean production build.",
    "End-to-end: headless Chrome loaded <code>/m/nyc-bikes</code> (full 3.3M-edge graph) — heatmap painted, zero console errors, no topology/vote-mismatch warnings.",
]

CHECKLIST = [
    "Open <code>bikepaths.cityedit.org</code> (or <code>/m/nyc-bikes</code>) on a real phone / iOS Safari. The heatmap should load without the “a problem repeatedly occurred” loop.",
    "On the same phone, exercise hover/tap on edges and nodes, cast a vote, and check the pinned card + tooltips — accessors back all of these, so any off-by-one would show as a wrong snap/label.",
    "Confirm street tooltips still reverse-geocode (binary topology drops names) and station-map names still show (JSON topology keeps them).",
    "Watch the tab’s memory (Safari Web Inspector → Timelines) on load — it should sit far below the prior ~500 MB+ spike.",
    "Pan/zoom the heatmap and check the redraw still culls + paints correctly (the hot loop now reads the typed arrays directly).",
    "Because the IndexedDB cache bumped to v3, the first load re-fetches topology fresh; subsequent loads should hit the cache.",
]

# ── Hierarchical "where does this block sit" context ──────────────────────────
# label, summary, changed?
FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/graphTopology.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "NEW — the flat typed-array graph representation shared by the heatmap, snapping, and proposals"),
        "file": ("graphTopology.ts", "NEW FILE — ~150 LOC: typed-array topology type, accessors, JSON normalizer, CSR adjacency"),
        "outline": [
            ("GraphTopology type", "coords:Int32Array, ends:Uint32Array, optional edgeNames", True),
            ("Element accessors", "nodeLat/nodeLon/nodeLatLng/edgeFrom/edgeTo/edgeName", True),
            ("topologyFromJson", "convert the station-net JSON shape → typed arrays", True),
            ("NodeAdj (CSR) + buildNodeAdj", "two Uint32Arrays replacing number[][]", True),
            ("adjEdgesOf / adjFirst", "cheap subarray view + first incident edge", True),
        ],
        "blocks": [
            "GraphTopology + COORD_SCALE — the mobile-safe representation",
            "nodeLat/nodeLon/edgeFrom/edgeTo/edgeName accessors",
            "topologyFromJson — clamp out-of-range nodes, keep names only if present",
            "buildNodeAdj — 2-pass CSR build (degree count → prefix sum → scatter)",
            "adjEdgesOf (subarray, no copy) + adjFirst",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the canvas heatmap + topology loader + proposal-marker renderer"),
        "file": ("GraphLayer.tsx", "~3430 LOC — one big component; ~95 sites rebound from boxed tuples to accessors"),
        "outline": [
            ("Module helpers", "buildNodeIndex/EdgeIndex, hitTest, projectOntoEdge, decodeTopologyBin", True),
            ("Component state & refs", "topologyRef:GraphTopology, nodeAdjRef:NodeAdj", True),
            ("resolveSelection / proposals", "snap + nearest-proposal now via accessors", True),
            ("Topology + vote loading", "decode → views; JSON → topologyFromJson; cache reads", True),
            ("redraw — heat passes", "hot loop reads coords/ends typed arrays directly", True),
            ("Hover / pinned tooltips", "edge/node names via edgeName + accessors", True),
            ("Indicator markers", "winner/station midpoints via accessors", True),
        ],
        "blocks": [
            "import graphTopology accessors",
            "decodeTopologyBin — return GraphTopology views (no boxing); clamp ends in place",
            "buildNodeIndex/buildEdgeIndex — iterate coords/ends typed arrays",
            "hitTest / projectOntoEdge / targetLatLng — accessor-based",
            "topology refs + load flow → GraphTopology; topologyFromJson on JSON path",
            "redraw projectNode/drawSeg — direct coords[]/ends[] reads",
            "tooltip + marker placement — edgeFrom/edgeTo/edgeName/adjFirst",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure vote-array mutation: edge/node counts + per-type breakdown"),
        "file": ("voteApply.ts", "~210 LOC — optimistic + authoritative vote application, node rederivation"),
        "outline": [
            ("rederiveNodes", "now walks CSR adjacency via adjEdgesOf", True),
            ("applyMyVoteChange / applyEdgeVoteChange", "affected nodes via edgeFrom/edgeTo", True),
            ("applyAuthoritativeCounts", "same — accessor endpoints", True),
        ],
        "blocks": [
            "import NodeAdj + edgeFrom/edgeTo/adjEdgesOf",
            "rederiveNodes(adj:NodeAdj) — iterate adjEdgesOf(adj,nid)",
            "affected.add(edgeFrom/edgeTo(data,eid)) in all three appliers",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure top-proposal selection (per-type winners → spacing → limit)"),
        "file": ("topProposals.ts", "~270 LOC — winner selection + edge-midpoint resolver"),
        "outline": [
            ("edgeMidpointResolver", "midpoint from GraphTopology accessors", True),
            ("selectTopProposals", "accepts Pick<votes> & GraphTopology", True),
            ("winner/spacing helpers", "unchanged", False),
        ],
        "blocks": [
            "import GraphTopology + accessors",
            "edgeMidpointResolver(data:GraphTopology) — edgeFrom/edgeTo + nodeLat/nodeLon",
            "selectTopProposals data type → & GraphTopology",
        ],
    },
    "client-react/src/types/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · types/", "shared TypeScript interfaces"),
        "file": ("index.ts", "GraphData and friends"),
        "outline": [
            ("GraphData", "topology fields now typed arrays (nNodes/nEdges/coords/ends)", True),
            ("other interfaces", "unchanged", False),
        ],
        "blocks": [
            "GraphData — replace nodes/edges tuples with nNodes/nEdges/coords/ends (+ optional edgeNames)",
        ],
    },
    "client-react/src/utils/graphCache.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "IndexedDB persistence for topology + vote arrays, keyed by graph version"),
        "file": ("graphCache.ts", "~170 LOC — get/set cached topology (JSON + binary) and votes"),
        "outline": [
            ("DB open / upgrade", "DB_VERSION 2 → 3 clears the boxed-shape store", True),
            ("topology / votes cache", "unchanged API", False),
        ],
        "blocks": [
            "DB_VERSION = 3 — drop any pre-refactor JSON topology whose shape lacks coords/ends",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for vote application"),
        "file": ("voteApply.test.ts", "fixtures + 12 tests"),
        "outline": [("makeData / ADJ", "build typed-array topology + CSR adjacency", True)],
        "blocks": ["makeData via topologyFromJson; ADJ via buildNodeAdj"],
    },
    "client-react/src/components/GraphLayer/topProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for proposal selection"),
        "file": ("topProposals.test.ts", "fixtures + 26 tests"),
        "outline": [("topo() helper", "build GraphTopology from the legacy {nodes,edges} shape", True)],
        "blocks": ["topo() helper; selectTopProposals fixtures spread ...topo(...)"],
    },
}


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


def context_html(path):
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
        <span class="ctx-k">System</span><span class="ctx-v">{SYSTEM_NAME}</span>
        <span class="pills">{pills}</span>
      </div>
      <div class="ctx-tier ctx-mod">
        <span class="ctx-k">Module</span><span class="ctx-v">{html.escape(mod_label)}</span>
        <span class="ctx-sum">{html.escape(mod_sum)}</span>
      </div>
      <div class="ctx-tier ctx-file">
        <span class="ctx-k">File</span><span class="ctx-v">{html.escape(file_label)}</span>
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
  h3 {{ font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent); margin: 20px 0 6px; }}
  ul {{ margin: 6px 0 0; padding-left: 22px; }} li {{ margin: 6px 0; }}
  ul.files li {{ font-family: var(--font-mono); font-size: 12.5px; color: #33312b; }}
  code {{ font-family: var(--font-mono); font-size: 0.88em; background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }}
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
  .ctx-k {{ font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: #fff; background: var(--accent); border-radius: 4px; padding: 2px 6px; }}
  .ctx-mod .ctx-k, .ctx-file .ctx-k {{ background: #8a857a; }}
  .ctx-v {{ font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--ink); }}
  .ctx-sum {{ font-size: 12.5px; color: var(--muted); }}
  .pills {{ display: inline-flex; flex-wrap: wrap; gap: 5px; }}
  .pill {{ font-size: 11px; color: #8a857a; background: #fff; border: 1px solid var(--hairline); border-radius: 999px; padding: 1px 9px; }}
  .pill.on {{ color: #fff; background: var(--accent); border-color: var(--accent); font-weight: 600; }}
  .ctx-map {{ margin-top: 12px; padding-left: 36px; }}
  .ctx-map-title {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; display: flex; gap: 12px; align-items: center; }}
  .legend {{ font-size: 11px; text-transform: none; letter-spacing: 0; color: var(--muted); display: inline-flex; align-items: center; }}
  .sw-ch, .sw-dim {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .sw-ch {{ background: var(--accent); }} .sw-dim {{ background: #d6d1c5; }}
  ul.ctx-outline {{ list-style: none; margin: 0; padding: 0; border-left: 2px solid #e3ded2; }}
  ul.ctx-outline li {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin: 0; padding: 4px 0 4px 12px; position: relative; }}
  ul.ctx-outline li.changed {{ border-left: 3px solid var(--accent); margin-left: -2px; padding-left: 11px; background: #fff6ef; }}
  ul.ctx-outline li.dim {{ opacity: 0.62; }}
  .ol-label {{ font-family: var(--font-mono); font-size: 12.5px; color: var(--ink); }}
  li.changed .ol-label {{ font-weight: 700; color: var(--accent); }}
  .ol-sum {{ font-size: 12px; color: var(--muted); }}
  .ol-tag {{ font-size: 10px; letter-spacing: 0.06em; text-transform: uppercase; color: #fff; background: var(--accent); border-radius: 4px; padding: 1px 6px; }}
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
    <div class="dateline">{DATE} · branch <code>{BRANCH}</code></div>
  </header>
  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>
  <p class="lede">{LEDE}</p>
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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_report_mobile.py</code>.
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
