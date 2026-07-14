#!/usr/bin/env python3
"""Generate the map-load-OOM + compact-arrays + 30-tenant-scaling changelog report.

Run from repo root: python changelog/build_oom_scaling_report.py
Reads changelog/changes.diff
  (git diff 0168569..HEAD -- . ':(exclude)changelog' — the exclude keeps the
  concurrent junction-disjoint report's own docs commit out of this diff),
writes changelog/2026-07-13-map-load-oom-and-scaling.html

Modeled on build_vote_mitigations_report.py (same styles + hierarchical context
diagrams). Covers commits 9962396, 2faac4a, cf1d576, 1f5f782, 2a6696f, dd067c5,
edecbf9 — plus bbbd0b2 and 8fb0df3, which land in the same range.
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-07-13-map-load-oom-and-scaling.html")

DATE = "2026-07-13"
TITLE = "Map-load OOM fix, compact-array graphs, 30-tenant scaling"


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
        elif raw.startswith("diff ") or raw.startswith("index ") or raw.startswith("new file") or raw.startswith("deleted file"):
            cls = "d-meta"
        elif raw.startswith("+"):
            cls = "d-add"
        elif raw.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        out.append(f'<span class="{cls}">{esc or "&nbsp;"}</span>')
    return "\n".join(out)


# Measured before/after under identical conditions.
RESULT_ROWS = [
    ("worker boot → all graphs warm (prod)",
     "minutes of unpickling, then SIGKILL at 8Gi — never finished",
     "~90 s prewarm, then steady"),
    ("full 7-city + 21-map prewarm RSS (local)", "~6 Gi and climbing (OOM)", "1.35 GB"),
    ("NYC graph runtime artifact", "311 MB pkl → multi-GB boxed-object transient", "37 MB npz, ~1 s load"),
    ("map load during a crash loop", "120 s+ hangs, 502s; Philly maps 500", "sub-second warm; Philly serves"),
    ("graph_votes p95 (40-agent swarm, local)", "13.3 s", "0.64 s"),
    ("routes p95 (40-agent swarm, local)", "9.2 s", "0.33 s (worst p95 of ANY interaction)"),
    ("/api/maps/<slug> under a 40-agent join wave (prod)", "60% client timeouts", "p95 0.85 s after index + single-flight"),
    ("NYC topology gzip", "~0.4 core-s PER REQUEST (nginx; ~12 core-s per 30-tenant join wave)", "once at prewarm, level 6 → 18.8 MB served verbatim"),
    ("cheap endpoints during a prod join wave (server-side)", "1.4–2.3 s p95 (CPU-starved by per-request gzip)", "graph_version p95 0.55 s · map_meta p95 0.85 s"),
    ("topology conditional requests", "nginx weakened the ETag to W/… — 304s silently dead", "strong ETag, W/ tolerated — 304s work"),
    ("point-vote snap on NYC", "1.3M-node linear Python scan per request", "kdtree query"),
    ("final prod swarm — 40 agents / 16 maps / 10 cycles (rev 00097)",
     "iter1 (00094): 60% map-config timeouts · iter2 (00096): gzip CPU tail",
     "ALL BUDGETS MET — 0 errors in ~1,150 timed interactions"),
]


def results_html():
    trs = "".join(
        f"<tr><td>{html.escape(l)}</td><td>{html.escape(b)}</td>"
        f"<td class='good'>{html.escape(a)}</td></tr>"
        for l, b, a in RESULT_ROWS)
    return (f"<table class='cmp'><thead><tr><th></th><th>before</th>"
            f"<th>after</th></tr></thead><tbody>{trs}</tbody></table>")


SECTIONS = [
    {
        "id": "oom",
        "tag": "Flask API · production incident",
        "title": "1 · The OOM crash loop — every map load queued behind a doomed unpickle",
        "symptom": (
            "Prod was OOM-SIGKILL crash-looping every ~3 minutes. Map loads hung 120&nbsp;s+ or 502'd, "
            "and Philly maps 500'd outright (their graph had never been baked into the image)."
        ),
        "cause": [
            "Each gunicorn worker boot unpickled the per-city <strong>networkx</strong> walk graphs. "
            "The decode transiently inflates to several GB of boxed Python objects per city — the worker "
            "died mid-prewarm at the 8&nbsp;Gi limit, Cloud Run restarted it, and the loop repeated.",
            "Every incoming map load queued behind the doomed graph load on the single gevent worker, "
            "so the outage presented as hangs and 502s, not as a clean error.",
        ],
        "fixes": [
            "<strong>Mitigated live first</strong>: a 12&nbsp;Gi memory bump broke the loop and restored service "
            "while the real fix was built.",
            "<strong>Fixed properly</strong> by removing networkx/pickle from the runtime entirely — "
            "sections 2–3 below. Prod prewarm is now ~90&nbsp;s for every city and map, at 1.35&nbsp;GB RSS.",
        ],
        "files": ["server/python_router.py", "server/graph_arrays.py", "terraform/main.tf"],
    },
    {
        "id": "arrays",
        "tag": "Flask API · graph artifacts",
        "title": "2 · Compact-array runtime artifact — walk_graph_arrays.npz",
        "symptom": (
            "The runtime's only graph source was <code>walk_graph.pkl</code> — a 311&nbsp;MB pickle for NYC "
            "whose decode needs several GB and minutes of CPU, paid by every worker boot."
        ),
        "cause": [
            "The pickle stores the full networkx graph: a Python dict per node and per edge. Everything the "
            "runtime actually needs — coords, edge endpoints/lengths, interned name/highway tags, adjacency — "
            "fits in flat numpy arrays an order of magnitude smaller.",
        ],
        "fixes": [
            "<strong>New build-time converter</strong> <code>graph_arrays.py</code>: pkl → "
            "<code>walk_graph_arrays.npz</code> (coords, node_osmid, edge_u/v/len, coded name/highway tables, "
            "JSON meta). NYC: 37&nbsp;MB npz, ~1&nbsp;s load. Adjacency is not stored — a vectorized CSR builder "
            "derives it at load, faster than reading it from disk.",
            "<strong>Edge iteration order is preserved exactly</strong> (same <code>edges(data=True)</code> walk, "
            "same skip rule as the legacy loader), so edge ids — what every stored vote and baked block references — "
            "are bit-identical. Verified: every city's <code>topology_etag</code> matches the etag stamped in its "
            "baked edge_blocks artifacts, all 7 cities.",
            "<code>python_router.py</code> loads ONLY the npz; networkx and pickle are build-time imports now. "
            "<code>build_graph</code> emits both artifacts so a fresh build is servable immediately; the Dockerfile "
            "builds Philly and emits npz on full builds.",
        ],
        "files": ["server/graph_arrays.py", "server/python_router.py", "Dockerfile"],
    },
    {
        "id": "citygraph",
        "tag": "Flask API · memory",
        "title": "3 · Array-native CityGraph — ~500 MB of dicts become ~60 MB of numpy",
        "symptom": (
            "Even after load, each city's resident CityGraph carried ~2.5&nbsp;GB (NYC) of Python lists, "
            "dicts and a retained ~150&nbsp;MB topology-JSON string."
        ),
        "cause": [
            "<code>osm_to_graph_idx</code> (~60&nbsp;MB) and <code>node_pair_to_edge</code> (~450&nbsp;MB of "
            "tuple-keyed dict) were boxed-object maps; <code>node_adj</code> was a list-of-lists; point-vote "
            "snapping linearly scanned all 1.3M NYC nodes in Python per request; the topology JSON string "
            "stayed resident for every network just to serve an endpoint streets never use.",
        ],
        "fixes": [
            "<strong>IntMap / IntPairMap</strong>: immutable sorted-numpy maps with dict-compatible "
            "<code>.get()</code> — binary search over flat arrays, duplicate pairs keeping the LAST value to "
            "match legacy dict-assignment semantics (parallel edges resolve identically).",
            "<strong>CSR node adjacency</strong> (<code>NodeAdjacency</code>): lexsort-ordered exactly like the "
            "legacy per-edge append loop, because <code>snap_point_to_edge</code> returns the FIRST incident "
            "edge — ordering is behavior. Self-edges recorded once so station networks keep snapping to "
            "their own edge.",
            "<strong>kdtree point snap</strong> replaces the 1.3M-node linear scan.",
            "<strong>topology JSON retained only for station networks</strong> (tiny, names matter); street "
            "networks hash the etag and drop the string. The JSON encoding remains a compatibility contract — "
            "baked blocks are stamped with its sha256.",
            "Dead <code>coord_to_edge_idx</code> / <code>coord_to_node_idx</code> reverse maps deleted; "
            "<code>encode_topology_bin</code> vectorized over the arrays.",
        ],
        "files": ["server/graph_registry.py"],
    },
    {
        "id": "snapshot",
        "tag": "Flask API · Redis · vote path",
        "title": "4 · Vectorized vote-snapshot build — graph_votes p95 13.3 s → 0.64 s",
        "symptom": (
            "Under the 40-agent swarm, every debounced graph-votes rebuild stalled the gevent loop for "
            "seconds: graph_votes p95 13.3&nbsp;s, and unrelated routes p95 dragged to 9.2&nbsp;s behind it."
        ),
        "cause": [
            "<code>vote_store.build_arrays</code> looped ALL edges and ALL nodes in pure Python "
            "(~3.3M edges + 1.3M nodes ≈ 4.6M iterations for NYC) per rebuild — work scaled with graph size, "
            "not with how many votes exist.",
        ],
        "fixes": [
            "<strong>Sparse numpy rebuild</strong>: one <code>fromiter</code> over the packed vote fields, "
            "bit-op unpack (eid/mode/vtid/dir), <code>np.add.at</code> scatter-adds for edge nets, and a "
            "sparse node pass — only positive-net edges can light a node, so scatter-max from those edges' "
            "endpoints via <code>edge_ends</code>. Work now scales with VOTED fields.",
            "Untyped empty cells share one immutable <code>_EMPTY</code> list instead of allocating millions "
            "of empty lists per rebuild.",
            "Output equivalence-tested against the legacy implementation; the signature takes "
            "<code>edge_ends</code> (the array) instead of the deleted list-of-lists <code>node_adj</code>.",
        ],
        "files": ["server/vote_store.py", "server/app.py"],
    },
    {
        "id": "pregzip",
        "tag": "Flask API · nginx · topology",
        "title": "5 · Pre-gzipped topology — stop paying 0.4 core-seconds per download",
        "symptom": (
            "Prod swarm iteration 2 (rev 00096): the big endpoints were fine, but the CHEAP endpoints "
            "dragged — graph_version / map_meta at 1.4–2.3&nbsp;s p95 server-side whenever tenants were "
            "joining."
        ),
        "cause": [
            "nginx was gzipping the ~37&nbsp;MB NYC GTB2 topology blob <strong>per request</strong> "
            "(~0.4 core-seconds each). A 30-tenant join wave burned ~12 core-seconds on the 4-CPU "
            "instance and starved everything else sharing it.",
            "Bonus defect: nginx's on-the-fly gzip weakened the topology ETag to <code>W/…</code>, and the "
            "server compared If-None-Match by exact string — so topology <strong>304s had been silently "
            "disabled</strong>: every revisit re-downloaded the full blob.",
        ],
        "fixes": [
            "<strong><code>CityGraph.topology_binary_gz()</code></strong>: the blob is compressed ONCE per "
            "load at level 6 (18.8&nbsp;MB for NYC — smaller than nginx's per-request level 1) and cached "
            "on the CityGraph; zlib releases the GIL so the one-time build doesn't stall the gevent hub. "
            "Prewarm builds it for every map so no tenant's first visitor pays the ~1–2&nbsp;s compression.",
            "<code>graph_topology</code> serves the pre-gzipped bytes verbatim with "
            "<code>Content-Encoding: gzip</code> + <code>Vary: Accept-Encoding</code> — nginx passes it "
            "through untouched, so the strong ETag survives.",
            "<strong>304s fixed</strong>: If-None-Match now tolerates a <code>W/</code> prefix, so clients "
            "that cached a weak validator from the nginx era revalidate correctly.",
            "The swarm's topology budget became <strong>bandwidth-aware</strong> (120&nbsp;s timeout / "
            "90&nbsp;s p95): N agents × ~18&nbsp;MB share ONE test-host downlink and finish together, so "
            "the client-side number measures the pipe, not the server — prod single-stream is ~1.0&nbsp;s "
            "and server-side latency &lt;0.3&nbsp;s; the server signal is errors + Cloud Run latency.",
        ],
        "files": ["server/graph_registry.py", "server/app.py", "server/tests/swarm_interactions.py"],
    },
    {
        "id": "purge",
        "tag": "Flask API + client · dead code",
        "title": "6 · Dead-code purge + logging unification",
        "symptom": (
            "Three unused endpoints and an entire legacy block pipeline were still shipping (and still had "
            "to survive every refactor); hot paths logged at INFO; client errors bypassed the debug channels."
        ),
        "cause": [
            "Layered rewrites left strata behind: the pre-graph-first blocks chain (generic/edge/foot/node/nyc "
            "builders + merge pass + eval suite + NYC planimetric pull), client hooks superseded by "
            "WebSocketContext and the binary topology path, and the coords→edge fallback that quietly masked "
            "real OSRM annotation-mapping failures.",
        ],
        "fixes": [
            "<strong>Endpoints deleted</strong>: <code>/api/nearest-node</code>, <code>/api/graph</code>, "
            "<code>/api/graph.geojson</code>. JSON <code>/api/graph-topology</code> now 410s for street "
            "networks (binary-only); station networks keep JSON.",
            "<strong>coords→edge fallback removed</strong> — an OSRM route resolving to 0 edge ids now logs a "
            "warning and surfaces, instead of being papered over by coordinate rounding.",
            "<strong>Legacy streetscape_blocks pipeline deleted</strong> (~2,900 lines): build_blocks_generic / "
            "build_edge_blocks / build_foot_blocks / build_node_blocks / build_nyc_blocks / merge_degenerate_blocks / "
            "compare_blocks / pull_nyc / plot_blocks / road_classes / run_all.sh + the whole eval/ suite. "
            "<code>build_blocks_graph_first.py</code> is the one blocks builder.",
            "<strong>Client</strong>: nearestNode.ts, useGraphNodes, useWebSocket deleted; the JSON-topology "
            "fallback removed (street networks are binary-only); bare <code>console.error</code> routed "
            "through <code>derror(channel, …)</code>.",
            "<strong>Logging unified</strong>: hot-path INFO demoted to DEBUG (per-route OSRM lines, per-vote "
            "lines), <code>[TAG]</code> prefixes everywhere (<code>[ROUTE]</code>, <code>[OSRM]</code>, "
            "<code>[GEOCODE]</code>, <code>[PREVIEWS]</code>, <code>[ROUTER]</code>, <code>[ARRAYS]</code>).",
        ],
        "files": [
            "server/app.py", "server/vote_store.py", "server/osrm_router.py",
            "server/streetscape_blocks/* (20 files deleted)",
            "client-react/src/hooks/useGraphNodes.ts (deleted)",
            "client-react/src/hooks/useWebSocket.ts (deleted)",
            "client-react/src/utils/nearestNode.ts (deleted)",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/context/RouteContext.tsx",
        ],
    },
    {
        "id": "swarm",
        "tag": "Tests + Flask API + Postgres",
        "title": "7 · Multi-tenant swarm harness — and the map-config stampede it caught",
        "symptom": (
            "No go/no-go instrument existed for \"30 tenants at once\". The first prod swarm run (40 agents) "
            "immediately found one: <code>/api/maps/&lt;slug&gt;</code> hit 60% client timeouts under a join wave."
        ),
        "cause": [
            "Every join-wave request ran <code>fetch_voted_vote_type_labels</code>' GROUP BY over the map's "
            "edge_votes rows (674K total, no covering index) through the 5-connection pool on a "
            "freshly-restarted SQL instance — N concurrent joiners stampeded N aggregates.",
        ],
        "fixes": [
            "<strong>server/tests/swarm_interactions.py</strong>: N agents spread across ALL public maps "
            "(weighted toward a primary), each performing the real client sequence — map meta, graph-version, "
            "binary topology, graph-votes, a held WebSocket, then route→vote→reverse-geocode cycles. EVERY "
            "interaction is individually timed with a hard timeout and a p95 budget; the run exits nonzero on "
            "any budget or error breach. Votes only land on the primary/test maps so tenant data stays clean. "
            "40 agents / 16 maps locally: ALL BUDGETS MET, worst p95 0.33&nbsp;s.",
            "<strong>idx_edge_votes_map_vt_created</strong> (map_slug, vote_type_id, created_at): the aggregate "
            "becomes an index-only scan. Applied to prod live via <code>CREATE INDEX CONCURRENTLY</code>; "
            "schema init creates it for fresh DBs.",
            "<strong>map_get single-flight TTL cache</strong>: 30&nbsp;s in-process cache for public maps with a "
            "per-slug lock, so a join-wave miss costs ONE query burst instead of N. After the index alone the "
            "30-agent prod swarm passed everything except map_meta p95 by 0.05&nbsp;s; single-flight closed the rest.",
        ],
        "files": ["server/tests/swarm_interactions.py", "server/database.py", "server/app.py"],
    },
    {
        "id": "infra",
        "tag": "Infra · Terraform + deploy",
        "title": "8 · Scale for 30+ tenants — and the drift a blanket apply would have shipped",
        "symptom": (
            "Prod had drifted: pinned to maxScale=1 (a resnap-runbook pin never lifted) with concurrency 320 "
            "set out-of-band — one gevent worker served every tenant. And terraform pointed OSRM at "
            "<code>:latest</code>, which in the registry is an OLDER dataset."
        ),
        "cause": [
            "Live-state drift vs terraform: a blanket <code>terraform apply</code> would have silently swapped "
            "the routing network out from under the graphs while \"fixing\" scaling.",
        ],
        "fixes": [
            "<strong>terraform/main.tf</strong>: maxScale 1(!)→8, explicit <code>container_concurrency=200</code> "
            "(each held WebSocket occupies a slot — the ceiling is concurrent viewers, ~1600 fleet-wide), "
            "4&nbsp;vCPU / 8&nbsp;Gi (measured 1.35&nbsp;Gi warm → ~6× headroom; extra cores keep gzip off the "
            "request path), Cloud SQL db-f1-micro → db-g1-small (vote persistence is on the request path).",
            "<strong>OSRM image pinned to the SERVING build tag</strong> in tf — update only alongside a "
            "deliberate dataset rebuild.",
            "<strong>Dockerfile.arrays-overlay + cloudbuild.arrays-overlay.yaml</strong>: overlays code/client "
            "onto the digest-pinned serving image and converts the BASE image's OWN pickles in-image "
            "(prod graphs differ from local — shipping locally-built npz would desync votes/blocks), plus "
            "stages the missing philly/test-cp/test-mid artifact sets. Runs on an E2_HIGHCPU_32 builder "
            "(the conversion needs the one last networkx unpickle, ~6&nbsp;GB transient for NYC).",
        ],
        "files": ["terraform/main.tf", "Dockerfile.arrays-overlay", "cloudbuild.arrays-overlay.yaml", "Dockerfile", ".gitignore"],
    },
    {
        "id": "proposals",
        "tag": "React / Leaflet client · proposals",
        "title": "9 · Route-proposal corridors — length budget ×3",
        "symptom": (
            "Top route proposals still read as short stubs rather than full corridors."
        ),
        "cause": [
            "The support-scaled meter budget (floor + growth·√score, capped) was tuned conservatively when "
            "corridors were new.",
        ],
        "fixes": [
            "All three knobs ×3: <code>ROUTE_LENGTH_BASE_M</code> 900→2700, "
            "<code>ROUTE_LENGTH_PER_SQRT_SCORE_M</code> 220→660, <code>ROUTE_LENGTH_MAX_M</code> 3500→10500. "
            "The √-score shape (support buys reach, sublinearly) is unchanged.",
        ],
        "files": ["client-react/src/components/GraphLayer/routeProposals.ts"],
    },
    {
        "id": "alsoinrange",
        "tag": "Blocks pipeline + deploy · same range",
        "title": "10 · Also in this range — disjoint junction cells + the artifacts-only overlay",
        "symptom": (
            "Two follow-on commits land in this diff range: 4,880 junction pairs (10% of NYC's 80,199 cells) "
            "still rendered as visibly stacked intersection polygons, and there was no way to ship a block "
            "re-bake without also sweeping in-flight code."
        ),
        "cause": [
            "The bisector trim between overlapping junction cells vetoed any cut that would evict a member "
            "node or detach a captured edge — at big multi-node intersections some captured stub always "
            "crossed the bisector, so the cut was vetoed on both sides and the full pre-trim overlap shipped.",
            "Dockerfile.blocks-overlay also copies <code>server/*.py</code> and rebuilds the client, so a "
            "pure block re-bake couldn't deploy while the tree carried unrelated work.",
        ],
        "fixes": [
            "<strong>Unconditional Voronoi cuts, membership repaired after</strong> (bbbd0b2): geometry wins; "
            "a cell left &lt;1&nbsp;m² on its own side is absorbed into that neighbour (swept to a fixpoint); "
            "a stranded captured edge is re-homed to the cell/corridor holding its midpoint; an edge no "
            "polygon holds gets its tube grafted back, clipped against neighbours. Re-baked nyc + test-cp: "
            "<code>residual_overlap_pairs</code> 0, audit still 100% mapped / 100% edge∩polygon, "
            "topology_etag unchanged — artifacts serve without a resnap. Also adapts the builder to the "
            "array-native CityGraph (per-edge name/highway re-read from the provider output the etag is built from).",
            "<strong>Dockerfile.blocks-artifacts-overlay</strong> (8fb0df3): the most surgical deploy we have — "
            "overlays only <code>.blocks-staging/</code> artifacts onto the digest-pinned serving image; no "
            "server code, no client build. graph_registry hard-rejects a mapping whose stamped etag doesn't "
            "match the live graph, so a wrong bake can't mis-color blocks.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py", "Dockerfile.blocks-artifacts-overlay"],
    },
]

VERIFY = [
    "Edge-id / etag equivalence: every city's <code>topology_etag</code> from the array-native load is "
    "bit-identical to the etag stamped in its baked edge_blocks artifacts — all 7 cities, so votes and "
    "blocks reference exactly the same edges.",
    "<code>vote_store.build_arrays</code> output equivalence-tested against the legacy pure-Python "
    "implementation before it replaced it.",
    "Full 7-city + 21-map prewarm measured locally: 1.35 GB RSS (was ~6 Gi and OOMing); NYC arrays load "
    "~1 s vs minutes of unpickle.",
    "Local swarm, 40 agents / 16 maps: ALL BUDGETS MET, worst p95 0.33 s. Same swarm before the sparse "
    "snapshot build: graph_votes p95 13.3 s, routes p95 9.2 s.",
    "Prod hardened over three deploy iterations, each driven by the swarm: iter 1 (rev 00094) exposed the "
    "map-config stampede (60% timeouts) → composite index + single-flight; iter 2 (rev 00096) exposed the "
    "nginx per-request-gzip CPU tail → pre-gzipped topology; iter 3 (rev 00097) all green.",
    "<strong>Final prod swarm (rev 00097): 40 agents / 16 maps / 10 cycles against https://cityedit.org — "
    "ALL BUDGETS MET, zero errors across ~1,150 timed interactions.</strong> p95s: graph_version 0.55 s, "
    "map_meta 0.85 s, graph_votes 1.30 s, route 0.49 s, vote 1.11 s, reverse_geocode 0.13 s, "
    "ws_connect 0.59 s. Topology single-stream ~1.0 s (18.8 MB pre-gzipped; server-side latency &lt;0.3 s "
    "— the swarm's multi-stream topology number is test-host downlink-bound).",
    "Blocks re-bake (nyc + test-cp): residual_overlap_pairs 0 (independent geojson scan confirms 0 pairs "
    "≥ 1 m²), audit 100% mapped / 100% edge∩polygon, topology_etag unchanged (a89beee774fd1b90).",
    "Prod deployed via the arrays overlay (converts the base image's own pickles in-image); prewarm ~90 s "
    "for all 7 cities incl. philly/test-cp/test-mid + every map (previously OOM-looped every ~3 min and "
    "Philly 500'd).",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>localhost:3000/m/nyc-walkways</a> and a Philly map — "
    "both should load the heatmap; watch the Flask log for <code>[ROUTER] Walk graph loaded from arrays</code> "
    "(seconds, not minutes).",
    "curl 'localhost:5001/api/graph-topology?map=nyc-walkways' (no format=bin) — expect a 410 pointing at "
    "format=bin; the same URL on a station map (e-bikes) still returns JSON.",
    "curl 'localhost:5001/api/nearest-node?lat=40.7&lng=-74' and '/api/graph.geojson' — both should 404 "
    "(endpoints deleted).",
    "curl -I -H 'Accept-Encoding: gzip' 'localhost:5001/api/graph-topology?map=nyc-walkways&format=bin' — "
    "expect Content-Encoding: gzip, Vary: Accept-Encoding, a STRONG ETag, and ~18.8 MB; repeat with "
    "If-None-Match (with or without a W/ prefix) — expect 304.",
    "Run <code>python server/tests/swarm_interactions.py --base http://localhost:5001 --agents 40</code> — "
    "expect ALL BUDGETS MET and exit 0; this is the regression instrument for tenant scaling.",
    "On a voted map, check the top route proposals: hot corridors should now read as long routes "
    "(up to ~10.5 km), not stubs.",
    "On prod: <code>gcloud run services describe desire-path-app</code> — maxScale 8, containerConcurrency "
    "200, 4 CPU / 8Gi; confirm the OSRM service still runs the pinned image tag, and that zooming NYC shows "
    "no stacked intersection polygons.",
    "Watch prod RSS over a day of tenant traffic — it should sit near ~1.4 GB warm, nowhere near the 8 Gi line.",
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


# ── Hierarchical "where does this block sit" context ──────────────────────────
# For every diff we render a recursively-summarized map: the SYSTEM (component
# pills) → the MODULE (subsystem + one-liner) → the FILE (one-liner + LOC) → a
# focus+context file map (changed sections highlighted) → the changed blocks.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    ".gitignore": {
        "on": [],
        "module": ("Repo root", "ignore rules"),
        "file": (".gitignore", "87 lines — one addition"),
        "outline": [
            ("build/artifact ignores", "env, osm_data blobs, exports, .blocks-staging", False),
            ("+ .arrays-staging/", "the arrays-overlay's new-city staging dir", True),
        ],
        "blocks": [".arrays-staging/ — local staging for cities absent from the prod base image"],
    },
    "Dockerfile": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Dockerfile", "the FULL app image build: client, venv, graph bake, nginx"),
        "file": ("Dockerfile", "64 lines — graph-bake stage touched"),
        "outline": [
            ("client build stage", "npm ci + vite build", False),
            ("python deps + code", "uv pip install, server code", False),
            ("graph bake", "refresh_osm per city — now also builds philly and emits walk_graph_arrays.npz; resnap warning documented", True),
            ("nginx + entrypoint", "unchanged", False),
        ],
        "blocks": [
            "philly added to the per-city refresh_osm bake",
            "header comment — npz is the ONLY runtime artifact; full rebuilds shift edge ids → resnap + block re-bake required",
        ],
    },
    "Dockerfile.arrays-overlay": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Dockerfile.arrays-overlay", "NEW — code overlay + in-image pkl→npz conversion of the base's own graphs"),
        "file": ("Dockerfile.arrays-overlay", "NEW FILE — 47 lines"),
        "outline": [
            ("client build stage", "npm ci + vite build", True),
            ("overlay onto BASE_IMAGE", "requirements re-sync, server/*.py, data/", True),
            (".arrays-staging copy", "philly/test-cp/test-mid artifact sets absent from the base", True),
            ("graph_arrays.py --all", "convert EVERY pickle in-image — bit-faithful to what prod votes/blocks reference", True),
        ],
        "blocks": [
            "whole file is new — same no-resnap contract as Dockerfile.overlay, plus npz generation from the base image's own pickles (never ship locally-built npz for baked cities)",
        ],
    },
    "Dockerfile.blocks-artifacts-overlay": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Dockerfile.blocks-artifacts-overlay", "NEW — artifacts-ONLY overlay: refreshed block bakes, nothing else"),
        "file": ("Dockerfile.blocks-artifacts-overlay", "NEW FILE — 21 lines"),
        "outline": [
            ("FROM BASE_IMAGE + COPY .blocks-staging/", "no server code, no client build — ships a re-bake while the tree carries in-flight work", True),
        ],
        "blocks": [
            "whole file is new — artifacts must be baked against the BASE image's graphs; graph_registry's etag check rejects anything else",
        ],
    },
    "cloudbuild.arrays-overlay.yaml": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Cloud Build", "NEW — build+push pipeline for the arrays overlay"),
        "file": ("cloudbuild.arrays-overlay.yaml", "NEW FILE — 33 lines"),
        "outline": [
            ("docker build w/ _BASE_IMAGE", "digest-pinned serving image in, :latest out", True),
            ("E2_HIGHCPU_32 + 1h timeout", "the conversion step unpickles networkx (~6GB transient for NYC)", True),
        ],
        "blocks": ["whole file is new — pass the digest-pinned base via _BASE_IMAGE"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "the heatmap layer: topology load, votes, deltas, hover/click, proposals"),
        "file": ("GraphLayer.tsx", "~4,930 LOC — this diff touches only the topology fetch path"),
        "outline": [
            ("module helpers", "decodeTopologyBin, buildEdgeIndex, votesMatchTopology", False),
            ("topology loading", "binary-only fetch — the JSON fallback branch is deleted (server 410s street JSON)", True),
            ("stale-topology refetch", "console.error → derror('topo', …)", True),
            ("votes / deltas / redraw", "unchanged", False),
            ("hover / hit-testing / proposals", "unchanged", False),
        ],
        "blocks": [
            "fetchTopologyFromNetwork — the try/catch JSON fallback collapses to a straight binary fetch; failures surface",
            "fresh-topology refetch error → derror('topo', …)",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer/routeProposals", "deterministic client-side top-proposal computation (PBTP/RBTP)"),
        "file": ("routeProposals.ts", "~870 LOC — three constants changed"),
        "outline": [
            ("clustering / peeling", "greedyHeaviestPath, block nets", False),
            ("corridor length budget", "BASE 900→2700m · PER_√SCORE 220→660m · MAX 3500→10500m", True),
            ("capPathToLengthBudget", "unchanged (same √-score shape)", False),
        ],
        "blocks": [
            "ROUTE_LENGTH_BASE_M = 2700 (was 900)",
            "ROUTE_LENGTH_PER_SQRT_SCORE_M = 660 (was 220)",
            "ROUTE_LENGTH_MAX_M = 10500 (was 3500)",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · RouteContext", "route state: waypoints, split-path calculation, casting"),
        "file": ("RouteContext.tsx", "~1,710 LOC — logging only"),
        "outline": [
            ("imports", "+ derror from utils/debugLog", True),
            ("split-path calculations (×3 sites)", "console.error → derror('proposals', …)", True),
            ("selection / casting logic", "unchanged", False),
        ],
        "blocks": ["three bare console.error sites routed through derror('proposals', …)"],
    },
    "client-react/src/hooks/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · hooks/", "barrel re-exporting the hooks"),
        "file": ("index.ts", "3 lines"),
        "outline": [("exports", "useWebSocket export removed (hook deleted)", True)],
        "blocks": ["- export { useWebSocket } from \"./useWebSocket\""],
    },
    "client-react/src/hooks/useGraphNodes.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · hooks/", "DELETED — viewport node fetcher for the old nearest-node flow"),
        "file": ("useGraphNodes.ts", "DELETED — 44 LOC; fetched /api/graph per moveend"),
        "outline": [("(whole file)", "unused since typed-array topology + resolveDragSnap; its /api/graph endpoint is gone too", True)],
        "blocks": ["deleted — nothing imported it; the endpoint it called is deleted in the same change"],
    },
    "client-react/src/hooks/useWebSocket.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · hooks/", "DELETED — legacy map_state WebSocket hook"),
        "file": ("useWebSocket.ts", "DELETED — 88 LOC; superseded by WebSocketContext"),
        "outline": [("(whole file)", "WebSocketContext (merged-delta protocol) is the one WS layer", True)],
        "blocks": ["deleted — the map_state message type it consumed no longer exists"],
    },
    "client-react/src/utils/nearestNode.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · utils/", "DELETED — O(n) nearest-node scan for the old snapping flow"),
        "file": ("nearestNode.ts", "DELETED — 24 LOC"),
        "outline": [("(whole file)", "snapping goes through the typed-array hit-test / resolveDragSnap now", True)],
        "blocks": ["deleted — companion of useGraphNodes"],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · app.py", "all HTTP endpoints + the vote codepath, snapshot cache, WS handler"),
        "file": ("app.py", "~2,060 LOC — array-native call sites, map-config cache, gzip topology serving, endpoint deletions, log demotions"),
        "outline": [
            ("imports / config / registries", "unchanged", False),
            ("snapshot build (_build_graph_votes_body_locked)", "n_edges/n_nodes/edge_ends from the array-native CityGraph", True),
            ("_prewarm", "also builds each map's pre-gzipped topology blob — no tenant's first visitor pays the compression", True),
            ("map_get (/api/maps/<slug>)", "NEW 30s single-flight TTL cache for public maps — kills the join-wave stampede", True),
            ("calculate_route", "OSRM annotations are the ONLY edge mapping; coords fallback deleted; INFO→DEBUG", True),
            ("cast_vote", "per-vote log line INFO→DEBUG", True),
            ("/api/nearest-node · /api/graph · /api/graph.geojson", "DELETED", True),
            ("graph_topology", "serves the pre-gzipped blob (Content-Encoding: gzip, Vary) · W/-tolerant 304s · street JSON → 410", True),
            ("geocode / previews", "[GEOCODE]/[PREVIEWS] tags on log lines", True),
            ("admin APIs / WS handler", "unchanged", False),
        ],
        "blocks": [
            "_build_graph_votes_body_locked — rmap.graph.n_edges/n_nodes/edge_ends (arrays, not lists)",
            "_prewarm — rmap.graph.topology_binary_gz() per map",
            "_map_get_cache + _map_get_locks — 30s TTL, per-slug single-flight, 256-entry bound, public maps only",
            "calculate_route — coords_to_edge_ids fallback removed; 0-edge routes log a warning and surface",
            "nearest_node() / graph_data() / graph_geojson() deleted",
            "graph_topology — If-None-Match .removeprefix('W/'); Accept-Encoding: gzip → pre-gzipped bytes + Content-Encoding/Vary headers",
            "graph_topology — JSON for street networks returns 410 'use format=bin'",
            "hot-path INFO→DEBUG ([ROUTE], [VOTE]); [GEOCODE]/[PREVIEWS] tags",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API · database.py", "Postgres persistence: pool, votes, maps, schema init"),
        "file": ("database.py", "~1,160 LOC — one index added"),
        "outline": [
            ("schema init — edge_votes indexes", "+ idx_edge_votes_map_vt_created (map_slug, vote_type_id, created_at)", True),
            ("pool / votes / maps", "unchanged", False),
        ],
        "blocks": [
            "idx_edge_votes_map_vt_created — makes fetch_voted_vote_type_labels' GROUP BY an index-only scan (applied to prod via CREATE INDEX CONCURRENTLY)",
        ],
    },
    "server/graph_arrays.py": {
        "on": ["Flask API"],
        "module": ("Flask API · graph_arrays.py", "NEW — build-time pkl→npz converter + vectorized CSR builder + loader"),
        "file": ("graph_arrays.py", "NEW FILE — 198 LOC"),
        "outline": [
            ("layout doc + ARRAYS_FILENAME", "coords/node_osmid/edge_u/v/len/name/highway + JSON meta", True),
            ("convert()", "EXACT legacy edge iteration (edges(data=True) + skip rule) → edge ids preserved; atomic tmp+rename", True),
            ("load()", "npz → dict of arrays + decoded meta (~1s for NYC)", True),
            ("build_csr_adjacency()", "vectorized node→incident-edges CSR, lexsort matches the legacy append order", True),
            ("main()", "--city/--all CLI (used by Dockerfile.arrays-overlay)", True),
        ],
        "blocks": [
            "whole file is new — the runtime's ONLY graph artifact; adjacency derives at load (faster than disk)",
        ],
    },
    "server/graph_registry.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · graph_registry.py", "per-city CityGraph + OSRM registries; topology blobs; block-layer load"),
        "file": ("graph_registry.py", "536 LOC — the array-native rewrite + pre-gzipped topology"),
        "outline": [
            ("module doc + imports", "memory model documented; + build_csr_adjacency", True),
            ("load_station_graph", "unchanged (stations stay tiny dict graphs)", False),
            ("IntMap / IntPairMap", "NEW — sorted-numpy int→int and (int,int)→int maps; last-wins duplicates match dict semantics", True),
            ("NodeAdjacency + _build_node_adjacency", "NEW — CSR view, legacy append order (first-incident-edge is behavior), self-edges once", True),
            ("encode_topology_bin", "vectorized over node_coords/edge_ends", True),
            ("CityGraph.__init__ / ensure_loaded", "lists+dicts → n_nodes/n_edges/node_coords/edge_ends/IntMap/IntPairMap; JSON kept only for stations; coord_to_* deleted", True),
            ("_load_edge_blocks", "n_edges checks; etag contract unchanged", True),
            ("topology_binary_gz", "NEW — the GTB2 blob gzipped ONCE per load at level 6, cached, served verbatim (was 0.4 core-s per request in nginx)", True),
            ("snap_point_to_edge", "1.3M-node Python scan → kdtree query (+ _ensure_node_tree)", True),
            ("edge_midpoint(s) / _ensure_edge_mid_tree", "array-native", True),
            ("unload", "clears the array/kdtree fields (incl. topology_bin + topology_bin_gz)", True),
            ("GraphRegistry / OsrmRegistry", "doc tightened (merged OSRM is the norm)", True),
        ],
        "blocks": [
            "IntMap — osm_to_graph_idx: ~60MB dict → ~8MB sorted arrays + binary search",
            "IntPairMap — node_pair_to_edge: ~450MB tuple-keyed dict → ~50MB packed-uint64 arrays, last-wins",
            "NodeAdjacency/_build_node_adjacency — CSR ordered exactly like the legacy loop",
            "ensure_loaded — array-native residents; topology JSON hashed then dropped for streets",
            "snap_point_to_edge — cKDTree over node_coords",
            "encode_topology_bin — np.rint over the coord array (same GTB2 bytes)",
            "topology_binary_gz() — one level-6 gzip per load (zlib releases the GIL); reset on unload",
        ],
    },
    "server/osrm_router.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask API · osrm_router.py", "HTTP client for the self-hosted OSRM"),
        "file": ("osrm_router.py", "164 LOC — logging only"),
        "outline": [
            ("route()", "per-request [OSRM] GET/response/OK lines INFO→DEBUG (hot path)", True),
            ("error handling / table()", "unchanged", False),
        ],
        "blocks": ["three per-route INFO log lines demoted to DEBUG"],
    },
    "server/python_router.py": {
        "on": ["Flask API"],
        "module": ("Flask API · python_router.py", "walk-graph provider: topology-for-bbox, snapping, reverse geocoding"),
        "file": ("python_router.py", "309 LOC (was ~430) — runtime never touches networkx/pickle"),
        "outline": [
            ("module doc + imports", "gc/os/pickle out; graph_arrays in", True),
            ("__init__", "npz-shaped fields: _node_osmid array, CSR _adj_indptr/_adj_edges; _bbox_cache deleted", True),
            ("_ensure_loaded", "60 lines of unpickle+extract → graph_arrays.load() + build_csr_adjacency + kdtree (~1s)", True),
            ("get_graph_for_bbox", "etag-critical contract documented; bbox result cache removed (single full-city caller)", True),
            ("reverse_geocode", "BFS walks the CSR adjacency via _incident_edges", True),
            ("reload / stats", "array-native resets", True),
            ("build_graph", "emits BOTH artifacts — pkl (build-time canonical) + npz (runtime) via graph_arrays.convert", True),
        ],
        "blocks": [
            "_ensure_loaded — loads prebaked arrays; no pickle, no networkx, no gc.collect",
            "_incident_edges — CSR slice helper",
            "get_graph_for_bbox — _bbox_cache deleted; output ordering is the topology-etag contract",
            "build_graph — graph_arrays.convert(output_path) after the pickle dump",
        ],
    },
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "on": ["Flask API"],
        "module": ("Blocks pipeline (build-time) · streetscape_blocks/", "THE one-pass graph-first Layer-2 blocks builder"),
        "file": ("build_blocks_graph_first.py", "726 LOC — disjoint-junction trim + array-native input"),
        "outline": [
            ("header spec", "step 4 rewritten: unconditional trim, absorption, re-home", True),
            ("graph load", "array-native CityGraph (node_coords/edge_ends); name/highway re-read from the provider output the etag hashes", True),
            ("clustering / corridors", "unchanged", False),
            ("Voronoi trim", "cuts UNCONDITIONAL; <1m² cells absorbed into the neighbour; fixpoint sweeps (last measure-only)", True),
            ("stranded-edge re-home", "NEW — midpoint cell/corridor, line-touch fallback, clipped tube graft (60 on nyc)", True),
            ("emit + meta", "+ cells_absorbed / residual_overlap_pairs / junction_edges_rehomed / junction_tube_grafts", True),
        ],
        "blocks": [
            "provider-output re-read (STATION_NETWORKS / get_graph_for_bbox) + edge-count guard",
            "absorb() + fixpoint trim sweeps — the old evict/detach veto (which shipped 4,880 stacked pairs) is gone",
            "stranded-edge re-home + tube graft, residual re-measured post-graft",
            "meta gains the four new audit counters",
        ],
    },
    "server/tests/swarm_interactions.py": {
        "on": ["Flask API", "OSRM", "Redis"],
        "module": ("Tests · server/tests/", "NEW — multi-tenant interaction swarm with per-interaction p95 budgets"),
        "file": ("swarm_interactions.py", "NEW FILE — 304 LOC, aiohttp/asyncio"),
        "outline": [
            ("BUDGETS", "per-interaction (timeout, p95 budget, max error rate) — topology budget now BANDWIDTH-AWARE (N×18MB share one test-host downlink; judge the server by errors + Cloud Run latency)", True),
            ("Metrics", "per-interaction latency/status table + FAIL verdicts", True),
            ("agent()", "the real client sequence: meta → version → topology(bin) → votes → held WS → route/vote/geocode cycles", True),
            ("fetch_maps + tenancy", "all public maps, weighted toward --primary; votes only on primary/test maps", True),
            ("main_async", "prints the table; exit 1 on any budget/error breach", True),
        ],
        "blocks": [
            "whole file is new — 30 agents over ~16 maps ≈ the 30-tenant target; route 404s (unroutable random pairs) don't count against the server",
        ],
    },
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · vote_store.py", "the Redis vote codec + write/read path + snapshot arrays"),
        "file": ("vote_store.py", "439 LOC — build_arrays vectorized; coords mapping deleted"),
        "outline": [
            ("codec / write path / deltas", "unchanged", False),
            ("build_arrays", "pure-Python all-edges/all-nodes loops → sparse numpy (bit-op unpack, scatter-add, endpoint scatter-max); signature takes edge_ends", True),
            ("coords_to_edge_ids", "DELETED — the coordinate-rounding route→edge fallback", True),
            ("osm_nodes_to_edge_ids", "unchanged — the ONE mapping path", False),
        ],
        "blocks": [
            "np.fromiter over packed fields; eid/mode/vtid/dbit via bit ops; keep-mask for edge_count + mode_filter",
            "np.add.at scatter-adds for edge up/down; sparse typed breakdown (vtid≠0 rows only)",
            "node pass: scatter-max from positive-net edges' endpoints via edge_ends (never loops all nodes)",
            "_EMPTY shared placeholder — no per-cell list allocation",
            "coords_to_edge_ids deleted (with its coord_to_edge_idx input)",
        ],
    },
    "terraform/main.tf": {
        "on": ["nginx", "Flask API", "OSRM"],
        "module": ("Infra · terraform/", "the whole GCP stack: Cloud Run services, Cloud SQL, Redis, networking"),
        "file": ("main.tf", "816 LOC — scaling knobs + drift pinned down"),
        "outline": [
            ("google_sql_database_instance.votes", "db-f1-micro → db-g1-small (vote persistence is on the request path)", True),
            ("cloud_run_service.osrm", "image pinned to the SERVING build tag — registry :latest is an older dataset", True),
            ("cloud_run_service.app — concurrency", "explicit container_concurrency=200 (WS slots = viewers; 200×8 ≈ 1600)", True),
            ("app resources", "2→4 vCPU / 8Gi, sized against the measured 1.35Gi warm RSS (~6x headroom)", True),
            ("app autoscaling", "maxScale 4→8 (prod live-state was pinned at 1); array loads make scale-out instances useful in seconds", True),
            ("redis / networking / monitoring", "unchanged", False),
        ],
        "blocks": [
            "db tier: db-g1-small",
            "OSRM image: :cbdf4b3a-… (serving tag) — a blanket apply of :latest would have swapped the routing network",
            "container_concurrency = 200",
            "app limits: cpu 4 / memory 8Gi (comment rewritten around the array memory model)",
            "maxScale = 8",
        ],
    },
}

# The legacy pre-graph-first blocks pipeline, deleted wholesale (section 5).
# One compact context entry per file, generated below.
LEGACY_PIPELINE = {
    "server/streetscape_blocks/build_blocks_generic.py": (179, "city-agnostic ROW-buffer block generator (centerline buffers + segment Voronoi)"),
    "server/streetscape_blocks/build_edge_blocks.py": (292, "mapped graph edges into pre-existing polygons geometrically — the nearest-snap defect's home"),
    "server/streetscape_blocks/build_foot_blocks.py": (229, "union-find foot-mesh blocks severed at junctions"),
    "server/streetscape_blocks/build_node_blocks.py": (341, "junction-cluster disc blocks + capture mapping"),
    "server/streetscape_blocks/build_nyc_blocks.py": (83, "NYC planimetric (roadbed+sidewalk) block generator"),
    "server/streetscape_blocks/compare_blocks.py": (122, "generic-vs-planimetric IoU comparison"),
    "server/streetscape_blocks/merge_degenerate_blocks.py": (487, "fixpoint merge pass absorbing degenerate blocks (now inside the graph-first builder)"),
    "server/streetscape_blocks/plot_blocks.py": (38, "matplotlib debug plots"),
    "server/streetscape_blocks/pull_nyc.py": (75, "NYC planimetric open-data pull + consolidated drive graph"),
    "server/streetscape_blocks/road_classes.py": (19, "road-class half-width table"),
    "server/streetscape_blocks/run_all.sh": (18, "the old five-script pipeline driver"),
    "server/streetscape_blocks/eval/RESULTS.md": (179, "eval writeup for the generation/mapping experiments"),
    "server/streetscape_blocks/eval/diag_coverage.py": (40, "coverage diagnostics"),
    "server/streetscape_blocks/eval/eval_generate.py": (253, "polygon-generation eval harness"),
    "server/streetscape_blocks/eval/eval_hybrid.py": (93, "hybrid generator eval"),
    "server/streetscape_blocks/eval/eval_ladder.py": (93, "cross-street 'ladder' defect eval"),
    "server/streetscape_blocks/eval/eval_mapping.py": (200, "edge→block mapping eval"),
    "server/streetscape_blocks/eval/eval_rectangles.py": (79, "rectangle-fit eval"),
    "server/streetscape_blocks/eval/extract_edges.py": (67, "eval-area edge extraction"),
    "server/streetscape_blocks/eval/gen_hybrid_manhattan.py": (66, "hybrid Manhattan generation"),
    "server/streetscape_blocks/eval/results_generate.json": (81, "stored eval results (generation)"),
    "server/streetscape_blocks/eval/results_mapping.json": (31, "stored eval results (mapping)"),
}

for _path, (_loc, _summary) in LEGACY_PIPELINE.items():
    _name = _path.rsplit("/", 1)[-1]
    FILE_CONTEXT[_path] = {
        "on": ["Flask API"],
        "module": (
            "Blocks pipeline (build-time) · streetscape_blocks/",
            "the LEGACY pre-graph-first bake chain — superseded end-to-end by build_blocks_graph_first.py",
        ),
        "file": (_name, f"DELETED — {_loc} LOC; {_summary}"),
        "outline": [("(whole file)", _summary, True)],
        "blocks": [
            "deleted — polygons-first + geometric edge mapping is the model the graph-first builder "
            "replaced (membership decides the polygons); nothing imports it",
        ],
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

    missing = [name for name, _ in files if name not in FILE_CONTEXT]
    if missing:
        print(f"WARNING: no FILE_CONTEXT for: {missing}")

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
  table.cmp {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 13.5px; }}
  table.cmp th {{ text-align: left; border-bottom: 2px solid var(--ink); padding: 6px 10px; font-weight: 600; }}
  table.cmp td {{ border-bottom: 1px solid var(--hairline); padding: 6px 10px;
    font-family: var(--font-mono); font-size: 12.5px; font-variant-numeric: tabular-nums; }}
  table.cmp td:first-child {{ font-family: var(--font-ui); color: var(--muted); }}
  table.cmp td.good {{ color: #06402b; font-weight: 600; }}
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · commits
      <code>9962396…edecbf9</code> · deployed as prod revision <code>00097</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#results">Before / after</a><a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Prod was OOM-SIGKILL crash-looping every ~3 minutes: each gunicorn worker boot
  unpickled the per-city networkx graphs (a multi-GB transient per city) and died mid-prewarm at
  8&nbsp;Gi, so map loads hung 120&nbsp;s+ or 502'd and Philly maps 500'd outright. Mitigated live
  with a 12&nbsp;Gi bump, then fixed properly: the runtime never touches networkx or pickle again.
  A new compact-array artifact (<code>walk_graph_arrays.npz</code>, 37&nbsp;MB for NYC) loads in
  ~1&nbsp;s with edge ids and topology etags verified bit-identical to what baked blocks and stored
  votes reference; CityGraph went array-native (~500&nbsp;MB of lookup dicts → ~60&nbsp;MB of sorted
  numpy, kdtree snap instead of a 1.3M-node scan); the vote-snapshot build went sparse-vectorized
  (graph_votes p95 13.3&nbsp;s → 0.64&nbsp;s under a 40-agent swarm); the topology blob is
  pre-gzipped once per city instead of nginx re-compressing 37&nbsp;MB per request (which had also
  been silently breaking topology 304s via weakened ETags); three dead endpoints, the coords→edge
  fallback, and the entire legacy blocks pipeline were deleted. A new multi-tenant swarm harness
  times every client interaction against p95 budgets — it caught the map-config stampede
  (composite index via CREATE INDEX CONCURRENTLY + single-flight cache) and the gzip CPU tail
  across three deploy iterations. Terraform now matches reality: maxScale 1(!)→8, concurrency 200,
  4CPU/8Gi, db-g1-small, and the OSRM image pinned to the serving dataset a blanket apply would
  have silently swapped. Full 7-city + 21-map prewarm: 1.35&nbsp;GB RSS. <strong>Final prod swarm
  against revision 00097: 40 agents / 16 maps / 10 cycles — ALL BUDGETS MET, zero errors across
  ~1,150 timed interactions.</strong></p>

  <section class="card" id="results">
    <div class="tag">Measured</div>
    <h2>Before / after</h2>
    {results_html()}
    <p style="color:var(--muted);font-size:13px;">Local swarm numbers are
    <code>server/tests/swarm_interactions.py</code> at 40 agents / 16 maps against local dev, same
    machine before/after the sparse snapshot build; memory numbers are the full prewarm
    (7 cities + 21 map vote bodies). Prod boot behavior is from the incident logs vs the deployed
    revision. Final prod swarm (rev 00097, 40 agents / 16 maps / 10 cycles vs https://cityedit.org)
    p95s: graph_version 0.55&nbsp;s · map_meta 0.85&nbsp;s · graph_votes 1.30&nbsp;s ·
    route 0.49&nbsp;s · vote 1.11&nbsp;s · reverse_geocode 0.13&nbsp;s · ws_connect 0.59&nbsp;s;
    topology single-stream ~1.0&nbsp;s (the swarm's multi-stream topology figure measures the
    test host's downlink, not the server).</p>
  </section>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Green is added, red removed. Click a
    file to expand — each opens with a System ▸ Module ▸ File context map before the diff.
    The 20-odd all-red files are the legacy blocks pipeline going away (section 6).</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by
    <code>changelog/build_oom_scaling_report.py</code>. Regenerate with
    <code>git diff 0168569..HEAD -- . ':(exclude)changelog' &gt; changelog/changes.diff &amp;&amp;
    python changelog/build_oom_scaling_report.py</code> (the exclude keeps concurrent changelog
    docs commits out of this report's diff).
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
