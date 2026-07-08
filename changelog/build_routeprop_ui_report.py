#!/usr/bin/env python3
"""Generate the route-proposal UI fixes changelog report.

Run from repo root: python changelog/build_routeprop_ui_report.py
Reads changelog/changes-routeprop-ui.diff
(captured with: git diff 12475e6^ -- <the five files> > changelog/changes-routeprop-ui.diff),
writes changelog/2026-07-05-routeprop-ui-fixes.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-routeprop-ui.diff")
OUT_PATH = os.path.join(HERE, "2026-07-05-routeprop-ui-fixes.html")

DATE = "2026-07-05"
TITLE = "Route-proposal UI — diamond stacking, drag-through, and the route summary card"


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
        "id": "zorder",
        "tag": "Client · marker stacking",
        "title": "1 · Route diamonds now sit on top of every settled point pin",
        "symptom": (
            "A route-proposal diamond could hide UNDER point pins: a matched waypoint pin (band 200k) or "
            "the selected pin (100k) outranked an uncovered diamond (old band 5k) and even a covered one "
            "(old 150k) — so the corridor's marker vanished exactly when you interacted near it."
        ),
        "cause": [
            "Leaflet stacks markers by <code>zIndexOffset</code> bands. Point pins used "
            "1k&nbsp;(browse)&nbsp;/&nbsp;100k&nbsp;(selected)&nbsp;/&nbsp;200k&nbsp;(matched waypoint)&nbsp;/"
            "&nbsp;300k&nbsp;(fanned-out spread); route diamonds were slotted at 5k/150k — below most of "
            "that, so any interacting point pin buried them.",
        ],
        "fixes": [
            "Diamonds re-banded ABOVE all settled point pins: uncovered <code>300000&nbsp;+&nbsp;score</code>, "
            "covered <code>400000&nbsp;+&nbsp;score</code> (score capped at 48k so bands can't bleed).",
            "The fanned-out spread moved from 300k to <code>500000</code> — the exploded cluster is the "
            "explicit disambiguation gesture, so it alone stays above diamonds.",
            "The band table is documented once, at the point-icon <code>zIndexOffset</code>, and referenced "
            "from the diamond's.",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "dragthrough",
        "tag": "Client · drag gesture",
        "title": "2 · Dragging a start that sits on a proposal works again",
        "symptom": (
            "With a start/end/mid waypoint sitting on a point-based top proposal, click-dragging the pin did "
            "nothing useful — reproduced live: the press landed on the route-proposal DIAMOND stacked at the "
            "same spot, which swallowed the drag and (on a plain click) force-routed through the corridor "
            "anchors instead."
        ),
        "cause": [
            "A matched waypoint's square pin is deliberately <code>passthrough</code> (pointer-events:none) so "
            "the invisible kite RouteMarker underneath takes the drag. But the diamond between them is "
            "interactive, and its band (now even higher) outranks the kite's <code>zIndexOffset:&nbsp;1000</code> "
            "— so the press never reached the kite.",
            "The same interplay made a click on the visually-topmost square actually hit the diamond — "
            "verified with <code>document.elementsFromPoint</code> on the live stack.",
        ],
        "fixes": [
            "A diamond whose icon box (34×42, tip-anchored — same geometry as the pins) overlaps any current "
            "waypoint goes <code>passthrough</code> + <code>interactive:false</code>, exactly like a matched "
            "point pin: the kite gets the gesture. Pan can't invalidate the test (both projections shift "
            "equally); zoom re-runs it via the existing <code>currentZoom</code> dep.",
            "Two stuck-hover-card bugs found while reproducing: the matched-waypoint hover card was cleared "
            "only when the match still existed, so dragging the waypoint OFF its proposal stranded the card "
            "forever. Un-hover now clears unconditionally, and both drag start and drag finish drop the card "
            "(the drop fires a mouseover with the pre-drag match still in state).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/MapView/MapView.tsx",
        ],
    },
    {
        "id": "rbtpselect",
        "tag": "Client · RBTP selection + styling",
        "title": "4 · Tapping an RBTP now selects its diamond — and diamonds match PBTP styling",
        "symptom": (
            "Clicking a route-based top proposal (RBTP) diamond on /m/plum forced the route through its "
            "anchors but the diamond never flipped to its selected look, and the summary card had no "
            "“Route Proposal” header. Separately, diamonds rendered plain white while point-based top "
            "proposal (PBTP) squares carry the map-ramp heat tint."
        ),
        "cause": [
            "Selected-state relied solely on the block-coverage rule (<code>isRouteCovered</code>), but the "
            "A→B leg between the anchors is routed by OSRM, which need not re-trace the vote corridor — the "
            "doc's “uses the proposal's stored path verbatim” contract was never implemented. On maps "
            "without block artifacts (singleton blocks, like plum) coverage essentially never fires.",
            "The diamond icons were built without the <code>heat</code>/<code>heatColor</code> options the "
            "PBTP squares get, so they skipped the ramp tint entirely.",
        ],
        "fixes": [
            "Explicit-tap selection: tapping a diamond records <code>selectedRbtpId</code>; the RBTP reads "
            "selected for as long as BOTH its anchors remain waypoints of the current route "
            "(<code>anchorsAreWaypoints</code>, 5&nbsp;m match — the tap inserted those exact coords). "
            "Editing an anchor away or clearing the route deselects. Block coverage remains the other route "
            "to selected; the summary-card header follows the same rule.",
            "Heat parity: diamonds now get the same log-normalized ramp tint as squares (normalized within "
            "the RBTP family by score), so both marker kinds read as one visual system.",
            "Terminology documented everywhere: <strong>PBTP</strong> = point-based top proposal (square, "
            "one hot edge, <code>topProposals.ts</code>); <strong>RBTP</strong> = route-based top proposal "
            "(diamond, hot corridor, <code>routeProposals.ts</code>) — defined in "
            "docs/three-layer-model.md §&nbsp;Terminology, both module headers, and both GraphLayer marker "
            "sections. §3.3 rewritten to match reality (OSRM leg + explicit-tap rule; verbatim corridor "
            "routing noted as an open follow-up).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/topProposals.ts",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "routecard",
        "tag": "Client · route summary modal",
        "title": "3 · Selecting a route shows a blocks-summary card",
        "symptom": (
            "Selecting a route-based top proposal (or tracing any route) gave no summary at all — the "
            "pinned card only exists for a lone point, so the main voting object (the route) had no modal."
        ),
        "cause": [
            "The ProposalCard was only wired to the pinned (lone start) selection; nothing rendered for a "
            "full start+end selection.",
        ],
        "fixes": [
            "New route-summary card for every full route selection on a street map: anchored to the route's "
            "middle path edge (tracks pan/zoom like the pinned card), headed by the covered route proposal "
            "(eyebrow “Route Proposal”) when the selection covers one, meta line "
            "<code>selects N blocks</code> (<code>segments</code> on maps without block artifacts, where "
            "blocks fall back to one-per-edge), and block-grain vote rows summed over the whole selection "
            "(<code>selectionVoteRows</code>; per-edge sums as the no-blocks fallback).",
            "Its ± buttons cast on the WHOLE selection through the same unified <code>castVotes</code> path "
            "(same edge set + block materialization) as the top-bar route cast, so pressed/unvote semantics "
            "match wherever the vote is made from.",
            "Coverage (the diamond's selected state AND the card header) is now matched with direction twins "
            "included — new <code>expandSelectionToUndirected</code> in routeProposals.ts (+3 unit tests): a "
            "routed path often traverses the twin of the edge a corridor's block recorded, so raw edge-id "
            "intersection under-reported coverage.",
            "Kept legible: hover cards are suppressed for non-winner edges of the selected route (the card "
            "already speaks for them), a diamond click drops any hover card at the click point, and the card "
            "renders in a new <code>is-elevated</code> z tier (1300) above the transient hover card (1200) — "
            "portals mount at different times, so DOM order can't keep the summary visible.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.test.ts",
            "client-react/src/styles/globals.css",
        ],
    },
]

VERIFY = [
    "Round 2 (RBTP selection + styling), verified live on /m/plum: before the fix a diamond click "
    "left the diamond unselected and the card headerless; after, the diamond flips to the selected "
    "double-ring, carries the same lavender ramp tint as the squares (zoomed screenshots), and the card "
    "is headed “ROUTE PROPOSAL · Improve bike lane”.",
    "Reproduced the drag bug live before fixing (localhost, <code>/m/plum</code>): a click on the "
    "square-with-diamond stack hit the DIAMOND (<code>document.elementsFromPoint</code> showed it topmost at "
    "z&nbsp;5842 over the square's 1460 and every kite's ~1400–1600) and force-routed through the corridor.",
    "After the fix: dragged the start onto that same stack (sticky-snap matched it to the proposal), then "
    "dragged it away — the start MOVED (URL waypoint updated), no corridor re-selection, no map pan.",
    "Diamond click on <code>/m/plum</code> now selects the corridor AND opens the summary card — "
    "“selects 383 segments · Improve bike lane −0/163/+163” — with the card on top of hover cards "
    "(z tiers verified in the DOM).",
    "Visual: the diamond paints above the browse square at the same anchor (zoomed screenshots before/after).",
    "<code>npx tsc --noEmit</code> clean; client suite green — 189/189 (3 new tests for "
    "<code>expandSelectionToUndirected</code>).",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/plum'>http://localhost:3000/m/plum</a> and find the icon cluster "
    "(Anza Vista area): the DIAMOND should read fully on top of the point squares.",
    "Click the diamond: the corridor routes through its anchors and a summary card opens mid-route — "
    "“selects N segments” with a votable Improve-bike-lane row. Press + and confirm the row count bumps and "
    "the heat lights the corridor.",
    "Click a point square to make it the start, set an end, then drag the start pin somewhere else: the pin "
    "must move (no corridor jump, no map pan), and no proposal card may be left stranded after the drop.",
    "Hover along the selected route: no per-segment hover cards over the summary card; hovering a top-proposal "
    "pin on the route still shows that proposal's card.",
    "On a map WITH block artifacts (NYC once votes exist), the meta line should say “blocks”, not “segments”, "
    "and tracing every block of a corridor should title the card “Route Proposal”.",
    "Click the RBTP diamond: it should flip to the selected double-ring immediately and stay selected while "
    "both anchors remain waypoints; drag one anchor elsewhere and it should deselect.",
    "Compare a diamond and a square side by side: same border, glyph, and heat tint treatment.",
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
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: heatmap canvas, top-proposal pins, route diamonds, hover + pinned cards"),
        "file": ("GraphLayer.tsx", "~3.9k LOC — topology load, winners, indicators, selection resolve, all proposal cards"),
        "outline": [
            ("IndicatorMarker", "marker wrapper (interactive flag, unmount hover release)", False),
            ("Waypoint→proposal matching", "startEdgeIdx/endEdgeIdx/midEdgeSet + passthrough rules", False),
            ("Pinned selection resolve", "override → node upgrade → sticky → lock", False),
            ("Route-card position effect", "NEW — anchors the summary card to the route's middle edge", True),
            ("Hover / pinned card content", "block-grain rows; NEW route-edge hover suppression", True),
            ("Route-summary content memos", "NEW — routeBlocks · routeVoteRows · coveredRouteProposal", True),
            ("castProposalVote / castRouteVote", "unified castVotes paths; route cast is NEW", True),
            ("indicatorMarkers", "point pins; spread band 300k → 500k", True),
            ("routeIndicatorMarkers", "diamonds; NEW top bands + waypoint-overlap passthrough", True),
            ("Render portals", "pinned card · hover card · NEW route-summary card (elevated)", True),
            ("ProposalCard", "NEW eyebrow / metaText / elevated props", True),
        ],
        "blocks": [
            "point-pin zIndexOffset — band table rewritten: spread 500k, diamonds 300k/400k documented",
            "routeIndicatorMarkers — waypointPts + overlapsWaypoint (34×42 icon-box test)",
            "diamond icon — passthrough class when overlapping a waypoint; interactive={!passthrough}",
            "diamond zIndexOffset — (covered ? 400000 : 300000) + min(48000, score)",
            "diamond onClick — clears the hover card at the click point before selecting",
            "route-card position effect — middle path edge midpoint, move/zoom rAF throttle",
            "routeBlocks / routeVoteRows / coveredRouteProposal memos — block-grain summary content",
            "hoverOnSelectedRoute — twin-aware suppression of non-winner hover cards on the route",
            "castRouteVote — castVotes over ALL path edges + materialized blocks",
            "route-summary portal — eyebrow 'Route Proposal', 'selects N blocks/segments', elevated",
            "ProposalCard — eyebrow/metaText/elevated props wired through header, meta line, className",
            "selectedRbtpId + anchorsAreWaypoints — explicit-tap RBTP selection (anchors-still-waypoints)",
            "diamond heat — same log-normalized ramp tint as PBTP squares (score-normalized)",
            "PBTP/RBTP terminology headers on indicatorMarkers + routeIndicatorMarkers",
        ],
    },
    "client-react/src/components/GraphLayer/topProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure PBTP selection logic (winners → dedupe → spacing → cap)"),
        "file": ("topProposals.ts", "~280 LOC — which edges get a square Top Proposal pin"),
        "outline": [
            ("Header", "NEW PBTP/RBTP terminology note", True),
            ("Selection pipeline", "computeVoteTypeWinners → … → applyTopProposalLimit", False),
        ],
        "blocks": ["header — PBTP defined, RBTP cross-referenced (docs §3.1 Terminology)"],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("docs", "the source-of-truth spec for the graph/blocks/route-proposals separation"),
        "file": ("three-layer-model.md", "layer definitions + block-scoped vote semantics"),
        "outline": [
            ("Layer diagram", "unchanged", False),
            ("Terminology callout", "NEW — PBTP / RBTP definitions + where each lives", True),
            ("§3.3 Selection behavior", "REWRITTEN — coverage (twin-expanded) OR explicit tap; OSRM-leg reality", True),
            ("§4 Vote semantics", "unchanged", False),
        ],
        "blocks": [
            "Terminology — PBTP (square, one hot edge) / RBTP (diamond, hot corridor), shared icon system",
            "§3.3 — selected = coverage OR tapped-with-anchors-still-waypoints; verbatim corridor routing = open follow-up",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapView", "the map host: waypoint markers, tap routing, drag wiring, GraphLayer props"),
        "file": ("MapView.tsx", "~750 LOC — owns start/end/mids state and every marker's drag/tap/hover handlers"),
        "outline": [
            ("Marker drag wrappers", "dragStart/dragFinish; NEW hover-card clears", True),
            ("Tap / indicator handlers", "restart-from-proposal, cluster exploder", False),
            ("GraphLayer wiring", "waypointMatch, hoverProposalPoint, drops", False),
            ("Ghost/start/end RouteMarkers", "hidden-when-matched kites; NEW unconditional un-hover clear", True),
        ],
        "blocks": [
            "handleMarkerDragStart — also clears the matched-waypoint hover card",
            "handleMarkerDragFinish — clears it again (drop fires mouseover with the PRE-drag match)",
            "mid/start/end onHoverChange — un-hover clears UNCONDITIONALLY (guarded clear stranded cards)",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure route-proposal logic (no React/Leaflet): parsing, coverage, dedupe, clustering"),
        "file": ("routeProposals.ts", "~620 LOC — client-side deterministic corridor extraction + coverage rules"),
        "outline": [
            ("Wire parsing", "parseRouteProposal(s)", False),
            ("Shape / highlight / coverage", "isRouteCovered; NEW expandSelectionToUndirected", True),
            ("Point dedupe · anchor order", "dropPointsCoveredByRoutes · chooseAnchorOrder", False),
            ("Deterministic clustering", "computeRouteProposals pipeline", False),
        ],
        "blocks": [
            "expandSelectionToUndirected — joins direction twins (shared undirected node pair) into the selection set",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "unit tests for the pure route-proposal logic"),
        "file": ("routeProposals.test.ts", "coverage/dedupe/clustering specs"),
        "outline": [
            ("isRouteCovered specs", "auto-select rule", False),
            ("expandSelectionToUndirected specs", "NEW — twins join, strangers don't, bounds respected", True),
        ],
        "blocks": [
            "3 new tests: twin joins selection · twin-traversing route reads covered · out-of-range candidates ignored",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · styles", "theme-invariant global styles: layout, z tiers, cards, indicators"),
        "file": ("globals.css", "~2k LOC — includes the proposal-card z-index tiers"),
        "outline": [
            ("Proposal card tiers", "1100 pinned · 1200 hover · NEW 1300 is-elevated", True),
        ],
        "blocks": [
            ".graph-indicator-modal.is-elevated — z-index 1300, above the transient hover card",
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
          <pre class="diff">{colorize_diff(chunk, name)}</pre>
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
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Three interaction fixes around the new route proposals, all reproduced and verified live
  on <code>/m/plum</code>: route diamonds now stack above every settled point pin; a diamond overlapping a
  waypoint goes click-through so the pin underneath is draggable again (the reported “can't drag a start on
  a top proposal” bug — the diamond was eating the press); and selecting a route — via a diamond or by
  tracing one — opens a summary card of the blocks the route selects, with block-grain vote rows and ±
  buttons that cast on the whole selection. Plus two stuck-hover-card fixes found while reproducing.
  <br><br><em>Note: the diff below spans the same files as the parallel debug-instrumentation workstream
  (commit <code>12475e6</code>); a handful of <code>dlog(...)</code> lines in it belong to that work.</em></p>

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
