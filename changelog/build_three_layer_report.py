#!/usr/bin/env python3
"""Generate the three-layer voting model changelog report.

Run from repo root: python changelog/build_three_layer_report.py
Reads changelog/changes.diff (git diff 00a5492..HEAD),
writes changelog/2026-07-04-three-layer-voting.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-07-04-three-layer-voting.html")

DATE = "2026-07-04"
TITLE = "Three-layer voting model — graph · blocks · route proposals"

COMMITS = [
    ("c584716", "feat(votes): authoritative per-block counts on the delta broadcast"),
    ("79bee5e", "fix(votes): widen the packed vote-type field 16 -> 24 bits (dir bit 44 -> 52)"),
    ("3151306", "feat(client): wire the block layer through the UI"),
    ("8a742bb", "fix(server): purge derived block-vote state on resnap"),
    ("a461a06", "refactor(server): drop the server-side route-proposal engine"),
    ("01bf22d", "feat(client): block-aware vote planning + deterministic local route clustering"),
    ("b3e5dc2", "feat(server): block-scoped clear-then-cast vote semantics + GTB2 topology"),
    ("ced39e1", "feat(blocks): one-command per-city block build + refresh_osm --blocks"),
    ("e0c135b", "docs: three-layer model spec; archive the propagate-era block docs"),
    ("ac24198", "Merge branch 'feat/block-voting' into fix/unify-voting"),
    ("f73ae3f", "feat(server): wire block layer into graph + DB (edge_block_id loader, device query)"),
    ("f5dbc81", "chore(dev): allow worktree stacks on alt ports (PORT env, VITE_API_BASE)"),
    ("9f78917", "feat(client): blocks as primary heat display + foot-path block coverage"),
    ("825c90e", "fix(blocks): correct shapely containment predicate (within, not contains)"),
    ("f55d97d", "docs(blocks): comparison report — generic vs Brook planimetric (NYC)"),
    ("8ba3123", "tune(blocks): document buffer-width calibration vs Brook ground truth"),
    ("4a99492", "feat(blocks): edge->block mapping builder (build_edge_blocks.py)"),
    ("48e61d5", "feat(server): Redis-native block-vote dedup (block_votes.py) + tests"),
    ("bfefd3c", "feat(blocks): city-agnostic generic block generator + Brook comparison"),
    ("c948a81", "docs: vote-system design (edge storage, Redis-native block dedup)"),
    ("4b8e147", "Add NYC streetscape-blocks pipeline (planimetric roadbed+sidewalk -> segment-Voronoi blocks)"),
]


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


MAX_DIFF_LINE_CHARS = 400  # the osmnx cache JSON is a single 110 MB line


def colorize(diff_chunk: str) -> str:
    out = []
    for raw in diff_chunk.splitlines():
        if len(raw) > MAX_DIFF_LINE_CHARS:
            raw = (raw[:MAX_DIFF_LINE_CHARS]
                   + f" … [line truncated: {len(raw):,} chars]")
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
        "id": "model",
        "tag": "Architecture · docs",
        "title": "1 · The three-layer model (the spec this implements)",
        "symptom": (
            "Voting grew three competing grains — edges (storage), blocks (an in-flight design), and "
            "route proposals (a server-side Leiden engine) — with conflicting write semantics. "
            "<a href=\"../docs/three-layer-model.md\"><code>docs/three-layer-model.md</code></a> is the new "
            "source of truth this workstream implements; the propagate-era docs "
            "(<code>vote-system-design.md</code>, <code>cluster-model.md</code>, "
            "<code>unify-voting-waves.md</code>) are archived with supersession notes."
        ),
        "cause": [
            "<strong>Layer 1 — edge/node graph (storage source of truth, schema unchanged)</strong>: votes stay "
            "rows in Postgres <code>edge_votes</code>, unique per (map, edge, vote_type, device) with a mutable "
            "direction ±1; Redis <code>ev:&lt;slug&gt;</code> serves packed aggregates. Node votes remain derived.",
            "<strong>Layer 2 — blocks (aggregation · display · interaction)</strong>: one precomputed polygon per "
            "street segment; every edge maps to exactly one block (strict many-to-one, baked "
            "<code>edge_blocks_&lt;network&gt;.npy</code>). Blocks aggregate votes deduped once per "
            "(voter, type, direction), are the highlight target, and define unvote/flip semantics.",
            "<strong>Layer 3 — route proposals (derived)</strong>: per-type corridors, now a pure deterministic "
            "function of (topology, vote state) computed on the client in real time. The server engine is gone.",
            "<strong>THE invariant (§2.5)</strong>: a block never holds both a <code>+</code> and a <code>−</code> "
            "from one device for one vote type — enforced server-side by block-scoped clear-then-cast (§4).",
        ],
        "fixes": [
            "Wrote <code>docs/three-layer-model.md</code> (263 lines): the layer contract, block generation + "
            "loading, the §4.1 button-state table, the §4.2 press matrix, the wire contract, and the six "
            "test-asserted invariants.",
            "Re-pointed <code>docs/README.md</code> and <code>docs/voting-architecture.md</code> at it "
            "(voting-architecture stays the Layer-1 mechanics doc; write-path <em>semantics</em> now live in §4).",
            "Moved <code>vote-system-design.md</code> / <code>cluster-model.md</code> / "
            "<code>unify-voting-waves.md</code> to <code>docs/archive/</code> with what-supersedes-what entries — "
            "the central \"propagate the vote across the block\" decision was reversed (votes land only on "
            "selection edges), while their <code>bd:</code>/<code>bagg:</code> dedup design did ship.",
        ],
        "files": [
            "docs/three-layer-model.md — NEW spec (source of truth)",
            "docs/voting-architecture.md — Layer-1 doc updated (codec + block-grain coverage)",
            "docs/README.md, docs/archive/README.md — index + supersession notes",
            "docs/archive/{vote-system-design,cluster-model,unify-voting-waves}.md — archived (renames)",
        ],
    },
    {
        "id": "blocks-gen",
        "tag": "Data pipeline",
        "title": "2 · Layer 2 blocks — procedural generation, evaluated against ground truth",
        "symptom": (
            "Blocks must exist for <em>any</em> city, but only NYC has open planimetric roadbed+sidewalk data "
            "(Brook's pipeline). New cities need blocks generated from the street graph alone — and we need "
            "evidence the procedural output matches the real-world shapes."
        ),
        "cause": [
            "<code>build_blocks_generic.py</code>: consolidated drive-centerline graph (osmnx, intersections "
            "merged at 12 m) → per-road-class half-width buffers → seed points every 6 m → Voronoi partition "
            "by nearest segment → dissolve + clip. One polygon per street segment, no city-specific data.",
            "<strong>Evaluation vs Brook's NYC planimetric blocks</strong> (<code>compare_blocks.py</code>, "
            "results in <code>COMPARISON.md</code>): median IoU <strong>0.84</strong>, median area ratio "
            "<strong>1.00</strong>, ~1 m median centroid offset over ~82k shared segments. NYC keeps the "
            "planimetric set; other cities use the generator, spot-checked with the same harness.",
            "<code>build_foot_blocks.py</code>: edges no street block covers (park paths, plazas, boardwalks — "
            "~18% in NYC) are buffered and merged into one block per connected foot component, making the "
            "edge→block mapping <strong>total</strong>.",
            "<code>build_edge_blocks.py</code>: bakes <code>edge_block_id: int32[n_edges]</code> "
            "(midpoint containment, else nearest block within 30 m) + a meta JSON stamping the graph's "
            "<code>topology_etag</code> and <code>blocks_sha256</code> — so a stale bake is detectable.",
        ],
        "fixes": [
            "<strong>One-command per-city build</strong>: <code>build_city_blocks.sh &lt;city&gt;</code> runs the "
            "5-step pipeline (generic blocks → bake pass 1 → foot blocks → final bake → tippecanoe "
            "<code>blocks.pmtiles</code> next to the graph tiles).",
            "<strong><code>refresh_osm.py --blocks</code></strong> chains it after a graph build — a rebuilt graph "
            "gets a new <code>topology_etag</code>, which invalidates any prior bake, so rebuilds of a "
            "blocks-enabled city should always pass it.",
            "Fixed the shapely containment predicate (<code>within</code>, not <code>contains</code>) and "
            "documented the buffer-width calibration attempts against Brook ground truth.",
            "Generated artifacts are git-ignored (<code>streetscape_blocks/output/</code>); the small eval "
            "fixtures (<code>eval/manhattan_edges.npz</code>, the osmnx cache JSON) are committed for "
            "reproducibility.",
        ],
        "files": [
            "server/streetscape_blocks/build_blocks_generic.py — NEW procedural generator",
            "server/streetscape_blocks/build_foot_blocks.py — NEW foot-component blocks (mapping totality)",
            "server/streetscape_blocks/build_edge_blocks.py — NEW edge→block bake (+ etag stamp)",
            "server/streetscape_blocks/build_city_blocks.sh — NEW one-command pipeline",
            "server/streetscape_blocks/compare_blocks.py + COMPARISON.md — eval harness + results",
            "server/streetscape_blocks/README.md — Layer-2 runbook rewrite",
            "server/refresh_osm.py — --blocks flag; .gitignore — output/",
        ],
    },
    {
        "id": "blocks-server",
        "tag": "Backend · Redis",
        "title": "3 · Blocks on the server — loading, GTB2 topology, deduped block votes",
        "symptom": (
            "The server had no notion of blocks: nothing loaded the baked mapping, the topology blob couldn't "
            "carry it to the client, and per-block vote counts (deduped per voter) didn't exist anywhere."
        ),
        "cause": [
            "<code>CityGraph._load_edge_blocks()</code> loads <code>edge_blocks_&lt;network&gt;.npy</code> only "
            "when its stamped <code>topology_etag</code> matches the live graph and the array length matches "
            "<code>n_edges</code> — a bake from a previous graph build is ignored (the map serves the edge "
            "heatmap) instead of mis-coloring blocks against wrong edge ids.",
            "<strong>GTB2 blob</strong> (<code>encode_topology_bin</code>): header grows to "
            "<code>[b'GTB2', u32 nNodes, u32 nEdges, u32 nBlocks]</code> and — when the map has blocks — a "
            "trailing <code>int32[nEdges] edge_block_id</code> section (−1 = unmapped). The binary ETag suffix "
            "changes <code>-bin</code> → <code>-bin2</code>: the blob's <em>format</em> is versioned separately "
            "from the topology content, otherwise clients holding the GTB1 blob under the same graph version "
            "would pin it in IndexedDB + the day-long HTTP cache and never receive the block mapping.",
            "<code>server/block_votes.py</code> (NEW): Redis-native per-block dedup — "
            "<code>bd:&lt;slug&gt;:&lt;mode&gt;:&lt;block&gt;:&lt;vt&gt;:&lt;dir&gt;</code> device-multiplicity "
            "hashes + a <code>bagg:</code> aggregate, so <em>count(block, type, dir) = # distinct devices with "
            "≥1 edge-vote of that (type, dir) inside the block</em>. Derived state: rebuilt from Postgres "
            "(<code>rebuild_from_db</code>, which also warns if the §2.5 invariant is violated) on cold start, "
            "eviction, or resnap.",
        ],
        "fixes": [
            "<code>/api/graph-votes</code> now projects <code>block_votes[]</code> (net), "
            "<code>block_vote_types[]</code> ([legendIdx, up, down] per block, own legend), "
            "<code>n_blocks</code> and <code>blocks_version</code> when the map has a mapping — lazily "
            "rebuilding <code>bd:/bagg:</code> from Postgres when cold while edge votes exist.",
            "<code>database.fetch_edge_vote_devices()</code> (NEW) keeps device identity (unlike the aggregate "
            "replay query) so the rebuild can reconstruct per-block dedup.",
            "<strong>Resnap purges derived block state</strong>: re-snapped edge ids invalidate "
            "<code>bd:/bagg:</code> (keyed by the OLD graph's edge→block), so <code>_resnap_city_maps</code> "
            "clears them; the next graph-votes build rehydrates.",
            "Unit-tested end to end: dedup (once per device across a block's edges), exact removal, reversal "
            "moves the device, rebuild == incremental, invariant warning (test_block_votes.py); GTB2 encode "
            "round-trip with/without the block section (test_topology_bin.py).",
        ],
        "files": [
            "server/graph_registry.py — _load_edge_blocks (etag guard), encode_topology_bin (GTB2), has_blocks",
            "server/block_votes.py — NEW bd:/bagg: write path, read path, rebuild_from_db",
            "server/database.py — fetch_edge_vote_devices (device-level replay)",
            "server/app.py — block arrays on /api/graph-votes, lazy rebuild, resnap purge, -bin2 ETag",
            "server/tests/unit/test_block_votes.py, test_topology_bin.py — NEW",
        ],
    },
    {
        "id": "semantics",
        "tag": "Backend · write path",
        "title": "4 · Block-scoped clear-then-cast — /api/vote enforces THE invariant",
        "symptom": (
            "Per-edge toggle semantics let a device scatter both directions of one vote type across a block "
            "(vote + on one edge, − on its neighbor) — incoherent at the block grain the UI now displays. "
            "The write path needed the §4.2 semantics: unvote when every touched block is covered, otherwise "
            "clear my same-type votes across the touched blocks and cast on exactly the selection edges."
        ),
        "cause": [
            "<code>server/vote_semantics.py</code> (NEW, pure): <code>plan_block_vote(edge_ids, direction, "
            "prior, edge_block_id)</code> → a <code>VotePlan</code> of <code>clear</code> (my same-type rows in "
            "touched blocks, either direction, not being re-cast) and <code>cast</code> (selection edges whose "
            "direction changes). Maps without blocks degrade to per-edge semantics via singleton "
            "<code>block(e) = {e}</code> — same behavior as before.",
            "<code>cast_vote</code> reads the device's prior rows once "
            "(<code>get_voter_type_rows</code> — replaces the per-edge "
            "<code>get_voter_edge_direction</code> N+1 query), computes the plan inside the per-voter Redis "
            "lock, applies clears then casts to Redis (edge counts <em>and</em> the block dedup mirror via "
            "<code>apply_block_delta</code>), and persists to Postgres (<code>delete_edge_votes</code> for "
            "cleared, <code>record_edge_votes</code> with migration anchors for cast).",
        ],
        "fixes": [
            "Wire contract unchanged on request (<code>direction: d | 0</code>); the response gains "
            "<code>cleared: number[]</code> — edges the server unvoted beyond the selection — so the client "
            "reconciles its local store.",
            "The soft per-IP cap still gates only <em>fresh</em> casts; reversals and block-scoped clears by "
            "the same device are exempt.",
            "<strong>Tests (§6 invariants)</strong>: the full §4.2 press matrix (fresh / unvote-all / flip / "
            "partial / outside-blocks-untouched / singleton fallback / unmapped edges / direction-0 / dup "
            "edges) <em>plus</em> a randomized property test — no sequence of plans leaves a block holding "
            "both directions from one (device, type).",
        ],
        "files": [
            "server/vote_semantics.py — NEW pure planner (VotePlan, block_of, plan_block_vote)",
            "server/app.py — cast_vote rewrite around the plan; blocks mirrored in the same voter lock",
            "server/database.py — get_voter_type_rows (all rows of one type, one query)",
            "server/tests/unit/test_vote_semantics.py — NEW §4.2 matrix + randomized invariant property",
        ],
    },
    {
        "id": "client",
        "tag": "Frontend · pure logic",
        "title": "5 · Client — the same semantics, mirrored as pure, tested logic",
        "symptom": (
            "The client must render button state, plan casts optimistically, and aggregate modal counts at "
            "block grain — identically to the server — without hauling topology into every module or "
            "regressing the mobile typed-array memory model."
        ),
        "cause": [
            "<code>graphTopology.ts</code> decodes both GTB1 and GTB2 (magic-dispatched), exposing "
            "<code>edgeBlockId</code>/<code>nBlocks</code>, a CSR <code>BlockIndex</code> "
            "(<code>buildBlockIndex</code>, subarray views — no boxed arrays), and the block-key convention: "
            "real blocks ≥ 0, unmapped edges as singleton keys <code>−edgeId−2</code> "
            "(<code>blockKeyOf / edgesOfBlockKey / touchedBlockKeys</code>).",
            "<code>voteStore.ts</code> adds <code>blockCoverage()</code> (per-direction "
            "<code>all | some | none</code> over materialized block edge lists — §4.1) and "
            "<code>myVotesInBlocks()</code>; the store stays topology-free — callers pass the block lists in.",
            "<code>castVote.ts</code>: <code>planBlockVote()</code> is the pure press rule (coverage "
            "<code>all</code> → unvote everything in the touched blocks, wire direction 0; else clear-then-cast "
            "— reversals are casts, not clears) and <code>voteButtonState()</code> renders \"active\" only on "
            "full coverage. <code>castVotes()</code> applies the plan optimistically with rollback and "
            "reconciles against the server's <code>cleared</code> response.",
            "<code>blockSelection.ts</code> (NEW): <code>materializeBlocks()</code> (selection → touched-block "
            "edge lists, singleton fallback) and <code>selectionVoteRows()</code> (modal rows = deduped "
            "per-block counts summed over touched blocks, mixed with per-edge rows for unmapped edges).",
            "<code>voteApply.ts</code>: <code>applyBlockCounts()</code> — the idempotent SET twin of "
            "<code>vtCounts</code> for the per-block arrays.",
        ],
        "fixes": [
            "Every rule above is a pure module with its own suite — GTB1/GTB2 decode + block index + key "
            "convention (graphTopology.test.ts), the planBlockVote matrix on singleton and real blocks "
            "(castVote.test.ts), blockCoverage/myVotesInBlocks (voteStore.test.ts), materializeBlocks/"
            "selectionVoteRows (blockSelection.test.ts), applyBlockCounts (voteApply.test.ts).",
            "<code>types/index.ts</code> extends <code>GraphData</code> (block arrays + legend + "
            "<code>edgeBlockId</code>/<code>nBlocks</code>) and <code>VoteDelta</code> "
            "(<code>blockCounts</code>).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/graphTopology.ts (+ NEW .test.ts) — GTB2 decode, BlockIndex, block keys",
            "client-react/src/utils/voteStore.ts (+ .test.ts) — blockCoverage, myVotesInBlocks",
            "client-react/src/utils/castVote.ts (+ .test.ts) — planBlockVote, voteButtonState, cleared-reconcile",
            "client-react/src/utils/blockSelection.ts (+ NEW .test.ts) — materializeBlocks, selectionVoteRows",
            "client-react/src/components/GraphLayer/voteApply.ts (+ .test.ts) — applyBlockCounts",
            "client-react/src/types/index.ts — GraphData/VoteDelta block fields",
        ],
    },
    {
        "id": "ui",
        "tag": "Frontend · UI wiring",
        "title": "6 · UI — blocks are the heat display, the highlight, and the modal grain",
        "symptom": (
            "With counts and semantics in place, the map itself still painted per-edge heat and per-edge "
            "highlights. Blocks needed to become what you <em>see</em> and <em>touch</em>."
        ),
        "cause": [
            "<code>MapLibreBackground.tsx</code> renders <code>blocks.pmtiles</code> as the primary heat: a "
            "<code>block-heat</code> fill layer driven by feature-state <code>heat</code> (transparent at 0 → "
            "the style's heat ramp) plus <code>block-select</code> fill + casing lit by feature-state "
            "<code>selected</code>. GraphLayer broadcasts <code>city-edit:block-votes</code> / "
            "<code>city-edit:block-select</code> events; the background applies them (and re-applies on style "
            "reload).",
            "<code>GraphLayer.tsx</code>: builds the block index on topology load, skips the per-edge canvas "
            "heatmap when blocks are active (edges show only on hover/selection), broadcasts covering block "
            "ids for selection + hover (route path, pinned point via the adjFirst node→edge rule, transient "
            "hover), sums modal cards from the deduped per-block counts, and requests /my-votes at block "
            "breadth (capped at 500 edges for merged foot blocks) so coverage sees sibling-edge votes from "
            "other sessions.",
            "<code>RouteContext.tsx</code>: GraphLayer registers a <code>BlockMaterializer</code> "
            "(selection edges → touched-block edge lists); casts and pressed-state fall back to singleton "
            "blocks until topology loads. Button pressed-state = <code>voteButtonState(blockCoverage(…))</code> "
            "— the same rule as the in-map modal.",
        ],
        "fixes": [
            "<code>config.ts</code>: per-city <code>blockTilesUrl</code> (rebound by "
            "<code>applyCityConfig</code>), plus <code>VITE_API_BASE</code>/<code>VITE_WS_BASE</code> overrides "
            "and a server <code>PORT</code> env so a worktree stack can run beside the main dev stack.",
            "Maps without block artifacts change nothing: the canvas edge heatmap, per-edge modal rows, and "
            "per-edge semantics all remain via the singleton fallback.",
        ],
        "files": [
            "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx — block-heat + block-select layers, event wiring",
            "client-react/src/components/GraphLayer/GraphLayer.tsx — block index, heat handoff, highlight + modal grain, my-votes breadth",
            "client-react/src/context/RouteContext.tsx — BlockMaterializer registration, block-scoped pressed state",
            "client-react/src/config.ts — blockTilesUrl + dev-port overrides",
        ],
    },
    {
        "id": "routes",
        "tag": "Both sides · Layer 3",
        "title": "7 · Route proposals move to the client — server Leiden engine removed",
        "symptom": (
            "Layer-3 corridors were computed server-side (igraph + Leiden), cached per revision, and served at "
            "<code>/api/route-proposals</code> — a heavyweight dependency and an extra round-trip for state "
            "that is a pure function of data the client already holds."
        ),
        "cause": [
            "<code>routeProposals.ts</code> gains <code>computeRouteProposals()</code> — a deterministic port of "
            "the pipeline: per-type net-weighted subgraph (MIN_NET gate) → connected components (replacing "
            "Leiden: components localize naturally; peeling separates parallel corridors) → heaviest simple "
            "path (exact DFS ≤ 12 vertices, greedy two-way extension above) → peel up to 8 paths at ≥ 0.25 "
            "dominance → score/length gates → blocks projection → same-type Jaccard ≥ 0.5 dedupe → rank + cap. "
            "All iteration orders and tie-breaks are by ascending edge/node id: same vote state ⇒ identical "
            "proposals on every client.",
            "GraphLayer recomputes locally on every vote delta (coalesced with a short timer), so corridors "
            "track votes in real time with no server round-trip and no persistence.",
        ],
        "fixes": [
            "<strong>Deleted</strong> <code>server/route_proposals.py</code> (499 LOC), its 372-LOC test file, "
            "the <code>/api/route-proposals</code> endpoint + its two caches in <code>app.py</code>, and the "
            "<code>python-igraph</code> + <code>leidenalg</code> dependencies (requirements recompiled with "
            "<code>uv pip compile</code>).",
            "The display/interaction surface is unchanged: proposals still render as their blocks with a "
            "diamond at the path midpoint, select when every block is covered (<code>isRouteCovered</code>), "
            "and insert ghost waypoints via <code>chooseAnchorOrder</code>.",
            "The deterministic port is test-pinned: net weighting, heaviest-path, peeling, per-type "
            "independence, block grouping, activity gates, ranking, and a determinism suite "
            "(routeProposals.test.ts, +291 lines).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts — computeRouteProposals (deterministic clustering)",
            "client-react/src/components/GraphLayer/routeProposals.test.ts — clustering port suite",
            "server/route_proposals.py — REMOVED; server/tests/unit/test_route_proposals.py — REMOVED",
            "server/app.py — /api/route-proposals + caches removed",
            "server/requirements.in / requirements.txt — python-igraph + leidenalg dropped",
        ],
    },
    {
        "id": "codec",
        "tag": "Critical fix · deploy note",
        "title": "8 · Packed vote-type field widened 16 → 24 bits (direction bit 44 → 52)",
        "symptom": (
            "Incidental but critical: <code>vote_types.id</code> is a global SERIAL that bulk imports pushed "
            "past 65 535. The packed codec gave vote_type 16 bits with the direction bit at 44 — so the "
            "overflowing id bit landed <strong>exactly on the direction bit</strong>, silently recording every "
            "such vote as a downvote."
        ),
        "cause": [
            "The 53-bit field (client <code>voteKey.ts</code> ↔ server <code>vote_store.py</code>, "
            "test-enforced parity) packed [dir 44 | vt 43..28 | mode 27..24 | edge 23..0]. Widening vt to 24 "
            "bits moves direction to bit 52 — still under <code>Number.MAX_SAFE_INTEGER</code>.",
            "Direction-less keys with vt &lt; 65 536 are byte-identical to the old layout, so persisted client "
            "localStorage stores need no migration; only the Redis <em>down</em>-count fields move.",
        ],
        "fixes": [
            "Widened both codecs in lockstep (<code>DIR_SHIFT = 2^52</code>, <code>VT_BITS = 24</code>); "
            "<code>pack()</code> now range-checks edge and vt ids instead of silently corrupting.",
            "Shared test vectors updated + a &gt;65 535 vt case and a 53-bit-safety assertion added on both "
            "sides (test_vote_codec.py ↔ voteKey.test.ts).",
            "<strong>Deploy note</strong>: the widened layout changes the down-field keys, so on deploy the "
            "<code>ev:*</code> and <code>bagg:*</code> Redis keys must be flushed and rehydrated from Postgres "
            "(the startup replay / lazy rebuild does this once the keys are gone).",
        ],
        "files": [
            "client-react/src/utils/voteKey.ts (+ .test.ts) — VT_BITS 24, DIR_SHIFT 2^52",
            "server/vote_store.py — DIR_BIT 52, 24-bit vt, pack() range checks",
            "server/tests/unit/test_vote_codec.py — shared vectors + wide-vt case",
            "docs/voting-architecture.md — layout + the overflow post-mortem note",
        ],
    },
    {
        "id": "delta",
        "tag": "Realtime",
        "title": "9 · blockCounts on the delta broadcast — modals and heat stay live",
        "symptom": (
            "Edge deltas already carry authoritative <code>vtCounts</code> so clients SET instead of "
            "increment. Block counts had no equivalent: an open modal or the block heat would go stale until "
            "the next full <code>/api/graph-votes</code> refetch."
        ),
        "cause": [
            "After a cast, <code>cast_vote</code> reads the deduped [up, down] for every touched block "
            "(<code>block_votes.read_block_vt_counts</code>) and <code>publish_delta</code> attaches them as "
            "<code>blockCounts</code> — the block twin of <code>vtCounts</code>, same SET semantics.",
        ],
        "fixes": [
            "Client <code>applyBlockCounts()</code> SETs them idempotently into the block arrays and "
            "re-broadcasts the block heat — open modals and the MapLibre fill track every vote in real time, "
            "no refetch.",
        ],
        "files": [
            "server/app.py — read_block_vt_counts on cast; server/vote_store.py — publish_delta(block_counts=…)",
            "client-react/src/components/GraphLayer/voteApply.ts — applyBlockCounts",
            "client-react/src/types/index.ts — VoteDelta.blockCounts",
        ],
    },
]

VERIFY = [
    "Server: <code>pytest server/tests/unit</code> — <strong>47 passed</strong>, including the §4.2 "
    "press-matrix + the randomized invariant property test (test_vote_semantics.py), the Redis block-dedup "
    "suite (test_block_votes.py), GTB2 encode (test_topology_bin.py), and the widened codec vectors "
    "(test_vote_codec.py).",
    "Client: <code>vitest run</code> — <strong>181 passed</strong>, including GTB1/GTB2 decode + block index "
    "(graphTopology.test.ts), the planBlockVote matrix on singleton and real blocks (castVote.test.ts), "
    "blockCoverage/myVotesInBlocks, blockSelection, applyBlockCounts, and the deterministic clustering port "
    "(routeProposals.test.ts).",
    "E2E on local dev: drove the §4.2 API matrix against host Flask (fresh / unvote / flip / partial casts, "
    "asserting <code>cleared</code> + block counts) and verified in the browser — block heat paints, "
    "selection lights the covering blocks, modal counts are deduped, buttons follow the tri-state rule.",
    "Codec parity: the shared test vectors are asserted byte-identical on both sides "
    "(test_vote_codec.py ↔ voteKey.test.ts).",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-walkways</code> — the heat should be <em>block fills</em> "
    "(MapLibre), not per-edge lines; hover/select an edge and the covering block polygon should light up "
    "with the edge emphasized on top.",
    "Vote matrix on one block: cast + on an edge (fresh), press + again on the same selection (unvote — "
    "both buttons neutral after), cast + then − (flip — the block must never show both from you). Then vote "
    "two edges of one block in separate casts and confirm the modal counts you as <strong>one</strong> voter.",
    "Curl <code>localhost:5001/api/graph-votes?map=nyc-walkways&amp;mode=walkways</code> — confirm "
    "<code>block_votes</code>, <code>block_vote_types</code>, <code>n_blocks</code>, "
    "<code>blocks_version</code> are present.",
    "With two browsers on the same map, vote in one and watch the other: the block heat and any open modal "
    "should update from the delta (blockCounts) without a reload.",
    "Vote on enough edges of a corridor to cross the activity gates and confirm a route-proposal diamond "
    "appears immediately (client-side clustering — no /api/route-proposals request in the network tab; that "
    "endpoint should now 404).",
    "<strong>Before deploying</strong>: back up the prod DB (per CLAUDE.md), then on deploy flush the "
    "<code>ev:*</code> and <code>bagg:*</code> Redis keys and let them rehydrate from Postgres — the widened "
    "vote-type codec moved the down-count fields. Rebuild blocks with <code>refresh_osm.py --blocks</code> "
    "for any city whose graph is rebuilt.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Context</h3>
      <p>{s['symptom']}</p>
      <h3>How it works</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


# ── Hierarchical "where does this block sit" context ──────────────────────────
# For every diff we render a recursively-summarized map: the SYSTEM (which of the
# app's components this file belongs to) → the MODULE (subsystem + one-line
# summary) → the FILE (summary + an outline of its top-level sections) → the
# changed BLOCKS. The file outline is a focus+context minimap: changed sections
# are highlighted, the rest are dimmed.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    # ── Repo / docs ───────────────────────────────────────────────────────────
    ".gitignore": {
        "on": [],
        "module": ("Repo root", "ignore rules"),
        "file": (".gitignore", "78 LOC — repo-wide ignore list"),
        "outline": [
            ("env/build/data ignores", "venvs, node_modules, osm_data, loadtest", False),
            ("streetscape_blocks/output/", "NEW — generated block data (regenerable, too big for git)", True),
        ],
        "blocks": ["ignore server/streetscape_blocks/output/"],
    },
    "docs/README.md": {
        "on": [],
        "module": ("Docs · docs/", "the docs index — what each doc covers and which is the source of truth"),
        "file": ("README.md", "45 LOC — doc table + conventions"),
        "outline": [
            ("Doc table — three-layer-model.md row", "NEW first row: the source of truth for the layer split + semantics", True),
            ("Doc table — voting-architecture.md row", "rescoped to Layer-1 mechanics; semantics deferred to §4", True),
            ("Other rows + conventions", "url-routing, flask, testing, archive", False),
        ],
        "blocks": [
            "add three-layer-model.md as the leading source-of-truth row",
            "voting-architecture.md row rescoped: Layer-1 mechanics; write semantics live in three-layer-model.md §4",
        ],
    },
    "docs/archive/README.md": {
        "on": [],
        "module": ("Docs · docs/archive/", "superseded designs with what-supersedes-what notes"),
        "file": ("README.md", "16 LOC — archive index table"),
        "outline": [
            ("roam.md / vote-to-heatmap rows", "pre-existing archive entries", False),
            ("vote-system-design.md row", "NEW — propagate-across-block decision reversed; bd:/bagg: design did ship", True),
            ("cluster-model.md row", "NEW — 'everything is a Cluster' voting on blockEdgeIds superseded; UX-parity ideas carried", True),
            ("unify-voting-waves.md row", "NEW — delegation prompts for the reversed plan; do not reuse", True),
        ],
        "blocks": ["three supersession rows pointing at ../three-layer-model.md"],
    },
    "docs/archive/cluster-model.md": {
        "on": [],
        "module": ("Docs · docs/archive/", "ARCHIVED — moved from docs/ (pure rename, content unchanged)"),
        "file": ("cluster-model.md", "163 LOC — the 'everything is a Cluster' design built on vote propagation"),
        "outline": [
            ("whole doc", "renamed docs/cluster-model.md → docs/archive/cluster-model.md", True),
        ],
        "blocks": ["rename only — superseded by docs/three-layer-model.md §3–4 (casts write to selection edges only)"],
    },
    "docs/archive/unify-voting-waves.md": {
        "on": [],
        "module": ("Docs · docs/archive/", "ARCHIVED — moved from docs/ (pure rename, content unchanged)"),
        "file": ("unify-voting-waves.md", "254 LOC — agent wave plan implementing the reversed propagate decision"),
        "outline": [
            ("whole doc", "renamed docs/unify-voting-waves.md → docs/archive/unify-voting-waves.md", True),
        ],
        "blocks": ["rename only — the W2-A propagation / W2-B backfill prompts must not be reused"],
    },
    "docs/archive/vote-system-design.md": {
        "on": [],
        "module": ("Docs · docs/archive/", "ARCHIVED — moved from docs/ (pure rename, content unchanged)"),
        "file": ("vote-system-design.md", "359 LOC — the 2026-06-18 propagate+dedup block design"),
        "outline": [
            ("whole doc", "renamed docs/vote-system-design.md → docs/archive/vote-system-design.md", True),
        ],
        "blocks": ["rename only — §2.1 propagation reversed; its bd:/bagg: Redis dedup (§2.3–2.7) shipped and remains accurate"],
    },
    "docs/three-layer-model.md": {
        "on": [],
        "module": ("Docs · docs/", "NEW — the source of truth this workstream implements"),
        "file": ("three-layer-model.md", "NEW FILE — 263 LOC: the layer contract + block-scoped vote semantics"),
        "outline": [
            ("§1 Layer 1 — edge/node graph", "storage source of truth; schema unchanged", True),
            ("§2 Layer 2 — blocks", "generation, loading, aggregation grain, THE §2.5 invariant", True),
            ("§3 Layer 3 — route proposals", "client-computed deterministic clustering", True),
            ("§4 Vote semantics", "button-state table + press matrix + wire contract (cleared)", True),
            ("§5 File map · §6 Invariants", "concern → file table; the six test-asserted invariants", True),
        ],
        "blocks": ["whole file is new — supersedes the archived propagate-era docs"],
    },
    "docs/voting-architecture.md": {
        "on": [],
        "module": ("Docs · docs/", "Layer-1 vote mechanics: identity, packed codec, storage, reconciliation"),
        "file": ("voting-architecture.md", "113 LOC — the unified edge-based vote model"),
        "outline": [
            ("Vote identity + Postgres schema", "unique per (map, edge, type, device)", False),
            ("Packed codec layout", "45→53 bits; vt 16→24 bits, dir bit 44→52 + the overflow post-mortem", True),
            ("Coverage / cast path", "rewritten at block grain; clear-then-cast per three-layer-model §4", True),
            ("Migration / reconciliation", "unchanged", False),
        ],
        "blocks": [
            "codec layout: bit 52 direction, bits 51..28 vote_type (24 bits) + the deploy note (flush ev:*/bagg:*)",
            "coverage → block grain (tri-state buttons); castVotes steps rewritten around clear-then-cast + `cleared`",
        ],
    },
    # ── Server: block pipeline ────────────────────────────────────────────────
    "server/streetscape_blocks/README.md": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "Layer-2 block generation: procedural per-city polygons + the edge→block bake"),
        "file": ("README.md", "76 LOC — the Layer-2 runbook (rewritten from the NYC-only version)"),
        "outline": [
            ("Header — Layer 2 of the three-layer model", "reframed around docs/three-layer-model.md §2.2", True),
            ("Adding a city (one command)", "geo venv + build_city_blocks.sh / refresh_osm --blocks", True),
            ("Evaluation against ground truth", "compare_blocks.py vs Brook planimetric; IoU 0.84", True),
            ("NYC ground-truth pipeline", "build_nyc_blocks.py (planimetric roadbed+sidewalk)", False),
            ("Output + known edge cases", "regenerable output/, caveats", True),
        ],
        "blocks": ["rewrite: city-agnostic pipeline, one-command build, eval harness, output contract"],
    },
    "server/streetscape_blocks/COMPARISON.md": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "evaluation report — procedural generator vs Brook's NYC planimetric blocks"),
        "file": ("COMPARISON.md", "NEW FILE — 64 LOC: method, results, width calibration"),
        "outline": [
            ("Method (compare_blocks.py)", "segment-matched IoU / area ratio / centroid offset", True),
            ("Results — NYC (81,985 shared segments)", "median IoU 0.84, area ratio 1.00, ~1 m offset", True),
            ("Coverage is better, not worse", "generator covers foot-only edges planimetric misses", True),
            ("Width calibration + Reproduce", "per-class half-width tuning trail; repro commands", True),
        ],
        "blocks": ["whole file is new — the evidence procedural blocks match ground truth"],
    },
    "server/streetscape_blocks/build_blocks_generic.py": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "city-agnostic procedural block generator (needs only OSM)"),
        "file": ("build_blocks_generic.py", "NEW FILE — 179 LOC: centerlines → buffers → segment-Voronoi → blocks"),
        "outline": [
            ("Env config + HALF_WIDTH table", "CITY/BBOX/WIDTH_SCALE; per-road-class half-widths", True),
            ("_consolidated_graph / load_segments", "osmnx drive graph, intersections merged at 12 m", True),
            ("buffered_row / tile_blocks", "6 m seed points → Voronoi partition by nearest segment", True),
            ("main", "dissolve + clip; one polygon per street segment → GeoJSON", True),
        ],
        "blocks": ["whole file is new — the §2.2 step-1 generator"],
    },
    "server/streetscape_blocks/build_foot_blocks.py": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "blocks for edges no street block covers — makes edge→block total"),
        "file": ("build_foot_blocks.py", "NEW FILE — 118 LOC: buffer + merge uncovered foot components"),
        "outline": [
            ("Env config", "CITY/NETWORK/FOOT_HALF_WIDTH_M (6 m buffer)", True),
            ("main", "uncovered (−1) edges → connected foot components → one merged block each, appended with continuing block_ids", True),
        ],
        "blocks": ["whole file is new — park paths / plazas / boardwalks (~18% of NYC edges) get blocks"],
    },
    "server/streetscape_blocks/build_edge_blocks.py": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "bakes the strict many-to-one edge→block mapping the server loads"),
        "file": ("build_edge_blocks.py", "NEW FILE — 135 LOC: edge midpoints → block containment → npy + meta"),
        "outline": [
            ("Env config", "CITY/NETWORK/BLOCKS_FILE, BLOCK_SNAP_M = 30", True),
            ("_sha256_file", "blocks_sha256 stamp for the meta", True),
            ("main", "midpoint containment, else nearest block ≤ 30 m, else −1 → edge_blocks_<network>.npy + meta JSON stamping topology_etag", True),
        ],
        "blocks": ["whole file is new — the baked int32[n_edges] artifact + staleness stamp"],
    },
    "server/streetscape_blocks/build_city_blocks.sh": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "the one-command per-city block build"),
        "file": ("build_city_blocks.sh", "NEW FILE — 66 LOC: 5-step pipeline runner"),
        "outline": [
            ("Usage + pipeline doc", "run AFTER the walk graph exists; graph rebuild ⇒ re-run", True),
            ("bbox plumbing", "cities.py (S,W,N,E) → generator W,S,E,N", True),
            ("Steps 1–5", "generic blocks → bake pass 1 → foot blocks → final bake → tippecanoe blocks.pmtiles", True),
        ],
        "blocks": ["whole file is new — wraps both venvs (geo + server) and tippecanoe"],
    },
    "server/streetscape_blocks/compare_blocks.py": {
        "on": ["Flask API"],
        "module": ("Block pipeline · server/streetscape_blocks/", "the eval harness scoring generated blocks against ground truth"),
        "file": ("compare_blocks.py", "NEW FILE — 122 LOC: segment-matched IoU / area / centroid metrics"),
        "outline": [
            ("load / _pct helpers", "read both block sets, percentile printer", True),
            ("main", "match segments → per-segment IoU, area ratio, centroid offset → summary table", True),
        ],
        "blocks": ["whole file is new — produced the COMPARISON.md numbers (median IoU 0.84 / area 1.00)"],
    },
    # ── Server: graph / votes / API ───────────────────────────────────────────
    "server/refresh_osm.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "per-city PBF download + walk-graph rebuild CLI"),
        "file": ("refresh_osm.py", "219 LOC — the graph refresh entrypoint"),
        "outline": [
            ("Download + build_city", "PBF fetch, osm_graph_builder invocation", False),
            ("main / argparse", "--blocks flag: chain build_city_blocks.sh after the graph (etag invalidates prior bakes)", True),
        ],
        "blocks": [
            "import subprocess",
            "--blocks argument + post-build loop running streetscape_blocks/build_city_blocks.sh per target",
        ],
    },
    "server/graph_registry.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "per-city graph lifecycle: load, LRU-evict, topology blob, snapping"),
        "file": ("graph_registry.py", "406 LOC — CityGraph + GraphRegistry"),
        "outline": [
            ("Station-graph loader", "ebike degenerate graphs", False),
            ("encode_topology_bin", "NEW — pure GTB2 assembler (n_blocks header + trailing int32 edge_block_id)", True),
            ("CityGraph state", "edge_block_id / n_blocks / blocks_version fields", True),
            ("ensure_loaded + _load_edge_blocks", "NEW loader — topology_etag + length guards; stale bake ⇒ edge-heatmap fallback", True),
            ("topology_binary", "GTB1 → GTB2 via encode_topology_bin; has_blocks property", True),
            ("GraphRegistry LRU", "unchanged", False),
        ],
        "blocks": [
            "encode_topology_bin(nodes, edges, edge_block_id, n_blocks) — [b'GTB2', u32×3] + coords + ends + optional int32 block section",
            "CityGraph.edge_block_id / n_blocks / blocks_version init",
            "_load_edge_blocks() — npy+meta load, topology_etag mismatch and length mismatch are warned + ignored",
            "has_blocks property; topology_bin built via encode_topology_bin",
        ],
    },
    "server/block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "NEW — Redis-native per-block vote dedup (Layer-2 aggregation grain)"),
        "file": ("block_votes.py", "NEW FILE — 268 LOC: bd:/bagg: structures, incremental deltas, rebuild"),
        "outline": [
            ("Key helpers + packed block field", "bd:<slug>:<mode>:<block>:<vt>:<dir> hashes; bagg aggregate", True),
            ("Write path — apply_block_delta(s)", "device-multiplicity hash inside the voter lock; count moves only on 0↔N transitions", True),
            ("Read path — build_block_arrays / read_block_vt_counts", "block_votes[] net + block_vote_types[] for /api/graph-votes and deltas", True),
            ("Rebuild — clear / rebuild_from_db", "derived-state recovery from Postgres; warns on §2.5 invariant violations", True),
        ],
        "blocks": ["whole file is new — count(block, type, dir) = # distinct devices with ≥1 edge-vote inside the block"],
    },
    "server/vote_semantics.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "NEW — the pure block-scoped clear-then-cast planner (docs §4)"),
        "file": ("vote_semantics.py", "NEW FILE — 77 LOC: VotePlan + plan_block_vote"),
        "outline": [
            ("VotePlan dataclass", "clear[(edge, prev)], cast[(edge, prev)], touched_blocks", True),
            ("block_of", "edge → block id; singleton fallback when unmapped / no block layer", True),
            ("plan_block_vote", "S → B(S); clear my same-type rows across touched blocks; cast d on exactly S (d=0 ⇒ unvote only)", True),
        ],
        "blocks": ["whole file is new — pure (no Redis/DB), shared by /api/vote and the unit matrix"],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "Postgres persistence: edge_votes, vote types, replay, migration"),
        "file": ("database.py", "971 LOC — all DB access"),
        "outline": [
            ("Connection / schema / vote CRUD", "record/delete_edge_votes, LRU eviction", False),
            ("get_voter_type_rows", "REPLACES get_voter_edge_direction — all of a device's rows of one type in ONE query (the prior state the plan clears against)", True),
            ("aggregate_votes_for_replay", "identity-less startup replay (unchanged)", False),
            ("fetch_edge_vote_devices", "NEW — device-level rows for one map so block_votes.rebuild_from_db can reconstruct dedup", True),
            ("Vote migration / resnap", "unchanged", False),
        ],
        "blocks": [
            "get_voter_edge_direction (per-edge, N+1) → get_voter_type_rows (map+type+device → {edge_id: direction})",
            "fetch_edge_vote_devices(map_slug) — (edge_id, vote_type_id, direction, device_id) rows",
        ],
    },
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "the Redis vote-cache layer: packed fields, read/write path, delta broadcast"),
        "file": ("vote_store.py", "415 LOC — packs votes into Redis fields, builds heatmap arrays"),
        "outline": [
            ("Module doc — field layout", "45 → 53 bits documented", True),
            ("Keys / vote-type cache", "hash_key, all_vote_types", False),
            ("Bit packing — DIR_BIT / pack / unpack", "vt 16→24 bits, direction bit 44→52, range checks + overflow post-mortem comment", True),
            ("apply_directional", "unchanged", False),
            ("publish_delta", "optional block_counts → delta['blockCounts'] (authoritative SET, like vtCounts)", True),
            ("read/build arrays + coord mapping", "unchanged", False),
        ],
        "blocks": [
            "layout comment + DIR_BIT 44 → 52, EDGE_BITS/VT_BITS = 24",
            "pack() range-checks edge_id and vt_id instead of silently overflowing into the direction bit",
            "unpack(): vt mask 0xFFFF → 0xFF_FFFF",
            "publish_delta(block_counts=…) → blockCounts on the wire",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WebSocket routes, vote cache, graph + OSRM registries"),
        "file": ("app.py", "1705 LOC — every API/WS route"),
        "outline": [
            ("Imports / setup", "+ vote_semantics + block_votes; − route_proposals", True),
            ("Vote-cache + _build_graph_votes_body_locked", "appends block arrays (lazy bd:/bagg: rebuild from Postgres when cold)", True),
            ("cast_vote — /api/vote", "block-scoped clear-then-cast plan inside the voter lock; blocks mirrored per edge; response gains `cleared`; delta gains blockCounts", True),
            ("_resnap_city_maps", "purges derived block state (bd:/bagg: keyed by the OLD edge→block)", True),
            ("graph_topology", "binary ETag suffix -bin → -bin2 (GTB2 format version)", True),
            ("/api/route-proposals + caches", "REMOVED — Layer 3 is client-computed", True),
            ("Admin / dev-run", "PORT env for worktree stacks", True),
        ],
        "blocks": [
            "imports: vote_semantics, block_votes in; route_proposals out; get_voter_type_rows in",
            "_build_graph_votes_body_locked — block arrays + blocks_version; lazy rebuild_from_db when bagg: cold but ev: has votes",
            "cast_vote — plan_block_vote(prior=get_voter_type_rows), _apply mirrors block dedup, IP cap only on fresh casts, cleared/cast persistence split, blockCounts on the delta, `cleared` in the response",
            "_resnap_city_maps — block_votes.clear per resnapped map",
            "graph_topology — '-bin2' ETag",
            "route-proposals endpoint + _route_proposals_cache/_engines deleted (−108 lines)",
            "app.run(port=int(os.environ.get('PORT', '5001')))",
        ],
    },
    "server/requirements.in": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "hand-written top-level Python deps (uv pip compile source)"),
        "file": ("requirements.in", "18 LOC"),
        "outline": [
            ("web/runtime deps", "flask, gevent, gunicorn, redis, …", False),
            ("graph deps", "python-igraph + leidenalg REMOVED (server clustering gone)", True),
        ],
        "blocks": ["- python-igraph / - leidenalg"],
    },
    "server/requirements.txt": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "uv-compiled lockfile"),
        "file": ("requirements.txt", "100 LOC — pinned deps"),
        "outline": [
            ("compiled pins", "igraph, leidenalg, texttable (their only dependents) dropped by uv pip compile", True),
        ],
        "blocks": ["recompiled via uv pip compile requirements.in -o requirements.txt"],
    },
    "server/route_proposals.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "REMOVED — the server-side Layer-3 route-proposal engine"),
        "file": ("route_proposals.py", "DELETED — was 499 LOC: igraph/Leiden corridor clustering + RouteProposalEngine"),
        "outline": [
            ("RouteProposal dataclass + id", "removed", True),
            ("build_type_graph / leiden_communities", "removed — igraph + Leiden localization", True),
            ("heaviest_path (exact / greedy) + peel_paths", "removed — ported deterministically to routeProposals.ts", True),
            ("group_blocks / dedupe_routes / is_route_covered", "removed — ported to the client", True),
            ("RouteProposalEngine (per-type cache)", "removed — no server cache needed; client recomputes per delta", True),
        ],
        "blocks": ["entire file deleted — Layer 3 is now computed on the client (routeProposals.ts)"],
    },
    # ── Server tests ──────────────────────────────────────────────────────────
    "server/tests/unit/test_block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Server tests · tests/unit/", "NEW — the bd:/bagg: dedup contract"),
        "file": ("test_block_votes.py", "NEW FILE — 131 LOC, 9 tests against fakeredis"),
        "outline": [
            ("dedup", "same user on multiple block edges counts once; distinct users accumulate", True),
            ("removal / reversal", "exact removal; reversal moves the device between direction hashes", True),
            ("edge cases", "unmapped edge ignored; apply_deltas dedups across a block's edges", True),
            ("rebuild", "rebuild_from_db == incremental; warns on both-directions-in-a-block; silent when consistent", True),
        ],
        "blocks": ["whole file is new — asserts invariant #4 (bagg == HLEN(bd) == distinct devices)"],
    },
    "server/tests/unit/test_vote_semantics.py": {
        "on": ["Flask API"],
        "module": ("Server tests · tests/unit/", "NEW — the §4.2 press matrix + the randomized invariant property"),
        "file": ("test_vote_semantics.py", "NEW FILE — 176 LOC, 12 tests"),
        "outline": [
            ("§4.2 press matrix", "fresh / unvote-all / flip / partial / outside-blocks-untouched / already-at-direction no-ops", True),
            ("fallbacks", "singleton (no block layer), unmapped-edge singletons, direction-0, duplicate selection edges", True),
            ("randomized property", "600 random plans over a fixed block map — no sequence leaves + and − from one device in a block", True),
        ],
        "blocks": ["whole file is new — asserts invariants #2 and #3 (postcondition: T-votes in each touched block == S ∩ block at direction d)"],
    },
    "server/tests/unit/test_topology_bin.py": {
        "on": ["Flask API"],
        "module": ("Server tests · tests/unit/", "NEW — the GTB2 blob layout"),
        "file": ("test_topology_bin.py", "NEW FILE — 51 LOC, 3 tests"),
        "outline": [
            ("test_gtb2_without_blocks", "header n_blocks=0, no trailing section", True),
            ("test_gtb2_with_blocks", "trailing int32[nEdges] edge_block_id, −1 for unmapped", True),
            ("test_gtb2_block_section_accepts_plain_lists", "numpy coercion", True),
        ],
        "blocks": ["whole file is new — byte-level pin of what graphTopology.test.ts decodes"],
    },
    "server/tests/unit/test_route_proposals.py": {
        "on": ["Flask API"],
        "module": ("Server tests · tests/unit/", "REMOVED — tests of the deleted server clustering engine"),
        "file": ("test_route_proposals.py", "DELETED — was 372 LOC"),
        "outline": [
            ("Leiden / heaviest-path / peel / dedupe suites", "removed with the engine; the behavior contract is re-pinned client-side in routeProposals.test.ts", True),
        ],
        "blocks": ["entire file deleted alongside server/route_proposals.py"],
    },
    "server/tests/unit/test_vote_codec.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Server tests · tests/unit/", "codec parity: the shared pack/unpack vectors both sides must match"),
        "file": ("test_vote_codec.py", "54 LOC — 5 tests mirrored by voteKey.test.ts"),
        "outline": [
            ("VECTORS", "shared vectors updated for the 24-bit vt layout + a >65535 vt case", True),
            ("direction-bit / round-trip / mode tests", "DIR at bit 52; unpack round-trips wide ids", True),
            ("53-bit safety", "NEW — packed values (incl. direction) stay ≤ Number.MAX_SAFE_INTEGER", True),
        ],
        "blocks": [
            "vectors regenerated for VT_BITS=24 / DIR_BIT=52 (byte-identical for vt < 65536, dirless)",
            "test_packed_values_fit_53_bit_safe_integer — the JS-interop guard",
        ],
    },
    # ── Client: pure logic ────────────────────────────────────────────────────
    "client-react/src/components/GraphLayer/graphTopology.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "typed-array topology model (the mobile-memory fix) + now the block index"),
        "file": ("graphTopology.ts", "285 LOC — decode, accessors, CSR adjacency, block index"),
        "outline": [
            ("GraphTopology + accessors", "coords/ends typed arrays; + edgeBlockId/nBlocks fields", True),
            ("topologyFromJson", "JSON fallback path (no blocks)", False),
            ("decodeTopologyBin", "GTB1 + GTB2 magics; optional trailing int32 edge_block_id section, strict length checks", True),
            ("buildNodeAdj / adjFirst", "CSR node→edge adjacency (unchanged)", False),
            ("BlockIndex — buildBlockIndex", "NEW — block → edge-ids CSR (counting sort, subarray views)", True),
            ("blockKeyOf / edgesOfBlockKey / touchedBlockKeys", "NEW — block-key convention: real ids ≥ 0, singletons as −edgeId−2", True),
        ],
        "blocks": [
            "GraphTopology gains edgeBlockId?: Int32Array + nBlocks",
            "decodeTopologyBin — TOPOLOGY_BIN_MAGIC_V2, 16-byte GTB2 header, trailing block section validated against byteLength",
            "buildBlockIndex / blockKeyOf / edgesOfBlockKey / touchedBlockKeys — the CSR block index + key convention",
        ],
    },
    "client-react/src/components/GraphLayer/graphTopology.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "NEW — decode + block-index contract tests"),
        "file": ("graphTopology.test.ts", "NEW FILE — 212 LOC, 5 suites"),
        "outline": [
            ("decodeTopologyBin — GTB1", "legacy blob still decodes (no blocks)", True),
            ("decodeTopologyBin — GTB2", "block section, n_blocks, truncation errors", True),
            ("buildBlockIndex", "CSR grouping, unmapped edges excluded", True),
            ("blockKeyOf / edgesOfBlockKey / touchedBlockKeys", "the key convention + dedupe", True),
        ],
        "blocks": ["whole file is new — client half of the GTB2 byte-level pin (mirrors test_topology_bin.py)"],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "Layer 3 — route proposals: parsing/display helpers + NOW the clustering itself"),
        "file": ("routeProposals.ts", "592 LOC — display contract + deterministic local clustering"),
        "outline": [
            ("RouteProposal type + parsers", "unchanged shape (blocks, blockEdgeIds, pathEdgeIds, anchors)", True),
            ("Display helpers", "proposalShapeClass, routeBlockEdges, isRouteCovered, dropPointsCoveredByRoutes", False),
            ("chooseAnchorOrder", "ghost-waypoint ordering (unchanged)", False),
            ("Clustering constants", "NEW — MIN_NET, EXACT_PATH_MAX_VERTICES=12, PEEL_MAX_PATHS=8, PEEL_DOMINANCE, MIN_ROUTE_SCORE/EDGES, cap, Jaccard", True),
            ("Type subgraph + components", "NEW — netsForType, buildTypeAdj, connectedComponents (deterministic Leiden replacement)", True),
            ("Heaviest simple path + peeling", "NEW — exactHeaviestPath (DFS ≤ 12 v), greedyHeaviestPath, peelPaths; all tie-breaks by ascending id", True),
            ("groupBlocks / dedupeRoutes / computeRouteProposals", "NEW — blocks projection, same-type Jaccard dedupe, rank + cap", True),
        ],
        "blocks": [
            "+423 lines: the deterministic port of the server engine (see the removed route_proposals.py map)",
            "computeRouteProposals(topology, nodeAdj, voteData) — the one entry point GraphLayer calls per vote delta",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the clustering behavior contract, re-pinned client-side"),
        "file": ("routeProposals.test.ts", "419 LOC — 15 suites"),
        "outline": [
            ("parse / display / anchor suites", "pre-existing (parseRouteProposal, isRouteCovered, chooseAnchorOrder …)", False),
            ("computeRouteProposals — net weighting + MIN_NET", "NEW", True),
            ("heaviest simple path / peeling / per-type independence", "NEW — parallel corridors separate", True),
            ("dedupeRoutes / block grouping / activity gates / ranking", "NEW", True),
            ("determinism", "NEW — same vote state ⇒ identical proposal list (invariant #5)", True),
        ],
        "blocks": ["+283 lines of clustering suites replacing the deleted server test_route_proposals.py coverage"],
    },
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "applies optimistic + authoritative vote changes to the in-memory arrays"),
        "file": ("voteApply.ts", "250 LOC — rederiveNodes, my-vote/edge transitions, authoritative SETs"),
        "outline": [
            ("legendIndex / rederiveNodes", "unchanged", False),
            ("applyMyVoteChange / applyEdgeVoteChange", "optimistic transitions (unchanged)", False),
            ("applyBlockCounts", "NEW — idempotent SET of deduped [up,down] per block (the blockCounts twin of vtCounts); returns changed for heat re-broadcast", True),
            ("applyAuthoritativeCounts", "edge SET (unchanged)", False),
        ],
        "blocks": ["applyBlockCounts(data, vtLabel, blockCounts) — updates block_vote_types + net block_votes, grows the block legend"],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "vote-apply unit suite"),
        "file": ("voteApply.test.ts", "177 LOC — 4 suites"),
        "outline": [
            ("optimistic increment / transition / authoritative SET suites", "pre-existing", False),
            ("applyBlockCounts (SET, idempotent, block twin of vtCounts)", "NEW suite", True),
        ],
        "blocks": ["+43 lines: applyBlockCounts idempotence, legend growth, net recompute, out-of-range block ids ignored"],
    },
    "client-react/src/utils/blockSelection.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "NEW — pure block-scoped selection helpers (no React/DOM)"),
        "file": ("blockSelection.ts", "NEW FILE — 87 LOC"),
        "outline": [
            ("materializeBlocks", "selection edges → touched-block edge lists (CSR subarray views; singleton fallback)", True),
            ("selectionVoteRows", "modal rows: deduped per-block counts summed over touched blocks; per-edge rows for singleton keys; null without a block layer", True),
        ],
        "blocks": ["whole file is new — the bridge between topology (blockIndex) and voteStore/castVote, which stay topology-free"],
    },
    "client-react/src/utils/blockSelection.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "NEW — blockSelection suite"),
        "file": ("blockSelection.test.ts", "NEW FILE — 97 LOC, 2 suites"),
        "outline": [
            ("materializeBlocks", "real blocks via CSR, singleton fallback, dedupe of shared blocks", True),
            ("selectionVoteRows", "block-grain sums, mixed singleton edges, null fallback", True),
        ],
        "blocks": ["whole file is new"],
    },
    "client-react/src/utils/castVote.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "the single vote-cast path — now block-scoped clear-then-cast"),
        "file": ("castVote.ts", "249 LOC — plan, optimistic dispatch, POST, rollback/reconcile"),
        "outline": [
            ("Module doc", "rewritten around docs §4 semantics", True),
            ("BlockVotePlan + planBlockVote", "REPLACES planVoteChange — coverage 'all' ⇒ unvote (wire 0); else clear-then-cast; castEdges vs clearEdges (reversals are casts, not clears)", True),
            ("voteButtonState", "NEW — 'active' iff coverage(dir) === 'all' (§4.1 tri-state)", True),
            ("dispatchOptimistic / groupByPrevDir", "optimistic transitions now grouped by prior direction across cast + clear sets", True),
            ("castVotes", "takes blocks (materialized lists; singleton fallback), rolls back on failure, reconciles the server's `cleared` response", True),
        ],
        "blocks": [
            "planVoteChange → planBlockVote({mode, edgeIds, label, direction, blocks}) via blockCoverage + myVotesInBlocks",
            "voteButtonState(coverage, dir)",
            "castVotes — optimistic apply of cast+clear with rollback snapshot; `cleared` from the response reconciled into voteStore",
        ],
    },
    "client-react/src/utils/castVote.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "the press-rule matrix, client-side"),
        "file": ("castVote.test.ts", "228 LOC — 4 suites"),
        "outline": [
            ("planBlockVote — singleton blocks", "the per-edge fallback matches old behavior", True),
            ("planBlockVote — real blocks", "selection ⊂ touched blocks: clears outside the selection, flips, partial coverage", True),
            ("voteButtonState", "tri-state mapping", True),
            ("castVotes", "optimistic dispatch, rollback, cleared-reconcile (fetch mocked)", True),
        ],
        "blocks": ["rewritten (+237/−53): mirrors the server's §4.2 matrix at the plan level"],
    },
    "client-react/src/utils/voteKey.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "the packed vote-key codec — bit-for-bit mirror of server vote_store"),
        "file": ("voteKey.ts", "83 LOC — pack/unpack/redisField (arithmetic, not 32-bit bitwise)"),
        "outline": [
            ("Layout doc + constants", "vt 16→24 bits (VT_BITS/VT_SPAN), DIR_SHIFT 2^44 → 2^52; overflow post-mortem + no-client-migration note", True),
            ("MODE_IDS / modeToInt / packVoteKey", "unchanged", False),
            ("unpackVoteKey / redisField", "vt mask (1<<16) → VT_SPAN", True),
        ],
        "blocks": [
            "VT_BITS = 24, VT_SPAN, DIR_SHIFT = 2 ** 52",
            "unpackVoteKey — % VT_SPAN (was % (1 << 16))",
        ],
    },
    "client-react/src/utils/voteKey.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "codec parity suite (mirrors server test_vote_codec.py)"),
        "file": ("voteKey.test.ts", "63 LOC — shared vectors + round-trips"),
        "outline": [
            ("voteKey codec — backend parity", "vectors updated for the 24-bit layout + a >65535 vt case + 53-bit safety", True),
        ],
        "blocks": ["shared vectors regenerated in lockstep with the server suite"],
    },
    "client-react/src/utils/voteStore.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "this device's local vote memory (localStorage) + coverage queries"),
        "file": ("voteStore.ts", "372 LOC — persisted my-votes, subscriptions, coverage"),
        "outline": [
            ("Persistence + label migration", "cityedit_votes_v2 stores (unchanged)", False),
            ("get/set/subscribe", "unchanged", False),
            ("coverage (edge-grain)", "kept for callers that still need per-edge splits", False),
            ("blockCoverage", "NEW — per-direction all|some|none over materialized block edge lists (§4.1); topology-free by design", True),
            ("myVotesInBlocks", "NEW — every edge in the touched blocks where I hold a vote (feeds the clear set)", True),
            ("reconcileEdge / _resetVoteStore", "unchanged", False),
        ],
        "blocks": [
            "CoverageLevel / BlockCoverage types + blockCoverage()",
            "myVotesInBlocks() — dedupes shared edges across blocks",
        ],
    },
    "client-react/src/utils/voteStore.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "voteStore suite"),
        "file": ("voteStore.test.ts", "204 LOC — 7 suites"),
        "outline": [
            ("direction / fallback / coverage / notification / reconcile suites", "pre-existing", False),
            ("blockCoverage (block-scoped button state)", "NEW suite — all/some/none per direction", True),
            ("myVotesInBlocks", "NEW suite", True),
        ],
        "blocks": ["+72 lines: the §4.1 coverage levels + the clear-set query"],
    },
    "client-react/src/types/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · types/", "shared API/domain types"),
        "file": ("index.ts", "149 LOC — RouteResponse, GraphData, VoteDelta, …"),
        "outline": [
            ("VoteDelta", "+ blockCounts?: authoritative deduped [up,down] per affected block", True),
            ("GraphData", "+ edgeBlockId/nBlocks (GTB2) and block_votes / block_vote_types / block_vote_type_legend / n_blocks / blocks_version", True),
            ("Other types", "unchanged", False),
        ],
        "blocks": [
            "VoteDelta.blockCounts — the delta's block twin of vtCounts",
            "GraphData block fields — the /api/graph-votes block payload + decoded-topology fields",
        ],
    },
    # ── Client: UI wiring ─────────────────────────────────────────────────────
    "client-react/src/config.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · src/", "runtime config: tile/API/WS URLs, per-city rebinding"),
        "file": ("config.ts", "125 LOC — CONFIG + applyCityConfig"),
        "outline": [
            ("Map/zoom defaults", "unchanged", False),
            ("graphTilesUrl / blockTilesUrl", "NEW blockTilesUrl (blocks.pmtiles beside graph tiles); dev URLs honor VITE_API_BASE", True),
            ("apiUrl / wsUrl", "VITE_API_BASE / VITE_WS_BASE overrides — worktree stacks on alt ports", True),
            ("applyCityConfig", "rebinds blockTilesUrl per city (graph.pmtiles → blocks.pmtiles)", True),
        ],
        "blocks": [
            "blockTilesUrl (+ VITE_API_BASE-aware dev URLs)",
            "apiUrl/wsUrl env overrides",
            "applyCityConfig — per-city blocks path",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "the GPU basemap under Leaflet — now also the block heat + selection renderer"),
        "file": ("MapLibreBackground.tsx", "312 LOC — style builder + sync with the Leaflet view"),
        "outline": [
            ("BlockVotesDetail / BlockSelectDetail events", "NEW — the GraphLayer→background contract (city-edit:block-votes / :block-select)", True),
            ("blockFillPaint", "NEW — feature-state heat ∈ [0,1] → style heat ramp; transparent at 0", True),
            ("buildStyle", "graph-edges line layer → blocks source (promoteId block_id) + block-heat fill + block-select fill/casing", True),
            ("Map init / Leaflet sync", "unchanged", False),
            ("Feature-state effect", "NEW — listens for both events, applies heat/selected feature-state, re-applies on style reload", True),
        ],
        "blocks": [
            "BLOCK_VOTES_EVENT / BLOCK_SELECT_EVENT + detail types",
            "blockFillPaint(heat) interpolation",
            "buildStyle — blocks.pmtiles source, block-heat + block-select layers (selection token fill + 2px casing)",
            "feature-state application effect (+ latest-payload cache for style reloads)",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · context/", "route/selection state machine + the cast entry point for the side panel"),
        "file": ("RouteContext.tsx", "1232 LOC — Selection model, waypoints, casting, pressed state"),
        "outline": [
            ("Imports", "coverage → blockCoverage; + voteButtonState", True),
            ("BlockMaterializer type + setBlockMaterializer", "NEW — GraphLayer registers selection→blocks (topology stays out of this context)", True),
            ("Selection / waypoint machinery", "unchanged", False),
            ("castVotes call", "passes blocks: materializer(edges) (singleton fallback pre-topology)", True),
            ("Pressed state", "voteButtonState(blockCoverage(…)) — active iff EVERY touched block holds my vote (same rule as the modal)", True),
            ("Context value", "exposes setBlockMaterializer", True),
        ],
        "blocks": [
            "BlockMaterializer type + blockMaterializerRef/setBlockMaterializer",
            "cast path — blocks: blockMaterializerRef.current?.(edges)",
            "pressed-state — blockCoverage + voteButtonState replace edge-grain coverage()",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the canvas heat/hover layer + topology loader + proposal renderer — the heart of the map"),
        "file": ("GraphLayer.tsx", "3707 LOC — load topology+votes, paint, hit-test, proposals, modals"),
        "outline": [
            ("Module helpers / constants", "decoder moved out to graphTopology.ts; MY_VOTES_EDGE_CAP = 500", True),
            ("Component refs", "blocksActiveRef, blockIndexRef, hasBlocksRef beside nodeAdj", True),
            ("Block materializer registration", "registers RouteContext's selection→blocks closure (singleton until topology loads)", True),
            ("Block highlight dispatch", "covering block ids for selection + pinned point (adjFirst) + hover → BLOCK_SELECT_EVENT", True),
            ("Vote/heat plumbing", "broadcastBlockVotes → BLOCK_VOTES_EVENT; canvas skips edge heat when blocks active", True),
            ("Route proposals", "recomputeRouteProposals (computeRouteProposals) + coalescing scheduler; recompute on vote deltas + mode switch", True),
            ("Topology load", "binVersion = version + '-bin2' (format-versioned cache key + URL buster, mirrors the server ETag)", True),
            ("Modals / cards", "hover + selection cards sum deduped per-block counts; ProposalCard tri-state buttons (§4.1)", True),
            ("my-votes reconcile", "requested at block breadth (capped), full-response apply via reconcileEdge", True),
            ("Hit-testing / markers / drag", "unchanged", False),
        ],
        "blocks": [
            "imports: graphTopology block API, blockSelection, computeRouteProposals, MapLibre block events; local GTB1 decoder deleted (−42 lines)",
            "block refs + materializer registration effect",
            "dispatchBlockSelect — selection/hover → covering real block ids (route path, pinned target, transient hover)",
            "broadcastBlockVotes + blocksActiveRef — MapLibre fill becomes the heat; canvas heat skipped",
            "recomputeRouteProposals / scheduleRouteProposalsRecompute — local Layer-3 clustering per (coalesced) vote delta",
            "binVersion '-bin2' cache key + buster; GTB2 decode populates blockIndexRef",
            "hover + selection cards: per-block deduped sums (adjFirst rule for nodes); ProposalCard pressed = full block coverage",
            "my-votes fetch at block breadth (MY_VOTES_EDGE_CAP) + authoritative full-response reconcile",
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

    diff_names = {name for name, _ in files}
    missing = sorted(diff_names - set(FILE_CONTEXT))
    extra = sorted(set(FILE_CONTEXT) - diff_names)
    if missing:
        print(f"WARNING: {len(missing)} diff files without FILE_CONTEXT: {missing}")
    if extra:
        print(f"WARNING: {len(extra)} FILE_CONTEXT entries not in diff: {extra}")

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
    commits_html = "\n".join(
        f'<li><code>{sha}</code> {html.escape(msg)}</li>' for sha, msg in COMMITS
    )

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
  .lede {{ font-family: var(--font-ed); font-size: 18px; color: #2c2a24; margin: 20px 0 12px; }}
  .layers {{ font-family: var(--font-mono); font-size: 12.5px; background: #fff; border: 1px solid var(--hairline);
    border-radius: 10px; padding: 14px 18px; overflow-x: auto; margin: 0 0 28px; }}
  .card {{ background: #fff; border: 1px solid var(--hairline); border-radius: 14px; padding: 24px 26px;
    margin: 20px 0; box-shadow: 0 1px 0 rgba(0,0,0,0.02); }}
  .tag {{ display: inline-block; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); border: 1px solid var(--hairline); border-radius: 6px; padding: 2px 8px; margin-bottom: 10px; }}
  h2 {{ font-family: var(--font-ed); font-size: 24px; margin: 4px 0 14px; }}
  h3 {{ font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--accent);
    margin: 20px 0 6px; }}
  ul {{ margin: 6px 0 0; padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  ul.files li, ul.commits li {{ font-family: var(--font-mono); font-size: 12.5px; color: #33312b; }}
  code {{ font-family: var(--font-mono); font-size: 0.88em; background: var(--code-bg);
    padding: 1px 5px; border-radius: 4px; }}
  p {{ margin: 6px 0; }}
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
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
  .ctx-tier {{ position: relative; }}
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · diff <code>00a5492..HEAD</code> · spec: <code>docs/three-layer-model.md</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#commits">Commits</a><a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Voting is now organized as three layers, per
  <code>docs/three-layer-model.md</code>: the <strong>edge/node graph</strong> stays the storage source of
  truth (schema unchanged); <strong>procedural blocks</strong> — evaluated against Brook's NYC planimetric
  ground truth at median IoU 0.84 / area ratio 1.00 over ~82k segments, made total by foot-component blocks
  — become the aggregation, display, and interaction grain, with votes deduped once per (voter, type,
  direction) and a server-enforced invariant that a block never holds + and − from one device for one vote
  type (block-scoped clear-then-cast in <code>/api/vote</code>); and <strong>route proposals</strong> are now
  computed deterministically on the client, deleting the server Leiden engine and its igraph deps. Along the
  way: a critical fix widening the packed vote-type field 16 → 24 bits (ids past 65 535 were overflowing into
  the direction bit and recording as downvotes — deploy must flush <code>ev:*</code>/<code>bagg:*</code> and
  rehydrate from Postgres), authoritative <code>blockCounts</code> on the delta broadcast, and the GTB2
  topology blob with its <code>-bin2</code> cache key.</p>

  <pre class="layers">Layer 3  Route proposals   dynamic · client-computed · deterministic clustering of vote state
                            │ many-to-one (edge → proposal), real time
Layer 2  Blocks            precomputed · procedural per-city polygons · aggregate + display votes
                           deduped per (voter, type, direction) · unvote/flip grain · highlight target
                            │ many-to-one (edge/node → block), baked
Layer 1  Edge/node graph   OSRM pathfinding + votable topology · votes stored per
                           (map, edge, vote_type, device) → direction ±1   ← storage source of truth</pre>

  {sections_html}

  <section class="card" id="commits">
    <div class="tag">History</div>
    <h2>Commits ({len(COMMITS)}) — <code>git log --oneline 00a5492..HEAD</code></h2>
    <ul class="commits">{commits_html}</ul>
  </section>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed.
    Every file carries its hierarchical context map (System ▸ Module ▸ File ▸ changed blocks).</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_three_layer_report.py</code>.
    Regenerate after further edits with <code>git diff 00a5492..HEAD &gt; changelog/changes.diff &amp;&amp; python changelog/build_three_layer_report.py</code>.
  </footer>
</div>
</body>
</html>
"""
    with open(OUT_PATH, "w") as f:
        f.write(doc)
    print(f"Wrote {OUT_PATH} ({len(files)} files in diff, {len(FILE_CONTEXT)} context entries)")


if __name__ == "__main__":
    main()
