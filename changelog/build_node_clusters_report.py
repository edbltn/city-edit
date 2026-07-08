#!/usr/bin/env python3
"""Generate the junction-cluster blocks (round 2) changelog report.

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
            "At high zoom, intersections drew as 2–3 stacked circles (your screenshots on Flatbush Ave), "
            "and the street bands showed bite-marks that lined up with no visible circle."
        ),
        "cause": [
            "A physical intersection is MANY OSM junction nodes — centerline node, crossing ends, sidewalk "
            "corners: measured on the NYC walk graph, <strong>94.2%</strong> of junctions have another "
            "junction within 24 m (1.24M overlapping pairs), so one 12 m disc per node stacked several "
            "circles per corner.",
            "The “misaligned cutouts” were the SAME bug seen from the other side: bites cut by invisible "
            "sibling discs (unselected, transparent fill) of the same intersection — the notch never had a "
            "visible circle to line up with.",
        ],
        "fixes": [
            "Nodes and edges now have separate block-FORMING logic: each junction contributes a 9 m disc "
            "(down from 12 — “a bit smaller”), and discs whose centres sit closer than 2R union-find into "
            "one cluster; each cluster becomes ONE multi-node block (union of member discs, "
            "<code>n_nodes</code> property). NYC: 415,967 discs → <strong>99,535 cluster blobs</strong> "
            "(248,428 blocks total).",
            "Cluster sizes: p50 = 2 nodes, p99 = 19 (an intersection complex), 61 clusters &gt; 50. The 3 "
            "giants (up to 1,607 nodes, ~300×400 m) are real dense-plaza meshes — WTC/Oculus and Governors "
            "Island — where junctions genuinely sit &lt; 18 m apart everywhere.",
            "The blobs are subtracted from the street/foot polygons exactly as before, so blocks stay "
            "disjoint — but now the subtraction matches ONE drawn shape per intersection: street segments "
            "end at the blob rim, chain-link style.",
        ],
        "files": [
            "server/streetscape_blocks/build_node_blocks.py",
            "server/streetscape_blocks/README.md",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "capture",
        "tag": "Bake · junction capture",
        "title": "2 · Separate node/edge MAPPING logic — capture by graph distance",
        "symptom": (
            "Shrinking the drawn radius to 9 m would have re-admitted the ladder: crossing-stub midpoints "
            "run to ~12 m from the junction, past the new rim, so midpoint-containment would bake them back "
            "into perpendicular street blocks."
        ),
        "cause": [
            "The drawn polygon and the mapping rule were the same thing (containment), so the blob size had "
            "to serve two masters: small enough to look right, big enough to catch stubs.",
        ],
        "fixes": [
            "The bake now runs a pass-0 <strong>junction capture</strong> BEFORE containment: any edge whose "
            "midpoint lies within <code>NODE_CAPTURE_M</code> (12 m) of a junction maps to that junction's "
            "cluster block — by distance to the node, not polygon containment. "
            "<code>build_node_blocks.py</code> writes the junction→block sidecar "
            "(<code>node_clusters_&lt;network&gt;.npz</code>) the bake reads.",
            "Re-measured ladder metric: <strong>5,351 / 5,351</strong> midtown-avenue crossing stubs map to "
            "their junction's block — 100.0%, 0 perpendicular (was 99.9% under r=12 containment). The "
            "metric is now a reusable script: <code>eval/eval_ladder.py</code>.",
            "Final NYC bake is total: 3,299,152 / 3,299,152 mapped (2,073,106 junction-captured + 723,532 "
            "contained + 502,514 snapped), 0 unmapped.",
        ],
        "files": [
            "server/streetscape_blocks/build_edge_blocks.py",
            "server/streetscape_blocks/build_node_blocks.py",
            "server/streetscape_blocks/eval/eval_ladder.py",
        ],
    },
    {
        "id": "staleheat",
        "tag": "Client + server · stale heat",
        "title": "3 · “Nodes not on the heatmap” was three stale caches, not a paint bug",
        "symptom": (
            "Node blocks with positive votes stayed dark on the heatmap — the voted corridor drew as dashed "
            "street rectangles with black gaps at every intersection — while the same discs rendered fine "
            "when selected."
        ),
        "cause": [
            "Boot painted a PRE-re-bake vote body from IndexedDB: the votes cache is keyed by (slug, "
            "topology version) and the topology etag doesn't change on a blocks re-bake — the cached "
            "<code>block_votes</code> (old ids, 147,349 slots, no node blocks) painted streets and left "
            "every node block dark. Live WS deltas then layered onto the stale array (27 stale + 45 delta = "
            "the 72 we watched) instead of healing it.",
            "The authoritative refetch couldn't heal it either: <code>/api/graph-votes</code>' ETag is "
            "<code>v-&lt;slug&gt;-&lt;mode&gt;-&lt;rev&gt;</code> and rev doesn't bump on a re-bake — the "
            "browser revalidated, got a <strong>304</strong>, and reused the pre-re-bake body. Verified in "
            "the tab: in-page fetches returned 171 positive blocks while the app broadcast 72.",
            "A third copy hid under a drifted IndexedDB key (<code>votes:walkways</code> vs "
            "<code>votes:nyc-walkways</code> — the boot read resolves <code>getMapSlug() || themeMode</code> "
            "before the slug settles), so the fresh write never overwrote the stale entry.",
        ],
        "fixes": [
            "<code>votesMatchTopology</code> now also compares <code>n_blocks</code> against the topology, "
            "and the boot paint additionally requires the cached body's <code>blocks_version</code> to equal "
            "the live one from /graph-version — stale entries are ignored with a "
            "<code>[votes] ignoring cached votes: stale block set</code> warning instead of painted.",
            "The graph-votes ETag now carries a blocks stamp: <code>CityGraph.blocks_stamp()</code> — the "
            "baked npy's mtime, a stat, so the etag-before-ensure_loaded latency optimization survives — "
            "producing <code>v-…-16-b1783468989</code>. A re-bake busts every HTTP-cached vote body.",
            "Verified on a fresh boot: no stale broadcast, one heat broadcast of 117 blocks lit — exactly "
            "the server's count, node blocks included; the 6th Ave heat trail is a continuous chain "
            "(junction blobs lit between segments).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "server/app.py",
            "server/graph_registry.py",
        ],
    },
    {
        "id": "ops",
        "tag": "Pipeline ops",
        "title": "4 · Two pipeline landmines found the hard way",
        "symptom": (
            "One aborted run + one concurrent run corrupted blocks_generic_nyc.geojson (valid JSON followed "
            "by trailing garbage), and a re-run against the already-punched file clipped 0 street blocks — "
            "leaving 3 m dead rings between the old 12 m holes and the new 9 m blobs."
        ),
        "cause": [
            "Both generator scripts wrote through ONE shared <code>.tmp</code> path — a stopped run's "
            "surviving python interleaved with the new run's writer on the same file, and os.replace raced.",
            "<code>build_node_blocks.py</code> punches holes into the street features IN PLACE: it can drop "
            "stale node/foot FEATURES on re-run, but it cannot restore street geometry — so a radius change "
            "requires re-running from step 1's pristine output.",
        ],
        "fixes": [
            "Atomic writes hardened: pid-unique tmp names + explicit close in both generators.",
            "Recovered the pristine June-18 geojson from the 17:29 APFS local snapshot (Time Machine) — "
            "twice — and re-ran clean. The in-place gotcha is documented in the module docstring, README, "
            "and the project memory.",
        ],
        "files": [
            "server/streetscape_blocks/build_node_blocks.py",
            "server/streetscape_blocks/build_foot_blocks.py",
        ],
    },
]

VERIFY = [
    "Ladder metric (now <code>eval/eval_ladder.py</code>): 5,351 / 5,351 midtown-avenue crossing stubs → "
    "their junction's block; 0 perpendicular, 0 same-street leakage.",
    "Cluster stats: 99,535 blobs from 415,967 junctions (p50 = 2, p99 = 19 nodes); final bake total — "
    "3,299,152 / 3,299,152 edges (2,073,106 captured / 723,532 contained / 502,514 snapped, 0 unmapped); "
    "248,428 blocks.",
    "Fresh boot console: topology 248,428 blocks under the new <code>-bin2-7b5f927a521aeb95</code> cache "
    "key; NO stale vote broadcast; a single heat broadcast of 117 blocks lit = the server's positive count "
    "(57 node blocks among them).",
    "Live screenshots: the 6th Ave voted corridor renders as a continuous chain — lit street segments with "
    "lit junction blobs between them (the dashes are gone); the 7th Ave selection shows ONE knot per "
    "intersection, street bands ending exactly at the blob rims (no stacked circles, no orphan bites).",
    "graph-votes ETag observed as <code>\"v-nyc-walkways-walk-16-b1783468989\"</code> — the blocks stamp "
    "busts 304s after any re-bake.",
    "Suites green: client 191/191 (tsc clean), server unit 47/47.",
]

CHECKLIST = [
    "Reload your open tab WITHOUT clearing anything: the heat corridor on 6th Ave must render as a "
    "continuous chain (segments + junction blobs), not dashes — the stale caches should self-heal.",
    "Zoom into any intersection on Flatbush (your screenshot spot): ONE blob per intersection, no stacked "
    "circles, and every bite in the street band should trace the rim of that single blob.",
    "Cast + on a fresh route and watch the intersections light along with the segments in real time (the "
    "delta path now lands on cluster ids).",
    "Hover the WTC/Oculus area: expect a plaza-sized junction block (1,607-node cluster) — flag it if that "
    "grain bothers you; a max-extent split is the follow-up knob.",
    "If you ever change NODE_BLOCK_RADIUS_M, re-run from step 1 (build_blocks_generic) — the hole-punching "
    "is in-place and a smaller radius inside old holes leaves dead rings (documented gotcha).",
    "Other cities still predate junction blocks entirely — re-run ./build_city_blocks.sh &lt;city&gt; before "
    "deploying them.",
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
        "file": ("build_node_blocks.py", "~215 LOC — junction clusters: discs → union-find merge → clip → sidecar"),
        "outline": [
            ("module docstring", "cluster rationale + separate node/edge logic + in-place gotcha", True),
            ("junction_nodes", "unique-neighbour degree ≥ 3 (unchanged)", False),
            ("cluster_junctions", "NEW — union-find over cKDTree pairs < 2R", True),
            ("main — blobs", "9 m discs, union per cluster, n_nodes prop", True),
            ("main — clip + append + sidecar", "subtract blobs; write node_clusters_<network>.npz", True),
        ],
        "blocks": [
            "cluster_junctions — path-compressed union-find over query_pairs(r=2R)",
            "one blob per cluster: union_all(member discs) → road_class=node, node_id, n_nodes",
            "sidecar npz: node_idx[junctions] → block_id (the bake's capture table)",
            "pid-unique tmp + explicit close on the atomic write",
        ],
    },
    "server/streetscape_blocks/build_edge_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "the edge→block bake"),
        "file": ("build_edge_blocks.py", "~200 LOC — now a three-pass assignment"),
        "outline": [
            ("module docstring", "pass order: capture → containment → nearest", True),
            ("pass 0 — junction capture", "NEW — cKDTree midpoint→junction, ≤ NODE_CAPTURE_M (12 m)", True),
            ("pass 1 — containment", "unchanged logic, runs on the uncaptured remainder", True),
            ("pass 2 — nearest ≤ 30 m", "unchanged", False),
            ("meta", "captured_junction + node_capture_m stamped", True),
        ],
        "blocks": [
            "sidecar load + same local-metres frame as the generators",
            "capture: dist,idx = cKDTree(junctions).query(midpoints); cap = dist ≤ 12 → jn_block",
            "meta records captured/contained/snapped separately",
        ],
    },
    "server/streetscape_blocks/build_foot_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "foot fill for uncovered edges"),
        "file": ("build_foot_blocks.py", "~145 LOC — severed at junction blobs (unchanged rule, 9 m > 6 m)"),
        "outline": [
            ("disc subtraction", "now subtracts merged blobs (same road_class=node features)", False),
            ("atomic write", "pid-unique tmp + explicit close", True),
        ],
        "blocks": ["tmp = f\"{path}.tmp{os.getpid()}\" — no shared tmp path between sibling runs"],
    },
    "server/streetscape_blocks/eval/eval_ladder.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks/eval", "block-mapping evaluation harness"),
        "file": ("eval_ladder.py", "NEW ~95 LOC — the ladder metric as a reusable script"),
        "outline": [
            ("stub selection", "named-avenue midtown edges, junction endpoint, < 24 m", True),
            ("classification", "node block / same-street / PERPENDICULAR / other", True),
        ],
        "blocks": ["run after any bake: CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/eval/eval_ladder.py"],
    },
    "server/streetscape_blocks/README.md": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "generator docs"),
        "file": ("README.md", "adding-a-city + evaluation instructions"),
        "outline": [("pipeline paragraph", "clusters + capture rule documented", True)],
        "blocks": ["node blocks' separate MAPPING rule (capture ≤ 12 m) spelled out next to the 9 m drawn radius"],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("server · Flask app", "routes: votes, topology, tiles, maps"),
        "file": ("app.py", "~3.4k LOC — the API surface"),
        "outline": [
            ("graph_votes ETag", "blocks stamp folded into the validator (still no graph load)", True),
        ],
        "blocks": [
            "etag = v-<slug>-<mode>-<rev>-b<npy mtime> — a re-bake can never 304 a stale vote body again",
        ],
    },
    "server/graph_registry.py": {
        "on": ["Flask API"],
        "module": ("server · graph registry", "CityGraph loading + topology serving"),
        "file": ("graph_registry.py", "~260 LOC"),
        "outline": [
            ("blocks_stamp", "NEW — baked npy mtime via os.stat; usable before ensure_loaded", True),
        ],
        "blocks": ["keeps the etag-first / load-second latency optimization intact"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "topology + votes loading, selection, cards"),
        "file": ("GraphLayer.tsx", "~4k LOC — this commit touches only the vote-staleness guards"),
        "outline": [
            ("votesMatchTopology", "n_blocks joins the dimension check", True),
            ("boot cached-votes paint", "requires blocks_version match; warns + skips otherwise", True),
        ],
        "blocks": [
            "blocksMatch = cached.blocks_version === live blocks && votesMatchTopology(cached, topo)",
            "dwarn '[votes] ignoring cached votes: stale block set' on rejection",
        ],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("docs", "the source-of-truth spec"),
        "file": ("three-layer-model.md", "layer definitions + block-scoped vote semantics"),
        "outline": [
            ("§2.1 What a block is", "junction clusters + separate node/edge forming logic", True),
            ("§2.2 Generation", "cluster step + sidecar + capture-first bake order", True),
        ],
        "blocks": ["§2.2 item 4 now specifies the three-pass bake: capture → containment → nearest"],
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

  <p class="lede">Round two on junction blocks, driven by three live reports: intersections drew as
  stacked overlapping circles, the street bands showed bite-marks aligned with nothing visible, and voted
  node blocks stayed dark on the heatmap. The first two were one geometry bug — a physical intersection is
  many OSM junction nodes, each of which got its own disc — fixed by merging overlapping discs into one
  multi-node block per cluster (9&nbsp;m radius, union-find), with a new pass-0 <em>junction capture</em> in
  the bake so the ladder stays at zero (now 100.0%) while the drawn blobs shrink. The dark heatmap was no
  paint bug at all but three stacked stale caches — the IndexedDB vote body, a 304-pinned HTTP vote body
  (rev-only ETag), and a drifted cache key — each now guarded or version-stamped. Plus two pipeline
  landmines (shared .tmp corruption, in-place hole-punching) found the hard way and documented.</p>

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
