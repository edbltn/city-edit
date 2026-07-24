#!/usr/bin/env python3
"""Generate the overnight-swarm changelog report (2026-07-23).

Run from repo root: python changelog/build_nightly_swarm_report.py
Reads changelog/changes-nightly-swarm.diff (captured with:
  git diff 407e76b..nightly/2026-07-23 > changelog/changes-nightly-swarm.diff),
writes changelog/2026-07-23-nightly-swarm.html

Modeled on build_idb_votes_purge_report.py (same styles + context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-nightly-swarm.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-nightly-swarm.html")

DATE = "2026-07-23"
TITLE = "Overnight Haiku swarm — 13 nightly tasks dispatched, 5 landed, 8 caught by review"


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
        "id": "swarm",
        "tag": "Process · multi-agent",
        "title": "1 · The run: 13 Haiku workers, 11 Sonnet reviewers, 1 integrator",
        "symptom": (
            "A nightly-maintenance backlog (test coverage, LOC reduction, docs consolidation, "
            "one refactor) executed unattended: each task went to a <strong>Haiku worker in an "
            "isolated git worktree</strong> committing to its own <code>nightly/0723-Txx</code> "
            "branch, then a <strong>Sonnet reviewer</strong> read the full branch diff read-only "
            "and approved/rejected. Only approved branches were cherry-picked onto "
            "<code>nightly/2026-07-23</code>, each gated by the recorded test baseline "
            "(68 passing + 7 known-failing pytest, 258 vitest, clean tsc)."
        ),
        "cause": [
            "Kept (4 approved + 1 salvage): <strong>T01</strong> un-drifted the 7 failing server "
            "tests, <strong>T03</strong> added 39 client tests, <strong>T08</strong> fixed the "
            "block-heat doc, <strong>T13</strong> extracted 615 lines of pure helpers out of "
            "GraphLayer.tsx, <strong>T04</strong> (junk cleanup) was rejected in review but its "
            "verified-safe parts were re-done by hand at integration.",
            "Rejected — the interesting part: 9 of 13 worktrees were snapshotted from a "
            "<strong>stale base</strong> (<code>ca9ed56</code>, 2026-06-02, 168 commits behind). "
            "Reviewers caught every one: T02's tests passed only because the worker manually "
            "copied an uncommitted <code>graph_arrays.py</code> into its worktree ('42 passed' "
            "was unreproducible from the branch); T06's sweep 'scanned all 13 server files' of a "
            "tree that has 25+, and its one edit resurrected the deleted "
            "<code>import_citibike.py</code>; T04 grepped a tree where <code>ebikes.png</code>'s "
            "referencing file didn't exist yet and declared it unreferenced; T09 groomed a "
            "seven-week-old TODO.md.",
            "Two workers (T05, T11) finished without reporting; their orphan commits were "
            "inspected by hand — both stale-based (T05 deleted files that no longer exist; "
            "T11 rewired WebSocket/drag hooks against old code) and were dropped.",
            "Lesson encoded for next time: verifiers must check <code>git merge-base</code> "
            "against the dispatch commit FIRST — it instantly explained every bogus claim.",
        ],
        "fixes": [],
        "files": [],
    },
    {
        "id": "t01",
        "tag": "Backend · pytest",
        "title": "2 · T01 — the 7 failing server tests were stale, not the code",
        "symptom": (
            "Since the vectorized-build_arrays work, <code>tests/unit</code> failed 7 tests: "
            "3 in test_hydration, 3 in test_topology_bin (AttributeError), 1 in test_vote_counts "
            "(TypeError at <code>vote_store.py:390</code>)."
        ),
        "cause": [
            "<code>vote_store.build_arrays</code> and <code>graph_registry.encode_topology_bin</code> "
            "were refactored to take <code>edge_ends</code> (an <code>np.ndarray [e, 2] int32</code>, "
            "per the docstring) instead of the old per-node <code>node_adj</code> lists — the swarm-era "
            "vectorization (<code>edge_ends[pos, 0]</code> fancy indexing is exactly line 390).",
            "The test suite still passed <code>node_adj</code> lists and compared results with "
            "<code>==</code> on what are now numpy arrays.",
        ],
        "fixes": [
            "<code>conftest.py</code>: <code>tiny_graph</code> fixture now provides "
            "<code>edge_ends=np.array([[0,1],[1,2]], dtype=np.int32)</code>.",
            "<code>test_hydration.py</code>: passes <code>None</code> for edge_ends (those tests "
            "only assert edge_votes) and compares via <code>.all()</code>.",
            "<code>test_topology_bin.py</code> / <code>test_vote_counts.py</code>: typed numpy "
            "inputs + <code>.all()</code> comparisons.",
            "Result: <strong>75 passed, 0 failed</strong> — first fully-green backend suite on "
            "this branch.",
        ],
        "files": [
            "server/tests/conftest.py",
            "server/tests/unit/test_hydration.py",
            "server/tests/unit/test_topology_bin.py",
            "server/tests/unit/test_vote_counts.py",
        ],
    },
    {
        "id": "t13",
        "tag": "Frontend · refactor",
        "title": "3 · T13 — 615 lines of pure helpers out of the 5,133-line GraphLayer.tsx",
        "symptom": (
            "GraphLayer.tsx carried every pure helper it ever grew — geometry math, Flatbush "
            "index builders, hit-testing, geocode caching, topology utilities — inside the same "
            "5,133-line file as the component."
        ),
        "cause": [
            "Strict cut-paste extraction (no logic edits, comments move with their functions) of "
            "module-scope functions/constants that use no hooks, JSX, or component state.",
        ],
        "fixes": [
            "<code>spatialLookup.ts</code> (406 L): Flatbush node/edge index builders with the "
            "yield-to-main batching, <code>hitTest</code>, <code>blockFiltersAt</code>, "
            "<code>adjShortestInBlock</code>, <code>projectOntoEdge</code>, hover/hit types, and "
            "the interaction constants (NODE_END_SHARE, cluster/spread, vote caps).",
            "<code>geometryHelpers.ts</code> (79 L): <code>pointToSegmentDist</code>, "
            "<code>haversineMeters</code>, <code>arrayMax</code>, heat-scale constants "
            "(HEAT_FULL_SCALE 50, NEG_HEAT_FULL_SCALE 10, HEATMAP_OPACITY).",
            "<code>topologyHelpers.ts</code> (83 L): <code>targetLatLng</code>, "
            "<code>rbtpDisplayPos</code>, spread keys, <code>votesMatchTopology</code>.",
            "<code>geocodingHelpers.ts</code> (53 L): the geocode cache/in-flight sets + "
            "<code>resolveAddress</code>.",
            "GraphLayer.tsx: 5,133 → 4,532 lines. Verified twice: the Sonnet reviewer re-ran "
            "tsc + vitest in the worker's worktree, and both gates ran again after cherry-pick.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/spatialLookup.ts",
            "client-react/src/components/GraphLayer/geometryHelpers.ts",
            "client-react/src/components/GraphLayer/topologyHelpers.ts",
            "client-react/src/components/GraphLayer/geocodingHelpers.ts",
        ],
    },
    {
        "id": "t03",
        "tag": "Frontend · vitest",
        "title": "4 · T03 — 39 new tests for mapStyles.ts and themes.ts",
        "symptom": (
            "mapStyles.ts (366 L) and themes.ts (423 L) — both pure-function modules — had no "
            "colocated tests."
        ),
        "cause": [
            "New <code>mapStyles.test.ts</code> (14 tests): <code>getMapStyle</code> resolution, "
            "<code>maplibreRasterTiles</code>, <code>heatGradientCss</code>.",
            "New <code>themes.test.ts</code> (25 tests): <code>iconSrc</code>/<code>iconForLabel</code> "
            "resolution, theme building from preset maps, fallback behavior.",
        ],
        "fixes": [
            "vitest: 258 → <strong>297 passing</strong> (+39), zero source files touched.",
            "Known nit from review (left as-is, worth a look): two themes tests "
            "('builds a theme from a preset map (bikepaths)', 'falls back to walkways theme "
            "defaults') never set <code>map.subdomain</code>, so <code>presetForMap</code> "
            "returns null and the assertions pass via the DEFAULT_STYLE fallback coinciding "
            "with the preset values — they don't yet pin the preset path.",
        ],
        "files": [
            "client-react/src/mapStyles.test.ts",
            "client-react/src/themes.test.ts",
        ],
    },
    {
        "id": "t08",
        "tag": "Docs",
        "title": "5 · T08 — three-layer-model.md caught up with differential block heat",
        "symptom": (
            "The doc still described block heat as 'total activity (up + down) — downvotes read "
            "hot too', which today's differential-block-heat work (53716b3) made false."
        ),
        "cause": [
            "Heat is now the <strong>signed top-proposal differential</strong> per block "
            "(<code>topProposalDiffs</code> in voteApply.ts), two-armed log-normalized "
            "(positives vs HEAT_FULL_SCALE 50, negatives vs NEG_HEAT_FULL_SCALE 10), cold arm "
            "for negatives, zero = invisible.",
        ],
        "fixes": [
            "Section 2.4 rewritten to match the code; every claim in the new text was verified "
            "against voteApply.ts / GraphLayer heat constants by the reviewer (and cross-checked "
            "against today's differential-block-heat changelog).",
        ],
        "files": ["docs/three-layer-model.md"],
    },
    {
        "id": "t04",
        "tag": "Repo hygiene",
        "title": "6 · T04 (salvaged by hand) — junk out of the repo root",
        "symptom": (
            "Three debug screenshots were <em>tracked at the repo root</em>: "
            "<code>drag problem.png</code>, <code>logo problem.png</code>, <code>ebikes.png</code> "
            "(~600 KB of binary history nobody asked for). The swarm branch for this task was "
            "stale-based and missed ebikes.png entirely, so this was re-done manually at "
            "integration using only the reviewer-verified facts."
        ),
        "cause": [
            "<code>drag problem.png</code> / <code>logo problem.png</code>: git-grep verified "
            "unreferenced on the current tree → deleted.",
            "<code>ebikes.png</code> IS referenced — it's the transcription source cited by "
            "<code>server/build_ebike_stations.py</code> — so it moved to <code>screenshots/</code> "
            "with both comment references updated.",
            "<code>.gitignore</code>: exact-path entries for the current untracked scratch files "
            "(<code>terraform/tfplan-donate</code>, <code>changelog/changes.diff</code>); "
            "<code>dump.rdb</code> was already covered by the existing unanchored rule.",
        ],
        "fixes": [],
        "files": [
            "drag problem.png",
            "logo problem.png",
            "screenshots/ebikes.png",
            "server/build_ebike_stations.py",
            ".gitignore",
        ],
    },
]

VERIFY = [
    "Backend: <code>env/bin/python -m pytest tests/unit</code> — <strong>75 passed, 0 failed</strong> "
    "(baseline was 68 passed / 7 failed; the 7 were the T01 targets, and no other test moved).",
    "Frontend: <code>npx vitest run</code> — <strong>297 passed | 1 skipped</strong> "
    "(baseline 258 | 1; +39 are exactly the two new T03 files).",
    "<code>npx tsc -b</code> — clean after the GraphLayer extraction.",
    "Every kept branch descends from the dispatch commit <code>407e76b</code> "
    "(<code>git merge-base</code> checked per branch); all 9 stale-based branches were rejected.",
    "T13 double-checked: the Sonnet reviewer independently re-ran tsc + vitest inside the "
    "worker's worktree and byte-compared the moved code; gates re-ran post-cherry-pick.",
    "Each Sonnet rejection was spot-verified before discarding (T02's ModuleNotFoundError "
    "reproduces from a clean archive of its branch; T05's deleted files don't exist at 407e76b; "
    "ebikes.png references confirmed in build_ebike_stations.py).",
]

CHECKLIST = [
    "Review branch <code>nightly/2026-07-23</code> (6 commits on top of 407e76b) and merge into "
    "<code>fix/unify-voting</code> if T01's direction — updating the TESTS to the numpy "
    "edge_ends signatures — matches your intent for the in-flight branch (the code side is "
    "untouched).",
    "After merging, load <code>http://localhost:3000/m/nyc-walkways</code> and sanity-check "
    "hover/click edge+node targeting, the heatmap, and marker fan-out — hitTest and the Flatbush "
    "index builders moved files (pure cut-paste, but this is the highest-blast-radius change).",
    "Skim the two themes.test.ts cases flagged in §4 — they pass via fallback coincidence and "
    "could pin the preset path tighter (set map.subdomain in the fixtures).",
    "The 13 <code>nightly/0723-Txx</code> work branches still exist for audit; delete them when "
    "done: <code>git branch -D $(git branch --list 'nightly/0723-*' | tr -d ' +')</code>.",
    "If overnight swarms become a habit: the worktree-snapshot staleness (9/13 workers got a "
    "June 2 tree) is the thing to fix first — pin dispatch to an explicit commit and make "
    "workers verify <code>git merge-base</code> themselves before starting.",
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
            file_rows.append(file_detail_html(f, chunk, open_=(len(s["files"]) <= 2)))
        else:
            file_rows.append(f'<ul class="files"><li>{html.escape(f)}</li></ul>')
    diffs_h = f"<h3>Diffs — files touched (click to expand)</h3>{''.join(file_rows)}" if s["files"] else ""
    fixes_h = f"<h3>What changed</h3><ul>{li(s['fixes'])}</ul>" if s["fixes"] else ""
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Context</h3>
      <p>{s['symptom']}</p>
      <h3>Detail</h3>
      <ul>{li(s['cause'])}</ul>
      {fixes_h}
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/tests/conftest.py": {
        "on": ["Flask API"],
        "module": ("Backend unit suite", "pytest + fakeredis fixtures shared across tests/unit — no live services"),
        "file": ("conftest.py", "~35 LOC — fakeredis client + the tiny 3-node/2-edge graph fixture"),
        "outline": [
            ("redis_client fixture", "in-process FakeStrictRedis, decode_responses=True (unchanged)", False),
            ("tiny_graph fixture", "was node_adj lists; now edge_ends np.int32 [e,2] matching build_arrays' real signature", True),
        ],
        "blocks": [
            "import numpy as np",
            "tiny_graph: node_adj [[0],[0,1],[1]] -> edge_ends np.array([[0,1],[1,2]], int32)",
        ],
    },
    "server/tests/unit/test_hydration.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Backend unit suite", "Postgres-replay hydration semantics: mode scoping + idempotence on fakeredis"),
        "file": ("test_hydration.py", "~63 LOC — 3 tests, all were failing on the old build_arrays signature"),
        "outline": [
            ("_hydrate helper", "replays (edge, vt, dir, n) rows through the real vote path", False),
            ("mode-surface test", "build_arrays(..., None) + .all() comparison", True),
            ("wrong-mode test", "same signature fix", True),
            ("idempotence test", "same signature fix", True),
        ],
        "blocks": [
            "3 call sites: build_arrays(votes, EDGES, NODES, NODE_ADJ, ...) -> (..., None, ...)",
            "3 asserts: list == -> (array == list).all()",
        ],
    },
    "server/tests/unit/test_topology_bin.py": {
        "on": ["Flask API"],
        "module": ("Backend unit suite", "GTB2 binary topology encoder round-trips (header, blocks section)"),
        "file": ("test_topology_bin.py", "~90 LOC — encode_topology_bin now takes typed numpy arrays"),
        "outline": [
            ("NODES / EDGES fixtures", "plain lists -> np.array with float64/int32 dtypes", True),
            ("gtb2 encode/decode tests", "assertions unchanged — only input construction moved", False),
        ],
        "blocks": [
            "NODES/EDGES: list literals -> np.array(..., dtype=...) matching encode_topology_bin's contract",
        ],
    },
    "server/tests/unit/test_vote_counts.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Backend unit suite", "count accumulation -> build_arrays net/breakdown derivation"),
        "file": ("test_vote_counts.py", "~80 LOC — the TypeError-at-vote_store.py:390 test lives here"),
        "outline": [
            ("count accumulation tests", "pure Redis-hash math (unchanged)", False),
            ("test_build_arrays_reflects_net_and_breakdown", "uses the fixture's edge_ends + .all()", True),
        ],
        "blocks": [
            "build_arrays fed tiny_graph['edge_ends'] (np.int32) instead of node_adj lists",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "the heatmap + interaction megacomponent: topology fetch, vote paint, hit-testing, markers, proposals"),
        "file": ("GraphLayer.tsx", "5,133 -> 4,532 LOC — pure helpers extracted to 4 sibling modules, imports rewired"),
        "outline": [
            ("imports", "now pulls from ./spatialLookup, ./geometryHelpers, ./topologyHelpers, ./geocodingHelpers", True),
            ("pure helper definitions", "geometry, Flatbush builders, hitTest, geocode cache, topology utils — MOVED OUT", True),
            ("component + hooks + JSX", "untouched — all stateful code stays", False),
        ],
        "blocks": [
            "import block gains the 4 new sibling modules",
            "~615 lines of module-scope functions/constants deleted here (moved verbatim)",
        ],
    },
    "client-react/src/components/GraphLayer/spatialLookup.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "NEW — spatial index + hit-testing extracted from GraphLayer.tsx"),
        "file": ("spatialLookup.ts", "406 LOC — Flatbush builders (yield-to-main batched), hitTest, block filters, projection"),
        "outline": [
            ("interaction constants", "NODE_END_SHARE, cluster/spread px+ms, vote caps, INDEX_YIELD_BATCH", True),
            ("hover/hit types", "HoverEdge/HoverNode/HitResult/ResolvedSelection/VoteTypeRow", True),
            ("buildNodeIndex / buildEdgeIndex", "async Flatbush construction with setTimeout yields", True),
            ("hitTest / blockFiltersAt / adjShortestInBlock / projectOntoEdge", "the pointer-resolution core", True),
        ],
        "blocks": ["entire file is new (verbatim moves from GraphLayer.tsx)"],
    },
    "client-react/src/components/GraphLayer/geometryHelpers.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "NEW — pure geometry + heat-scale constants"),
        "file": ("geometryHelpers.ts", "79 LOC"),
        "outline": [
            ("pointToSegmentDist / haversineMeters / arrayMax", "pure math", True),
            ("HEAT_FULL_SCALE / NEG_HEAT_FULL_SCALE / HEATMAP_OPACITY", "the two-armed heat normalization anchors (50 / 10)", True),
        ],
        "blocks": ["entire file is new (verbatim moves)"],
    },
    "client-react/src/components/GraphLayer/topologyHelpers.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "NEW — topology-coordinate utilities"),
        "file": ("topologyHelpers.ts", "83 LOC"),
        "outline": [
            ("targetLatLng / rbtpDisplayPos", "edge/node/proposal -> display coordinates", True),
            ("spreadKeyEdge / spreadKeyRoute / votesMatchTopology", "fan-out keys + the topology/vote dimension guard", True),
        ],
        "blocks": ["entire file is new (verbatim moves)"],
    },
    "client-react/src/components/GraphLayer/geocodingHelpers.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "NEW — reverse-geocode caching"),
        "file": ("geocodingHelpers.ts", "53 LOC"),
        "outline": [
            ("geocodeCache / geocodeInFlight", "module-level memo + dedupe sets", True),
            ("resolveAddress", "cached /api/reverse-geocode fetch", True),
        ],
        "blocks": ["entire file is new (verbatim moves)"],
    },
    "client-react/src/mapStyles.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client theming", "map style resolution + raster tile URLs + heat gradient CSS"),
        "file": ("mapStyles.test.ts", "NEW — 127 LOC, 14 tests over the pure exports of mapStyles.ts"),
        "outline": [
            ("getMapStyle", "known styles, default fallback", True),
            ("maplibreRasterTiles", "tile URL templates", True),
            ("heatGradientCss", "gradient string construction", True),
        ],
        "blocks": ["entire file is new"],
    },
    "client-react/src/themes.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client theming", "vote-type icon resolution + per-map theme building"),
        "file": ("themes.test.ts", "NEW — 213 LOC, 25 tests over iconSrc/iconForLabel/theme building"),
        "outline": [
            ("icon resolution", "iconSrc, iconForLabel label matching + fallback", True),
            ("theme building", "preset map themes; NOTE: 2 cases assert via DEFAULT_STYLE fallback coincidence (see §4)", True),
        ],
        "blocks": ["entire file is new"],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("Docs", "the canonical graph=storage / blocks=aggregation / proposals=client model description"),
        "file": ("three-layer-model.md", "346 -> 355 LOC — section 2.4 (block heat display) rewritten"),
        "outline": [
            ("layers 1-2 storage/aggregation", "unchanged", False),
            ("§2.4 heat display", "was 'up + down, downvotes read hot'; now signed top-proposal differential, two-armed log norm, cold arm, zero invisible", True),
            ("layer 3 proposals + invariants", "unchanged", False),
        ],
        "blocks": [
            "block_votes[] description: 'total activity (up + down)' -> 'total deduped activity per block'",
            "heat display paragraph rewritten around topProposalDiffs (voteApply.ts) + HEAT_FULL_SCALE/NEG_HEAT_FULL_SCALE",
        ],
    },
    ".gitignore": {
        "on": ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"],
        "module": ("Repo hygiene", "ignore rules"),
        "file": (".gitignore", "+4 — exact-path scratch artifacts"),
        "outline": [
            ("existing rules", "env/, node_modules, dump.rdb (unanchored), exports/ ...", False),
            ("scratch artifacts section", "terraform/tfplan-donate, changelog/changes.diff", True),
        ],
        "blocks": ["append: terraform/tfplan-donate + changelog/changes.diff with a comment"],
    },
    "server/build_ebike_stations.py": {
        "on": ["Flask API"],
        "module": ("E-bike station network", "geocodes the 50 Lyft charging-station intersections into the station graph"),
        "file": ("build_ebike_stations.py", "250 LOC — only the two source-image comment references changed"),
        "outline": [
            ("module docstring", "transcription source now cited as screenshots/ebikes.png", True),
            ("station intersection list comment", "same path update", True),
            ("geocoding + graph build", "unchanged", False),
        ],
        "blocks": ["2 comment lines: ebikes.png -> screenshots/ebikes.png"],
    },
    "drag problem.png": {
        "on": ["React / Leaflet client"],
        "module": ("Repo hygiene", "debug screenshot accidentally committed at repo root"),
        "file": ("drag problem.png", "132 KB binary — unreferenced (git grep verified)"),
        "outline": [("binary", "deleted", True)],
        "blocks": ["git rm"],
    },
    "logo problem.png": {
        "on": ["React / Leaflet client"],
        "module": ("Repo hygiene", "debug screenshot accidentally committed at repo root"),
        "file": ("logo problem.png", "152 KB binary — unreferenced (git grep verified)"),
        "outline": [("binary", "deleted", True)],
        "blocks": ["git rm"],
    },
    "screenshots/ebikes.png": {
        "on": ["Flask API"],
        "module": ("Repo hygiene", "the Lyft charging-map source image build_ebike_stations.py was transcribed from"),
        "file": ("ebikes.png", "310 KB binary — moved from repo root to screenshots/ (still referenced, so kept)"),
        "outline": [("binary", "git mv ebikes.png screenshots/ebikes.png", True)],
        "blocks": ["rename only; referencing comments updated in build_ebike_stations.py"],
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
    <div class="dateline">{DATE} · branch <code>nightly/2026-07-23</code> (off <code>fix/unify-voting</code> @ 407e76b)</div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a>{diff_link}
  </nav>

  <p class="lede">An overnight experiment in delegated maintenance: 13 nightly tasks (test coverage, dead
  code, docs consolidation, one refactor) were dispatched to Haiku workers in isolated worktrees, each
  branch adversarially reviewed by a Sonnet agent, and only reviewer-approved, test-gated work was
  integrated. Five tasks landed: the backend suite is fully green for the first time on this branch
  (75/75, was 68/7), the client suite grew from 258 to 297 tests, GraphLayer.tsx shed 615 lines of pure
  helpers into four focused modules, the three-layer-model doc caught up with differential block heat,
  and ~600&nbsp;KB of stray screenshots left the repo root. The other eight tasks were rejected — most
  because their worktrees were silently snapshotted from a seven-week-old commit, which the reviewers
  caught by checking <code>git merge-base</code> against the dispatch commit and refusing to trust any
  test evidence that didn't reproduce from the committed branch alone.</p>

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
    Generated from <code>changelog/changes-nightly-swarm.diff</code> by <code>changelog/build_nightly_swarm_report.py</code>.
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
