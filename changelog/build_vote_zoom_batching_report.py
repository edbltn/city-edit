#!/usr/bin/env python3
"""Generate the vote/zoom batching + exploded-RBTP changelog report.

Run from repo root: python changelog/build_vote_zoom_batching_report.py
Reads changelog/changes-vote-zoom-batching.diff
(captured with: git diff -- <the five files> > changelog/changes-vote-zoom-batching.diff),
writes changelog/2026-07-09-vote-zoom-batching.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-vote-zoom-batching.diff")
OUT_PATH = os.path.join(HERE, "2026-07-09-vote-zoom-batching.html")

DATE = "2026-07-09"
TITLE = "Instant voting, storm-free zoom, live exploded diamonds, longer corridors"


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
        "id": "votepath",
        "tag": "Client · vote path",
        "title": "1 · Voting is instant — top-proposal recomputation batched off the cast",
        "symptom": (
            "Casting a single vote on the NYC bikes map froze the app: ~430 ms of immediate main-thread "
            "blocking (three long tasks) followed by a 1.9–2.2 s freeze ~800 ms later. Measured live with a "
            "PerformanceObserver on <code>/m/nyc-bikes</code> (3.3 M edges, ~183 k voted)."
        ),
        "cause": [
            "Every cast ran <code>selectTopProposals</code> — a full O(nEdges) scan of the vote table — "
            "SYNCHRONOUSLY, once per transition group (a cast often emits 2+ <code>optimistic-vote</code> "
            "events: clears then casts).",
            "The route-based clustering (<code>computeRouteProposals</code>) was only 800 ms-debounced, and "
            "took <strong>2153 ms</strong> on this graph: per vote type it re-scanned all 3.3 M edge slots "
            "twice (<code>netsForType</code> + <code>buildTypeAdj</code>) and rebuilt the edge→block index "
            "from scratch on every call.",
        ],
        "fixes": [
            "Votes no longer touch the proposals at all: a cast/delta applies the vote arrays, repaints the "
            "heatmap (<code>refreshHeatmapDisplay</code>) and just marks a dirty flag. A minute-cadence sweep "
            "(<code>PROPOSALS_REFRESH_INTERVAL_MS</code> = 60 s, flushed immediately when a hidden tab comes "
            "back) recomputes both proposal families in idle time. Escape hatch: a delta that GROWS the "
            "legend refreshes promptly (winners were ranked against a shorter legend).",
            "The clustering itself became a <strong>sliced job</strong> (<code>createRouteProposalJob</code>): "
            "one vote type per <code>requestIdleCallback</code> slice, yielding to input between slices — "
            "byte-identical output to the old single pass (same iteration orders, same determinism contract).",
            "Sparse nets: one pass over the vote table builds per-type <code>Map&lt;edgeId, net&gt;</code>s "
            "(<code>netsByType</code>) instead of a fresh 3.3 M-slot <code>Int32Array</code> scan per type; "
            "types with no net-positive edges are skipped outright.",
            "The prebuilt block index GraphLayer already holds is passed in (<code>opts.blockIndex</code>) "
            "instead of being rebuilt (30–90 ms) on every recompute.",
            "PBTP selection (<code>selectTopProposals</code>, ~80 ms) moved into the same batched sweep — "
            "the only two places proposals recompute are initial load / refetch / mode switch (prompt, idle) "
            "and the dirty sweep.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.perf.test.ts",
            "docs/three-layer-model.md",
        ],
    },
    {
        "id": "zoomstorm",
        "tag": "Client · MapLibre block heat",
        "title": "2 · Zoom no longer re-applies the whole block heat per tile",
        "symptom": (
            "Zooming in/out on the bikes map was very slow and looked like “the whole bike path map reloads”. "
            "Instrumented live: ONE wheel zoom-out+in fired 94 <code>sourcedata</code> events → 94 full heat "
            "re-applies → <strong>4,388,108 <code>setFeatureState</code> calls</strong> (94 × ~46.7 k lit "
            "blocks), ~1.9 s of accumulated long tasks plus the matching GPU re-uploads."
        ),
        "cause": [
            "<code>MapLibreBackground</code> bound its full heat apply to EVERY <code>sourcedata</code> event "
            "— which fires per tile load during zoom/pan — and each apply did a wholesale "
            "<code>removeFeatureState</code> + <code>setFeatureState</code> for every lit block (46,677 on "
            "nyc-bikes).",
            "The same full rewrite ran on every vote broadcast, adding ~50–80 ms × 2 per cast to the vote "
            "path above.",
        ],
        "fixes": [
            "Feature-state lives on the SOURCE, not the tiles — once written it survives every tile load. "
            "The <code>sourcedata</code> listener now only retries until the FIRST successful apply (for "
            "votes that arrive before the source exists), then goes quiet: re-verified live — 45 sourcedata "
            "events during a zoom, 0 re-applies.",
            "Vote-driven updates are DIFFED against the last applied state: only blocks whose heat actually "
            "changed are written (a cast writes ~3–100, not 46,677). A full rewrite happens only when the "
            "log-normalization ceiling moves (rare) — and re-applies the selection state the wholesale clear "
            "drops.",
            "The heat therefore stays LIVE (no 10-minute staleness needed): after the diff rework a vote's "
            "block update costs a few writes, so batching it would only add lag.",
            "Diamond icons are cached per label+heat-bucket like the squares, so the per-zoom marker-memo "
            "rebuild no longer tears down and recreates every diamond's DOM element (<code>setIcon</code>) "
            "on every zoom step.",
        ],
        "files": [
            "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
    {
        "id": "deadpins",
        "tag": "Client · exploded RBTP pins",
        "title": "3 · Exploded route diamonds are hoverable + clickable again",
        "symptom": (
            "Fanning out a crowded pin stack that contained 2+ route-based (diamond) top proposals left the "
            "diamonds dead — no hover, no click — while the point squares in the same fan-out worked. "
            "Reproduced live: diamonds stacked under a route waypoint carried "
            "<code>leaflet-interactive: false</code> forever, even after the passthrough class was removed."
        ),
        "cause": [
            "react-leaflet applies <code>interactive</code> only when a Marker is CREATED — its updater "
            "handles position/icon/zIndex/opacity and nothing else. A diamond whose settled spot overlapped "
            "a waypoint mounted with <code>interactive:false</code> (the deliberate “hand the press to the "
            "kite” rule) and could never be revived: Leaflet never registered its event targets, so the "
            "fan-out's non-passthrough icon looked normal but heard nothing.",
            "Corridors stack exactly where routes are (their anchors ARE waypoints once selected — and "
            "short corridors put their display midpoint nearly on the anchors), so ≥2-diamond stacks near a "
            "waypoint were precisely the mount-dead case — matching “only when more than one route-based "
            "icon explodes”.",
        ],
        "fixes": [
            "Markers are now ALWAYS created interactive; the icon's <code>passthrough</code> class "
            "(<code>pointer-events:none</code>), which swaps with the icon on every render, is the single "
            "gate. Behaviorally identical for the waypoint-overlap case (events still fall through to the "
            "kite), but a state change can now actually flip a pin live.",
            "Verified live on a 3-diamond stack under the start waypoint: mounted "
            "<code>interactive:1 / passthrough:1</code>, exploded to <code>passthrough:0</code>, and the "
            "fanned diamond's click selected its corridor "
            "(<code>[proposals] diamond click a829b450 override=true passthrough=false</code>).",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "longer",
        "tag": "Client · corridor length",
        "title": "4 · Route proposals earn noticeably longer corridors",
        "symptom": (
            "The corridor clustering identified routes that read too short — a well-supported avenue "
            "corridor was trimmed to a handful of blocks."
        ),
        "cause": [
            "The support-earned length budget was tuned conservatively: floor 600 m, +150 m per √score, "
            "hard ceiling 2500 m.",
        ],
        "fixes": [
            "Budget raised ~50%: floor <code>ROUTE_LENGTH_BASE_M</code> 600 → <strong>900 m</strong>, growth "
            "<code>ROUTE_LENGTH_PER_SQRT_SCORE_M</code> 150 → <strong>220 m</strong>/√score, ceiling "
            "<code>ROUTE_LENGTH_MAX_M</code> 2500 → <strong>3500 m</strong>. The best-supported-window trim "
            "(<code>capPathToLengthBudget</code>) is unchanged — corridors still snap to their hottest "
            "stretch, they just may keep more of it.",
            "Observed live: the top Broadway corridor now runs W 4th → 57th St (~3.3 km, “selects 98 "
            "blocks”) instead of being clipped at 2.5 km.",
        ],
        "files": ["client-react/src/components/GraphLayer/routeProposals.ts"],
    },
]

VERIFY = [
    "Baselines measured live on <code>/m/nyc-bikes</code> (3.3 M edges, ~183 k voted edges, 46,677 lit "
    "blocks) BEFORE the changes: vote = long tasks [169, 98, 160] + 1902 ms deferred clustering; "
    "recompute = 2153 ms; one wheel zoom = 94 sourcedata events × 46,677 = 4.39 M "
    "<code>setFeatureState</code> calls.",
    "AFTER, same page: a cast produces <strong>zero long tasks</strong>; its block-heat update is "
    "<code>heat apply (diff): 3 writes</code>; a zoom produces <strong>0 heat re-applies</strong> across "
    "45 sourcedata events.",
    "Headless Chrome (unthrottled): the batched sweep recomputes as idle slices of "
    "<strong>[87, 145, 284, 135, 149] ms</strong> — worst single block 284 ms (was one 1.9–2.2 s task).",
    "Node perf harness against the real graph (<code>PERF=1 …routeProposals.perf.test.ts</code>): full "
    "clustering 2153 → ~750 ms; job setup 43 ms; worst per-type slice ~250 ms; block-index reuse saves "
    "another 30–90 ms/recompute. Same 20 corridors.",
    "Exploded-diamond repro (start waypoint parked on a 3-diamond stack): pre-fix the diamonds mounted "
    "<code>leaflet-interactive:false</code> and stayed dead after fan-out; post-fix they mount interactive, "
    "fan out non-passthrough, and clicking one selects its corridor (app log "
    "<code>diamond click … override=true passthrough=false</code>, URL gains the <code>f&lt;id&gt;</code> "
    "corridor token).",
    "<code>npx tsc --noEmit</code> clean; client suite green — 231/231 (clustering output is byte-identical "
    "by construction: the sliced job runs the same per-type pipeline in the same order).",
    "<code>npm run build</code> (production bundle) passes.",
]

CHECKLIST = [
    "Open <a href='https://bikepaths.cityedit.org/'>bikepaths.cityedit.org</a>, select a route, and mash the "
    "± vote buttons: each press should land instantly (no beat of frozen UI, no delayed 2 s hitch).",
    "Wheel-zoom in and out a few times over Manhattan: the heat should ride the zoom smoothly and the app "
    "should stay responsive — no multi-second “whole map reloading” stall.",
    "Cast a vote and confirm the heatmap + the card's counts update immediately; the top-proposal PINS may "
    "reshuffle up to a minute later (that lag is the new batching, by design).",
    "Find a stack of 2+ diamond pins, tap it to fan out, then hover and click a fanned DIAMOND: hover should "
    "light its corridor + show the route card, click should select the corridor.",
    "Check a few diamond proposals: corridors should read meaningfully longer than before (the hottest "
    "ones up to ~3.5 km).",
    "Leave the tab hidden for a few minutes, vote from another device, then return: the pins should refresh "
    "within a second of the tab becoming visible.",
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
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: heatmap canvas, top-proposal pins, route diamonds, hover + pinned cards"),
        "file": ("GraphLayer.tsx", "~4.4k LOC — topology load, winners, indicators, selection resolve, all proposal cards"),
        "outline": [
            ("Module constants", "NEW PROPOSALS_REFRESH_INTERVAL_MS + scheduleIdleSlice helper", True),
            ("IndicatorMarker", "marker wrapper — now ALWAYS created interactive (CSS passthrough is the gate)", True),
            ("refreshHeatmapDisplay / recomputeTopProposals", "SPLIT of the old refreshGraphDisplay: cheap repaint vs full PBTP scan", True),
            ("recomputeRouteProposals", "REWRITTEN as a cancellable sliced job (one type per idle slice)", True),
            ("requestProposalsRecompute + dirty sweep", "NEW — coalesced idle recompute, 60 s dirty flush, visibility flush", True),
            ("fetchVotes / initial loader / theme effect", "call the cheap repaint + prompt idle recompute", True),
            ("WS delta + optimistic handlers", "repaint + dirty-mark only (legend growth → prompt refresh)", True),
            ("redraw / zoom handlers", "unchanged (canvas heat is skipped on block maps)", False),
            ("indicatorMarkers (squares)", "interactive prop dropped; icon cache as before", True),
            ("routeIndicatorMarkers (diamonds)", "NEW per-label+heat-bucket icon cache; interactive prop dropped", True),
        ],
        "blocks": [
            "PROPOSALS_REFRESH_INTERVAL_MS + scheduleIdleSlice — the batching knobs",
            "IndicatorMarker — always-interactive rationale comment (react-leaflet never updates `interactive`)",
            "refreshHeatmapDisplay — version bump + scheduleRedraw, NO proposal scan",
            "recomputeTopProposals — the PBTP scan + legend-growth force, batched-only",
            "recomputeAllProposals / requestProposalsRecompute / dirty-sweep effect",
            "recomputeRouteProposals — createRouteProposalJob + token-cancelled idle slices + prebuilt blockIndex",
            "delta + optimistic handlers — legendLenBefore/After check → dirty or prompt refresh",
            "diamond icon cache — `${label}|d${heatBucket}` shared divIcons for plain diamonds",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "pure route-proposal logic (no React/Leaflet): parsing, coverage, dedupe, clustering"),
        "file": ("routeProposals.ts", "~700 LOC — client-side deterministic corridor extraction + coverage rules"),
        "outline": [
            ("Wire parsing / coverage / dedupe", "unchanged", False),
            ("Length-budget constants", "600/150/2500 → 900/220/3500 m", True),
            ("netsByType", "NEW — one-pass sparse per-type nets (was per-type full rescans)", True),
            ("buildTypeAdj", "takes the sparse Map; same iteration order (determinism preserved)", True),
            ("createRouteProposalJob", "NEW — resumable per-type pipeline: types / step / finish", True),
            ("computeRouteProposals", "now a thin wrapper: job.finish(job.types.map(job.step))", True),
        ],
        "blocks": [
            "ROUTE_LENGTH_BASE_M / PER_SQRT_SCORE_M / MAX_M — 900 / 220 / 3500",
            "netsByType — Map<edgeId, net> per legend type, ascending-eid insertion",
            "buildTypeAdj — iterates the sparse nets; Map.get in the arc loop",
            "RouteProposalOptions.blockIndex — caller-supplied edge→block index",
            "createRouteProposalJob — the slice boundary is the vote type",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.perf.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "opt-in perf harness against the real local graph"),
        "file": ("routeProposals.perf.test.ts", "NEW — skipped unless PERF=1; needs the dev stack"),
        "outline": [
            ("Perf run", "fetch real topology+votes → time decode, adj, blockIndex, full run, per-slice", True),
        ],
        "blocks": [
            "PERF=1 gate + reference numbers in the header (750 ms full, 250 ms worst slice)",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "the GL base map + block-heat fill layers, camera-synced from Leaflet"),
        "file": ("MapLibreBackground.tsx", "~530 LOC — style build, zoom-anim ride, block feature-state"),
        "outline": [
            ("Style / camera sync / zoom ride", "unchanged", False),
            ("Block heat effect", "REWRITTEN — diff-apply vs last state; full only on denom change", True),
            ("sourcedata listener", "retries only until the FIRST successful apply", True),
        ],
        "blocks": [
            "applied.current — the sparse id→heat map + denom actually written to the source",
            "apply() — full (first / denom moved) vs diff (changed blocks + cooled blocks) paths",
            "onSourceData — `if (!applied.current) apply()` (was: full apply per tile event)",
            "dlog 'heat apply (full|diff): N writes' + blockHeatNonzero debugState",
        ],
    },
    "docs/three-layer-model.md": {
        "on": ["React / Leaflet client"],
        "module": ("docs", "the source-of-truth spec for the graph/blocks/route-proposals separation"),
        "file": ("three-layer-model.md", "layer definitions + block-scoped vote semantics"),
        "outline": [
            ("Layer diagram", "edge→proposal arrow: 'real time' → 'minute-batched'", True),
            ("§3.1 What they are", "documents the batched sweep + idle slices; heat/counts stay live", True),
            ("Clustering pipeline / §4", "unchanged", False),
        ],
        "blocks": [
            "§3.1 — recomputation batched off the vote path; may lag votes by up to a minute",
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

  <p class="lede">Four fixes for the NYC bikes map's scale (3.3 M edges, ~47 k lit blocks), all reproduced
  and measured live before fixing. Voting froze the app for over two seconds because every cast re-ran both
  top-proposal computations — they now run as a minute-batched, idle-sliced sweep while the heatmap and
  counts stay live. Zooming re-applied the entire block heat once per loaded tile (4.4 M
  <code>setFeatureState</code> calls per zoom) — heat is now applied once and diffed per vote. Exploded
  route diamonds were permanently event-dead because react-leaflet only applies <code>interactive</code> at
  marker creation — the CSS passthrough class is now the single gate. And corridors earn a ~50% longer
  length budget.</p>

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
    Generated from <code>changelog/changes-vote-zoom-batching.diff</code> by
    <code>changelog/build_vote_zoom_batching_report.py</code>. Regenerate after further edits with
    <code>git diff -- &lt;files&gt; &gt; changelog/changes-vote-zoom-batching.diff &amp;&amp;
    python changelog/build_vote_zoom_batching_report.py</code>.
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
