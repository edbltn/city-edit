#!/usr/bin/env python3
"""Generate the RBTP-parity changelog report (2026-07-07).

Run from repo root: python changelog/build_rbtp_parity_report.py
Reads changelog/changes-rbtp-parity.diff
(captured with: git diff -- <the seven files> > changelog/changes-rbtp-parity.diff),
writes changelog/2026-07-07-rbtp-parity.html

Modeled on build_routeprop_ui_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-rbtp-parity.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-rbtp-parity.html")

DATE = "2026-07-07"
TITLE = "RBTP parity — mixed cluster fan-out, corridor drops, people-not-blocks vote counts, and a route ✕"


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
        "id": "explode",
        "tag": "Client · cluster fan-out",
        "title": "1 · Route diamonds explode out with point-pin clusters",
        "symptom": (
            "A route-proposal diamond stacked on (or near) point pins never joined the crowded-cluster "
            "fan-out: tapping the stack exploded only the squares, and a diamond sitting on top could be "
            "picked blind — or bury the squares entirely."
        ),
        "cause": [
            "The spread machinery (<code>clusterAround</code> / <code>spreadCluster</code> / the exploder) was "
            "built over <code>placed</code> — the PBTP winners only. The spread map was keyed by EDGE index, "
            "which a route proposal (id-keyed) can't participate in.",
            "The diamond's own click never ran cluster detection at all — squares explode-before-selecting in "
            "<code>handleClick</code>; diamonds went straight to corridor selection.",
        ],
        "fixes": [
            "Spread keys generalized to strings that carry the kind — <code>spreadKeyEdge(e)</code> / "
            "<code>spreadKeyRoute(id)</code> — and the cluster list (<code>clusterables</code>) is now squares "
            "PLUS diamonds at their shared display positions, so a mixed stack fans out as ONE grid.",
            "Diamonds honor their fanned-out cell: position override, top z band (500k, same as fanned "
            "squares), tail-less symmetric-diamond shape (the same drop-the-locating-tip rule squares get via "
            "<code>square</code>), never passthrough while fanned, and hovering a fanned diamond pauses the "
            "snap-back timer exactly like a fanned square (<code>armSpreadTimer</code> hoisted for both memos).",
            "Diamond clicks run the same explode-before-select gate squares use (via a new internal exploder "
            "ref shared across the two marker memos); picking a fanned diamond collapses the spread — unlike "
            "a point pin it anchors no modal at its grid cell.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/voteTypeIcon.ts",
        ],
    },
    {
        "id": "uniquevotes",
        "tag": "Server + client · route card counts",
        "title": "2 · Route-card votes count people, not blocks",
        "symptom": (
            "The route-summary card's vote rows double-counted: one person's route cast fans a vote onto "
            "every edge of every block the route covers, so summing per-block counts along a 10-block "
            "corridor showed their single vote as 10."
        ),
        "cause": [
            "The client only holds aggregated per-edge/per-block tallies — it cannot know that the +1 on "
            "block 3 and the +1 on block 7 are the same device. Only the canonical DB (one row per "
            "map·edge·vote-type·device) can dedupe across a selection.",
        ],
        "fixes": [
            "New <code>POST /api/route-votes</code> — body <code>{{ map, edge_ids }}</code> — counting "
            "<code>COUNT(DISTINCT device_id)</code> per (vote type, direction) across the whole edge set "
            "(<code>count_unique_voters_for_edges</code> in database.py). POST because a selection's "
            "block-edge union routinely exceeds URL limits; capped at 20k edges server-side.",
            "Verified against the local DB: over the 912 nyc-walkways edges carrying “Add bike lane” rows "
            "(one device alone holds 904 per-edge rows), the endpoint returns <code>up: 2, down: 1</code> — "
            "distinct devices, not row sums.",
            "The route card fetches these rows (debounced 350 ms, capped at 4k edges from the selection's "
            "block union) whenever the selection or any vote signal changes, and shows them the moment they "
            "arrive; the local block-grain sums remain the stand-in until then (and on DB-less dev setups). "
            "The ± buttons' pressed state is untouched (still block coverage of MY votes).",
        ],
        "files": [
            "server/app.py",
            "server/database.py",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "selectcap",
        "tag": "Client · copy",
        "title": "3 · “Selects N blocks” capitalized",
        "symptom": "The route card's meta line read “selects 5 blocks” — lowercase sentence start.",
        "cause": ["Literal template string."],
        "fixes": ["<code>Selects N block(s)</code> / <code>Selects N segment(s)</code>."],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "routex",
        "tag": "Client · route card",
        "title": "4 · ✕ on the route card deselects the whole route",
        "symptom": (
            "There was no one-tap way out of a route selection — the pinned (lone point) card has a ✕, but "
            "the route-summary card offered only minimize."
        ),
        "cause": [
            "The route card never passed <code>onRemove</code>, and GraphLayer had no “clear the whole "
            "route” callback (only <code>onRemoveSelected</code> → clearStart, the lone-point remove).",
        ],
        "fixes": [
            "New <code>onClearRoute</code> prop threaded MapView → GraphLayer → the route card's "
            "<code>onRemove</code>; it calls <code>clearPoints()</code>, dropping start, end, mids, splits, "
            "and the rendered route in one tap. Verified live: the ✕ empties the Start/End bar and the URL.",
            "The ✕'s aria label is now configurable (<code>removeLabel</code>) — “Deselect this route” here, "
            "“Remove this point” (the default) on the pinned card.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/MapView/MapView.tsx",
        ],
    },
    {
        "id": "corridordrop",
        "tag": "Client · corridor drop + removal",
        "title": "5 · Dropping a waypoint on a diamond routes through the whole corridor — with its own [×]",
        "symptom": (
            "Dragging a ghost (mid) waypoint onto a route-based top proposal just moved the mid to that "
            "point — the route ignored the corridor. And once a corridor WAS part of the route (via a "
            "diamond tap) there was no [×] to pull it back out, unlike matched point proposals."
        ),
        "cause": [
            "Only PBTP squares were sticky drop targets (<code>proposalIconAt</code>); nothing resolved a "
            "drop against the diamonds, and no waypoint operation could insert (or atomically remove) a "
            "PAIR of waypoints.",
        ],
        "fixes": [
            "Diamonds are drop targets everywhere a waypoint can land: a new "
            "<code>routeProposalIconAt</code> pixel hit-test (same icon-box geometry, spread-aware) backs a "
            "diamond sticky snap, a drop-target ring on the hovered diamond, and a published "
            "<code>routeProposalAtRef</code> resolver for the host — resolving a drop also SELECTS the RBTP, "
            "exactly like its click.",
            "On drop, MapView threads the route through the WHOLE corridor: the two anchors join the "
            "sequence between the drop's neighbors in whichever order gives the shorter chain — "
            "<code>prev → A → corridor → B → next</code> vs <code>prev → B → corridor → A → next</code> "
            "(<code>chooseAnchorOrder</code> with the local prev/next; the corridor's own length is equal "
            "both ways, so the approach + departure legs decide). Covered drops: dragging an existing mid "
            "(<code>replaceGhostWaypointWithPair</code> — the mid BECOMES the corridor), dragging the path "
            "out to a new mid (<code>insertWaypointPairAtSegment</code> via the shared "
            "<code>handleSegmentDrag</code>), and drags out of an exploded proposal "
            "(<code>onProposalDrop</code> reuses both). Anchors that coincide with a neighboring waypoint "
            "are skipped, not doubled — a zero-length segment breaks the recalc.",
            "Mirroring the matched point pin's [×]: a selected RBTP diamond (its anchors are current "
            "waypoints, whether inserted by tap or drop) carries the same corner badge — "
            "<code>data-x-route</code>, resolved by the one delegated capture handler — and removal pulls "
            "BOTH anchors out atomically via the new <code>removeWaypointsNear</code> (one selection change "
            "+ one recalc, mirroring <code>removePoint</code>'s choreography; two separate removals would "
            "shift indices between recalcs). The badge hugs the diamond's top-right edge (new CSS offset — "
            "the square's corner is empty air on a diamond).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/voteTypeIcon.ts",
            "client-react/src/components/MapView/MapView.tsx",
            "client-react/src/context/RouteContext.tsx",
            "client-react/src/styles/globals.css",
        ],
    },
    {
        "id": "tapdrift",
        "tag": "Round 2 · gesture",
        "title": "6 · A slow click is a click — drags now require actual movement",
        "symptom": (
            "Clicking a route-based top proposal kept ADDING ghost waypoints instead of selecting it: a "
            "press held past 300 ms counted as a drag even with zero movement, and the path's drag handler "
            "then dropped a mid where pressed — which the new sticky diamond snap turned into corridor "
            "threading, over and over."
        ),
        "cause": [
            "The shared tap-vs-drag rule (<code>utils/gesture.ts</code>) was TIME-only by design; "
            "usePathDrag documented the consequence — “a long press that never moved still reads as a drag "
            "and drops a mid where pressed” — which was tolerable before proposal pins became sticky drop "
            "targets, and corridor-threading ones at that.",
        ],
        "fixes": [
            "New <code>TAP_MAX_DRIFT_PX</code> (8 px): a press that never strayed past it is a TAP no matter "
            "how long it was held. Drift is the MAX displacement during the gesture, not the net — pulling a "
            "mid out and dropping it back on its proposal (the “upgrade” gesture) still reads as a drag. "
            "usePathDrag tracks the drift on both its mouse and touch start paths.",
            "Taps that land inside an RBTP diamond's icon box now SELECT that proposal (MapView.handleTap "
            "consults the diamond resolver before falling through to the bare map click) — the tap only "
            "reaches the map when the diamond couldn't take its own click (passthrough over a waypoint, or "
            "the press was captured by the path), and in those cases the user was still aiming at the "
            "proposal.",
            "Re-tapping an already-selected diamond is a no-op: handleRouteProposalClick bails when both "
            "anchors are already waypoints (5 m match), so repeated clicks can't insert duplicate mids.",
        ],
        "files": [
            "client-react/src/utils/gesture.ts",
            "client-react/src/hooks/usePathDrag.ts",
            "client-react/src/components/MapView/MapView.tsx",
        ],
    },
    {
        "id": "verbatim",
        "tag": "Round 2 · corridor routing",
        "title": "7 · A selected RBTP physically routes through its corridor — verbatim",
        "symptom": (
            "Selecting a route-based top proposal (by tap or by dropping a waypoint on it) drew a route that "
            "didn't match the heatmap highlight or the hover: the anchors were inserted as waypoints but the "
            "leg between them was routed by OSRM, which almost never re-traces the vote corridor."
        ),
        "cause": [
            "The proposal's stored path (<code>edgeIds</code>) was only used for display/coverage; the actual "
            "route between the anchors came from <code>/api/routes</code>. The docs called routing the stored "
            "path verbatim an open follow-up.",
        ],
        "fixes": [
            "New pure helper <code>routeProposals.corridorCoordinates</code> (4 unit tests): walks the "
            "ordered path edges from <code>anchors[0]</code> into a GeoJSON [lng, lat] chain, handling edges "
            "stored in either direction and bailing (→ OSRM fallback) if the chain breaks on stale topology.",
            "GraphLayer registers an anchors→corridor resolver with RouteContext "
            "(<code>setCorridorSegmentResolver</code>, same registration pattern as the block materializer): "
            "a waypoint pair within 5 m of a proposal's two anchors — either order — resolves to the "
            "corridor's geometry (oriented a→b) + its path edge ids.",
            "EVERY segment calculation consults it: <code>calculateAllSegments.fetchSegment</code> returns "
            "the corridor segment locally (no OSRM request) for an anchor pair, and all four direct "
            "start→end call sites (main effect, removePoint, removeWaypointsNear, history stepTo) funnel "
            "through a new <code>routeDirect</code> that represents a corridor-direct selection as a single "
            "split segment. The segment's <code>edgeIds</code> are the corridor's own path edges, so the "
            "heat/hover highlight, the block coverage (the diamond's selected ring + the card's “Route "
            "Proposal” header now fire via REAL coverage, not just the tapped-id rule), and the vote target "
            "all trace exactly what's selected.",
            "The diamond click also inserts its anchors through the atomic pair op now "
            "(<code>insertWaypointPairAtSegment</code>) — the old two sequential single inserts could take "
            "the local-geometry fast path and split the stale OSRM route without ever consulting the "
            "corridor resolver.",
            "Verified live on /m/nyc-walkways: one diamond tap → start “Greenwich Avenue & West 13th”, end "
            "“West 58th Street & 7th Avenue”, and the drawn route traces the corridor street end-to-end, "
            "coinciding with the block highlight; card headed “ROUTE PROPOSAL · Improve sidewalk”, "
            "“Selects 94 blocks”.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.test.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/context/RouteContext.tsx",
            "client-react/src/components/MapView/MapView.tsx",
        ],
    },
]

VERIFY = [
    "<code>npx tsc -b --force</code>: no errors in any touched file (the only project errors are "
    "pre-existing in MapLibreBackground.tsx, untouched here — note that bare <code>tsc --noEmit</code> "
    "checks NOTHING against this solution-style tsconfig, which let a missing context wiring slip to "
    "runtime once before being caught live and fixed). Client suite green — 195/195 (4 new "
    "corridorCoordinates tests).",
    "<code>POST /api/route-votes</code> verified against the local DB: 912 “Add bike lane” edges "
    "(one device holds 904 of the rows) → <code>{\"rows\":[{\"label\":\"Add bike lane\",\"up\":2,\"down\":1}]}</code> "
    "— distinct devices, not per-edge sums. Empty edge sets → <code>{\"rows\":[]}</code>.",
    "Live on <code>/m/nyc-walkways</code>: the square+diamond stack near Union Square fans out into a "
    "two-cell grid (square left, diamond right — screenshots), with the fanned diamond drawn tail-less "
    "and on top.",
    "Live: diamond tap → corridor route + route card headed “ROUTE PROPOSAL · Add bike lane”, meta "
    "“Selects 5 blocks” (capital S), unique-voter row −1 / 0 / +1 matching the DB, ✕ in the header, and "
    "the [×] badge on the selected diamond's top-right edge (zoomed screenshot).",
    "Live: the route card ✕ cleared the whole selection (Start/End bar and URL emptied) — request 4.",
    "Round 2, live on <code>/m/nyc-walkways</code> (re-baked 237,881-block topology): one diamond tap "
    "selected the corridor and the drawn route traced the corridor street end-to-end (Greenwich Ave & W "
    "13th → W 58th & 7th Ave), coinciding with the block highlight — card “ROUTE PROPOSAL · Improve "
    "sidewalk · Selects 94 blocks” with distinct-people rows.",
    "NOT verified interactively: the drag-a-mid-onto-a-diamond drop, the diamond [×] tap, and the "
    "slow-click-stays-a-click gesture — the Chrome automation's synthetic events don't reliably reach "
    "Leaflet markers / can't hold an unmoved press, so those are on the manual checklist.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>/m/nyc-walkways</a>, find the square+diamond "
    "stack near Union Square (E 17th St): one tap should fan BOTH out into a grid; tapping the fanned "
    "diamond should select the corridor.",
    "With a route selected, drag a mid waypoint onto a diamond: the route should re-thread through the "
    "corridor's two endpoints (two new mids at the corridor ends replace the dragged one), picking the "
    "shorter approach order.",
    "Drag the route path itself onto a diamond (pull out a fresh ghost mid and drop it there): same "
    "corridor threading.",
    "The selected diamond should show a small [×] on its upper-right edge; tapping it must remove BOTH "
    "corridor anchors from the route in one step (route re-heals between the remaining waypoints).",
    "Open the route card: meta reads “Selects N blocks”, the ✕ deselects the entire route, and the vote "
    "rows should briefly show local sums then settle to distinct-people counts (cast a route vote from "
    "two devices on overlapping corridors to see the dedup).",
    "Watch the network tab for <code>POST /api/route-votes</code>: one debounced request per selection "
    "change / vote, none while no route is selected.",
    "Round 2 — press a diamond (or the path) slowly WITHOUT moving and release: it must SELECT / tap, "
    "never drop a mid or thread a corridor; then re-click the selected diamond: no duplicate waypoints.",
    "Round 2 — select any RBTP (tap or drop): the drawn route, the heat/hover highlight, and the card "
    "must all trace the SAME corridor; drag one anchor off it and the leg should fall back to OSRM "
    "routing.",
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
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: heatmap canvas, top-proposal pins, route diamonds, hover + pinned + route cards"),
        "file": ("GraphLayer.tsx", "~4.1k LOC — topology load, winners, indicators, selection resolve, all proposal cards"),
        "outline": [
            ("Module helpers", "NEW rbtpDisplayPos + spreadKeyEdge/spreadKeyRoute", True),
            ("Props interface", "NEW onClearRoute · onRouteProposalRemove · routeProposalAtRef", True),
            ("[×] capture handler", "NEW data-x-route branch (corridor removal) beside data-x-edge", True),
            ("Spread state", "keys generalized number → string; NEW internal exploder ref", True),
            ("proposalIconAt / sticky snap", "NEW routeProposalIconAt + diamond sticky + dropTargetRbtpId", True),
            ("Route-summary content memos", "routeBlocks · routeVoteRows · NEW routeUniqueRows fetch", True),
            ("armSpreadTimer", "hoisted next to collapseSpread — shared by both marker memos", True),
            ("indicatorMarkers", "cluster list now squares + diamonds (clusterables)", True),
            ("routeIndicatorMarkers", "spread override, [×] badge, drop ring, explode-on-click", True),
            ("Render portals + ProposalCard", "route card: Selects…, unique rows, onRemove, removeLabel", True),
        ],
        "blocks": [
            "rbtpDisplayPos — ONE definition of where a diamond 'is' (marker, cluster, drop hit-test)",
            "spreadKeyEdge/spreadKeyRoute — string spread keys carrying the proposal kind",
            "ROUTE_VOTES_EDGE_CAP / ROUTE_VOTES_DEBOUNCE_MS — route-votes fetch bounds",
            "capture handler — data-x-route → onRouteProposalRemove(proposal)",
            "routeProposalIconAt + routeProposalAtRef — diamond drop resolver (selects on hit)",
            "stickyProposalSnap — diamonds sticky after squares; drag preview skips edge ring for diamonds",
            "routeUniqueRows effect — debounced POST /api/route-votes over the block-edge union",
            "clusterables/clusterAround/spreadCluster/explodeClusterAt — mixed-kind fan-out",
            "routeIndicatorMarkers — override pos, square:!!override, removeRoute badge, 500k band, timer pause",
            "route card — metaText 'Selects …', rows routeUniqueRows ?? routeVoteRows, onRemove=onClearRoute",
        ],
    },
    "client-react/src/components/GraphLayer/voteTypeIcon.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the divIcon builder for proposal pins (square/diamond, tint, heat, badges)"),
        "file": ("voteTypeIcon.ts", "~150 LOC — one atomic SVG pin + optional [×] badge"),
        "outline": [
            ("Options", "NEW removeRoute (data-x-route badge)", True),
            ("Shape paths", "NEW symmetric (tail-less) diamond for fanned-out state", True),
            ("Badge markup", "removeEdge → data-x-edge; NEW removeRoute → data-x-route", True),
        ],
        "blocks": [
            "DIAMOND_SQUARE_PATH / INNER_DIAMOND_SQUARE_PATH — fanned diamond drops its locating tip",
            "removeBadge — data-x-route variant for corridor removal",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapView", "the map host: waypoint markers, tap routing, drag wiring, GraphLayer props"),
        "file": ("MapView.tsx", "~830 LOC — owns start/end/mids state and every marker's drag/tap/hover handlers"),
        "outline": [
            ("chainDurationOf", "NEW module-level equirectangular chain metric", True),
            ("routeProposalAtRef", "NEW — GraphLayer's drop-on-diamond resolver", True),
            ("corridorPairFor + handlers", "NEW ghost-drag-end / segment-drag corridor threading", True),
            ("removeRouteProposal", "NEW — [×] badge → removeWaypointsNear(anchors)", True),
            ("onProposalDrop", "mid/new-mid branches now corridor-aware", True),
            ("GraphLayer + path layers wiring", "onClearRoute, onRouteProposalRemove, handleSegmentDrag", True),
        ],
        "blocks": [
            "chainDurationOf — hoisted from handleRouteProposalClick (shared with corridor drops)",
            "corridorPairFor — chooseAnchorOrder([prev, next], A, B): the two-option rule",
            "handleGhostWaypointDragEnd — mid onto diamond → replaceGhostWaypointWithPair",
            "handleSegmentDrag — path drag onto diamond → insertWaypointPairAtSegment",
            "removeRouteProposal — both anchors out in one step",
            "GraphLayer props — routeProposalAtRef · onRouteProposalRemove · onClearRoute={clearPoints}",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · context", "the canonical ordered Selection + route/split recalculation"),
        "file": ("RouteContext.tsx", "~1.4k LOC — selection reducer plumbing, casts, history"),
        "outline": [
            ("Context interface", "NEW replaceGhostWaypointWithPair · insertWaypointPairAtSegment · removeWaypointsNear", True),
            ("Corridor pair ops", "NEW — atomic two-waypoint insert/replace + one recalc", True),
            ("removeWaypointsNear", "NEW — multi-remove mirroring removePoint's choreography", True),
            ("Provider value", "three new entries", True),
        ],
        "blocks": [
            "replaceGhostWaypointWithPair — selUpdateAt + selInsertMid; neighbor-coincident anchors skipped",
            "insertWaypointPairAtSegment — two selInsertMid into one segment, one runSplitCalc",
            "removeWaypointsNear — 5 m match, single applySelection + recalc, endpoint-promotion geocode",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · styles", "theme-invariant global styles: layout, z tiers, cards, indicators"),
        "file": ("globals.css", "~2k LOC — includes the proposal-pin badge geometry"),
        "outline": [
            ("[×] badge position", "NEW diamond offset — top-right EDGE midpoint (24,10)", True),
        ],
        "blocks": [
            ".vote-type-indicator.is-diamond .vote-type-indicator-x — left 24px / top 10px",
        ],
    },
    "client-react/src/utils/gesture.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils", "the single tap-vs-drag convention every press differentiator shares"),
        "file": ("gesture.ts", "~40 LOC — TAP_MAX_MS + the isTap rule"),
        "outline": [
            ("TAP_MAX_MS", "unchanged (300 ms)", False),
            ("TAP_MAX_DRIFT_PX", "NEW — an unmoved hold is a tap; drift is MAX, not net", True),
            ("isTap", "now takes optional maxDriftPx", True),
        ],
        "blocks": ["TAP_MAX_DRIFT_PX (8) + isTap(pressStartMs, maxDriftPx)"],
    },
    "client-react/src/hooks/usePathDrag.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · hooks", "dragging the route polyline to pull out a mid (tap = restart)"),
        "file": ("usePathDrag.ts", "~340 LOC — press/ghost/trail lifecycle for mouse + touch"),
        "outline": [
            ("Press bookkeeping", "NEW pressPos + maxDriftSq refs (both start paths)", True),
            ("handleGlobalMove", "NEW drift tracking per move", True),
            ("handleGlobalEnd", "tap if quick OR unmoved; drag only if it traveled", True),
        ],
        "blocks": [
            "pressPosRef/maxDriftSqRef — reset on mouse handleStart AND the touchstart path",
            "release — isTap(pressStart, sqrt(maxDriftSq)) replaces the time-only rule",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure route-proposal logic: parsing, coverage, clustering, corridor geometry"),
        "file": ("routeProposals.ts", "~700 LOC — deterministic corridor extraction + coverage rules"),
        "outline": [
            ("Coverage / anchor order", "isRouteCovered · chooseAnchorOrder", False),
            ("corridorCoordinates", "NEW — ordered edgeIds → [lng,lat] chain from anchors[0]", True),
            ("Deterministic clustering", "computeRouteProposals pipeline", False),
        ],
        "blocks": [
            "corridorCoordinates — walks edges in either stored direction; null on a broken chain (OSRM fallback)",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for the pure route-proposal logic"),
        "file": ("routeProposals.test.ts", "coverage/clustering/corridor specs"),
        "outline": [
            ("corridorCoordinates specs", "NEW — chain, reversed edges, broken chain, stale topology", True),
        ],
        "blocks": ["4 new tests for corridorCoordinates"],
    },
    "server/app.py": {
        "on": ["Flask API"],
        "module": ("Flask API", "routes: votes, graph data, maps, geocode, admin"),
        "file": ("app.py", "~1.8k LOC — every HTTP endpoint + startup warm/lock plumbing"),
        "outline": [
            ("/api/my-votes", "unchanged neighbor", False),
            ("/api/route-votes", "NEW — distinct-voter rows for a route selection", True),
            ("Graph data APIs", "unchanged", False),
        ],
        "blocks": [
            "ROUTE_VOTES_EDGE_CAP — 20k edge-id bound per request",
            "route_votes() — POST, _locked gate, labels via vote_store.resolve_vote_type, net-desc sort",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API · persistence", "canonical Postgres vote rows (one per map·edge·type·device)"),
        "file": ("database.py", "~1k LOC — schema, vote CRUD, migration helpers"),
        "outline": [
            ("Voter reads", "get_voter_edge_directions · get_voter_type_rows", False),
            ("count_unique_voters_for_edges", "NEW — COUNT(DISTINCT device_id) per (type, direction)", True),
        ],
        "blocks": [
            "count_unique_voters_for_edges — one GROUP BY over edge_id = ANY(edge_ids)",
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

  <p class="lede">Five requests bringing route-based top proposals (RBTPs, the corridor diamonds) to full
  parity with the point pins, and making the route card honest and closable: diamonds now fan out with
  crowded point-pin clusters (and explode-before-select on their own clicks); dropping any waypoint — an
  existing mid, a fresh path-drag ghost, or an exploded-proposal drag — onto a diamond threads the route
  through the WHOLE corridor (choosing the shorter of the two anchor orders) and gives the diamond the same
  [×] removal the matched point pins have; the route card's vote rows now count distinct people via a new
  <code>POST /api/route-votes</code> (one route cast used to count once per block); its meta line is
  capitalized; and a new ✕ deselects the entire route in one tap.
  <br><br>Round 2 (same day): a slow unmoved press is a TAP again — time-only gesture detection was turning
  careful clicks on diamonds into zero-distance drag-and-drops that threaded corridors repeatedly — and a
  selected RBTP now routes through its corridor VERBATIM (the proposal's own stored path and edges, no OSRM
  leg), so the drawn route, the heat/hover highlight, the coverage-based selected ring, and the vote target
  finally all agree.
  <br><br><em>Note: the GraphLayer/MapView/globals.css diffs below also carry the still-uncommitted 07-05
  route-proposal work (already reported in
  <a href="2026-07-05-routeprop-ui-fixes.html">routeprop-ui-fixes</a>) and a few in-flight edits from a
  parallel session (e.g. <code>adjShortest</code>); each section's “changed blocks” map points at the hunks
  that belong to THIS workstream.</em></p>

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
    Generated from <code>changelog/changes-rbtp-parity.diff</code> by <code>changelog/build_rbtp_parity_report.py</code>.
    Regenerate after further edits with
    <code>git diff -- &lt;files&gt; &gt; changelog/changes-rbtp-parity.diff &amp;&amp; python changelog/build_rbtp_parity_report.py</code>.
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
