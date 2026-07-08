#!/usr/bin/env python3
"""Generate the phantom path-hover / sticky RBTP-selection changelog report.

Run from repo root: python changelog/build_ghost_hover_report.py
Reads changelog/changes-ghost-hover.diff,
writes changelog/2026-07-08-ghost-hover-phantom.html

Modeled on build_forced_corridor_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-ghost-hover.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-ghost-hover-phantom.html")

DATE = "2026-07-08"
TITLE = "Phantom path-hover pockets + the Top Route Proposal that wouldn't let go"


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
        "id": "phantom",
        "tag": "CSS · desire-path hit area",
        "title": "1 · pointer-events: all hit-tested the path's invisible interior",
        "symptom": (
            "Hovering the map in certain regions — nowhere near the visible route — behaved as if the "
            "cursor were on the desire path: grab-hand cursor, the placement kite and the block hover "
            "card both suppressed, yet no path ghost pin either (a fully dead patch of map). The pockets "
            "moved around: the same street corner misbehaved at one zoom level and worked at the next. "
            "Threaded corridors (a selected route-based top proposal) made it dramatically worse."
        ),
        "cause": [
            "The invisible 20px-wide interactive twin of the desire path had "
            "<code>pointer-events: all !important</code>. On SVG, <code>all</code> makes the element a "
            "hit target over its <em>interior fill</em> as well as its stroke — even though a polyline "
            "paints no fill. The implicit fill region is the polygon you get by closing the path, so a "
            "zigzagging or looping route (exactly what a threaded corridor produces — the current one "
            "loops around Stuyvesant Town) turns every enclosed pocket into a phantom “on the path” zone.",
            "Inside a pocket, <code>mouseover</code>/<code>mousemove</code> fire on the interactive layer "
            "→ <code>isHoveringPath</code> goes true → SnapMarker + block hover card suppress — but "
            "<code>closestPointOnPath</code> correctly reports the cursor &gt;24px from the line, so no "
            "ghost pin renders. Every affordance disappears at once.",
            "Zoom dependence: Leaflet re-clips and re-simplifies the rendered path on each view change, "
            "so the closed shape — and therefore the phantom pocket — reshapes with zoom and pan. "
            "That's why the grab hand appeared at 3rd Ave &amp; E 24th at one zoom and not the next.",
        ],
        "fixes": [
            "<code>pointer-events: stroke !important</code> — hit-test the stroke only, regardless of its "
            "0.01 paint opacity (which is exactly what the fat transparent stroke needs), leaving the "
            "interior inert.",
            "Verified by DOM hit-map: a 12px-grid <code>elementsFromPoint</code> scan over the corridor "
            "area found 269 grid points hitting the path under <code>all</code> vs 179 under "
            "<code>stroke</code> — 90 phantom points forming a solid band through the loop's interior, "
            "all gone after the fix while on-line points still hit (grab cursor intact).",
        ],
        "files": ["client-react/src/styles/globals.css"],
    },
    {
        "id": "deselect",
        "tag": "GraphLayer · RBTP selection",
        "title": "2 · A mid dropped inside the corridor now deselects the tapped proposal",
        "symptom": (
            "After tapping a route-based top proposal (diamond) and then drag-inserting a ghost waypoint "
            "on the route, the proposal stayed marked selected — ✕ badge on the diamond, card still headed "
            "“Top Route Proposal · Add bike greenway” — even though the inserted mid had broken the "
            "corridor leg and many of the proposal's blocks were no longer selected."
        ),
        "cause": [
            "<code>selectedRbtpId</code> is never cleared explicitly; a tapped diamond reads deselected "
            "only when <code>anchorsAreWaypoints</code> fails. That test asked “are BOTH anchors still "
            "waypoints (within 5 m)?” — and inserting a mid <em>between</em> the anchors moves neither "
            "of them, so it kept passing.",
            "Meanwhile the selection reducer had already done the right thing: <code>insertMid → "
            "clearForcedAt</code> un-forces the corridor segment, the leg reroutes via OSRM, and the "
            "corridor's blocks drop out of the selection. UI badge and reality disagreed.",
        ],
        "fixes": [
            "<code>anchorsAreWaypoints</code> now builds the waypoint list in ROUTE order (start, mids…, "
            "end) and requires the two anchor matches to be <em>neighbors</em>. A mid inserted between "
            "the anchors breaks adjacency → the diamond and card header deselect; a mid added on an "
            "outer leg (before/after the corridor) leaves the corridor leg — and the selection — intact.",
            "This mirrors the reducer's own break rule: adjacency is exactly the condition under which "
            "the forced-corridor flag survives, so badge and routing can no longer drift apart.",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
]

VERIFY = [
    "Root-caused live on <code>/m/nyc-bikes</code> with the corridor deep link "
    "(<code>,f4a7e92fa</code>): DOM probe showed the interactive path computed "
    "<code>pointer-events: all</code>, and a 12px <code>elementsFromPoint</code> grid over the corridor "
    "loop found 90 phantom hit points (269 → 179 after switching the element to <code>stroke</code>).",
    "Post-fix probes: a point ON the line → <code>hitsPath: true</code>, cursor <code>grab</code>; the "
    "worst former phantom point → <code>hitsPath: false</code>, cursor <code>crosshair</code>.",
    "The earlier screenshot forensics match the mechanism: in the dead frame the map was pixel-identical "
    "to a healthy frame except ALL hover UI missing — no ghost pin, exactly what fill-hits produce "
    "(<code>isHoveringPath</code> true, <code>hoverLatLng</code> null).",
    "Deselection, live: tapped the greenway diamond (card header “Top Route Proposal”), then drag-inserted "
    "a mid on the path (real 120 ms hold + 72 px drift so the gesture reads as a drag, not a tap-restart). "
    "The URL gained the mid and dropped the <code>,f…</code> token (reducer un-forced the leg) and the "
    "card header fell to “Route Proposal · Selects 105 blocks” — deselected.",
    "<code>tsc --noEmit</code> clean.",
    "Incidental, worth separate looks: (1) a <code>TypeError: …reading '_leaflet_pos'</code> fires from "
    "GraphLayer's <code>containerPointToLayerPoint</code> during load; (2) during testing the Mode "
    "dropdown once flipped NYC Bikes → Walkways across an HMR reload.",
]

CHECKLIST = [
    "Open the corridor deep link (<code>/m/nyc-bikes?w=…,f4a7e92fa;…</code>) and sweep the cursor across "
    "the areas enclosed by the corridor loop (e.g. inside Stuy Town / the E 20s pockets): the block hover "
    "card + cyan kite should stay alive everywhere off the line; the grab hand should appear only ON the line.",
    "Zoom in and out over the same pocket — behavior should now be identical at every zoom.",
    "Hover the line itself: the white ghost kite should still appear and drag-to-insert should still work.",
    "Tap the greenway diamond (✕ appears, card reads “Top Route Proposal”), then drag the path to insert "
    "a mid between the anchors: the ✕ should vanish and the card header should drop to “Route Proposal”.",
    "Add a mid on a leg OUTSIDE the corridor (e.g. the Williamsburg approach): the proposal should STAY "
    "selected — only a mid between the anchors breaks it.",
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
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("Client styles · globals.css", "the app-wide stylesheet — design tokens, map panes, marker/card/cursor rules"),
        "file": ("globals.css", "~2300 LOC — this diff touches one rule in the Desire Path Interactive Layer block"),
        "outline": [
            ("tokens / reset / topbar / sidebar", "unchanged", False),
            ("map container + pane z-order", "unchanged", False),
            (".desire-path-interactive / .split-path-interactive", "pointer-events all → stroke (+ why-comment)", True),
            ("dragging-from-path cursor states", "unchanged", False),
            ("markers · vote-type indicators · proposal cards", "unchanged", False),
        ],
        "blocks": [
            ".desire-path-interactive/.split-path-interactive — pointer-events: stroke !important, with the SVG fill-hit explanation",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "topology + votes + proposal indicators; owns RBTP tap/selection state"),
        "file": ("GraphLayer.tsx", "~4100 LOC — this diff touches one callback: the tapped-RBTP staying-selected rule"),
        "outline": [
            ("topology load / vote fetch / heat canvas", "unchanged", False),
            ("selectedRbtpId state", "unchanged — still set on diamond tap / drop, never cleared directly", False),
            ("anchorsAreWaypoints", "REWRITTEN — route-ordered list + anchors must be NEIGHBORS", True),
            ("coveredRouteProposal / diamond selected ring / ✕ badge", "unchanged consumers — all inherit the stricter rule", False),
        ],
        "blocks": [
            "anchorsAreWaypoints — wps built as (start, mids…, end); findIndex per anchor; |i−j| === 1 required",
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

  <p class="lede">Two small fixes with one investigation behind them. The “ghost waypoint hover
  triggers far from the route” mystery turned out to be an SVG hit-testing rule:
  <code>pointer-events: all</code> on the desire path's invisible 20px interactive stroke also
  hit-tests the path's <strong>unpainted interior fill</strong>, so a zigzagging or looping route —
  which is exactly what threading a route-based top proposal's corridor produces — turned every
  pocket it enclosed into a dead zone where the hover UI vanished, reshaping with each zoom because
  Leaflet re-clips the path per view. And the tapped Top Route Proposal now actually deselects when
  a ghost waypoint splits its corridor: the staying-selected rule requires the two anchors to be
  <em>consecutive</em> waypoints, matching the reducer's existing break rule for the forced-corridor
  flag.</p>

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
    Generated from <code>changelog/changes-ghost-hover.diff</code> by <code>changelog/build_ghost_hover_report.py</code>.
    Regenerate with <code>git diff 86497fc^..09a1dd0 &gt; changelog/changes-ghost-hover.diff &amp;&amp;
    python changelog/build_ghost_hover_report.py</code>.
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
