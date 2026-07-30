#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-07-29-slug-redirects-src-tracking.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-07-30-top-proposal-threshold-ghost-waypoints.html")

DATE = "2026-07-30"
TITLE = "Top-proposal support floor, modal badges, and routing-consistent corridors with ghost waypoints"


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
        "id": "floor",
        "tag": "React client · proposals",
        "title": "1 · Top proposals now require &gt;100 net votes",
        "symptom": (
            "A vote type with a handful of votes could still be a <em>Top Proposal</em> — its best "
            "edge only had to beat the rest of its own (rare) type. Selecting such a pin opened a "
            "modal where OTHER vote types showed far higher counts right below the “top” one, which "
            "read as a contradiction."
        ),
        "cause": [
            "PBTP winner selection (<code>computeVoteTypeWinners</code>) admitted any edge with net &gt; 0 "
            "— the bar was <em>relative to the type</em>, never absolute.",
            "RBTP corridors had only structural gates (<code>MIN_ROUTE_SCORE</code> = 3, edges, blocks) — "
            "again no absolute support bar.",
        ],
        "fixes": [
            "<strong><code>TOP_PROPOSAL_MIN_NET = 100</code></strong> (topProposals.ts): a proposal counts as "
            "“top” only with STRICTLY more than 100 net votes. Threaded as a <code>minNet</code> param through "
            "<code>computeVoteTypeWinners</code> / <code>selectTopProposals</code> (default = the floor, so the "
            "product rule is the default; tests pass 0 to probe mechanics).",
            "GraphLayer passes the floor for street maps and <strong>0 for station networks</strong> — station "
            "pins are synthesized per station, not vote winners, so the floor would only break their few "
            "internal winner uses.",
            "RBTPs share the same bar: the proposal job gets <code>minRouteScore: TOP_PROPOSAL_MIN_NET + 1</code> "
            "(score = sum of path-edge nets), and the pipeline now SKIPS any connected component whose total "
            "weight can't reach the score gate — with the 100-floor that prunes almost every component before "
            "any routing work runs (the perf enabler for §3).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/topProposals.ts",
            "client-react/src/components/GraphLayer/topProposals.test.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "badges",
        "tag": "React client · modal",
        "title": "2 · The modal badges every vote type that is a top proposal there",
        "symptom": (
            "Even with the floor, a modal row's counts alone can't tell you WHICH vote type earned the pin "
            "you clicked — the top-proposal row could sit below rows with bigger raw numbers (distinct-voter "
            "rows vs fanned-out net scores) with nothing marking it."
        ),
        "cause": [
            "The cards (<code>ProposalCard</code>) rendered label + −/net/+ tallies only; “is this label a "
            "current top proposal for what this card shows?” existed nowhere as data.",
        ],
        "fixes": [
            "<strong>Purely derived, never stored</strong>: <code>topKindsFor(edgeIds, includeRoutes)</code> in "
            "GraphLayer computes a <code>Map&lt;label, \"point\"|\"route\"|\"both\"&gt;</code> from the SAME "
            "<code>winners</code>/<code>routeProposals</code> arrays that render the map pins, so a badge can never "
            "disagree with a pin (both refresh on the same batched proposal sweep).",
            "<strong>point</strong>: a PBTP winner sits on one of the card's blocks — the same block grain the rows "
            "sum over. <strong>route</strong>: an RBTP whose corridor is FULLY contained in the selection "
            "(<code>expandSelectionToUndirected</code> + <code>isRouteCovered</code> — brushing a corridor doesn't "
            "badge it). Point-only cards skip the containment scan (a one-block card can't contain a ≥5-block corridor).",
            "Per-card memos feed all four cards (pinned point, edge hover, diamond hover, route summary); "
            "<code>ProposalCard</code> gains a <code>topKinds</code> prop and renders a mini square (point) / "
            "diamond (route) badge before the label + bolds the row — the same glyph language as the map pins "
            "(<code>proposalShapeClass</code>). CSS in globals.css.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/styles/globals.css",
        ],
    },
    {
        "id": "ghosts",
        "tag": "React client · proposals + selection + URL",
        "title": "3 · Corridors grow routing-consistently — ghost waypoints in the URL",
        "symptom": (
            "Route proposals were built by greedily chaining high-vote edges with no regard for how the app "
            "actually ROUTES. A selected proposal's URL carried just its two anchors plus an "
            "<code>f&lt;id&gt;</code> token — the corridor reproduced only while the live proposal existed; once "
            "votes reshaped it, the OSRM fallback between the anchors could wander far off the corridor, so "
            "shared top-proposal links didn't persist. Roundaboutness was held down by geometric heuristics "
            "(straightness splitting + budget-window trimming) rather than anything routing-shaped."
        ),
        "cause": [
            "<code>greedyHeaviestPath</code>/<code>exactHeaviestPath</code> optimized path WEIGHT on the voted "
            "subgraph only — nothing constrained the corridor to be reproducible by routing between any set of "
            "waypoints, so no waypoint set could persist it.",
        ],
        "fixes": [
            "<strong>New growth</strong> (<code>growCorridor</code>): start at the component's heaviest edge; "
            "repeatedly take the heaviest net-positive arc off either tip (ties: lowest edge id) that fits the "
            "support-earned length budget. An extension is accepted outright only if the OPEN SEGMENT (tip → "
            "nearest inner waypoint) <em>stays a shortest path through the full graph</em>; otherwise the previous "
            "tip is pinned as a <strong>ghost waypoint</strong>. At most <code>MAX_GHOST_WAYPOINTS</code> (3) pins — "
            "the 3rd ends growth — so every proposal is reproducible as ≤ 5 route waypoints. This REPLACES "
            "<code>splitLoopyPath</code> + <code>capPathToLengthBudget</code> (roundaboutness is now bounded by the "
            "pin budget, per spec).",
            "<strong>The oracle</strong> (<code>makeSegmentShortestCheck</code>): a bounded, deterministic A* over the "
            "full topology — crow-flies heuristic (×0.999 for admissibility), every g-score pruned at the corridor "
            "length, so the search explores exactly the ellipse of paths that could beat the corridor (razor-thin "
            "for the near-straight segments consistent growth produces). Ties and sub-eps (1 m) shortcuts are NOT "
            "detours; pop cap fails OPEN. Injectable via <code>opts.segmentShortestCheck</code> for tests.",
            "<strong>Proposal shape</strong>: <code>RouteProposal</code> gains <code>waypointNodes</code> / "
            "<code>waypointCoords</code> ([anchor, ghosts…, anchor]) and per-segment <code>segments</code> edge "
            "slices; the wire parse synthesizes anchor-only chains for legacy payloads.",
            "<strong>Selection = the chain</strong>: clicking a diamond selects ALL waypoints (per-segment forced "
            "flags), so the URL serializes <code>?w=a,f&lt;id&gt;;g1,f&lt;id&gt;;…;b</code> — the serializer already "
            "supported per-waypoint tokens. The corridor resolver now slices the live proposal between the two "
            "waypoints nearest each segment (<code>corridorSliceBetween</code>); retired proposals fall back to the "
            "per-segment edge snapshots, then to OSRM through the ghosts — which now approximates the corridor by "
            "construction. That's the persistence story.",
            "<strong>Threading generalized pair → chain</strong>: RouteContext's corridor ops "
            "(<code>selectCorridor</code>, <code>replaceStart/EndWithChain</code>, "
            "<code>insertWaypointChainAtSegment</code>, <code>replaceGhostWaypointWithChain</code>) insert the whole "
            "chain with end-dedupe against neighbors; MapView's click/drop handlers orient the chain via "
            "<code>chooseAnchorOrder</code> on its endpoints; <code>anchorsAreWaypoints</code> now requires every "
            "chain point to be a consecutive route waypoint (either direction); the diamond's [×] pulls the whole "
            "chain back out.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.test.ts",
            "client-react/src/components/GraphLayer/routeProposals.perf.test.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/context/RouteContext.tsx",
            "client-react/src/components/MapView/MapView.tsx",
            "docs/three-layer-model.md",
        ],
    },
]

