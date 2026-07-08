#!/usr/bin/env python3
"""Generate the forced-corridor waypoint-threading changelog report.

Run from repo root: python changelog/build_forced_corridor_report.py
Reads changelog/changes-forced-corridor.diff,
writes changelog/2026-07-08-forced-corridor-threading.html

Modeled on build_latency_opts_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-forced-corridor.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-forced-corridor-threading.html")

DATE = "2026-07-08"
TITLE = "Forced-corridor threading — drop any waypoint on a diamond, get an explicit flag"


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
        "id": "flag",
        "tag": "Selection model · forcedCorridor",
        "title": "1 · Forcing is now a first-class flag, not a coordinate coincidence",
        "symptom": (
            "“Forcibly routed through the top proposal” was implicit: any consecutive waypoint "
            "pair whose coords happened to float-equal a live RBTP's anchors routed through its corridor. "
            "That broke silently under proposal churn (live vote deltas recompute the proposal set), "
            "couldn't distinguish “user threaded this” from coincidence, and left no room for the "
            "endpoint-drop scenarios."
        ),
        "cause": [
            "The corridor resolver matched <code>(a, b)</code> against every live proposal's "
            "<code>anchorCoords</code> within 5&nbsp;m — no stored state anywhere.",
        ],
        "fixes": [
            "<code>SelWaypoint.forcedCorridor: {proposalId, edgeIds?}</code> — the segment LEAVING a "
            "waypoint is flagged as forced. <code>edgeIds</code> is a snapshot of the corridor's ordered "
            "path edges taken at threading time, so the forced geometry survives proposal churn; "
            "<code>proposalId</code> (the deterministic FNV id) is what the URL carries.",
            "Break rules live in the reducer, exhaustively unit-tested: moving either end of a flagged "
            "pair clears it (<code>updateAt</code>/<code>setStart</code>/<code>setEnd</code>), inserting a "
            "mid between the anchors clears it (<code>insertMid</code>), removing an anchor clears it "
            "(<code>removeAt</code>, and the multi-remove in <code>removeWaypointsNear</code>). An "
            "in-place replace (same coords, e.g. re-pinning a vote edge) keeps it.",
            "URL: <code>?w=lat,lng,f&lt;proposalId&gt;;…</code> — one compact marker per forced segment. "
            "History entries compare flags too, so back/forward restores forcing exactly.",
            "<code>insertWaypointAtSegment</code> skips the instant local geometry split when the split "
            "segment was forced — splitting the corridor polyline in place would silently KEEP the shape "
            "the user just asked to break; it does a full recalc instead.",
        ],
        "files": [
            "client-react/src/selection/types.ts", "client-react/src/selection/reducer.ts",
            "client-react/src/selection/serialize.ts", "client-react/src/context/RouteContext.tsx",
        ],
    },
    {
        "id": "endpoints",
        "tag": "MapView · endpoint drops",
        "title": "2 · Dropping START or END on a diamond threads the corridor (scenarios 1 & 3)",
        "symptom": (
            "Dragging an endpoint onto a route-based top proposal did nothing corridor-ish: the sticky "
            "snap glued the point to the diamond's midpoint, the kite hid behind the indicator "
            "(“the endpoint seemingly disappears”), and the route just re-ran OSRM to that spot."
        ),
        "cause": [
            "The start/end kites ended their drags with bare <code>setStartPoint</code>/"
            "<code>setEndPoint</code> (MapView) — no diamond hit-test; only mid/path drops were "
            "corridor-aware.",
        ],
        "fixes": [
            "Drop E on a diamond → <code>replaceEndWithPair</code>: route becomes S…A→Z with the near "
            "anchor joining as the last mid, the far anchor becoming the END proper, and the pair "
            "flagged. <code>chooseAnchorOrder</code> with a head-only chain picks SAZ vs SZA — the "
            "corridor's own length cancels, so it reduces to “is the last fixed point closer to A or "
            "Z” on the straight-line proxy every drop decision shares.",
            "Drop S on a diamond → <code>replaceStartWithPair</code>: AZ…E or ZA…E, one anchor becomes "
            "the START proper (kite + geocode), ordered by the new <code>chooseAnchorOrderBefore</code> "
            "(the head-side complement).",
            "Both ops are atomic: one selection change, one history entry, one recalc — mirroring "
            "removePoint's choreography (<code>handlingRemovalRef</code> suppresses the main effect's "
            "duplicate recalc). Re-dropping an already-threaded corridor in the same orientation is a "
            "no-op.",
            "The exploded-proposal drop path (<code>onProposalDrop</code>) routes its start/end branches "
            "through the same handlers, and the mid/path pair ops now stamp the flag too. Clicking a "
            "diamond funnels through the new atomic <code>selectCorridor</code> (replacing the "
            "clearPoints+setStart+setEnd dance).",
        ],
        "files": [
            "client-react/src/components/MapView/MapView.tsx",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/context/RouteContext.tsx",
        ],
    },
    {
        "id": "resolver",
        "tag": "GraphLayer · corridor resolver",
        "title": "3 · Flag-driven resolution: live proposal → snapshot → OSRM",
        "symptom": (
            "A deep link or history restore of a corridor selection rendered the OSRM path between the "
            "anchors — the coordinate match only worked while the proposal set happened to contain the "
            "same anchors, and nothing re-routed when proposals finished computing."
        ),
        "cause": [
            "Resolution keyed on live anchor coords only; proposals compute AFTER the first route calc "
            "on load, and nothing re-triggered the calc when they arrived.",
        ],
        "fixes": [
            "The resolver takes the flag: live proposal by id first (keeps the corridor current while it "
            "exists — deep links carry only the id), then <code>corridorFromEdgeIds</code> rebuilds the "
            "chain from the snapshot (start node inferred from the first two edges, polyline oriented "
            "a→b geometrically), else null → graceful OSRM fallback.",
            "<code>notifyCorridorsChanged</code>: RouteContext remembers when a FLAGGED segment failed "
            "to resolve; GraphLayer pokes it whenever the live proposal set changes, so a restored link "
            "recalcs exactly once and the corridor snaps in — verified live: a cold reload with only "
            "<code>f1c98f342</code> in the URL rendered the segment as the corridor's verbatim 70-point "
            "chain.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/context/RouteContext.tsx",
        ],
    },
]

VERIFY = [
    "Unit: 213/213 client tests green (31 reducer tests incl. every break rule, serialize round-trip "
    "with the f-marker, corridorFromEdgeIds orientation/broken-chain cases, chooseAnchorOrderBefore).",
    "Typecheck + full suite ALSO green on a clean HEAD-plus-only-these-ten-files worktree, so the "
    "commit set stands alone.",
    "Live on /m/nyc-walkways — scenario 1: dragged E onto the Broadway diamond → "
    "<code>S; A,f-feff65e3; Z</code>, corridor rendered verbatim (188-point chain), card showed the "
    "corridor's 96 blocks + the approach leg's ≈37.",
    "Live break rules: dragging Z away cleared the flag (OSRM reroute); dropping E back re-threaded "
    "identically (idempotent); a path-drag between the anchors inserted a mid AND un-forced the pair.",
    "Live scenario 3: dragged S onto a second diamond → start=anchor with <code>f1c98f342</code>, "
    "other anchor became the first mid, order chosen toward the next fixed point.",
    "Live scenario 2: path drag onto the street-lighting diamond threaded its pair with "
    "<code>f13111bf1</code> — TWO independent forced corridors coexisted, both verbatim (70-pt and "
    "21-pt chains, OSRM everywhere else).",
    "Live URL restore + history: cold reload with id-only markers re-resolved both corridors after "
    "proposals computed; back/forward stepped the flag states exactly.",
    "NOT verified live: dragging an EXISTING mid kite onto a diamond (replaceGhostWaypointWithPair) — "
    "synthetic pointer drags wouldn't grab that one kite; the op's branches are covered by the reducer "
    "tests and it shares corridorPairFor with the proven paths.",
    "Environmental (pre-existing, hit during testing): a lazy blocks re-bake mid-session can serve a "
    "pre-bake graph-votes body under the post-bake ETag — the browser then 304-pins the stale body and "
    "the client loops on “topology/vote mismatch” with an empty map. Unblocked by bumping "
    "<code>vote_rev:nyc-walkways</code> (21→22). Worth a server-side look separately.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>http://localhost:3000/m/nyc-walkways</a>, "
    "place a route S→E, then drag the END kite onto a diamond: the end should land on the far anchor "
    "(kite + address update), the near anchor should appear as a mid, and the segment between them "
    "should trace the proposal exactly (its diamond reads selected).",
    "Drag either anchor away — the route should recompute normally (no corridor); drag the end back "
    "onto the diamond — it should re-thread.",
    "Pull the corridor segment itself out to a street (insert a mid between the anchors) — the forced "
    "shape should break, not split in place.",
    "Drag the START kite onto a diamond — same behavior mirrored at the head of the route.",
    "Copy the URL while a corridor is threaded (look for the <code>,f&lt;id&gt;</code> token), open it "
    "in a fresh tab — the corridor should snap in once proposals load.",
    "Try the in-app back/forward arrows across a threading — forcing should restore with the step.",
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
    "client-react/src/selection/types.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Selection model · selection/", "the canonical ordered-waypoint selection — single source of truth for start/end/mids"),
        "file": ("types.ts", "~80 LOC — SelWaypoint/Selection shapes + selectionPhase"),
        "outline": [
            ("ForcedCorridor", "NEW — {proposalId, edgeIds?} annotating the segment leaving a waypoint", True),
            ("SelWaypoint", "gains forcedCorridor (URL carries coords + the marker; rest is runtime sugar)", True),
            ("Selection / EMPTY_SELECTION / selectionPhase", "unchanged", False),
        ],
        "blocks": [
            "ForcedCorridor interface — snapshot semantics + why only the id rides the URL",
            "SelWaypoint.forcedCorridor field",
        ],
    },
    "client-react/src/selection/reducer.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Selection model · selection/", "pure waypoint-array transitions — every selection mutation funnels through here"),
        "file": ("reducer.ts", "~170 LOC — setStart/setEnd/insertMid/updateAt/removeAt + the new flag rules"),
        "outline": [
            ("makeWaypoint / WaypointInit", "carries forcedCorridor; clearForcedAt helper", True),
            ("setStart", "moving the start breaks its departing corridor; in-place replace keeps it", True),
            ("setEnd", "moving the end breaks the corridor ARRIVING at it (flag on predecessor)", True),
            ("insertMid", "splitting a forced segment un-forces it", True),
            ("updateAt", "clears the flag on BOTH sides of the moved point", True),
            ("removeAt / setForcedCorridorAt", "predecessor clears on removal; NEW stamp helper (never the last waypoint)", True),
            ("clearWaypoints / setVoteType / fullIndexOf", "unchanged", False),
        ],
        "blocks": [
            "clearForcedAt — the one funnel every break rule goes through",
            "setStart — sameCoords guard preserves the flag on in-place replaces",
            "setEnd — clearForcedAt(wps, len-2) when the end actually moved",
            "insertMid — clearForcedAt(at-1) before the splice",
            "updateAt — own flag + predecessor's flag",
            "removeAt + setForcedCorridorAt (stamp/clear, guarded to indices with a successor)",
        ],
    },
    "client-react/src/selection/serialize.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Selection model · selection/", "Selection ⇄ URL (?w=…&vt=…) — the shareable form"),
        "file": ("serialize.ts", "~120 LOC — waypoint tokens, legacy slat/slng back-compat"),
        "outline": [
            ("token grammar", "lat,lng[,f<proposalId>] — NEW third field, validated by regex", True),
            ("selectionToParams", "appends ,f<id> for flagged waypoints", True),
            ("ParsedWaypoint / selectionFromParams", "waypoints now carry forcedProposalId (id only — no snapshot in URLs)", True),
            ("legacy slat/slng parsing", "unchanged semantics, new return shape", False),
        ],
        "blocks": [
            "FORCED_TOKEN regex + ParsedWaypoint",
            "parseWaypointToken — 2- or 3-field tokens; malformed third field drops the token",
            "selectionToParams — f-marker emission",
            "ParsedSelection.waypoints: LatLng[] → ParsedWaypoint[]",
        ],
    },
    "client-react/src/selection/reducer.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Selection model · selection/", "reducer unit tests"),
        "file": ("reducer.test.ts", "31 tests — +11 for the forced-corridor stamp + every break rule"),
        "outline": [
            ("existing transitions", "setStart/setEnd/insertMid/updateAt/removeAt", False),
            ("forced corridors (stamping + break rules)", "NEW describe block — 11 cases", True),
        ],
        "blocks": ["stamp guard (never last waypoint) · move-either-anchor · insert-between · insert-outside no-op · remove-either · setEnd in-place keep · setStart move/keep"],
    },
    "client-react/src/selection/serialize.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Selection model · selection/", "URL round-trip tests"),
        "file": ("serialize.test.ts", "12 tests — f-marker round-trip + ParsedWaypoint shape"),
        "outline": [
            ("selectionToParams", "+ f-marker emission + id-only round-trip", True),
            ("selectionFromParams", "expectations moved to ParsedWaypoint; malformed-marker rejection", True),
        ],
        "blocks": ["f-token round-trip (snapshot stays session-only)", "fdeadbeef third-field accept + ,extra reject"],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · route proposals", "pure RBTP logic: client-side clustering, corridor geometry, anchor ordering"),
        "file": ("routeProposals.ts", "~760 LOC — computeRouteProposals + corridor/anchor helpers"),
        "outline": [
            ("parse / marker shape / coverage / dedupe", "unchanged", False),
            ("corridorCoordinates", "unchanged — walks a proposal's ordered edges", False),
            ("corridorFromEdgeIds", "NEW — rebuild a corridor from a bare edge-id snapshot, oriented a→b", True),
            ("chooseAnchorOrder", "unchanged — head/tail chain comparison", False),
            ("chooseAnchorOrderBefore", "NEW — anchor order for a pair inserted BEFORE a point (start drop)", True),
            ("computeRouteProposals pipeline", "unchanged", False),
        ],
        "blocks": [
            "corridorFromEdgeIds — start-node inference (first edge's node the second doesn't touch), corridorCoordinates on a stub, geometric a→b orientation",
            "chooseAnchorOrderBefore — durationOf(b,next) <= durationOf(a,next) ? [a,b] : [b,a]",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · route proposals", "pure-logic tests"),
        "file": ("routeProposals.test.ts", "43 tests — +7 for the two new helpers"),
        "outline": [
            ("existing clustering/corridor tests", "unchanged", False),
            ("corridorFromEdgeIds", "NEW — orientation both ways, reversed first edge, single edge, broken/stale chains", True),
            ("chooseAnchorOrderBefore", "NEW — nearer-anchor-goes-second", True),
        ],
        "blocks": ["corridorFromEdgeIds describe (4 cases)", "chooseAnchorOrderBefore describe"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "topology + votes + proposal indicators; registers resolvers into RouteContext"),
        "file": ("GraphLayer.tsx", "~4000 LOC — this diff touches the corridor resolver + a poke effect (NOTE: the raw diff below also carries adjacent in-flight hunks from the parallel pin-heat workstream)"),
        "outline": [
            ("topology load / vote fetch / stale guards", "unchanged by this request", False),
            ("corridor resolver registration", "REWRITTEN — flag-driven: live proposal by id → edge-id snapshot → null", True),
            ("routeProposals state + refs", "NEW effect: poke notifyCorridorsChanged when the live set changes", True),
            ("indicators / hit tests / sticky snap", "unchanged by this request", False),
        ],
        "blocks": [
            "import corridorFromEdgeIds; useRoute() gains notifyCorridorsChanged",
            "setCorridorSegmentResolver((a, b, forced) => …) — live-by-id with geometric orientation, snapshot fallback",
            "effect on [routeProposals] → notifyCorridorsChanged() (deep-link corridors snap in)",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("MapView", "the map host — markers, drop handlers, click orchestration"),
        "file": ("MapView.tsx", "~900 LOC — this diff rewires every diamond-drop path through the flag"),
        "outline": [
            ("corridorOf / corridorPairFor", "pair + proposal (was pair only); stamps ride to RouteContext", True),
            ("handleGhostWaypointDragEnd / handleSegmentDrag", "pass the ForcedCorridor stamp", True),
            ("handleEndDragEnd / handleStartDragEnd", "NEW — endpoint drops hit-test diamonds and thread (scenarios 1 & 3)", True),
            ("onProposalDrop", "start/end branches → the corridor-aware handlers", True),
            ("handleRouteProposalClick", "→ atomic selectCorridor (station networks keep the point select)", True),
            ("kite markers", "start/end onDragEnd → the new handlers", True),
        ],
        "blocks": [
            "corridorOf — {proposalId: p.id, edgeIds: p.edgeIds}",
            "corridorPairFor — returns {pair, proposal}",
            "handleEndDragEnd — chooseAnchorOrder([prev]) → replaceEndWithPair",
            "handleStartDragEnd — chooseAnchorOrderBefore(next) → replaceStartWithPair",
            "onProposalDrop start/end branches; RouteMarker onDragEnd wiring",
            "handleRouteProposalClick → selectCorridor",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("RouteContext", "selection state + route/split calculation — the corridor-verbatim routing lives here"),
        "file": ("RouteContext.tsx", "~1700 LOC — resolver contract, pair ops, recalc choreography"),
        "outline": [
            ("CorridorSegmentResolver type", "(a, b, forced) — resolution is flag-driven now", True),
            ("selectionsEqual / URL seeding", "flags count as navigable state; deep links restore id-only flags", True),
            ("corridorSegmentFor", "reads waypoints[segmentIndex].forcedCorridor; tracks unresolved misses", True),
            ("insertWaypointAtSegment", "skips the local geometry split when the segment was forced", True),
            ("pair ops", "replaceGhostWaypointWithPair / insertWaypointPairAtSegment stamp the flag (all coincidence branches)", True),
            ("replaceEndWithPair / replaceStartWithPair / selectCorridor", "NEW atomic endpoint-threading ops", True),
            ("notifyCorridorsChanged", "NEW — recalc once when proposals arrive and a flagged segment was unresolved", True),
            ("removeWaypointsNear / clearSplitPaths", "clear flags whose pair broke", True),
            ("history / main effect / cast path", "unchanged", False),
        ],
        "blocks": [
            "ForcedCorridor import + CorridorSegmentResolver signature",
            "selectionsEqual — proposalId compared per waypoint",
            "URL seeding — forcedProposalId → {proposalId} (never on the last waypoint)",
            "unresolvedForcedRef + corridorSegmentFor rewrite",
            "insertWaypointAtSegment — wasForced skips the in-place split",
            "replaceGhostWaypointWithPair — leadIdx per coincidence branch + stamp",
            "insertWaypointPairAtSegment — same, incl. re-force when both anchors already ARE the segment",
            "replaceEndWithPair / replaceStartWithPair — atomic thread + geocode + explicit recalc",
            "selectCorridor — diamond click as ONE selection change",
            "notifyCorridorsChanged + removeWaypointsNear/clearSplitPaths flag hygiene",
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

  <p class="lede">Dropping a waypoint onto a route-based top proposal (a diamond) now threads the route
  through the proposal's whole corridor with an <strong>explicit forced-corridor flag</strong> in the
  canonical Selection — replacing the old implicit “the pair's coords happen to equal a live
  proposal's anchors” matching. All three drop scenarios work (end, start, and mid/path drops),
  every break rule from the spec un-forces the pair (drag either anchor away, insert a mid between
  them), the flag rides the URL as a compact <code>,f&lt;proposalId&gt;</code> token and the in-app
  history, and an edge-id snapshot keeps the forced geometry stable under live proposal churn.
  <em>Note:</em> the raw diffs for GraphLayer/MapView/RouteContext below also include adjacent
  uncommitted hunks from the parallel in-flight workstream in this worktree — the per-file
  “changed blocks” lists name exactly what THIS request changed.</p>

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
    Generated from <code>changelog/changes-forced-corridor.diff</code> by <code>changelog/build_forced_corridor_report.py</code>.
    Regenerate with <code>git diff HEAD -- client-react/src/selection client-react/src/components/GraphLayer/routeProposals.ts client-react/src/components/GraphLayer/routeProposals.test.ts client-react/src/components/GraphLayer/GraphLayer.tsx client-react/src/components/MapView/MapView.tsx client-react/src/context/RouteContext.tsx &gt; changelog/changes-forced-corridor.diff &amp;&amp; python changelog/build_forced_corridor_report.py</code>.
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
