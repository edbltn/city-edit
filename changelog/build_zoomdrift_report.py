#!/usr/bin/env python3
"""Generate the MapLibre↔Leaflet zoom-drift changelog report.

Run from repo root: python changelog/build_zoomdrift_report.py
Reads changelog/changes-zoomdrift.diff,
writes changelog/2026-07-05-maplibre-zoom-drift.html

Modeled on build_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-zoomdrift.diff")
OUT_PATH = os.path.join(HERE, "2026-07-05-maplibre-zoom-drift.html")

DATE = "2026-07-05"
TITLE = "MapLibre ↔ Leaflet position drift — zoom off-by-one + riding the zoom animation"


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


SECTIONS = [
    {
        "id": "offbyone",
        "tag": "Client · camera sync",
        "title": "1 · The drift itself: same zoom number, 2× the scale",
        "symptom": (
            "Everything Leaflet draws — top-proposal pins, route lines, hover highlights — sat off its "
            "street on the MapLibre base map. The offset was zero at the screen center and grew linearly "
            "toward the edges, and it changed as you panned or zoomed: classic “position drift”. "
            "Measured in the live app: a point 0.01° from center projected 117&nbsp;px away in Leaflet but "
            "233&nbsp;px away in MapLibre — exactly 2×."
        ),
        "cause": [
            "Leaflet and MapLibre define zoom against different world-tile sizes: Leaflet's world at zoom "
            "<code>z</code> is <code>256·2^z</code> px, MapLibre's is <code>512·2^z</code> px. The same "
            "numeric zoom therefore renders MapLibre at exactly twice Leaflet's scale.",
            "<code>syncCamera</code> passed <code>leafletMap.getZoom()</code> straight into "
            "<code>ml.jumpTo(...)</code>, and the constructor did the same with "
            "<code>CONFIG.initialView.zoom</code>. Centers matched, scales didn't — so the two renderers "
            "agreed only at the screen center.",
            "This only became visible now: until commit <code>a1fc806</code> the opaque Leaflet raster "
            "fallback painted OVER the MapLibre canvas in local dev, so the visible base map was Leaflet's "
            "own tiles (self-consistent by construction) and the mismatch was hidden.",
        ],
        "fixes": [
            "New module constant <code>LEAFLET_TO_MAPLIBRE_ZOOM = 1</code> with the why, subtracted in both "
            "places that drive the MapLibre camera: the map constructor and <code>syncCamera</code>'s "
            "<code>jumpTo</code>.",
            "Verified live: leaflet&nbsp;14&nbsp;/&nbsp;maplibre&nbsp;13, and the projection residual for "
            "off-center probe points dropped from hundreds of px to &lt;&nbsp;1&nbsp;px (float rounding), "
            "before and after pan/zoom.",
        ],
        "files": ["client-react/src/components/MapLibreBackground/MapLibreBackground.tsx"],
    },
    {
        "id": "zoomride",
        "tag": "Client · zoom animation",
        "title": "2 · Mid-zoom drift: the GL map froze while Leaflet glided",
        "symptom": (
            "During a wheel/button zoom, the icons and heat canvas rescale smoothly (last workstream) but "
            "the MapLibre base map + block heat stayed FROZEN for the 250&nbsp;ms animation, then snapped to "
            "the target view — the layers visibly drifted apart mid-zoom on every single zoom step."
        ),
        "cause": [
            "During its animated zoom Leaflet suppresses <code>move</code> events: <code>_animateZoom</code> "
            "calls <code>_move(center, zoom, undefined, supressEvent=true)</code> at animation start, and the "
            "only real <code>move</code> fires 250&nbsp;ms later in <code>_onZoomTransitionEnd</code>.",
            "<code>syncCamera</code> only listens to <code>move</code>, so the GL camera held the OLD view "
            "for the whole transition while every <code>.leaflet-zoom-animated</code> element rode a CSS "
            "transform transition to the new one.",
            "(Pinch zoom was never affected: Leaflet's TouchZoom fires per-frame <code>move</code> events, "
            "which keep <code>jumpTo</code> live.)",
        ],
        "fixes": [
            "New <code>zoomanim</code> handler rides the animation the same way the heat canvas does: it maps "
            "every current container point <code>p</code> to its target-frame position <code>q + p·scale</code> "
            "(where <code>q = project(nw, z') − project(center', z') + size/2</code> and "
            "<code>scale = 2^Δz</code>) and sets that as a CSS transform on the GL container with Leaflet's "
            "exact transition — <code>transform 0.25s cubic-bezier(0, 0, 0.25, 1)</code> — so base map, block "
            "heat, heat canvas and icons all tween on the identical curve.",
            "On <code>zoomend</code> the transform is cleared in the same tick as the final <code>move</code>'s "
            "<code>jumpTo</code>: MapLibre re-renders in its own rAF (before paint), so the crisp final frame "
            "and the cleared transform land in the same compositor frame — no flash.",
            "Guarded by Leaflet's <code>_animatingZoom</code> flag so pinch-zoom and <code>flyTo</code> "
            "(which sync per-frame via <code>move</code>) never fight the transition.",
            "<code>transformOrigin: \"0 0\"</code> added to the container so the scale is about the viewport's "
            "top-left corner, matching the transform math.",
        ],
        "files": ["client-react/src/components/MapLibreBackground/MapLibreBackground.tsx"],
    },
    {
        "id": "overzoom",
        "tag": "Client · deep zoom",
        "title": "3 · Follow-on: don't lose the base map at deep zoom",
        "symptom": (
            "With the camera now offset by −1, the old <code>maxzoom: 19</code> on the raster LAYER would "
            "have blanked the base map at Leaflet zooms ≥ 20 (the app allows 21) — a MapLibre layer-maxzoom "
            "HIDES the layer once the camera passes it."
        ),
        "cause": [
            "CartoDB serves tiles up to z19. The cap belongs on the SOURCE, where it means “overzoom "
            "(stretch) z19 tiles beyond this” — the GL equivalent of Leaflet's "
            "<code>maxNativeZoom</code> — not on the layer, where it means “hide me”.",
        ],
        "fixes": [
            "Moved <code>maxzoom: 19</code> from the <code>carto-tiles</code> layer onto the "
            "<code>carto-base</code> source (and dropped the layer's redundant <code>minzoom: 0</code>).",
        ],
        "files": ["client-react/src/components/MapLibreBackground/MapLibreBackground.tsx"],
    },
]

VERIFY = [
    "Quantified the bug before fixing: injected probe points via the browser console — dx/dy between "
    "<code>lm.latLngToContainerPoint</code> and <code>ml.project</code> was 0 at center, 116/-154&nbsp;px at "
    "+0.01°, -350/-461&nbsp;px at ±0.03° (exactly 2× slope). After the fix: &lt;&nbsp;1&nbsp;px everywhere.",
    "Exercised zoom in the live app (zoom control + reloads): cameras stay locked (leaflet z / maplibre z−1) "
    "and the projection residual stays sub-pixel after every zoom.",
    "Caught the ride mid-flight: during the 250&nbsp;ms animation the GL container carries "
    "<code>translate3d(…) scale(2)</code> with the Leaflet curve, and the transform reads back cleared "
    "(<code>\"\"</code>) once settled; mid-animation screenshot shows pins tracking the scaled base map.",
    "<code>npx tsc --noEmit</code> clean; full client suite green — 186/186 tests.",
    "A dev-only <code>window.__lmap</code> handle was added to MapView (committed meanwhile in "
    "<code>401db4b</code>) alongside the existing <code>window.__ml</code>, so both cameras stay measurable.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>http://localhost:3000/m/nyc-walkways</a>, pan around "
    "at several zooms: pins and route lines should sit exactly on their streets everywhere on screen, not "
    "just at the center.",
    "Zoom with the +/− control and the mouse wheel: the base map + block heat should glide and rescale in "
    "lockstep with the icons through the whole animation — no freeze, no end-of-zoom snap.",
    "On a touch device (or DevTools touch emulation), pinch-zoom: should stay live and aligned per-frame.",
    "Zoom all the way in (Leaflet z20–21): the base map should stay visible (stretched z19 tiles), not blank.",
    "Cast a vote and confirm the block heat still lights the block under the edge you voted on (feature-state "
    "still keyed correctly after the camera change).",
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
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "the GL base map + block-heat renderer that sits UNDER the interactive Leaflet overlay"),
        "file": ("MapLibreBackground.tsx", "~410 LOC — builds the GL style (carto raster + PMTiles blocks), mirrors the Leaflet camera, colors blocks via feature-state"),
        "outline": [
            ("Module setup", "pmtiles protocol · NEW LEAFLET_TO_MAPLIBRE_ZOOM constant", True),
            ("Block-event contracts", "BLOCK_VOTES_EVENT / BLOCK_SELECT_EVENT payloads", False),
            ("blockFillPaint / blockLinePaint", "heat-driven fill + outline ramps", False),
            ("buildStyle", "sources (carto raster · pmtiles blocks) + layers; maxzoom moved layer→source", True),
            ("Map init effect", "constructor now offsets the initial zoom by −1", True),
            ("Camera-sync effect", "jumpTo on move (−1) + NEW zoomanim CSS ride + zoomend clear", True),
            ("Block feature-state effect", "heat + selection painting, sourcedata re-apply", False),
            ("Container div", "absolute-fill; NEW transformOrigin: 0 0", True),
        ],
        "blocks": [
            "LEAFLET_TO_MAPLIBRE_ZOOM = 1 — the 256px- vs 512px-tile zoom conversion, documented",
            "carto-base source — maxzoom: 19 (overzoom/stretch, like Leaflet maxNativeZoom)",
            "carto-tiles layer — minzoom/maxzoom removed (layer maxzoom would HIDE the base map)",
            "map constructor — zoom: CONFIG.initialView.zoom - LEAFLET_TO_MAPLIBRE_ZOOM",
            "syncCamera — jumpTo at leafletZoom - LEAFLET_TO_MAPLIBRE_ZOOM",
            "handleZoomAnim — container transform translate(q) scale(2^dz), Leaflet's 0.25s cubic-bezier",
            "handleZoomEnd — clear transform in the same tick as the final jumpTo (same compositor frame)",
            "container style — transformOrigin: '0 0' for the ride's scale math",
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

  <p class="lede">The “position drift” after the icons + heatmap zoom-rescale work was two separate
  camera bugs between the two renderers that now share the screen: MapLibre (base map + block heat) ran at
  2× Leaflet's scale because the two libraries define zoom against different tile sizes, and during zoom
  animations the GL map froze while the Leaflet layers glided — Leaflet suppresses <code>move</code> events
  mid-animation. Both fixed in MapLibreBackground; measured to sub-pixel in the live app.</p>

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
    Generated from <code>changelog/changes-zoomdrift.diff</code> by <code>changelog/build_zoomdrift_report.py</code>.
    Regenerate after further edits with <code>git diff &gt; changelog/changes-zoomdrift.diff &amp;&amp; python changelog/build_zoomdrift_report.py</code>.
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
