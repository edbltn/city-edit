#!/usr/bin/env python3
"""Generate the graph-first blocks changelog report (2026-07-08).

Run from repo root: python changelog/build_graph_first_report.py
Reads changelog/changes-graphfirst.diff (captured with:
  git add -N server/streetscape_blocks/build_blocks_graph_first.py &&
  git diff -- server/streetscape_blocks client-react/src/components/GraphLayer/GraphLayer.tsx \
    docs/three-layer-model.md > changelog/changes-graphfirst.diff),
writes changelog/2026-07-08-graph-first-blocks.html

Modeled on build_blockgraph_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-graphfirst.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-graph-first-blocks.html")

DATE = "2026-07-08"
TITLE = "Graph-first blocks — membership decides the polygons, and no point on the map is a dead zone"


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


SECTIONS = [
    {
        "id": "builder",
        "tag": "Pipeline · NEW single-pass builder",
        "title": "1 · Blocks are built graph-first — coverage and edge∩polygon overlap by construction",
        "symptom": (
            "On the central-park test map the heatmap had gaps — voted edges whose block never existed "
            "(2,450 unmapped edges, 3.4%) — and many edges/nodes mapped to polygons they didn't even "
            "touch (10,336 edges assigned by “nearest polygon within 30 m”)."
        ),
        "cause": [
            "The five-script pipeline generated polygons FIRST (drive-centerline Voronoi + junction cells "
            "+ foot tubes) and then mapped edges INTO them geometrically (containment → nearest-within-30m). "
            "Whatever the geometry passes couldn't claim stayed unmapped (gap), and whatever only "
            "nearest-snap could claim landed in a polygon up to 30 m away (mismatch). The two defects are "
            "structural: geometry and membership were computed independently.",
        ],
        "fixes": [
            "New <code>build_blocks_graph_first.py</code> replaces the whole chain "
            "(generic → node cells → bake → foot → bake → merge) with ONE pass that inverts the premise: "
            "<strong>membership first, topologically, for every edge</strong> — junction clusters "
            "(deg ≥ 3 nodes union-found within 18 m, oversized clusters principal-axis-bisected), "
            "same-cluster edges → that junction, ≤ 30 m cross-cluster crosswalk stubs → the nearer one, "
            "everything else → corridors (components linked through non-junction endpoints).",
            "The degenerate cases the old merge pass fixed post-hoc run as a topological fixpoint before "
            "any geometry: driveway-class stubs (≤ 25 m, one junction) melt into their junction; a "
            "junction touching ≤ 1 corridor isn't a junction and dissolves into it.",
            "<strong>Geometry is generated FROM the groups</strong>: a junction cell is the union of 8 m "
            "discs at its member nodes plus its captured edges' tubes; a corridor is the union of its "
            "member edges buffered by per-road-class half-width, minus the junction cells (junctions win). "
            "A corridor edge left entirely inside junction cells is reassigned to the cell containing its "
            "midpoint — membership follows the final geometry.",
            "A build-time audit proves both invariants and stamps them into the meta: test-cp bakes "
            "<strong>72,924/72,924 edges mapped (100%)</strong> and <strong>72,924/72,924 member edges "
            "touching their polygon (100%)</strong> — was 96.6% / 85.5%. Runs in the server venv alone "
            "(no osmnx/geopandas geo venv needed).",
            "First iteration used the old clipped-Voronoi discs for junction cells and audited at 85.5% "
            "overlap — the radius cap + bisector clipping cut members out of their own cells. That measured "
            "failure is what drove cells to be generated from their members too.",
            "<strong>Field-caught follow-up (Eric's screenshot at Terrace/West Drive):</strong> a junction "
            "cluster that captures NO edges (a simple path fork — all incident edges belong to corridors) "
            "used to still get a cell polygon. An edge-less cell can never hold or display a vote, so the "
            "pin's tooltip said “No votes yet” while the selection ring and proposal card belonged to a "
            "corridor outside the blob. Such clusters (484 on test-cp) now get no cell — the corridors' own "
            "tubes cover the fork, so the junction area lights with whichever corridor actually holds the "
            "votes, and hovering there resolves inside the polygon you're pointing at. Zero edge-less "
            "blocks remain (counted as <code>empty_junctions_skipped</code> in the meta).",
        ],
        "files": [
            "server/streetscape_blocks/build_blocks_graph_first.py",
            "server/streetscape_blocks/build_city_blocks.sh",
        ],
    },
    {
        "id": "equiv",
        "tag": "Pipeline · equivalence fixpoint + cell shapes",
        "title": "2 · Parallel corridors and equivalent junctions merge; cells are hulls with Voronoi boundaries",
        "symptom": (
            "On the street grid every segment rendered as TWO parallel sidewalk corridors (plus the "
            "roadway), and every intersection drew as a bulbous 4-lobed “clover” — the union of 8 m discs "
            "at each corner node (Eric's 5th Avenue screenshot)."
        ),
        "cause": [
            "Corridors were keyed only by connectivity, so each sidewalk of a street was its own block; "
            "junction-cell geometry was a per-node disc union, which reads as stacked circles wherever a "
            "cluster has several member nodes.",
        ],
        "fixes": [
            "The degeneracy fixpoint gained two equivalence rules, iterated with V1/V2 to convergence "
            "(each rule strictly shrinks the corridor or cluster count, so it terminates; one O(E) "
            "incidence sweep per round, frozenset-signature dict grouping — no pairwise scans): "
            "<strong>A</strong> corridors with the SAME two endpoint clusters merge (both sidewalks + "
            "roadway of one street segment become ONE block — 4,843 merged on test-cp, 4 rounds); "
            "<strong>B</strong> clusters with the SAME incident-corridor set (≥ 2 corridors) merge (an "
            "over-split intersection becomes one junction; 0 fired on test-cp — the splitter didn't "
            "over-split here, but A→B→A feedback is what the loop is for).",
            "Junction cells are now the CONVEX HULL of the member nodes (⊕ 8 m pad) unioned with the "
            "captured edges' tubes — one compact intersection footprint instead of the clover — and "
            "overlapping cells are cut at the perpendicular bisector of their member centroids "
            "(Voronoi-style shared boundaries, 889 trims). A cut that would evict a member node or detach "
            "a captured edge is skipped: the 100% audit invariants win over aesthetics.",
            "test-cp: 9,552 → 4,849 blocks (3,461 corridors + 1,388 junction cells), audit still "
            "100% mapped / 100% edge∩polygon.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "ids",
        "tag": "Pipeline · feature ids",
        "title": "3 · block_ids are 1-based — MVT can't carry feature id 0",
        "symptom": (
            "tippecanoe warned <em>“Can't represent too-large feature ID 0”</em> and dropped the native "
            "id from block 0's tile feature — so block 0 could never light (heat) or ring (selection): "
            "MapLibre feature-state attaches by native id."
        ),
        "cause": [
            "The old generators numbered blocks from 0. The Mapbox-Vector-Tile spec makes <code>id</code> "
            "an optional field where 0 is indistinguishable from unset, so tippecanoe refuses it. Every "
            "prior block set shipped one silently state-less block.",
        ],
        "fixes": [
            "The builder numbers blocks from 1 and sizes <code>n_blocks = len(features) + 1</code> "
            "(slot 0 unused). Server vote arrays and the client's typed-array mapping index by block id, "
            "so both stay dense.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "resolver",
        "tag": "Client · hover/click/drag",
        "title": "4 · Every hover and click resolves to the closest node/edge and its block",
        "symptom": (
            "Gaps you could hover or click with nothing appearing selected: wherever the cursor wasn't "
            "over a block polygon, hover/click fell back to a 4px radius-bounded hit-test — so any hole "
            "in block coverage (and any point a few pixels off a thin path) was a dead zone."
        ),
        "cause": [
            "<code>resolveDragSnap</code> — the shared hover/click/drag resolver — was deliberately gated: "
            "over a block polygon it used the full always-resolve hierarchy, off-polygon it used the "
            "radius-bounded <code>hitTest</code>, and far from everything it returned null so clicks "
            "placed free (unsnapped) waypoints. With imperfect block coverage that design turned every "
            "coverage hole into an interaction hole.",
        ],
        "fixes": [
            "<code>resolveDragSnap</code> now IS <code>resolveSelection</code>: sticky proposal first, "
            "then the block polygon under the point constrains the search to that block's members, "
            "otherwise the closest node/edge resolves uncapped. Hover, click/pin, the registered snapFn, "
            "the drop preview and the live trail all share it, so hover still shows exactly what a click "
            "selects — there are just no dead zones anymore.",
            "The now-unused <code>SNAP_EDGE_PX</code> constant is gone (hitTest's radius default is "
            "Infinity; the parameter remains for radius-bounded callers).",
            "Verified with a 280-point grid sweep over the live viewport via <code>cityedit.resolveAt</code>: "
            "280/280 points resolve to a target, 0 cases where the vote edge's block disagrees with the "
            "hovered polygon.",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "docs",
        "tag": "Docs",
        "title": "5 · three-layer-model.md §2.1–2.2 describe the graph-first pipeline",
        "symptom": (
            "The spec still described the five-script generate→map→patch→merge chain (and a 12 m "
            "midpoint-capture rule that predated even the previous redesign)."
        ),
        "cause": ["Doc drift."],
        "fixes": [
            "§2.1 states the by-construction invariants (total mapping, 100% edge∩polygon overlap); "
            "§2.2 documents the one-pass builder, the 1-based ids, and marks the old scripts as "
            "reference-only.",
        ],
        "files": ["docs/three-layer-model.md"],
    },
]

VERIFY = [
    "Four build iterations on <code>test-cp</code>: v1 (clipped-Voronoi junction cells) audited "
    "100% mapped / <strong>85.5%</strong> overlap → v2 (cells from member discs + captured tubes, "
    "corridor-edge reassignment) → <strong>100% / 100%</strong> → v3 (junction disc radius 10 m → 8 m "
    "after visual review; still 100% / 100%) → v4 (edge-less junction clusters get no cell; still "
    "100% / 100%, 9,552 blocks). Audit numbers are stamped in "
    "<code>edge_blocks_streets.json</code> (<code>edges_overlap_ok / edges_overlap_checked</code>).",
    "Equivalence round (request 3): fixpoint converges in 4 rounds on test-cp — 4,843 parallel corridors "
    "merged, 889 cells bisector-trimmed, 9,552 → 4,849 blocks, audit still 100% / 100%; street-grid and "
    "park-interior geometry rendered offline (matplotlib over <code>blocks_final</code>) — one solid band "
    "per street segment, compact hull cells, no clovers; Flask serves n_blocks 4,850 with 1,995 blocks lit.",
    "Empty-cell fix verified at the reported fork (40.76830, −73.97823, node 5979): before — pin inside "
    "block 8746 (<code>road_class=node, n_edges=0</code>), tooltip “No votes yet”, ring on a corridor "
    "outside the blob; after — the point sits inside the three corridor tubes (513/519/541) that own the "
    "fork's edges, zero <code>n_edges=0</code> features in the whole set, and the pin's card shows the "
    "fork's proposal with its votes.",
    "tippecanoe runs clean after the 1-based ids (the <em>“Can't represent too-large feature ID 0”</em> "
    "warning is gone); a decoded z15 tile shows every feature carrying its native id == block_id.",
    "Flask restarted on the new artifacts: <code>/api/graph-votes?map=test-central-park</code> serves "
    "<code>n_blocks 10035</code>, new <code>blocks_version 4e80935f…</code>, and the block aggregate "
    "auto-rebuilt (2,199 blocks lit from existing votes — every voted edge now has a lit block).",
    "Browser (the request's URL): heat renders as continuous corridor tubes + junction cells along "
    "voted routes; console <code>[topo] ready … 9948 blocks</code> → 10035 after the radius iteration.",
    "Grid sweep over the live viewport (<code>cityedit.resolveAt</code>, 20×14 points): 280/280 resolve, "
    "0 hovered-block vs vote-edge-block mismatches.",
    "Click tests in former dead zones (dark gaps between polygons): both clicks selected the nearest "
    "corridor — white highlight ring + <code>[blocks] select: [189]</code> / <code>[189,1078]</code> — "
    "and opened the proposal card.",
    "Client suite 231/231 green, <code>tsc --noEmit</code> clean.",
    "Visual pass was cut short by the occluded-window rAF trap (Chrome kept re-hiding the tab), so the "
    "z18 close-up aesthetics check is on the manual checklist.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/test-central-park?z=18&lat=40.77461&lng=-73.96947&slat=40.77374&slng=-73.96927&vt=Improve+sidewalk'>"
    "the central-park URL from the request</a>: every voted path segment should show a heat polygon — "
    "no dark holes along a voted route between junction cells.",
    "Hover slowly across a junction and along a path: something is ALWAYS highlighted (edge, node, or a "
    "junction's members), and clicking pins exactly what hover showed — including in the dark areas "
    "between polygons.",
    "Check polygons hug their paths: a corridor's tube should trace its own edges (no polygon lighting "
    "for a path 20–30 m away), and junction blobs should sit ON the fork, sized ~like the path width.",
    "Cast a vote on a previously-unvoted path: the new block lights immediately and the modal's block "
    "rows match.",
    "If junction blobs still read too big/small at z18, re-run with "
    "<code>NODE_BLOCK_RADIUS_M=&lt;m&gt; ./streetscape_blocks/build_city_blocks.sh test-cp</code> "
    "(then restart Flask) — the whole rebuild is ~30 s.",
    "When happy, rebuild the real cities with the same command (nyc will take a few minutes; deploys "
    "need the resnap-on-deploy runbook as usual).",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def file_detail_html(name, chunk, open_=False):
    added = sum(1 for ln in chunk.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in chunk.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return f"""
        <details{" open" if open_ else ""}>
          <summary><span class="fname">{html.escape(name)}</span>
            <span class="stat"><span class="d-add">+{added}</span> / <span class="d-del">-{removed}</span></span>
          </summary>
          {context_html(name)}
          <pre class="diff">{colorize_diff(chunk, name)}</pre>
        </details>
        """


def section_html(s, diff_by_file):
    file_rows = []
    for f in s["files"]:
        chunk = diff_by_file.get(f)
        if chunk is not None:
            file_rows.append(file_detail_html(f, chunk))
        else:
            file_rows.append(f'<ul class="files"><li>{html.escape(f)}</li></ul>')
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
      <h3>Diffs — files touched (click to expand)</h3>
      {''.join(file_rows)}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · THE builder (new)", "one pass: topological grouping → polygons from the groups → baked mapping"),
        "file": ("build_blocks_graph_first.py", "~430 LOC — clusters, grouping, fixpoint, geometry, audit, emit"),
        "outline": [
            ("Constants", "cluster link/extent · NODE_R_M 8 · capture 30m · stub 25m · class half-widths", True),
            ("UnionFind / split_oversized", "principal-axis bisection of >40m clusters (from build_node_blocks)", True),
            ("1 · junction clusters", "deg≥3 union-find within 18m", True),
            ("2 · edge grouping (total)", "same-cluster · ≤30m crosswalk stubs · corridors via non-junction endpoints", True),
            ("3 · degeneracy fixpoint", "V1 stubby corridors → junction; V2 ≤1-corridor junctions dissolve", True),
            ("4 · geometry from membership", "cells = member discs ∪ captured tubes; corridors = member tubes − cells; reassign eaten edges", True),
            ("5 · emit (1-based ids)", "blocks_final geojson + edge_blocks npy + meta", True),
            ("6 · audit", "100% mapped + 100% edge∩polygon, stamped into the meta", True),
        ],
        "blocks": [
            "edge grouping — every non-self edge gets a group BEFORE any geometry exists",
            "fixpoint — the old merge pass's V1/V2, run topologically pre-geometry",
            "tube_of/cells — polygons generated from exactly the member edges/nodes",
            "reassignment — a corridor edge fully eaten by junction cells moves to the midpoint cell",
            "audit — poly.distance(edge line) ≤ ε for every mapped edge, counts in the meta",
        ],
    },
    "server/streetscape_blocks/build_city_blocks.sh": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · orchestrator", "one-command Layer-2 build for a city"),
        "file": ("build_city_blocks.sh", "7 steps → 2"),
        "outline": [
            ("Step 1", "build_blocks_graph_first.py (server venv — geo venv requirement dropped)", True),
            ("Step 2", "tippecanoe from blocks_final_<city>.geojson (unchanged flags)", True),
        ],
        "blocks": [
            "five old steps (generic/node/bake/foot/bake/merge) replaced by the single builder",
            "no geo-venv check, no foot-sidecar hygiene — those artifacts no longer exist",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "graph rendering, hit-testing, hover/click/vote resolution"),
        "file": ("GraphLayer.tsx", "~4,780 LOC — the resolver seam is ~40 of them"),
        "outline": [
            ("Constants", "SNAP_EDGE_PX deleted (dead)", True),
            ("hitTest", "radius default → Infinity; docstring updated", True),
            ("resolveSelection", "unchanged — block-constrained, always resolves", False),
            ("resolveDragSnap", "now delegates to resolveSelection — no off-polygon radius gate", True),
            ("drop-preview / hover / snap comments", "reflect always-resolve semantics", True),
        ],
        "blocks": [
            "resolveDragSnap — one-line delegation; hover=click=drag parity preserved, dead zones removed",
            "hover handler comment — hover shows exactly what a click would select, everywhere",
        ],
    },
    "docs/three-layer-model.md": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("docs", "the Layer-1/2/3 source-of-truth spec"),
        "file": ("three-layer-model.md", "§2.1 what a block is · §2.2 generation"),
        "outline": [
            ("§2.1", "corridor+junction blocks; by-construction invariants", True),
            ("§2.2", "graph-first one-pass pipeline; 1-based ids; old scripts reference-only", True),
        ],
        "blocks": [
            "§2.2 rewritten around build_blocks_graph_first.py's five numbered stages",
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
    diff_by_file = dict(files)
    claimed = {f for s in SECTIONS for f in s["files"]}
    leftover_blocks = "".join(
        file_detail_html(name, chunk) for name, chunk in files if name not in claimed
    )
    leftover_html = f"""
  <section id="diff">
    <h2 style="font-family:var(--font-ed);font-size:24px;margin:32px 0 10px;">Other files in this diff</h2>
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Changes not tied to a section above.</p>
    {leftover_blocks}
  </section>""" if leftover_blocks else ""
    diff_link = '<a href="#diff">Other diffs</a>' if leftover_blocks else ""

    sections_html = "\n".join(section_html(s, diff_by_file) for s in SECTIONS)
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
    border-top: 1px solid var(--hairline); font-family: var(--font-mono); font-size: 12px; line-height: 1.25; }}
  pre.diff span {{ display: block; white-space: pre; }}
  pre.diff span span {{ display: inline; }}
{SYNTAX_CSS}

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
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a>{diff_link}
  </nav>

  <p class="lede">The central-park test map exposed two structural defects in Layer-2 blocks: heatmap gaps
  (voted edges whose polygon never existed) and edges mapped to polygons they don't even touch. Both came
  from the same premise — polygons were generated first (drive-centerline Voronoi) and edges were mapped
  into them geometrically afterwards. A new one-pass builder inverts that: every edge is grouped
  topologically FIRST (junction clusters + corridors severed at junctions), and each block's polygon is
  generated from exactly its own members — so 100% coverage and 100% edge∩polygon overlap hold by
  construction, audited at build time. Block ids became 1-based because MVT drops feature id 0 (block 0
  could never light or select). And the client's shared hover/click/drag resolver lost its off-polygon
  4px radius gate: every hover and click now maps to the closest node/edge and its encompassing block —
  no dead zones. The old five-script pipeline runs no more; the build is one script + tippecanoe, in the
  server venv alone.</p>

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

{leftover_html}

  <footer>
    Generated from <code>changelog/changes-graphfirst.diff</code> by <code>changelog/build_graph_first_report.py</code>.
    Regenerate after further edits with
    <code>git diff -- server/streetscape_blocks client-react/src/components/GraphLayer/GraphLayer.tsx docs/three-layer-model.md &gt; changelog/changes-graphfirst.diff &amp;&amp; python changelog/build_graph_first_report.py</code>.
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
