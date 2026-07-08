#!/usr/bin/env python3
"""Generate the block-graph-redesign changelog report (2026-07-08).

Run from repo root: python changelog/build_blockgraph_report.py
Reads changelog/changes-blockgraph.diff
(captured with: git show 451b9f0 --format="" > changelog/changes-blockgraph.diff
             && git show edfc716 --format="" >> changelog/changes-blockgraph.diff),
writes changelog/2026-07-08-block-graph-redesign.html

Modeled on build_rbtp_parity_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-blockgraph.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-block-graph-redesign.html")

DATE = "2026-07-08"
TITLE = "Block-graph redesign — absolute-vote heat, Voronoi junction cells, graph-first foot blocks, degenerate-block merging"


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
        "id": "heat",
        "tag": "Server + client · heat semantics",
        "title": "1 · Heat = total absolute votes (up + down), not net",
        "symptom": (
            "A heavily-downvoted block cancelled toward zero and vanished from the heatmap — controversy "
            "read as silence. Downvotes are engagement and should read hot."
        ),
        "cause": [
            "<code>block_votes[b]</code> was net (<code>up − down</code>) on the server "
            "(<code>build_block_arrays</code>) and the client's delta mirror "
            "(<code>applyBlockCounts</code>) adjusted the same way.",
        ],
        "fixes": [
            "<code>block_votes</code> now carries TOTAL deduped activity (<code>up + down</code>) on both "
            "paths, and the per-type breakdown sorts by total. Blocks are the only heat display; edges and "
            "nodes keep their raw up/down breakdowns for modals but carry no heat of their own.",
            "Unit tests updated on both sides (a reversal now keeps the block at total 1 instead of "
            "flipping to −1); with totals the heat array can never go negative, so the MapLibre "
            "<code>v &gt; 0</code> sparse-set path lights every voted block.",
        ],
        "files": [
            "server/block_votes.py",
            "server/tests/unit/test_block_votes.py",
            "client-react/src/components/GraphLayer/voteApply.ts",
            "client-react/src/components/GraphLayer/voteApply.test.ts",
            "client-react/src/types/index.ts",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "capture",
        "tag": "Pipeline · junction capture",
        "title": "2 · Junction capture is topological — 62.8% of NYC edges freed",
        "symptom": (
            "Central Park (and every dense path network) rendered as “gloops of nodes”: heat landed on "
            "junction blobs, not path polygons. Root cause measured on the real graph: the midpoint-≤12m "
            "capture rule mapped 2.07M of 3.3M edges (62.8% city-wide, 54.8% inside the park) into node "
            "blocks — including 120m footways whose midpoints merely passed near a junction."
        ),
        "cause": [
            "Capture was geometric (edge midpoint within 12 m of any junction, euclidean KDTree) — tuned "
            "to catch intersection-crossing stubs (the “ladder” fix) but with no notion of what the edge "
            "actually connects.",
        ],
        "fixes": [
            "An edge is captured iff BOTH endpoints are junction-cluster members AND they share a cluster, "
            "OR the edge is ≤ <code>NODE_CAPTURE_LEN_M</code> (30 m) between two different clusters — "
            "crosswalks spanning a wide avenue connect that avenue's two corner clusters, so the pure "
            "same-cluster rule alone would regress the ladder. Cross-cluster stubs go to the cluster whose "
            "centroid is nearer the edge midpoint.",
            "Measured candidates on the full graph before choosing: midpoint-12m 62.8% · same-cluster only "
            "14.4% · chosen rule 15.7% (park capture 54.8% → ~13%).",
            "The final bake also applies the foot sidecar (§4) before any geometry pass, and the meta "
            "records the new counters (<code>captured_junction</code>, <code>foot_by_construction</code>).",
        ],
        "files": ["server/streetscape_blocks/build_edge_blocks.py"],
    },
    {
        "id": "nodecells",
        "tag": "Pipeline · node polygons",
        "title": "3 · Junction blocks are clipped Voronoi cells, not disc gloops",
        "symptom": (
            "Node blocks drew as unions of 9 m discs — stacked-circle “gloopy agglomerations” wherever a "
            "physical intersection contributed several OSM junction nodes."
        ),
        "cause": [
            "Polygon = <code>union_all(member discs)</code>; and single-linkage union-find (link 18 m) "
            "percolates — the worst NYC cluster chained 1,607 junctions across 483 m of FiDi, and 20% of "
            "captured edges lived in clusters wider than 56 m, so their heat would have collapsed into one "
            "small blob.",
        ],
        "fixes": [
            "Each cluster's polygon is now <code>voronoi_cell(centroid vs neighbouring cluster centroids) "
            "∩ disc(centroid, R)</code> with <code>R = clamp(member extent + 6 m, 10 m, 28 m)</code> — "
            "computed locally by clipping a 24-gon disc with the perpendicular bisector toward every "
            "centroid closer than 2R. Adjacent intersections can never overlap (bisectors) and a sprawling "
            "cluster can't balloon (radius cap).",
            "Percolation fixed at the source: clusters wider than <code>NODE_CLUSTER_MAX_EXTENT_M</code> "
            "(40 m) are recursively bisected along their principal axis (2×2 eigenproblem, cut at the "
            "median projection) — 12,343 splits in NYC, largest cluster now 50 nodes (was 1,607).",
            "Cells are still punched out of every street block they touch, so blocks never overlap and "
            "bake containment stays unambiguous.",
        ],
        "files": ["server/streetscape_blocks/build_node_blocks.py"],
    },
    {
        "id": "foot",
        "tag": "Pipeline · foot blocks",
        "title": "4 · Foot blocks are graph-first — polygons from exactly their member edges",
        "symptom": (
            "Foot-block membership was geometric (midpoint-in-polygon against tubes severed by junction "
            "discs), so a block's polygon and its actual edge set could disagree — and Central Park's "
            "polygons barely participated in heat at all (1,020 node blobs vs 144 tubes in the park bbox)."
        ),
        "cause": [
            "The old script buffered ALL unmapped edges into one mesh, severed it geometrically with the "
            "9 m discs, split into components, and then re-derived membership by baking midpoints back "
            "against those polygons.",
        ],
        "fixes": [
            "Uncovered edges are grouped as connected components of the walk graph, severed TOPOLOGICALLY "
            "at junction-cluster members (traversal never crosses a cluster node) — the same grain as "
            "street blocks, no disc-radius coupling.",
            "Each component's polygon is the union of exactly its own buffered edge polylines (6 m tubes), "
            "minus intersecting node cells — “the edges define the polygon”, purely procedurally.",
            "Membership ships by construction in <code>foot_clusters_&lt;network&gt;.npz</code>; the final "
            "bake applies it verbatim. NYC: 13,850 foot components, zero skipped, and the park now has 179 "
            "foot polygons holding ~4.7K park edges.",
        ],
        "files": ["server/streetscape_blocks/build_foot_blocks.py"],
    },
    {
        "id": "merge",
        "tag": "Pipeline · NEW merge pass",
        "title": "5 · Degenerate blocks merge to a fixpoint (garage entrances disappear into their street)",
        "symptom": (
            "Tiny stub blocks (garage entrances, driveways, alley mouths) rendered as their own blocks "
            "beside the street they belong to — e.g. the West 31st Street &amp; PPS 100 LLC garage — and "
            "spurious mid-street “junctions” (deg-3 only because of such a stub) punched pointless node "
            "blobs into street blocks."
        ),
        "cause": [
            "No post-bake structural pass existed: whatever the drive-graph Voronoi + bake produced was "
            "final, however degenerate the block adjacency was.",
        ],
        "fixes": [
            "New <code>merge_degenerate_blocks.py</code> iterates two rules to a fixpoint on the block "
            "adjacency graph (derived purely from the walk graph + baked mapping — an edge-block is "
            "adjacent to a node-block iff a member edge endpoint sits in that junction cluster): "
            "<strong>V1</strong> an edge-block adjacent to exactly ONE node-block with bbox extent ≤ 40 m "
            "merges INTO that junction (cul-de-sacs exceed the cap and survive); <strong>V2</strong> a "
            "node-block left adjacent to exactly ONE distinct edge-block dissolves into it.",
            "The W 31st case traces: garage stub →(V1)→ junction blob; the driveway isn't in the drive "
            "graph so West 31st is ONE segment through that junction → the junction sees one distinct "
            "neighbour →(V2)→ everything dissolves into the West 31st Street block. Verified live: the "
            "garage mouth, the driveway interior, and mid-block W 31st all resolve to block 1772 "
            "(<code>road_name=\"West 31st Street\", n_merged=4</code>), and selecting the entrance "
            "highlights the whole street block.",
            "<strong>Field fix (Eric's screenshot):</strong> V2 originally allowed ≤ 2 distinct neighbours "
            "(“merge into a corridor”). Times Square proved that wrong: pedestrian-plaza junctions seeded "
            "cascading corridor welds until one 404 m block held Broadway + 7th Avenue + W 44th/45th/46th "
            "edges (n_merged=14). With the ==1 rule a dissolve can only fold a junction into a single "
            "existing block, so street-street welding is structurally impossible; junction dissolves "
            "dropped 42,768 → 26,715 and Times Square's streets separate cleanly.",
            "Merge output goes to <code>blocks_final_&lt;city&gt;.geojson</code> (pmtiles source) with "
            "densely renumbered ids; the npy is remapped in place and the bake's raw mapping is kept as "
            "<code>edge_blocks_&lt;net&gt;.premerge.npy</code> so merge knobs can be re-tuned without a "
            "re-bake. NYC: 264,074 → 235,769 blocks, fixpoint in 3 rounds.",
        ],
        "files": ["server/streetscape_blocks/merge_degenerate_blocks.py"],
    },
    {
        "id": "pipeline",
        "tag": "Pipeline · orchestration",
        "title": "6 · build_city_blocks.sh — 7 steps, pristine intermediates",
        "symptom": (
            "The pipeline needed re-ordering around the new pieces, and a stale foot sidecar from a "
            "previous run could poison a pass-1 bake with dead block ids."
        ),
        "cause": ["New merge step + by-construction foot assignment changed the artifact flow."],
        "fixes": [
            "Steps: generic street blocks → junction Voronoi cells → bake pass 1 → graph-first foot "
            "blocks → final bake → degenerate merge → tippecanoe from <code>blocks_final</code>. "
            "<code>blocks_generic</code> stays a pristine intermediate; the foot sidecar is deleted before "
            "pass 1 and rebuilt in step 4.",
            "Full re-runs must start at step 1 (node cells are punched into street polygons in place — "
            "the existing dead-rings gotcha, now documented in the header).",
        ],
        "files": ["server/streetscape_blocks/build_city_blocks.sh"],
    },
]

VERIFY = [
    "Server + client unit tests green after the heat change (9/9 <code>test_block_votes.py</code>, "
    "16/16 <code>voteApply.test.ts</code>).",
    "Capture-rule candidates measured on the full NYC graph before choosing (midpoint-12m 62.8% vs "
    "chosen topological rule 15.7%; Central Park 54.8% → ~13%); cluster percolation measured "
    "(1,607-junction / 483 m worst cluster; 20% of captured edges in clusters wider than 56 m) and "
    "fixed with the 40 m principal-axis split (largest cluster now 50 nodes).",
    "Full NYC pipeline run end-to-end twice (initial + after the Times Square fix): 3,299,152/3,299,152 "
    "edges mapped (100%), 264,074 → 235,769 final blocks, merge fixpoint in 3 rounds, pmtiles rebuilt, "
    "<code>blocks_version</code> sha <code>b7ae6a78e6a74158</code> served by Flask and picked up by the "
    "client (fresh GTB2 blob, nBlocks 235,769).",
    "Garage acceptance test (the URL from the request): the app resolves the garage-entrance selection to "
    "block 1772 “West 31st Street” — console <code>[blocks] select</code>, MapLibre "
    "<code>queryRenderedFeatures</code> at the garage mouth / driveway interior / mid-block all return the "
    "same feature with <code>selected: true</code>, and the service edges' baked ids match.",
    "Times Square regression (Eric's screenshot, 40.757713 −73.985667): before the V2 fix one 404 m "
    "“West 46th Street” block held Broadway/7th Ave/W 44th/W 45th edges; after, every street is its own "
    "block and the junctions hold only crossing edges.",
    "Heat sanity over the live API: 425 blocks lit, zero negative values (absolute totals).",
    "NOT verified visually by me: Central Park's new tube polygons and overall heat aesthetics — Chrome "
    "kept losing MapLibre to the occluded-window rAF trap while Eric was using the browser, so the visual "
    "pass is on the manual checklist.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways?z=18&lat=40.74873&lng=-73.99017&slat=40.74885&slng=-73.99163'>"
    "the W 31st garage URL</a>: selecting the garage entrance should highlight the whole West 31st Street "
    "block — driveway notch included, no separate stub block.",
    "Open <a href='http://localhost:3000/m/nyc-walkways?tab=blocks&w=40.757713%2C-73.985667&vt=Improve+sidewalk'>"
    "the Times Square coordinate</a>: Broadway, 7th Ave and each cross street should light as separate "
    "blocks; junction cells stay intersection-sized.",
    "Pan Central Park with some votes cast on paths: path segments should render as tube polygons between "
    "junction cells (not blobs); junctions are convex Voronoi cells.",
    "Cast a − (downvote) on an unvoted block: it should HEAT UP (absolute totals), and a block with +1/−1 "
    "should read as 2, not 0.",
    "Route down an avenue for ~10 blocks and check the touched blocks (route card / highlight): avenue "
    "blocks + intersections only — no perpendicular side-street blocks (the ladder).",
    "Re-run knobs if visuals suggest tuning: <code>NODE_BLOCK_MAX_RADIUS_M</code> (28), "
    "<code>NODE_CLUSTER_MAX_EXTENT_M</code> (40), <code>STUB_MAX_M</code> (40), "
    "<code>NODE_CAPTURE_LEN_M</code> (30) — the merge re-runs from <code>.premerge.npy</code> without a "
    "re-bake.",
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
    "server/block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · block votes", "the deduped per-block projection of edge votes (bd:/bagg: Redis structures)"),
        "file": ("block_votes.py", "~250 LOC — delta writes, aggregate reads, rebuild-from-DB"),
        "outline": [
            ("Write path (apply_block_delta[s])", "unchanged — device-multiplicity hashes + aggregate", False),
            ("build_block_arrays", "block_votes = up + down; per-type sort by total", True),
            ("read_block_vt_counts / rebuild", "unchanged", False),
        ],
        "blocks": [
            "build_block_arrays — heat value switched to total deduped activity; docstring states the rule",
        ],
    },
    "server/tests/unit/test_block_votes.py": {
        "on": ["Flask API"],
        "module": ("Flask API · tests", "pins the dedup/removal/reversal invariants of the block projection"),
        "file": ("test_block_votes.py", "9 specs against a fake Redis"),
        "outline": [
            ("net() helper → total()", "renamed + documents the up+down rule", True),
            ("reversal / rebuild expectations", "−1 → 1 (downvotes read hot)", True),
        ],
        "blocks": ["total() helper — block_votes is TOTAL activity; reversal keeps the block at 1"],
    },
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure vote-state mutators (optimistic + authoritative SETs)"),
        "file": ("voteApply.ts", "~230 LOC — edge/node/block count application"),
        "outline": [
            ("applyEdgeVoteChange / authoritative", "unchanged", False),
            ("applyBlockCounts", "delta mirror: oldTotal, sort by total, += up+down−oldTotal", True),
        ],
        "blocks": ["applyBlockCounts — the client twin of build_block_arrays' up+down rule"],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for the pure vote mutators"),
        "file": ("voteApply.test.ts", "16 specs"),
        "outline": [("applyBlockCounts specs", "3+1 → 4 (total), wording updated", True)],
        "blocks": ["blockVotes expectation 2 → 4 for {up:3, down:1}"],
    },
    "client-react/src/types/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · types", "the GraphData/VoteData shapes shared across layers"),
        "file": ("types/index.ts", "~140 LOC"),
        "outline": [("block_votes comment", "documents up+down semantics", True)],
        "blocks": ["block_votes[b] = total activity (up + down)"],
    },
    "docs/three-layer-model.md": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("docs", "the Layer-1/2/3 source-of-truth spec"),
        "file": ("three-layer-model.md", "§2.4 blocks serving"),
        "outline": [("§2.4 served fields", "block_votes[] = total activity", True)],
        "blocks": ["one-line semantics update beside the bd:/bagg: description"],
    },
    "server/streetscape_blocks/build_node_blocks.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · step 2", "junction clusters → their own blocks, punched out of street blocks"),
        "file": ("build_node_blocks.py", "~290 LOC — clustering, cell geometry, punch, sidecar"),
        "outline": [
            ("Constants", "LINK_M · NEW MAX_EXTENT_M · PAD/MIN/MAX radius · DISC_SEGS", True),
            ("junction_nodes", "unchanged (unique-neighbour deg ≥ 3)", False),
            ("cluster_junctions", "union-find → NEW split_oversized post-pass", True),
            ("split_oversized", "NEW — principal-axis bisection of >40m clusters", True),
            ("clipped_voronoi_cell", "NEW — disc ∩ bisector half-planes vs neighbour centroids", True),
            ("main", "centroid+radius per cluster, KDTree neighbour pairs, cells, punch, sidecar", True),
        ],
        "blocks": [
            "split_oversized — recursive 2-means-style bisection (2×2 eigenproblem, median cut)",
            "clipped_voronoi_cell — 24-gon disc clipped by perpendicular bisectors within 2R",
            "main — R = clamp(extent+PAD, MIN, MAX); cells punched from street blocks; node_clusters sidecar",
        ],
    },
    "server/streetscape_blocks/build_foot_blocks.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · step 4", "blocks for edges the street ROW never covered (parks, plazas)"),
        "file": ("build_foot_blocks.py", "~230 LOC — components, tubes, sidecar"),
        "outline": [
            ("Component build", "NEW — union-find severed at junction-cluster nodes", True),
            ("Per-component tubes", "NEW — union of the component's own 6m buffers", True),
            ("Node-cell trim", "STRtree difference (blocks never overlap)", True),
            ("foot_clusters sidecar", "NEW — edge→block by construction", True),
        ],
        "blocks": [
            "union-find that never traverses a junction member — components = path segments between junctions",
            "singleton components for uncaptured junction-to-junction links",
            "emit features + foot_clusters_<network>.npz (skipped slivers fall through to the bake)",
        ],
    },
    "server/streetscape_blocks/build_edge_blocks.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · steps 3+5", "the edge→block bake that powers block-level vote display"),
        "file": ("build_edge_blocks.py", "~240 LOC — 4 passes: capture, foot sidecar, containment, nearest"),
        "outline": [
            ("Pass 0: junction capture", "topological — same-cluster OR ≤30m cross-cluster", True),
            ("Pass 1: foot sidecar", "NEW — by-construction assignments applied verbatim", True),
            ("Pass 2: containment", "unchanged (STRtree within)", False),
            ("Pass 3: nearest ≤30m", "unchanged", False),
            ("Meta", "new counters: foot_by_construction, node_capture_len_m", True),
        ],
        "blocks": [
            "capture — node_cluster[endpoints]; same-cluster direct; cross-cluster ≤30m to the nearer centroid",
            "foot sidecar application before any geometry pass",
        ],
    },
    "server/streetscape_blocks/merge_degenerate_blocks.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · step 6 (NEW)", "fixpoint merge of degenerate blocks on the block adjacency graph"),
        "file": ("merge_degenerate_blocks.py", "~300 LOC — adjacency, union-find, geometry dissolve, remap"),
        "outline": [
            ("Premerge cache", "edge_blocks .premerge.npy — re-tune without re-baking", True),
            ("Primitive incidences", "edge-block↔node-block pairs from edge endpoints in clusters", True),
            ("V1 stub merge", "1 adjacent node-block + extent ≤ 40m → into the junction", True),
            ("V2 junction dissolve", "EXACTLY ONE distinct neighbour → into it (weld-proof)", True),
            ("Feature rebuild", "union member geometries, dense renumber, blocks_final geojson", True),
            ("Remap npy + meta", "final sha stamps blocks_version", True),
        ],
        "blocks": [
            "premerge guard — refuses to double-merge; caches the bake's raw npy",
            "round loop — V1 then V2 per round, group adjacency recomputed from fixed primitive pairs",
            "V2 == 1 rule — a dissolve can only fold into ONE existing block (Times Square weld fix)",
            "rebuild — representative props (node keeps junction identity; corridors take the largest street member)",
        ],
    },
    "server/streetscape_blocks/build_city_blocks.sh": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · orchestrator", "one-command Layer-2 build for a city"),
        "file": ("build_city_blocks.sh", "7 steps"),
        "outline": [
            ("Steps 1-5", "generic → node cells → bake → foot → final bake", True),
            ("Step 6", "NEW — merge_degenerate_blocks.py", True),
            ("Step 7", "tippecanoe from blocks_final_<city>.geojson", True),
            ("Hygiene", "stale foot sidecar removed before pass 1", True),
        ],
        "blocks": [
            "rm -f foot_clusters before pass-1 bake",
            "FINAL_FILE feeds tippecanoe; blocks_generic stays pristine",
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · commits <code>451b9f0</code> + <code>edfc716</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a>{diff_link}
  </nav>

  <p class="lede">Four requests reshaping how Layer-2 blocks are formed, drawn and heated. The heatmap now
  shows TOTAL absolute votes (up + down — a downvoted block reads hot, not silent), and only blocks carry
  heat. The block pipeline was redesigned end-to-end: junction blocks are Voronoi cells at cluster
  centroids (max-radius clipped) instead of gloopy disc unions, with oversized single-linkage clusters
  split along their principal axis; junction capture is topological (both endpoints in clusters) instead
  of midpoint-distance — freeing the 62.8% of NYC edges the old rule swallowed and letting Central Park's
  paths keep their heat; foot blocks are built graph-first (components severed at junctions, polygons from
  exactly their member edges, membership by construction); and a new fixpoint merge pass absorbs
  degenerate blocks — garage-entrance stubs disappear into their street's block (the W 31st &amp; PPS 100
  LLC acceptance case), while a field-reported Times Square regression (2-neighbour corridor merges
  welding Broadway to its cross streets) tightened the dissolve rule to single-neighbour junctions only.
  NYC re-baked: 3.3M edges 100% mapped, 235,769 final blocks.</p>

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
    Generated from <code>changelog/changes-blockgraph.diff</code> by <code>changelog/build_blockgraph_report.py</code>.
    Regenerate after further edits with
    <code>git show 451b9f0 edfc716 --format="" &gt; changelog/changes-blockgraph.diff &amp;&amp; python changelog/build_blockgraph_report.py</code>.
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
