#!/usr/bin/env python3
"""Generate the node/edge targeting-parity changelog report.

Run from repo root: python changelog/build_node_parity_report.py
Reads changelog/changes-node-parity.diff,
writes changelog/2026-07-08-node-edge-parity.html

Modeled on build_ghost_hover_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-node-parity.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-node-edge-parity.html")

DATE = "2026-07-08"
TITLE = "Node/edge parity — the ends of every segment belong to their nodes"


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
        "id": "parity",
        "tag": "GraphLayer · hit-test",
        "title": "1 · Why a radius rule can never make nodes selectable — and what can",
        "symptom": (
            "The bar: nodes must be as easy to select as edges — 20 random clicks on "
            "/m/test-central-park should pin roughly 50/50 nodes vs edges. The 8px absolute-priority "
            "node disc failed it both ways at once: at street zooms random clicks were overwhelmingly "
            "edges (the uncapped nearest-edge fallback owned everything outside the discs), while over "
            "the park's curvy paths at z15 everything was a node (geometry nodes sit every 4–16px on "
            "screen, so the discs pave the whole line)."
        ),
        "cause": [
            "A pixel radius is structurally incapable of parity. Every node IS an endpoint of some "
            "edge, so distance-to-nearest-node ≥ distance-to-nearest-edge for every cursor position — "
            "any “pick the closer one” rule degenerates to all-edges, and any absolute-priority disc "
            "is a zoom-dependent patch: too small and edges win everything, bigger than the on-screen "
            "edge length and nodes win everything.",
            "The catchment areas are the thing to split, not the distances. The only scale-invariant "
            "boundary between an edge and its endpoints is the projection parameter t along the edge "
            "itself.",
        ],
        "fixes": [
            "<code>hitTest</code> rebuilt: the nearest edge decides the hit, then t hands the outer "
            "<code>NODE_END_SHARE</code> (0.25) of the segment to that end's node — nodes own half of "
            "every edge's catchment area at every zoom, plus the clamped region beyond the endpoints. "
            "Degenerate self-edges (e-bike stations) are never split.",
            "<code>hitTest</code> grew a <code>maxEdgeDistPx</code> cap (Infinity = always resolve) "
            "and subsumed <code>findNearestEdgeIndex</code> — <code>resolveSelection</code> is now one "
            "uncapped call per constraint level instead of a radius pass plus a fallback pass. The "
            "always-resolve mode falls back to the nearest eligible NODE when a block has no member "
            "edges, so capture-only node cells stay selectable from anywhere in their polygon.",
            "The node index left the hot path entirely: the mousemove-rate hit-test no longer scans "
            "nodes at all (the t-split reads the two endpoints of the already-found edge).",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "hoverclick",
        "tag": "GraphLayer · hover = click",
        "title": "2 · One resolver for hover, snap, preview, trail — and two divergences it killed",
        "symptom": (
            "Requirement two: what hover shows must be EXACTLY what a click pins. The acceptance runs "
            "surfaced two places where they disagreed: far from the graph (open water / park interior) "
            "hover highlighted a component but a click placed a free waypoint and pinned nothing; and "
            "inside a top proposal's sticky snap annulus hover showed the underlying graph node while "
            "a click linked the route to the proposal (the ,f corridor token) — again pinning nothing."
        ),
        "cause": [
            "Idle hover used <code>resolveSelection</code> (always resolves, by contract — the "
            "point-vote and pinned-override paths need that), but the click's snap path is deliberately "
            "radius-bounded off-polygon so waypoint drags stay free far from the graph. Two resolvers, "
            "two answers.",
            "The sticky proposal snap ran only inside the registered snapFn and the drag drop-preview — "
            "idle hover never consulted it, so the annulus between the icon's DOM box and its snap "
            "radius telegraphed the wrong action.",
        ],
        "fixes": [
            "New shared <code>resolveDragSnap</code> — over a block polygon it IS the hover resolver "
            "(full hierarchy, always resolves); off-polygon it is the radius-bounded hitTest. Used "
            "verbatim by idle hover, the registered waypoint snapFn, the drag drop-preview, and the "
            "live-trail snap, so all four agree by construction.",
            "Idle hover now checks the sticky proposal first and rings the proposal's edge — exactly "
            "what the drag drop-preview already did — and shows NOTHING far from the graph, matching "
            "the free-waypoint click there.",
            "<code>cityedit.resolveAt</code> now also reports <code>hoverTarget</code> (the gated "
            "resolver) and <code>sticky</code>/<code>stickyEdgeIdx</code> alongside the always-resolve "
            "<code>target</code>, so harnesses measure what the cursor actually sees.",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "hijack",
        "tag": "GraphLayer · pinned effect",
        "title": "3 · The pinned effect's transforms hijacked interactive clicks (Eric's junction)",
        "symptom": (
            "Field-caught after the parity work shipped: hovering the 72nd Street Transverse &amp; East "
            "Drive junction showed the node (“No votes yet”), but clicking it pinned “East Drive &amp; "
            "72nd Street Transverse · 1 proposal · Improve sidewalk” and highlighted the neighbouring "
            "street's whole block corridor in white. The resolver had carried the hovered node "
            "correctly — something AFTER it swapped the selection."
        ),
        "cause": [
            "The pinned effect applies post-resolution transforms meant for deep links: proposal "
            "reconciliation (a target that isn't a winner edge re-snaps to the nearest winner MIDPOINT "
            "within 8 m — built for shared URLs that name a proposal only by its midpoint coords) and "
            "the node→strongest-proposal-edge upgrade. The junction node sat within 8 m of the East "
            "Drive proposal's midpoint, so reconciliation replaced it.",
            "They fired on interactive clicks because a plain map click stores the RAW click latlng "
            "(the snap path only records drags), so nothing marked the pin as user-made.",
            "The acceptance harness was blind to all of it: <code>debugState(\"pinnedTarget\")</code> "
            "recorded the PRE-transform resolution, so the probe said “node” while the UI showed the "
            "hijacked proposal. The 20/20 pass was real for the resolver layer only.",
        ],
        "fixes": [
            "Interactive-pin detection: a new map <code>click</code> listener records the raw click "
            "coords verbatim; a pin at exactly those coords is a live click (<code>clickMatch</code>), "
            "alongside the existing drag-carried <code>snapMatch</code>. Link-derived pins (URL parse "
            "→ different doubles) can never match.",
            "Both transforms now run for link-derived pins ONLY. An interactive pin resolves through "
            "the SAME gated resolver hover uses — including “nothing far from the graph”, so a click "
            "on open parkland places a free waypoint and pins no card, exactly what hover showed.",
            "Sticky reselection near an open card is now touch-only for interactive pins: with a "
            "cursor, hover already showed the new target, so keeping the old one would contradict it.",
            "<code>debugState(\"pinnedTarget\")</code> moved to AFTER all transforms — the probe now "
            "reports what the card and highlight actually show, so no harness can be fooled the same "
            "way again.",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
]

VERIFY = [
    "Acceptance run (headless Chrome-for-Testing 138 + puppeteer-core, per-trial page reload so no "
    "waypoint state leaks): 20 random viewport clicks on <code>/m/test-central-park</code> → "
    "<strong>10 nodes / 10 edges, 20/20 pinned target == hover target</strong>.",
    "3/3 far-from-graph points: hover shows nothing, click pins nothing (asserted, not skipped).",
    "28/30 exact-on-junction hovers resolve to that junction node; the 2 exceptions are "
    "cross-block-boundary re-derivations (the sampled point and the node's own coordinate sit in "
    "different block polygons) — the documented reason selection carries its target instead of "
    "re-resolving stored coordinates. Hover=click is unaffected.",
    "Deterministic replay of the one run-3 miss proved the sticky-annulus divergence: the click URL "
    "gained a <code>,f73b5cd68</code> forced-corridor token — a proposal link, not a failed pin.",
    "Chrome 148 headless is frame-dead on this machine (rAF never ticks → MapLibre never fires load); "
    "the harness pins Chrome 138. In-tab synthetic clicks in a hidden window are untrustworthy "
    "(intensive timer throttling froze commits and produced 15 bogus \"mismatches\" — all with the "
    "pin frozen at one stale value).",
    "<code>tsc --noEmit</code> clean.",
    "Follow-up (52a567b), after Eric field-caught the junction hijack: his exact flow replayed "
    "headless (z18, <code>vt=Improve+sidewalk</code>, click the 72nd St Transverse &amp; East Drive "
    "junction) — hover node 15956 → pin node 15956; his slat/slng deep link still reconciles to "
    "proposal edge 37544; 20-click acceptance re-run with the now-honest post-transform probe: "
    "20/20 hover=pin, 3/3 in-bounds far-from-graph clicks pin nothing. Pooled node/edge split across "
    "honest runs: 32/28.",
]

CHECKLIST = [
    "On <code>/m/test-central-park</code>, click ~20 random spots at a few zooms: pins should feel "
    "about half nodes (circle card) / half edges (segment card), and each pin should be exactly what "
    "the hover highlight showed at that spot.",
    "Hover along one street segment end to end: the middle half should ring the edge, the outer "
    "quarters should ring the endpoint junctions — at any zoom.",
    "Hover open water / deep park interior far from any path: NO hover highlight or tooltip; a click "
    "there places a free start waypoint and opens no card.",
    "Hover just OUTSIDE a top-proposal icon (within its snap ring): the proposal's edge should light "
    "up — and a click should link/thread to that proposal, not pin a node.",
    "On <code>/m/e-bikes-3</code>, hover and click stations: still selectable exactly as before "
    "(self-edges are never handed to their nodes).",
    "Drag a waypoint across a block: the live trail, the drop-preview ring, and where the waypoint "
    "actually lands on release should all agree.",
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
        "module": ("GraphLayer", "topology + votes + hover/selection resolution; owns the hit-test every pointer interaction flows through"),
        "file": ("GraphLayer.tsx", "~4200 LOC — this diff rebuilds the hit-test core and unifies the four pointer resolvers"),
        "outline": [
            ("constants (SNAP_EDGE_PX · NODE_END_SHARE)", "SNAP_NODE_PX deleted; NODE_END_SHARE=0.25 parity split + why-comment", True),
            ("findNearestEdgeIndex", "DELETED — subsumed by hitTest's maxEdgeDistPx=Infinity mode", True),
            ("hitTest", "REBUILT — nearest edge + t-split hands outer quarters to endpoint nodes; node scan only on the no-edge cold path", True),
            ("blockFiltersAt / adjShortestInBlock / projectOntoEdge", "unchanged", False),
            ("resolveSelection", "block-constrained + unrestricted paths each ONE uncapped hitTest call", True),
            ("resolveAt debug probe", "now also reports hoverTarget + sticky/stickyEdgeIdx", True),
            ("resolveDragSnap (NEW) + snapFn registration", "one shared gated resolver; snapFn shrinks to sticky → resolveDragSnap → record", True),
            ("drop-preview effect / live-trail snap", "both now call resolveDragSnap instead of raw hitTest", True),
            ("idle hover (mousemove handler)", "sticky proposal first, then resolveDragSnap — no hover far from graph", True),
            ("pinned effect / tooltips / indicators / vote casting", "unchanged consumers", False),
        ],
        "blocks": [
            "NODE_END_SHARE constant — the parity argument (why a radius can't work) lives here",
            "hitTest — maxEdgeDistPx param; pixel-space t; self-edge guard; nearest-node cold path",
            "resolveSelection — two uncapped calls, fallback passes deleted",
            "resolveAt probe — hoverTarget + sticky fields",
            "resolveDragSnap + resolveDragSnapRef — the shared gated resolver",
            "setSnapFn — sticky → resolveDragSnap → lastSnapSelectionRef",
            "drop-preview effect — resolveDragSnapRef",
            "mousemove: suppressed-hover proposal path + idle hover — sticky-first, gated",
            "live-trail snap — resolveDragSnapRef",
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · commits <code>b47a197</code> + <code>52a567b</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Nodes are now genuinely as easy to select as edges — not by a bigger radius (a
  radius structurally cannot do it: every node is an edge endpoint, so the nearest edge is always
  at least as close as the nearest node) but by splitting each edge's <em>catchment area</em>: the
  nearest edge decides the hit, and the outer quarter at each end belongs to that end's node. The
  split rides the projection parameter, so it holds at every zoom. Alongside it, hover and click
  now share one gated resolver end to end — sticky proposal, block-constrained hierarchy over a
  polygon, radius-bounded off it, nothing far from the graph — which closed the last two
  hover≠click gaps the acceptance harness surfaced. Final run: 20 random clicks →
  <strong>10 nodes / 10 edges, 20/20 pins identical to their hover</strong>.</p>

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
    Generated from <code>changelog/changes-node-parity.diff</code> by <code>changelog/build_node_parity_report.py</code>.
    Regenerate with <code>git diff b47a197^..52a567b -- client-react &gt; changelog/changes-node-parity.diff &amp;&amp;
    python changelog/build_node_parity_report.py</code>.
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
