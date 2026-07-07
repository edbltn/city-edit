#!/usr/bin/env python3
"""Generate the junction-node disc blocks changelog report.

Run from repo root: python changelog/build_node_blocks_report.py
Reads changelog/changes-node-blocks.diff
(captured with: git show deac972 --format="" > changelog/changes-node-blocks.diff),
writes changelog/2026-07-07-node-block-discs.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-node-blocks.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-node-block-discs.html")

DATE = "2026-07-07"
TITLE = "Junction-node disc blocks — killing the cross-street ladder"


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
        "id": "ladder",
        "tag": "Blocks pipeline · node discs",
        "title": "1 · Every junction is its own block — the ladder dies at the bake",
        "symptom": (
            "Casting a route down an avenue selected — and block-scoped casting voted on — every "
            "perpendicular cross street it passed: ladder-shaped selections (see the 7th/6th-Ave screenshot "
            "that motivated this). Wrong-orientation blocks got real votes, not just a wrong highlight."
        ),
        "cause": [
            "Street block polygons extend ACROSS intersections: the segment-Voronoi assigns each "
            "intersection's flare to ONE of the crossing segments (that's also why the tuned half-widths "
            "outsize a naive area/length calibration — COMPARISON.md).",
            "The walk graph splits avenue edges at crossings, so a route carries short stubs through every "
            "intersection. A stub's midpoint lands in the flare — i.e. inside the PERPENDICULAR street's "
            "polygon — so <code>build_edge_blocks.py</code> baked it into the cross street's block.",
            "Selection and casting are block-scoped (docs §2.4, §4): one contaminated stub pulls the whole "
            "cross-street block into every route selection that passes it.",
        ],
        "fixes": [
            "New <code>build_node_blocks.py</code>: every walk-graph junction (unique-neighbour degree ≥ 3 — "
            "geometry nodes and dead ends stay in their street block) becomes a 12 m disc block "
            "(<code>road_class=\"node\"</code>, 16-gon, <code>NODE_BLOCK_RADIUS_M</code> env knob). 12 m ≈ the "
            "client's 3 px node hover/snap affordance at z≈15, and covers NYC intersection interiors.",
            "The discs are SUBTRACTED from every street block they touch, so blocks stay disjoint and the "
            "bake's midpoint-containment stays unambiguous. Crossing stubs now land in the disc, not the "
            "cross street.",
            "Measured on the re-baked NYC mapping: of 5,351 crossing stubs on midtown avenues, "
            "<strong>0</strong> map to a perpendicular street block (99.9% → their junction's disc, "
            "0.1% → their own street's block).",
        ],
        "files": [
            "server/streetscape_blocks/build_node_blocks.py",
            "server/streetscape_blocks/build_city_blocks.sh",
            "server/streetscape_blocks/README.md",
        ],
    },
    {
        "id": "centralpark",
        "tag": "Blocks pipeline · foot blocks",
        "title": "2 · Central Park: from union-find blobs to per-segment blocks",
        "symptom": (
            "Central Park (and every park/plaza network) chunked into a handful of GIANT blocks — hovering "
            "one path segment selected half the park. Visibly different logic than the street grid."
        ),
        "cause": [
            "It literally was different logic: streets get one block per segment via segment-Voronoi, but "
            "<code>build_foot_blocks.py</code> buffered the uncovered foot edges by 6 m and "
            "<code>union_all</code>-ed them — one block per CONNECTED component. A park's path network is one "
            "connected component, so the whole park merged.",
        ],
        "fixes": [
            "The foot builder now subtracts the junction discs from the merged mesh before splitting into "
            "parts. Disc radius (12 m) > tube radius (6 m), so the mesh SEVERS at every junction: one block "
            "per path segment between junctions — the same grain as streets.",
            "Central Park went from a few giant blobs to <strong>163 per-segment foot blocks</strong> "
            "(largest 5,106 m², median 626 m²) + 7,357 junction discs.",
            "City-wide re-bake: 563,812 blocks (138,346 street + 415,967 discs + 9,499 foot), "
            "3,299,150 / 3,299,152 edges mapped (2 degenerate leftovers).",
        ],
        "files": [
            "server/streetscape_blocks/build_foot_blocks.py",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "staleness",
        "tag": "Server + client · cache staleness",
        "title": "3 · A re-baked block set can no longer serve stale derived state",
        "symptom": (
            "Re-baking blocks against the SAME graph left three caches silently stale: the client's GTB2 "
            "topology blob (IndexedDB + day-long HTTP cache, keyed only by the topology etag), the Redis "
            "block-vote aggregate (bd:/bagg: keyed by now-renumbered block ids), and blocks.pmtiles "
            "(week-long HTTP cache under a fixed URL — mixed old/new byte ranges)."
        ),
        "cause": [
            "The blocks artifacts are versioned separately (blocks_sha256) but nothing downstream consumed "
            "that version: /api/graph-version reported only the topology etag, the bagg rebuild only fired "
            "when the key was MISSING, and the tiles URL carried no version at all.",
        ],
        "fixes": [
            "<code>/api/graph-version</code> now carries <code>blocks</code> (blocks_sha256); the client folds "
            "it into the blob cache key + URL buster (<code>-bin2-&lt;blocks&gt;</code>), mirroring the "
            "server's bin ETag which now carries the same suffix.",
            "graph-votes rebuilds bd:/bagg: from Postgres when a <code>bver:&lt;slug&gt;:&lt;mode&gt;</code> "
            "marker mismatches the live blocks_version — verified live: first request after the re-bake "
            "rebuilt 634 blocks' worth of existing votes onto the new ids.",
            "<code>cities.to_public</code> ships <code>blocksVersion</code> (blocks.pmtiles mtime — a stat, no "
            "graph load) and <code>applyCityConfig</code> appends <code>?v=</code> to the tiles URL "
            "(verified in the live tab: <code>blocks.pmtiles?v=1783465143</code>).",
        ],
        "files": [
            "server/app.py",
            "server/cities.py",
            "client-react/src/config.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "adjshortest",
        "tag": "Client · node→block resolution",
        "title": "4 · Nodes resolve to their disc via the shortest incident edge",
        "symptom": (
            "With discs in place, a hovered/pinned NODE could still highlight (and vote into) a full street "
            "block: the node→edge upgrade rule was “first incident edge by array order” — arbitrary, and "
            "usually a long street edge whose midpoint sits mid-block."
        ),
        "cause": [
            "<code>adjFirst</code> picked the lowest edge id among the node's incident edges; which block that "
            "lands in is an accident of edge numbering.",
        ],
        "fixes": [
            "New <code>adjShortest</code> (graphTopology.ts, +2 unit tests): the SHORTEST incident edge — "
            "cos-corrected straight-line — is the one whose midpoint falls inside the junction's own disc, so "
            "node targets resolve to the disc block. Swapped in at all four call sites: selection resolver "
            "(voteEdgeId), block-select broadcast, hover tooltip rows, pinned-card vote edge.",
            "docs §2.1 updated: a node belongs to its shortest incident edge's block (the adjShortest rule).",
            "Verified live: hovering an off-route intersection lights a disc-sized ring — no street-length "
            "band; the 7th Ave route corridor selects clean (screenshots), vs the ladder in the motivating "
            "screenshot.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/graphTopology.ts",
            "client-react/src/components/GraphLayer/graphTopology.test.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "docs/three-layer-model.md",
        ],
    },
]

VERIFY = [
    "Offline ladder metric on the re-baked mapping: 5,351 intersection-crossing stubs on midtown avenues "
    "(named *Avenue, junction endpoint, &lt;24 m) — 99.9% map to their junction's disc, 0.1% to their own "
    "street, <strong>0</strong> to a perpendicular street block.",
    "Central Park bbox scan: 163 foot blocks (largest 5,106 m², median 626 m²) + 7,357 node discs — "
    "previously a handful of park-sized union blobs.",
    "Final bake is total: 3,299,150 / 3,299,152 edges mapped (563,812 blocks); tippecanoe kept the discs "
    "at max zoom — a dense midtown z14 tile decodes with 2,025 node features carrying native block ids.",
    "Live on <code>/m/nyc-walkways</code> (debug tab): a W 58th → W 12th route down 7th Ave selects ONE "
    "clean corridor — no ladder rungs (screenshots before/after zoom); hovering an off-route intersection "
    "lights a disc-sized ring, not a cross-street band.",
    "Staleness plumbing observed live: fresh topology fetched under the new "
    "<code>-bin2-cac9f883edd12ab6</code> key (client logged 563,812 blocks), bagg auto-rebuilt onto new "
    "block ids (634 blocks lit from existing votes, <code>bver</code> marker set), tiles fetched as "
    "<code>blocks.pmtiles?v=1783465143</code>.",
    "<code>npx tsc --noEmit</code> clean; graphTopology + blockSelection suites green (24/24, incl. 2 new "
    "adjShortest specs); server unit suite green (47/47).",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>http://localhost:3000/m/nyc-walkways</a>, set a "
    "start at W 58th St &amp; 7th Ave and an end at W 12th St: the selected corridor must hug 7th Ave with "
    "NO cross-street rungs (compare your ladder screenshot).",
    "Zoom into any intersection on the route: a small disc should read selected at the junction, with the "
    "street blocks ending at its rim (no overlap).",
    "Pan to Central Park and hover a path: the highlight should cover ONE path segment between junctions, "
    "not half the park.",
    "Hover an intersection node off the route: a disc-sized highlight, and its tooltip counts come from the "
    "disc block; click it and cast — the vote should land on the disc, not a street.",
    "Hard-reload once: the console <code>[topo] ready</code> line must say 563812 blocks (stale-cache "
    "guard), and the network tab must show <code>blocks.pmtiles?v=…</code>.",
    "For other cities (philly/sf/dc/chicago), re-run <code>./build_city_blocks.sh &lt;city&gt;</code> before "
    "their next deploy — their baked mappings predate the disc step.",
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
        "module": ("server · streetscape_blocks", "Layer-2 block generators: street Voronoi, junction discs, foot fill, edge→block bake"),
        "file": ("build_node_blocks.py", "NEW ~144 LOC — one disc block per walk-graph junction, punched out of street blocks"),
        "outline": [
            ("module docstring", "why discs exist (the ladder) + pipeline position", True),
            ("junction_nodes", "unique-neighbour degree ≥ 3 over the walk graph (vectorized)", True),
            ("main — disc build", "12 m discs in the local-metres frame (same frame as the foot builder)", True),
            ("main — clip + append", "STRtree subtract from street blocks; append road_class=node features", True),
        ],
        "blocks": [
            "junction_nodes — np.unique over sorted undirected pairs → bincount degree",
            "disc build — shp_buffer(points, 12, quad_segs=4), transformed back to lon/lat",
            "clip — tree.query(street_polys, 'intersects') grouped per block → shapely.difference",
            "append — block_ids continue after streets; node_id property kept for debugging",
        ],
    },
    "server/streetscape_blocks/build_foot_blocks.py": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "Layer-2 block generators"),
        "file": ("build_foot_blocks.py", "~140 LOC — buffers the edges no street block covers; makes the mapping total"),
        "outline": [
            ("module docstring", "REWRITTEN — severing rationale (12 m disc > 6 m tube)", True),
            ("edge buffering", "6 m round-cap buffers over unmapped edges, union_all", False),
            ("disc subtraction", "NEW — subtract road_class=node features before get_parts", True),
            ("append", "road_class=foot features, continuing block_ids", False),
        ],
        "blocks": [
            "loads the blocks geojson BEFORE splitting so the discs can cut the merged mesh",
            "shapely.transform of the lon/lat discs into the same local-metres frame → difference(merged, union)",
        ],
    },
    "server/streetscape_blocks/build_city_blocks.sh": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "one-command per-city Layer-2 build"),
        "file": ("build_city_blocks.sh", "~75 LOC — orchestrates generators → bakes → pmtiles"),
        "outline": [
            ("pipeline comment", "5 → 6 steps", True),
            ("step 2 (NEW)", "build_node_blocks.py between street blocks and the first bake", True),
            ("tippecanoe", "unchanged — block_id stays the NATIVE feature id", False),
        ],
        "blocks": ["steps renumbered [1/6]…[6/6]; node discs run in the server venv like the bakes"],
    },
    "server/streetscape_blocks/README.md": {
        "on": ["Flask API"],
        "module": ("server · streetscape_blocks", "generator docs"),
        "file": ("README.md", "adding-a-city + evaluation instructions"),
        "outline": [("pipeline paragraph", "discs + severed foot blocks documented", True)],
        "blocks": ["one-paragraph description of the disc step and why foot blocks sever at junctions"],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("server · Flask app", "routes: votes, topology, tiles, maps"),
        "file": ("app.py", "~3.3k LOC — the API surface"),
        "outline": [
            ("_build_graph_votes_body_locked", "bagg cold-check → NEW cold-OR-stale check via bver marker", True),
            ("graph_version", "NEW blocks field next to version", True),
            ("graph_topology ?format=bin", "bin ETag now suffixed with the blocks version", True),
            ("serve_tiles", "unchanged (max-age=1w — hence the ?v= buster)", False),
        ],
        "blocks": [
            "bver:<slug>:<mode> marker — mismatch vs graph.blocks_version triggers block_votes.rebuild_from_db",
            "graph-version response: {version: topology_etag, blocks: blocks_sha256}",
            "bin_etag: \"<etag>-bin2-<blocks>\" — a re-bake busts intermediary caches too",
        ],
    },
    "server/cities.py": {
        "on": ["Flask API"],
        "module": ("server · cities registry", "static per-city config exposed to the client at bootstrap"),
        "file": ("cities.py", "~150 LOC — City dataclass + registry"),
        "outline": [
            ("to_public", "NEW blocksVersion field", True),
            ("_blocks_version", "NEW — blocks.pmtiles mtime via os.stat (no graph load)", True),
        ],
        "blocks": ["blocksVersion: int mtime or None when the city ships no block artifacts"],
    },
    "client-react/src/config.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · config", "bootstrap-time CONFIG + per-city rebinding"),
        "file": ("config.ts", "~130 LOC — CONFIG object, CityConfig shape, applyCityConfig"),
        "outline": [
            ("CityConfig interface", "NEW blocksVersion field", True),
            ("applyCityConfig", "blocks tiles URL now carries ?v=<blocksVersion>", True),
        ],
        "blocks": ["blockTilesUrl = <tilesPath→blocks.pmtiles> + ?v= — busts the week-long HTTP cache on re-bake"],
    },
    "client-react/src/components/GraphLayer/graphTopology.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "typed-array topology + CSR adjacency/block indexes"),
        "file": ("graphTopology.ts", "~290 LOC — the mobile-safe topology model"),
        "outline": [
            ("accessors / decode", "unchanged", False),
            ("buildNodeAdj / adjFirst", "unchanged (adjFirst kept for API compat)", False),
            ("adjShortest", "NEW — shortest incident edge (cos-corrected), the node→disc rule", True),
            ("block index / keys", "unchanged", False),
        ],
        "blocks": ["adjShortest(d, adj, nid) — int32-coord squared lengths, lon scaled by cos(lat)"],
    },
    "client-react/src/components/GraphLayer/graphTopology.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for the topology model"),
        "file": ("graphTopology.test.ts", "decode/index/block-key specs"),
        "outline": [("adjShortest specs", "NEW — long-edge-first star topology; null cases", True)],
        "blocks": ["2 new tests: picks the short edge over array order · null without adjacency / isolated node"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: topology load, selection resolve, cards"),
        "file": ("GraphLayer.tsx", "~3.9k LOC — this commit touches only the version probe + the four node→edge sites"),
        "outline": [
            ("topology load effect", "graph-version probe now reads blocks; blob key -bin2-<blocks>", True),
            ("resolveSelection", "node voteEdgeId via adjShortest", True),
            ("dispatchBlockSelect", "node targets broadcast their disc's block", True),
            ("hover / pinned card content", "node rows + pinned vote edge via adjShortest", True),
            ("markers / proposals / casts", "unchanged in this commit", False),
        ],
        "blocks": [
            "blocksVersion from /api/graph-version (vj.blocks)",
            "binVersion = `${version}-bin2${blocksVersion ? `-${blocksVersion}` : \"\"}`",
            "4 × adjFirst → adjShortest (resolver, block-select, hover rows, pinned vote edge)",
        ],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("docs", "the source-of-truth spec for the graph/blocks/route-proposals separation"),
        "file": ("three-layer-model.md", "layer definitions + block-scoped vote semantics"),
        "outline": [
            ("§2.1 What a block is", "REWRITTEN — junction discs + the adjShortest rule + why (the ladder)", True),
            ("§2.2 Generation", "4-step list — build_node_blocks inserted; foot severing documented", True),
            ("§2.2 Evaluation", "corrected: EVERY city (NYC included) serves the procedural output", True),
            ("§2.3–§4", "unchanged", False),
        ],
        "blocks": [
            "§2.1 — discs (12 m), disjointness, node → shortest-incident-edge block",
            "§2.2 — pipeline list matches build_city_blocks.sh's six steps",
            "evaluation para — Brook's planimetric blocks are the reference ONLY (bake meta proves it)",
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

  <p class="lede">Why did a route down 7th Avenue select every cross street it passed? Because street
  block polygons extend across intersections, so the short graph edges that CROSS an intersection baked into
  a perpendicular street's block — and selection + casting are block-scoped. The fix makes every junction its
  own small block: a 12&nbsp;m disc punched out of the street and foot polygons. The same discs sever the
  park-path mesh at junctions, so Central Park went from a handful of giant merged blocks to per-segment
  blocks — the same grain as the street grid (answering “why is Central Park clustering so bad”: its old
  foot blocks merged one block per CONNECTED component, a genuinely different rule than the street
  Voronoi). NYC re-baked to 563,812 blocks with a total edge mapping, plus staleness plumbing so a re-baked
  block set busts every downstream cache (topology blob, Redis block aggregates, pmtiles).</p>

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
