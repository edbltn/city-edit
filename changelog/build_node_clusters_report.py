#!/usr/bin/env python3
"""Generate the junction-node disc blocks changelog report.

Run from repo root: python changelog/build_node_blocks_report.py
Reads changelog/changes-node-clusters.diff
(captured with: git show 9729c70 --format="" > changelog/changes-node-clusters.diff),
writes changelog/2026-07-07-node-cluster-blocks.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-node-clusters.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-node-cluster-blocks.html")

DATE = "2026-07-07"
TITLE = "Junction clusters, capture mapping, and the stale-heat guards"


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
        "id": "clusters",
        "tag": "Blocks pipeline · merged clusters",
        "title": "1 · Overlapping discs merge into one block per junction cluster",
        "symptom": (
            "At high zoom, intersections drew as 2-3 STACKED circles, and the street bands showed bite "
            "marks that lined up with no visible circle (your two screenshots)."
        ),
        "cause": [
            "A physical intersection is MANY OSM junction nodes — centerline node, crossing ends, sidewalk "
            "corners: 94% of NYC's 415,967 junctions have another within 24 m (1.24M overlapping pairs). One "
            "12 m disc per node meant stacks of overlapping circles.",
            "The “misaligned cutouts” were the same bug seen from the other side: a street band's notch was "
            "cut by an INVISIBLE sibling disc of the same intersection (unselected blocks draw nothing), so "
            "the bite appeared to belong to nobody.",
        ],
        "fixes": [
            "Per your design: discs whose radii overlap map to the SAME block — union-find over centre pairs "
            "&lt; 2R, one merged blob per cluster (<code>road_class=\"node\"</code>, <code>n_nodes</code>), "
            "radius down from 12 m to <strong>9 m</strong>. NYC: 415,967 discs → <strong>99,535 cluster "
            "blocks</strong>; 248,428 blocks total.",
            "Whole-intersection blobs also fix the visual: one knot per intersection, and every cutout is "
            "filled by its own (selectable, lightable) blob.",
            "Streets were re-generated from the pristine step-1 output before re-punching — the previous 12 m "
            "holes would otherwise have left a 3 m dead ring around every 9 m blob (the in-place geojson edit "
            "is destructive; noted in the runbook).",
        ],
        "files": [
            "server/streetscape_blocks/build_node_blocks.py",
            "server/streetscape_blocks/README.md",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "capture",
        "tag": "Blocks pipeline · bake",
        "title": "2 · Nodes get their own mapping rule: 12 m junction capture",
        "symptom": (
            "Shrinking the drawn radius to 9 m would have brought the ladder back: crossing-stub midpoints "
            "run to 11.9 m from the junction (measured, midtown), so pure midpoint-containment would leak "
            "~4% of them into perpendicular street bands."
        ),
        "cause": [
            "One geometry was doing two jobs: the drawn block shape AND the mapping boundary. The user's "
            "third ask — separate mapping logic for nodes vs edges — is exactly the decoupling needed.",
        ],
        "fixes": [
            "The bake gains pass 0, JUNCTION CAPTURE: any edge whose midpoint lies within "
            "<code>NODE_CAPTURE_M</code> (12 m) of a junction maps to that junction's cluster block by GRAPH "
            "distance (cKDTree over junction centres), independent of the 9 m drawn rim. The generator writes "
            "a <code>node_clusters_&lt;network&gt;.npz</code> sidecar (junction node idx → block id) for it.",
            "Result on the re-bake: 2,073,106 edges junction-captured; 5,351/5,351 midtown-avenue crossing "
            "stubs land in junction blocks — <strong>0 perpendicular</strong>; the mapping is total "
            "(0 unmapped of 3.3M).",
            "Central Park keeps the per-segment grain (179 foot blocks + 1,643 junction blobs; largest foot "
            "block 5,186 m²).",
        ],
        "files": [
            "server/streetscape_blocks/build_edge_blocks.py",
            "server/streetscape_blocks/build_node_blocks.py",
        ],
    },
    {
        "id": "staleheat",
        "tag": "Client + server · stale heat",
        "title": "3 · “Nodes not appearing on the heatmap” was a stale-cache class, not rendering",
        "symptom": (
            "Voted junction blocks stayed dark on the heatmap — the corridor heat read as a dashed line with "
            "gaps at every intersection — even though the server's block_votes carried positive counts for "
            "them (verified: 111 positive node blocks in the payload while the map showed none)."
        ),
        "cause": [
            "The boot painted block heat from a PRE-RE-BAKE IndexedDB votes body: block ids renumber on every "
            "re-bake under the SAME topology etag and SAME revision, and the votes cache was validated by "
            "etag alone. The stale body (147,349 old block ids — node blocks didn't exist in it) painted "
            "streets correctly (street ids happened to be preserved) and nothing else.",
            "Live WS deltas then applied on TOP of the stale array instead of healing it (27 lit + 45 from "
            "the delta = the 72 we kept observing), and the day's graph-votes HTTP cache could 304 a client "
            "back onto the same stale body since the ETag was rev-scoped only.",
            "Diagnosis rabbit-holes worth recording: the tiles were fine (a dense z14 tile decodes 2,343 node "
            "features, zero coalesced), the render path was fine (a debug solid-fill layer painted 8,552 "
            "discs), and setFeatureState was fine — the array feeding it was simply from another block era.",
        ],
        "fixes": [
            "<code>votesMatchTopology</code> also rejects <code>block_votes.length ≠ topology.nBlocks</code>, "
            "and the boot cache path additionally requires <code>blocks_version</code> equality with the "
            "live <code>/api/graph-version</code> blocks hash (mismatch logs and falls through to the "
            "authoritative fetch).",
            "<code>/api/graph-votes</code>' ETag folds in a blocks mtime stamp "
            "(<code>graph_registry.blocks_stamp()</code> — a stat, no graph load), so a re-bake busts the "
            "HTTP validator even though rev didn't change.",
            "<code>build_foot_blocks</code> writes through a pid-unique tmp file: two pipeline runs fighting "
            "over one shared <code>.tmp</code> interleaved writes and corrupted the 300&nbsp;MB geojson "
            "mid-rebuild (NUL runs at byte 308,050,886 — found the hard way).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "server/app.py",
            "server/graph_registry.py",
            "server/streetscape_blocks/build_foot_blocks.py",
        ],
    },
]

VERIFY = [
    "Ladder metric on the re-baked mapping (now also a checked-in eval, "
    "<code>eval/eval_ladder.py</code>): 5,351/5,351 midtown-avenue crossing stubs → junction blocks, "
    "0 perpendicular, 0 unmapped city-wide.",
    "Cluster stats: 415,967 junctions → 99,535 cluster blobs (largest 1,607 nodes — a park mesh); "
    "248,428 blocks total; blocks.pmtiles 39.5 MB.",
    "Central Park bbox: 179 per-segment foot blocks (max 5,186 m²) + 1,643 junction blobs.",
    "Live on <code>/m/nyc-walkways</code>: the 7th Ave route selects a clean 94-block corridor with single "
    "knots at intersections (no stacked circles); the 6th Ave heat trail is CONTINUOUS — junction blobs lit "
    "between street segments where the dashes used to be (screenshots in-session).",
    "Boot staleness: fresh topology fetched under the new blocks hash (248,428 blocks logged), the earlier "
    "cast re-projected onto the new ids by the bver-marker bagg rebuild (route card still shows the +1, "
    "+ button pressed), and mode buckets self-heal (walkways=2 rebuilds on its next fetch).",
    "Suites green: server 47/47, client 195/195, tsc clean.",
]

CHECKLIST = [
    "Zoom deep into any Manhattan intersection: ONE merged knot, no stacked circles, and the street bands "
    "should end exactly at the knot's rim (no floating bite marks, no dead ring).",
    "Re-run your two screenshot views (Flatbush/Grand Army Plaza and the midtown corridor): both should "
    "read as single blobs per intersection.",
    "Cast + on a route, then Clear: the heat trail must be CONTINUOUS through intersections — lit junction "
    "knots between lit street segments, no dashes.",
    "Hard-reload once and watch the console: <code>[topo] ready … 248428 blocks</code>, and if an old votes "
    "body was cached you should see <code>ignoring cached votes: stale block set</code> once, then the "
    "authoritative heat.",
    "Hover a lit junction knot: the whole intersection highlights as one block; its tooltip sums the "
    "cluster's votes.",
    "Other cities still need <code>./build_city_blocks.sh &lt;city&gt;</code> re-runs before deploy (their "
    "bakes predate clusters + capture).",
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


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/streetscape_blocks/build_node_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "Layer-2 block generators: street Voronoi, junction clusters, foot fill, edge→block bake"),
        "file": ("build_node_blocks.py", "~200 LOC — one merged blob per junction cluster + the capture sidecar"),
        "outline": [
            ("module docstring", "REWRITTEN — separate node/edge block-forming logic", True),
            ("junction_nodes", "unique-neighbour degree ≥ 3 (unchanged)", False),
            ("cluster_junctions", "NEW — union-find over centre pairs < 2R", True),
            ("blob build", "NEW — union_all per cluster, r=9 m, n_nodes property", True),
            ("clip + append", "subtract blobs from street blocks; one feature per cluster", True),
            ("sidecar write", "NEW — node_clusters_<network>.npz (node_idx → block_id)", True),
        ],
        "blocks": [
            "cluster_junctions — path-compressed union-find over cKDTree.query_pairs(2R)",
            "per-cluster union_all of member discs; lowest member node idx as node_id",
            "np.savez sidecar aligned blob-order → first_blob_id + k",
        ],
    },
    "server/streetscape_blocks/build_edge_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "the edge→block bake"),
        "file": ("build_edge_blocks.py", "~180 LOC — now a three-pass assignment"),
        "outline": [
            ("docstring", "three passes documented", True),
            ("pass 0 — junction capture", "NEW — cKDTree over junction centres, ≤12 m → cluster block", True),
            ("pass 1 — containment", "unchanged logic, now only over uncaptured midpoints", True),
            ("pass 2 — nearest ≤30 m", "unchanged", False),
            ("meta", "captured_junction + node_capture_m stamped", True),
        ],
        "blocks": [
            "NODE_CAPTURE_M = 12 (env) — measured stub-midpoint p99 = 10.9 m, max 11.9 m",
            "capture: dist, j = cKDTree(jxy).query(midpoints, workers=-1); cap = dist <= 12",
            "meta counts: 2,073,106 captured + 723,532 contained + 502,514 snapped, 0 unmapped",
        ],
    },
    "server/streetscape_blocks/build_foot_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "foot-path fill for uncovered edges"),
        "file": ("build_foot_blocks.py", "~140 LOC"),
        "outline": [
            ("severing", "subtracts node blobs (9 m > 6 m tube) — unchanged behavior", False),
            ("atomic write", "pid-unique tmp file", True),
        ],
        "blocks": ["tmp = f\"{path}.tmp{os.getpid()}\" — concurrent runs can no longer interleave into one .tmp"],
    },
    "server/streetscape_blocks/README.md": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "generator docs"),
        "file": ("README.md", "adding-a-city + pipeline description"),
        "outline": [("pipeline paragraph", "clusters + capture rule documented", True)],
        "blocks": ["capture is called out as the ladder-holding rule, separate from the drawn 9 m rim"],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("server · Flask app", "routes: votes, topology, tiles, maps"),
        "file": ("app.py", "~3.3k LOC"),
        "outline": [
            ("graph_votes ETag", "now rev + blocks mtime stamp", True),
            ("bver bagg rebuild", "from the previous commit — unchanged here", False),
        ],
        "blocks": ["etag = v-<slug>-<mode>-<rev>-b<blocks_stamp> — a re-bake can never 304 old block ids"],
    },
    "server/graph_registry.py": {
        "on": ["Flask API"],
        "module": ("server · graph registry", "CityGraph: topology, blocks, binary blob"),
        "file": ("graph_registry.py", "~260 LOC"),
        "outline": [("blocks_stamp", "NEW — mapping npy mtime via stat, usable without loading the graph", True)],
        "blocks": ["blocks_stamp() → int mtime | None — the graph-votes validator's blocks component"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "topology load, votes, selection, cards"),
        "file": ("GraphLayer.tsx", "~4k LOC — this commit touches only the votes-cache guards"),
        "outline": [
            ("votesMatchTopology", "also rejects block_votes.length ≠ topology.nBlocks", True),
            ("boot cached-votes path", "requires blocks_version equality; dwarn + fall through when stale", True),
            ("markers / proposals", "unchanged in this commit", False),
        ],
        "blocks": [
            "n_blocks dimension guard — same class as the n_edges mobile-crash guard",
            "blocksMatch = cached.blocks_version === live blocks + votesMatchTopology(cached, topo)",
        ],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("docs", "the source-of-truth spec"),
        "file": ("three-layer-model.md", "layer definitions + block semantics"),
        "outline": [
            ("§2.1 What a block is", "merged multi-node junction blocks", True),
            ("§2.2 Generation", "cluster step + sidecar + capture pass", True),
        ],
        "blocks": [
            "§2.1 — separate block-forming logic for nodes (clustered discs) vs edges (segment Voronoi)",
            "§2.2 item 4 — the three-pass bake with junction capture first",
        ],
    },
    "server/streetscape_blocks/eval/eval_ladder.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks/eval", "mapping-quality harnesses"),
        "file": ("eval_ladder.py", "NEW — the ladder metric as a repeatable eval"),
        "outline": [("stub scan", "midtown avenue crossing stubs → block class distribution", True)],
        "blocks": ["run after any re-bake: PERPENDICULAR must be 0"],
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

  <p class="lede">Round two on junction blocks, driven by three live findings: intersections drew as
  stacked overlapping circles (a physical intersection is many OSM junction nodes — 94% of NYC's junctions
  have a neighbour within 24 m); the street-band cutouts seemed misaligned (they were bites from invisible
  sibling discs); and voted node blocks stayed dark on the heatmap. Per your design: overlapping discs now
  MERGE into one multi-node block per cluster at a smaller 9 m radius, and nodes get their own MAPPING rule —
  the bake captures any edge midpoint within 12 m of a junction into that junction's block by graph distance,
  which is what keeps the ladder at exactly zero while the drawn shape shrinks. The dark heatmap was the
  best find of the session: not rendering at all, but a stale-votes-cache class — block ids renumber on
  re-bake under the same topology etag AND revision, and three separate caches (IndexedDB body, WS deltas
  layering on it, and the rev-scoped HTTP ETag) all conspired to keep painting the previous block era.</p>

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
    Generated from <code>changelog/changes-routeprop-ui.diff</code> by <code>changelog/build_routeprop_ui_report.py</code>.
    Regenerate after further edits with
    <code>git diff 12475e6^ -- &lt;files&gt; &gt; changelog/changes-routeprop-ui.diff &amp;&amp; python changelog/build_routeprop_ui_report.py</code>.
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