VERIFY = [
    "Unit: <code>npx vitest run</code> — 327 tests green, incl. 73 in routeProposals.test.ts "
    "(new suites: A* oracle accepts only-path / rejects shortcut / tolerates ties / fails open at the "
    "pop cap; growCorridor no-ghost, pin-on-inconsistency, 3-pin stop, budget skip, determinism; "
    "end-to-end ghost pinning on a triangle-with-shortcut topology; segments always partition the path; "
    "corridorSliceBetween slicing/orientation) and 37 in topProposals.test.ts (floor default, "
    "strictly-greater, minNet 0 escape hatch).",
    "Perf (real nyc-bikes graph, 3.3M edges, ~183k voted, PERF=1 harness now passing the app's "
    "<code>minRouteScore</code>): full recompute <strong>~1.33s</strong>, worst per-type slice "
    "<strong>341ms</strong>, 20 corridors — same ballpark as the old pipeline (~750ms/250ms) despite "
    "per-extension A* checks, thanks to the component-weight prune.",
    "Browser (localhost:3000/m/nyc-bikes): 20 diamonds render; clicking one produced "
    "<code>?w=40.741481,-73.988975,ffa1a7130;40.767609,-73.981485</code>, traced Broadway verbatim, and "
    "the route card showed “TOP ROUTE PROPOSAL — Add sharrow” with the <strong>Add sharrow row bolded + "
    "diamond-badged</strong> while higher-count rows (Add bike lane +394…) stayed plain — the exact "
    "confusion this fixes.",
    "Deep-link restore: fresh navigation to that URL re-selected the corridor, re-traced it via the "
    "forced-corridor slice resolver, and re-badged the card.",
    "A low-net point modal (net 1 “Add bike lane”) shows NO badge and a plain “Proposal” eyebrow — "
    "the floor working on the point side.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-bikes</code> — every square/diamond pin should belong to a "
    "type with real support (&gt;100 net); hover a low-vote street: its card rows show no badge.",
    "Click a diamond: the URL should list the proposal's waypoints (2–5) each with an "
    "<code>,f&lt;id&gt;</code> token, the corridor should trace exactly, and the route card should bold + "
    "diamond-badge the proposal's own row.",
    "Copy that URL into a fresh tab — the corridor, card header, and badge should all restore.",
    "Trace a route that fully contains a corridor by hand (start before, end after) — the diamond's row "
    "badges in the route card; shorten the route so a block drops out — the badge disappears.",
    "Drag your route's END onto a diamond — the whole chain threads in (ghost pins appear for ghosted "
    "proposals); the diamond's [×] pulls all of them back out at once.",
    "On a map with a corridor that bends around a shorter parallel path, confirm the proposal carries "
    "mid ghost pins (≤3) and that routing through them (delete the f-token from the URL to force OSRM) "
    "still follows the corridor.",
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
# For every diff we render a recursively-summarized map: the SYSTEM (which of the
# app's components this file belongs to) → the MODULE (subsystem + one-line
# summary) → the FILE (summary + an outline of its top-level sections) → the
# changed BLOCKS. The file outline is a focus+context minimap: changed sections
# are highlighted, the rest are dimmed, so you see where the diff lands among its
# peers at every zoom level.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

# label, summary, changed?
FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/topProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "PBTP selection: which edges earn a point-based Top Proposal square"),
        "file": ("topProposals.ts", "~360 LOC — the 5-step winner pipeline (per-type winners → edge/block dedupe → spacing → limit)"),
        "outline": [
            ("shuffleKey / compareWinners", "salted deterministic tiebreak", False),
            ("TOP_PROPOSAL_MIN_NET", "NEW — the >100 net support floor both proposal families share", True),
            ("computeVoteTypeWinners", "step 1 — now drops edges at/below max(0, minNet)", True),
            ("topLabelForEdges", "deep-link vote-type fallback", False),
            ("dedupeWinnersByEdge / ByBlock / spaceOutWinners / applyTopProposalLimit", "steps 2–5", False),
            ("selectTopProposals", "full path — minNet param, default = the floor", True),
        ],
        "blocks": [
            "TOP_PROPOSAL_MIN_NET = 100 — strictly-greater floor, doc'd as the shared PBTP/RBTP bar",
            "computeVoteTypeWinners(…, minNet = 0) — count <= max(0, minNet) skips",
            "selectTopProposals(…, minNet = TOP_PROPOSAL_MIN_NET) — threads the floor through step 1",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "unit tests for the PBTP winner pipeline"),
        "file": ("topProposals.test.ts", "~400 LOC — per-step + full-path suites"),
        "outline": [
            ("computeVoteTypeWinners / dedupe / spacing suites", "mechanics probes — now pass minNet 0 where counts are tiny", True),
            ("top-proposal support floor suite", "NEW — default floor, strictly-greater, minNet-0 escape hatch", True),
        ],
        "blocks": [
            "5 selectTopProposals call sites gain (600, undefined, 0) — mechanics tests opt out of the floor",
            "new describe: floor default drops net-3 winner, keeps net-130; net == 100 excluded (strict)",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "RBTP corridors: client-side deterministic extraction + selection/corridor helpers"),
        "file": ("routeProposals.ts", "~1180 LOC — parse/shape/coverage helpers, the growth pipeline, the resumable job"),
        "outline": [
            ("RouteProposal + wire parse", "now carries waypointNodes / waypointCoords / per-segment `segments` (legacy synthesized)", True),
            ("shape / coverage / dedupe helpers", "diamond class, isRouteCovered, twin expansion, point subsumption", False),
            ("corridorCoordinates / corridorFromEdgeIds", "verbatim geometry + snapshot fallback", False),
            ("corridorSliceBetween", "NEW — the sub-chain between the waypoints nearest a/b, oriented a→b (per-segment resolver)", True),
            ("chooseAnchorOrder / Before", "chain-orientation choice for drops", False),
            ("constants", "MAX_GHOST_WAYPOINTS / ROUTE_CONSISTENCY_EPS_M / ROUTE_CHECK_MAX_POPS join the gates; splitLoopy/straightness constants deleted", True),
            ("makeSegmentShortestCheck", "NEW — bounded deterministic A* consistency oracle (fails open at the pop cap)", True),
            ("growCorridor", "NEW — routing-consistent two-tip growth; pins ghosts, 3rd pin ends growth; budget-gated", True),
            ("peelCorridors", "grow-and-remove peel (replaces peelPaths + exact/greedy heaviest path + loop split + window trim)", True),
            ("createRouteProposalJob / computeRouteProposals", "component-weight prune; proposals carry the waypoint chain", True),
        ],
        "blocks": [
            "RouteProposal { waypointNodes, waypointCoords, segments } + wire parse synthesis",
            "corridorSliceBetween — walk the chain, locate waypoint positions from segment lengths, slice + orient",
            "MAX_GHOST_WAYPOINTS=3, ROUTE_CONSISTENCY_EPS_M=1, ROUTE_CHECK_MAX_POPS=30000",
            "makeSegmentShortestCheck — A* from segment end toward its bound; g pruned at corridor length; ties tolerated",
            "growCorridor — heaviest-seed, best-arc-off-either-tip, open-segment bookkeeping per side, pin/stop rules, segments assembly",
            "step() — skip components whose total weight < minRouteScore; peelCorridors(grow); proposals with waypoint fields",
            "DELETED: exactHeaviestPath, greedyHeaviestPath, pathWeight, heaviestPathFromAdj, peelPaths, splitLoopyPath, capPathToLengthBudget, EXACT_PATH_MAX_VERTICES, ROUTE_STRAIGHTNESS_*/WINDOW_*/SPLIT_MAX_DEPTH",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "unit + integration tests for the corridor pipeline"),
        "file": ("routeProposals.test.ts", "~740 LOC — parse/coverage/dedupe suites + the new growth/oracle/slice suites"),
        "outline": [
            ("fixtures", "route()/bareRoute() gain waypoint fields (default = anchors, one segment)", True),
            ("parse / shape / coverage / dedupe suites", "kept — parse now also checks waypoint synthesis + wire carry", True),
            ("computeRouteProposals mechanics suites", "net weighting, peeling, per-type, blocks, gates, quota, determinism — expectations preserved under growth", True),
            ("splitLoopyPath + capPathToLengthBudget suites", "DELETED with the functions", True),
            ("makeSegmentShortestCheck suite", "NEW — only-path, shortcut, tie, pop-cap fail-open", True),
            ("growCorridor suite", "NEW — no-ghost, pin, 3-pin stop, budget skip, tie determinism (fake oracles)", True),
            ("ghost end-to-end + corridorSliceBetween suites", "NEW — triangle-with-shortcut pins node 1; U with no shortcut stays whole; slice orientation/nulls", True),
        ],
        "blocks": [
            "imports swap splitLoopy/cap for growCorridor/makeSegmentShortestCheck/corridorSliceBetween/MAX_GHOST_WAYPOINTS",
            "makeTopo2D helper — 2-D coords (makeTopo's colinear nodes make every corridor trivially shortest)",
            "the corridor-length-cap suite now documents budget-limited GROWTH (same expectations, new mechanism)",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.perf.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "opt-in perf harness against the real nyc-bikes graph (PERF=1)"),
        "file": ("routeProposals.perf.test.ts", "~75 LOC — decode, adjacency, timed recomputes, per-slice timings"),
        "outline": [
            ("timed runs + job slices", "now pass minRouteScore: TOP_PROPOSAL_MIN_NET + 1 — the in-app shape", True),
        ],
        "blocks": [
            "all three createRouteProposalJob/computeRouteProposals call sites mirror the app's floor",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "the canvas heatmap + proposal pins + cards host (4.7k LOC hub)"),
        "file": ("GraphLayer.tsx", "~4700 LOC — heatmap, hover/pinned/route cards, PBTP squares, RBTP diamonds, resolvers"),
        "outline": [
            ("corridor resolver registration", "live proposal → corridorSliceBetween per segment; snapshot / OSRM fallback", True),
            ("recomputeTopProposals", "passes the support floor (stations exempt)", True),
            ("anchorsAreWaypoints", "now chain-aware: every proposal waypoint consecutive in the route, either direction", True),
            ("recomputeRouteProposals job", "opts gain minRouteScore: TOP_PROPOSAL_MIN_NET + 1", True),
            ("hover/pinned card content", "hoverRowsEdgeId tracked for the badge grain", True),
            ("topKindsFor + per-card memos", "NEW — derived Map<label, point|route|both> from live winners/routeProposals", True),
            ("marker memos / cluster engine", "unchanged", False),
            ("ProposalCard", "topKinds prop; rows render square/diamond badges + bold", True),
        ],
        "blocks": [
            "import swap: corridorCoordinates → corridorSliceBetween; + TOP_PROPOSAL_MIN_NET",
            "resolver: corridorSliceBetween(topo, p, a, b) — anchor-only proposals degenerate to the whole corridor",
            "selectTopProposals(…, isStationNetwork ? 0 : TOP_PROPOSAL_MIN_NET)",
            "createRouteProposalJob opts + minRouteScore comment (shared bar)",
            "anchorsAreWaypoints — index-run check over p.waypointCoords",
            "hoverRowsEdgeId — the edge the hover rows summed over",
            "topKindsFor + pinnedTopKinds/hoverTopKinds/hoverRbtpTopKinds/routeTopKinds memos",
            "TopProposalKind/TopKindMap types; ProposalCardProps.topKinds; row badge markup",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · context/", "the canonical selection owner: waypoints, routing, casts, history"),
        "file": ("RouteContext.tsx", "~1740 LOC — selection state, recalc choreography, corridor ops, history"),
        "outline": [
            ("interface — corridor ops", "pair signatures → chain signatures (points + per-segment corridors)", True),
            ("selection seed / history / recalc core", "unchanged", False),
            ("replaceGhostWaypointWithChain", "mid → whole chain; end-dedupe vs neighbors; per-segment stamps", True),
            ("insertWaypointChainAtSegment", "chain into a segment; chainStart accounting mirrors the old 4 pair cases", True),
            ("replaceEnd/StartWithChain", "chain threads at an endpoint; no-op re-drop check generalized", True),
            ("selectCorridor", "the chain BECOMES the selection (≤5 waypoints, flags stamped)", True),
            ("removeWaypointsNear / notifyCorridorsChanged", "unchanged (already list-shaped)", False),
        ],
        "blocks": [
            "four ops renamed *WithPair → *WithChain; all bodies generalized from 2 anchors to k-point chains",
            "selectCorridor builds waypoints from chain.map with forcedCorridor per leading index",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · MapView/", "the map shell: tools, click/drag handlers, marker wiring"),
        "file": ("MapView.tsx", "~800 LOC — placement handlers, corridor threading, marker render"),
        "outline": [
            ("corridorChainOf", "NEW — proposal → oriented {points, corridors} (per-segment stamps)", True),
            ("handleRouteProposalClick", "selects the full chain; re-tap no-op compares whole chain either direction", True),
            ("corridorChainFor", "drop → oriented chain via chooseAnchorOrder on the chain endpoints", True),
            ("ghost/segment/end/start drop handlers", "thread the chain instead of a pair", True),
            ("removeRouteProposal", "removes ALL chain waypoints", True),
        ],
        "blocks": [
            "corridorChainOf(p, reversed) — reverse points + segment order; slice edge order left to the resolver",
            "handlers call the renamed chain ops; orientation from chooseAnchorOrder/Before on [first,last]",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · styles/", "the app-wide stylesheet (cards, pins, controls)"),
        "file": ("globals.css", "~2200 LOC — design tokens through component styles"),
        "outline": [
            ("proposal card rows", "gains the top-proposal badge styles", True),
        ],
        "blocks": [
            ".graph-proposal-row.is-top-proposal — bold label",
            ".graph-proposal-row-top(-square/-diamond) — 6px ink squares, diamond = rotate(45deg)",
        ],
    },
    "docs/three-layer-model.md": {
        "on": [],
        "module": ("Docs · docs/", "the three-layer voting model: graph / blocks / proposals"),
        "file": ("three-layer-model.md", "~380 LOC — model, pipeline, selection behavior"),
        "outline": [
            ("§3.2 clustering pipeline", "steps 2–5 rewritten: component prune, routing-consistent growth, ghost pins, budget-as-growth-limit", True),
            ("§3.3 selection behavior", "unchanged", False),
        ],
        "blocks": [
            "step 3 now documents growCorridor / makeSegmentShortestCheck / MAX_GHOST_WAYPOINTS and the URL-persistence rationale",
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
  .twocol {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 680px) {{ .twocol {{ grid-template-columns: 1fr; }} h1 {{ font-size: 27px; }} }}
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
    <div class="dateline">{DATE} · branch <code>main</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Three fixes to Top Proposals: a pin now takes real support (&gt;100 net votes, both point and route families); every modal badges the vote types that are CURRENT top proposals for what it shows (square = point, diamond = route fully inside the selection); and route corridors are now grown ROUTING-CONSISTENTLY — an extension either keeps the segment a shortest path or pins a ghost waypoint (max 3), and the whole waypoint chain lands in the URL, so a shared top-proposal link keeps routing into its corridor long after the proposal itself has churned away.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_report.py</code>.
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
