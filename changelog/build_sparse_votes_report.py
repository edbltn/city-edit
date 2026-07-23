#!/usr/bin/env python3
"""Generate the HTML changelog report for the sparse graph-votes format,
brotli topology, and the first-map-load P99 beacon.

Run from repo root:
  git diff -- server/... client-react/... terraform/monitoring.tf \
    > changelog/changes.diff
  python changelog/build_sparse_votes_report.py
Reads changelog/changes.diff, writes changelog/2026-07-22-sparse-votes-mapload-p99.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-07-22-sparse-votes-mapload-p99.html")

DATE = "2026-07-22"
TITLE = "Sparse vote bodies (the bikepaths OOM), brotli topology, and a first-load P99 dashboard"


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
        "id": "sparse",
        "tag": "Backend + Frontend",
        "title": "1 · The bikepaths mobile crash — the vote body was the last boxed giant",
        "symptom": (
            "Opening bikepaths.cityedit.org (map <code>nyc-bikes</code>, the full NYC streets "
            "graph, 906K votes) on mobile Safari showed the loading indicator twice and died with "
            "“a problem repeatedly occurred” — WebKit's message after the tab OOM-crashes, "
            "auto-reloads once, and crashes again. The walkways map on the SAME graph survived."
        ),
        "cause": [
            "The topology was migrated to typed arrays after the last mobile OOM — but "
            "<code>/api/graph-votes</code> never was. Its dense JSON body carries arrays sized to "
            "the graph: 3.3M <code>edge_votes</code> + 3.3M <code>edge_vote_types</code> + "
            "1.35M×2 node arrays + 290K×2 block arrays ≈ <strong>9.3M JSON values</strong> "
            "(26MB decompressed). <code>JSON.parse</code> materializes every empty "
            "<code>[]</code> as a DISTINCT boxed JS array — ~3.3M of them for edges alone, "
            "roughly 250–400MB of transient+resident heap on a phone whose tab jetsams around "
            "1–1.5GB.",
            "Why bikes and not walkways: nyc-bikes has 4.5–8× the vote density (37K voted edges, "
            "48K voted nodes, 29K lit blocks vs 8K/8.6K/3.4K) — so everything DOWNSTREAM of the "
            "parse (per-type breakdown sub-arrays, the ~29K-block first "
            "<code>setFeatureState</code> sweep, route-proposal nets, pins) is also several times "
            "heavier, on top of the same fixed 9.3M-slot parse. The peak lands just past jetsam "
            "on bikes and just under it on walkways.",
            "The loading indicator showing twice is two stacked things: a benign two-phase "
            "loader (config-resolve splash → map+heatmap splash) on every cold load, and "
            "Safari's own crash-and-auto-reload. The React ErrorBoundary can't help — a jetsam "
            "kill terminates the process; there is no exception to catch.",
        ],
        "fixes": [
            "<strong>format=sparse wire format.</strong> The server now builds a sparse twin of "
            "every vote snapshot: nonzero <code>(idx, val)</code> pairs for edge/node/block "
            "totals (numpy <code>flatnonzero</code> — no O(graph) Python loop on the request "
            "path) and voted-only dicts for the per-type breakdowns, sourced from the sparse "
            "dicts <code>build_arrays</code>/<code>build_block_arrays</code> already hold. "
            "Breakdown keys come from the vote dicts, NOT from nonzero totals — counter-votes "
            "can cancel an edge's net to 0 while its breakdown must survive (the nyc-bikes "
            "Broadway case).",
            "<strong>Typed + holey decode client-side.</strong> <code>decodeSparseVotes()</code> "
            "rebuilds <code>edge_votes</code>/<code>node_votes</code>/<code>block_votes</code> as "
            "<code>Int32Array</code> and the breakdowns as HOLEY arrays (entries only at voted "
            "indices). Every consumer already guards with <code>?? []</code> / "
            "<code>if (!pairs)</code>, and the mutation paths materialize a fresh array before "
            "writing — holes are safe where a shared <code>[]</code> sentinel would be "
            "catastrophic (voteApply pushes in place). ~600K materialized values instead of "
            "9.3M boxed slots.",
            "<strong>Both formats served side by side.</strong> The dense body stays the default "
            "for old cached clients; sparse rides the same snapshot cache entry with its own "
            "<code>-sp</code> ETag token. IndexedDB now caches the raw sparse payload (~50× "
            "smaller structured-clone) and re-decodes on read; old dense cache entries still "
            "decode as-is.",
            "<strong>Compat fix en route:</strong> <code>broadcastBlockVotes</code> gated on "
            "<code>Array.isArray</code>, which an <code>Int32Array</code> fails — it now checks "
            "length, so block heat survives the typed migration.",
        ],
        "files": [
            "server/vote_store.py — build_arrays returns _evt_sparse/_nvt_sparse companions",
            "server/block_votes.py — build_block_arrays returns _bvt_sparse",
            "server/app.py — _sparse_votes_body(), sp_body/sp_gz cache twins, format=sparse serving, -sp ETag",
            "client-react/src/utils/sparseVotes.ts — NEW: decodeSparseVotes / isSparseVotes",
            "client-react/src/components/GraphLayer/GraphLayer.tsx — fetch + IDB cache + broadcast changes",
            "client-react/src/types/index.ts — GraphData vote fields widen to number[] | Int32Array",
        ],
    },
    {
        "id": "brotli",
        "tag": "Backend",
        "title": "2 · First loads: brotli for the 50MB topology blob",
        "symptom": (
            "A first-time visitor to any NYC map downloads the GTB2 binary topology: 50MB raw, "
            "18.8MB gzipped — the dominant network cost of a cold load (5–15s on mobile LTE)."
        ),
        "cause": [
            "The blob was pre-gzipped once per graph load (level 6) and served with "
            "<code>Content-Encoding: gzip</code>; brotli was never wired in even though every "
            "current browser advertises <code>br</code>.",
        ],
        "fixes": [
            "<strong>Pre-brotli'd twin (quality 5).</strong> Measured on the real NYC blob: "
            "<strong>15.5MB vs 18.8MB (−17%) and 0.6s vs 1.4s to build</strong> — strictly "
            "better than the gzip it shadows. Served when the client's Accept-Encoding tokens "
            "include <code>br</code>; gzip stays the fallback, identity last. Guarded import — "
            "no brotli package, no br variant, no error.",
            "Prewarm now pre-compresses both twins, and <code>unload()</code> drops the br "
            "blob with the rest.",
        ],
        "files": [
            "server/graph_registry.py — topology_binary_br(), unload() clears it",
            "server/app.py — token-parsed Accept-Encoding negotiation in graph_topology, prewarm",
            "server/requirements.in / requirements.txt — brotli (uv pip compile)",
        ],
    },
    {
        "id": "p99",
        "tag": "Frontend + Infra",
        "title": "3 · First-map-load P99 on the System Health dashboard",
        "symptom": (
            "No visibility into what users actually experience on first load — Cloud Run's "
            "request_latencies only see individual API calls, not the navigation-to-map-visible "
            "journey."
        ),
        "cause": [
            "The client had no telemetry at all; the existing P99 tiles chart server-side "
            "per-request latency.",
        ],
        "fixes": [
            "<strong>One beacon per page load.</strong> When the full-screen loader first "
            "dismisses (base map ready AND heatmap painted), the client sendBeacons "
            "<code>{map, ms, cachedTopo, nav}</code> — <code>ms</code> measured from navigation "
            "start via <code>performance.now()</code>, <code>cachedTopo</code> separating true "
            "cold first loads from IndexedDB repeat visits. Sent as a plain string: a Blob typed "
            "application/json needs a CORS preflight sendBeacon can't perform, and Chrome "
            "silently drops it (found live in dev).",
            "<strong>/api/client-timing → [MAPLOAD] log line.</strong> Validates and sanitizes, "
            "always 204s (never an error-noise source), logs one line for the log-based metric.",
            "<strong>cityedit_map_load_ms distribution metric + dashboard row.</strong> "
            "Log-based DISTRIBUTION (ms, 50ms–10min buckets) labeled by map / cached / nav, plus "
            "two new System Health tiles: a First-map-load P99 scorecard and a cold-vs-warm "
            "P50/P99 chart. Terraform, targeted-apply safe per monitoring.tf's header.",
        ],
        "files": [
            "client-react/src/utils/loadTelemetry.ts — NEW: reportTopologySource / reportMapLoaded",
            "client-react/src/App.tsx — beacon on first loader dismissal",
            "server/app.py — /api/client-timing",
            "terraform/monitoring.tf — google_logging_metric.map_load_ms + dashboard row",
        ],
    },
]

VERIFY = [
    "Server: sparse↔dense reconstruction checked cell-by-cell on <code>test-central-park</code> and the full <code>nyc-bikes</code> (3.3M edges): ALL CONSISTENT, including 4.8K net-zero edges that keep their counter-vote breakdowns.",
    "Topology: local Flask serves <code>Content-Encoding: br</code> at 15.5MB (was 18.8MB gzip) when br is accepted; gzip fallback intact.",
    "Client: <code>tsc --noEmit</code> clean; <code>vitest run</code> 253 passed / 1 skipped, including new sparse-decode and holey/typed mutation tests; production build passes.",
    "Live (dev, real Chrome): <code>/m/nyc-bikes</code> loads via format=sparse — topo ready, 20 route proposals recomputed from holey arrays, no console errors; beacon lands as <code>[MAPLOAD] map=nyc-bikes ms=17162 cached_topo=1 nav=navigate</code> (17s = the 15s occluded-window mapReady backstop, not real paint time).",
    "Terraform: <code>terraform validate</code> clean.",
    "Server unit suite: 7 pre-existing failures (numpy/list fixture drift) — identical with these changes stashed; nothing new fails.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-bikes</code> in a NORMAL window: heatmap + pins appear as before; DevTools Network shows <code>graph-votes?…&format=sparse</code> and a much smaller JSON body.",
    "Vote on an edge and watch the heat update live (WS delta onto the Int32Array path), then reload — the vote survives.",
    "On prod after deploy: from a phone, open bikepaths.cityedit.org fresh (Safari, private tab) — it should load without the crash-reload loop.",
    "In DevTools on prod: <code>graph-topology?format=bin</code> response header should read <code>content-encoding: br</code>.",
    "After ~a day of traffic: the System Health dashboard's new “First map load” row shows data, split cold vs warm.",
    "<code>gcloud logging read 'textPayload:\"[MAPLOAD]\"' --limit 5</code> shows real beacons with sane ms values.",
]


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


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


# ── Hierarchical "where does this block sit" context ──────────────────────────
SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client", "Terraform / GCP"]

FILE_CONTEXT = {
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask · vote storage", "packed-field Redis votes → per-edge/node arrays for the heatmap"),
        "file": ("vote_store.py", "~460 LOC — codec, build_arrays (snapshot builder), OSM→edge mapping"),
        "outline": [
            ("53-bit vote codec", "pack/unpack edge·mode·vt·dir", False),
            ("read_all / revision keys", "Redis hash access", False),
            ("build_arrays", "votes → dense arrays; NOW also sparse companions", True),
            ("osm_nodes_to_edge_ids", "OSRM annotation mapping", False),
        ],
        "blocks": [
            "build_arrays return — adds _evt_sparse/_nvt_sparse dicts keyed from edge_vt/node_vt_merged (O(voted))",
        ],
    },
    "server/block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask · block aggregation", "deduped per-block vote aggregates (bagg:) → block arrays"),
        "file": ("block_votes.py", "~250 LOC — block field codec + aggregate rebuild + array projection"),
        "outline": [
            ("block field codec", "pack/unpack block·vt·dir", False),
            ("rebuild_from_db", "bagg: reconstruction", False),
            ("build_block_arrays", "aggregate hash → arrays; NOW also sparse companion", True),
        ],
        "blocks": [
            "build_block_arrays return — adds _bvt_sparse dict keyed from bvt (voted blocks only)",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask · API surface", "all HTTP endpoints, vote-snapshot cache, prewarm, WS hub"),
        "file": ("app.py", "~2200 LOC — the graph-votes snapshot LRU + graph-topology + admin/stats live here"),
        "outline": [
            ("vote cache (LRU + single-flight)", "_vote_cache, _entry_bytes, debounce", True),
            ("_build_graph_votes_body_locked", "snapshot build; NOW builds the sparse twin too", True),
            ("_sparse_votes_body", "NEW — nonzero idx/val + voted-only breakdown dicts", True),
            ("prewarm", "graph + vote-body + topology-blob warm; NOW also br", True),
            ("/api/client-timing", "NEW — [MAPLOAD] beacon sink", True),
            ("/api/graph-topology", "GTB2 bin serving; NOW br/gzip/identity negotiation", True),
            ("/api/graph-votes", "snapshot serving; NOW format=sparse + -sp ETag", True),
            ("vote cast path / WS / admin", "unchanged", False),
        ],
        "blocks": [
            "_entry_bytes — counts sp_body/sp_gz toward the cache budget",
            "_build_graph_votes_body_locked — pops _*_sparse keys, dumps sp_body/sp_gz twins",
            "_sparse_votes_body() — flatnonzero idx/val pairs + metadata + block section",
            "_prewarm — topology_binary_br() alongside gz",
            "_graph_votes_etag — sparse flag → -sp token",
            "graph_topology — token-parsed Accept-Encoding, br → gzip → identity",
            "client_timing() — validate, sanitize, log [MAPLOAD], always 204",
            "graph_votes — format=sparse serves the sp twins",
        ],
    },
    "server/graph_registry.py": {
        "on": ["Flask API"],
        "module": ("Flask · graph registry", "LRU of loaded CityGraphs; GTB2 topology encoding + blob cache"),
        "file": ("graph_registry.py", "~520 LOC — CityGraph lifecycle, topology_binary/_gz, blocks metadata"),
        "outline": [
            ("encode_topology_bin", "GTB2 layout", False),
            ("CityGraph load/unload", "npz load, residents, unload frees; NOW clears br blob", True),
            ("topology_binary / _gz", "blob + pre-gzip cache", False),
            ("topology_binary_br", "NEW — pre-brotli'd twin (q5, guarded import)", True),
        ],
        "blocks": [
            "topology_bin_br resident + topology_binary_br() (15.5MB vs 18.8MB gzip on NYC, 0.6s to build)",
            "unload() — drops the br blob with the rest",
        ],
    },
    "server/requirements.in": {
        "on": ["Flask API"],
        "module": ("Flask · dependencies", "hand-written top-level deps (uv pip compile source)"),
        "file": ("requirements.in", "~25 lines"),
        "outline": [("deps list", "adds brotli", True)],
        "blocks": ["+ brotli"],
    },
    "server/requirements.txt": {
        "on": ["Flask API"],
        "module": ("Flask · dependencies", "uv-pip-compiled lockfile (installed by Dockerfile.overlay too)"),
        "file": ("requirements.txt", "locked pins"),
        "outline": [("locked pins", "brotli==1.2.0", True)],
        "blocks": ["+ brotli==1.2.0"],
    },
    "client-react/src/utils/sparseVotes.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "wire-format decoding for the vote payload"),
        "file": ("sparseVotes.ts", "NEW ~95 LOC — SparseVotesPayload type + decodeSparseVotes/isSparseVotes"),
        "outline": [
            ("SparseVotesPayload", "wire shape (idx/val pairs + voted dicts)", True),
            ("denseInt32 / holeyVt", "typed + holey reconstruction", True),
            ("decodeSparseVotes", "sparse → GraphData vote fields", True),
        ],
        "blocks": ["entire file is new"],
    },
    "client-react/src/utils/loadTelemetry.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "first-load telemetry"),
        "file": ("loadTelemetry.ts", "NEW ~48 LOC — one beacon per page load"),
        "outline": [
            ("reportTopologySource", "cold vs warm label from GraphLayer", True),
            ("reportMapLoaded", "sendBeacon as text/plain (CORS-safelisted)", True),
        ],
        "blocks": ["entire file is new"],
    },
    "client-react/src/utils/sparseVotes.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · tests", "vitest"),
        "file": ("sparseVotes.test.ts", "NEW — decode round-trip, holey independence, block omission"),
        "outline": [("decode tests", "6 tests", True)],
        "blocks": ["entire file is new"],
    },
    "client-react/src/types/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · types", "shared TS shapes"),
        "file": ("index.ts", "~200 LOC — GraphData is the merged topology+votes shape"),
        "outline": [
            ("GraphData topology fields", "typed arrays (unchanged)", False),
            ("GraphData vote fields", "widen to number[] | Int32Array; holey docs; n_edges/n_nodes stamps", True),
        ],
        "blocks": ["vote totals — number[] | Int32Array; breakdowns documented holey; dimension-stamp fields added"],
    },
    "client-react/src/App.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · app shell", "map resolution, provider tree, full-screen loaders"),
        "file": ("App.tsx", "~200 LOC — MapApp (config splash) + AppContent (map splash)"),
        "outline": [
            ("FullScreenLoader", "the two-phase splash", False),
            ("AppContent", "loader dismissal; NOW fires the load beacon", True),
            ("MapApp", "config resolve + redirect", False),
        ],
        "blocks": ["AppContent effect — !isInitialLoading → reportMapLoaded() (idempotent)"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "topology/vote loading, heatmap, proposals, deltas"),
        "file": ("GraphLayer.tsx", "~5000 LOC — the load effect (topology → cached votes → live votes) is the heart"),
        "outline": [
            ("arrayMax / votesMatchTopology", "helpers; NOW ArrayLike", True),
            ("broadcastBlockVotes", "block heat event; NOW typed-array safe", True),
            ("fetchVotes (refresh)", "NOW format=sparse + decode", True),
            ("load effect", "topology (reports source) → cached votes (decode) → live votes (sparse fetch, raw cached)", True),
            ("delta apply / redraw / tooltips", "unchanged — guarded index reads", False),
        ],
        "blocks": [
            "imports — sparseVotes + loadTelemetry",
            "arrayMax(ArrayLike<number>), votesMatchTopology(ArrayLike)",
            "broadcastBlockVotes — length check replaces Array.isArray",
            "fetchVotes — &format=sparse + isSparseVotes decode",
            "load effect — reportTopologySource, cached-votes decode, sparse fetch, cache voteRaw",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · tests", "vitest"),
        "file": ("voteApply.test.ts", "mutation-path tests"),
        "outline": [
            ("existing suites", "dense-array mutations", False),
            ("sparse-decoded suite", "NEW — Int32Array + holey mutations", True),
        ],
        "blocks": ["new describe: applyEdgeVoteChange/applyMyVoteChange/applyAuthoritativeCounts/applyBlockCounts on typed+holey data"],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "MapLibre base map + block feature-state heat"),
        "file": ("MapLibreBackground.tsx", "~600 LOC — PMTiles sources, block heat diff-apply"),
        "outline": [
            ("BlockVotesDetail", "event payload type; NOW ArrayLike", True),
            ("heat apply", "diff-applied setFeatureState (unchanged — index loop)", False),
        ],
        "blocks": ["BlockVotesDetail.blockVotes: ArrayLike<number>"],
    },
    "terraform/monitoring.tf": {
        "on": ["Terraform / GCP"],
        "module": ("Terraform · monitoring", "pure Cloud Monitoring config — targeted-apply safe by design"),
        "file": ("monitoring.tf", "~1200 LOC — log metrics, alerts, the System Health mosaic dashboard"),
        "outline": [
            ("api_latency log metric", "server-side per-endpoint distribution", False),
            ("map_load_ms log metric", "NEW — client [MAPLOAD] distribution (ms, map/cached/nav labels)", True),
            ("alert policies", "unchanged", False),
            ("system_health dashboard", "NEW row: first-load P99 scorecard + cold/warm chart", True),
        ],
        "blocks": [
            "google_logging_metric.map_load_ms — textPayload [MAPLOAD], ms= extractor, 50ms–10min buckets",
            "dashboard tiles yPos=83 — P99 scorecard + cold-vs-warm P50/P99 xyChart",
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

  <p class="lede">The bikepaths mobile crash traced to the one payload the typed-array migration missed: the dense 26MB /api/graph-votes JSON, whose parse materializes ~9M boxed JS values. Votes now travel sparse and decode into typed arrays, the 50MB topology blob ships as brotli (−17%), and a first-map-load beacon feeds a new P99 row on the System Health dashboard.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_sparse_votes_report.py</code>.
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
