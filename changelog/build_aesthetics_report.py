#!/usr/bin/env python3
"""Generate the aesthetics changelog report (2026-07-08).

Run from repo root: python changelog/build_aesthetics_report.py
Reads changelog/changes-aesthetics.diff
(captured with: git diff e24a359^..54c1a55 -- client-react > changelog/changes-aesthetics.diff),
writes changelog/2026-07-08-heat-tip-border-inset.html

Modeled on build_rbtp_parity_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-aesthetics.diff")
OUT_PATH = os.path.join(HERE, "2026-07-08-heat-tip-border-inset.html")

DATE = "2026-07-08"
TITLE = "Aesthetic fixes — slimmer selection rings, and an incandescent tip for the heat ramp"


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
        "id": "inset",
        "tag": "Client · proposal markers",
        "title": "1 · The selected double border hugs its outer ring — evenly, on both marker kinds",
        "symptom": (
            "Selecting a top proposal drew its inner ring too deep inside the outer one: the point-based "
            "pin's inset was a bit too thick, and the route-based diamond's was thicker still (and uneven), "
            "so the double border crowded the glyph and the two marker kinds didn't match."
        ),
        "cause": [
            "The square pin's inner was a true 3px contour of the outer — consistent, just deep.",
            "The diamond's inner was NOT a contour at all: hand-placed vertices gave a perpendicular inset "
            "that varied from ≈3.5px to ≈4.2px edge to edge, which is why the RBTP diamond read chunkier "
            "than the PBTP square at the same selected state.",
        ],
        "fixes": [
            "Every inner shape is now a true <strong>2.25px perpendicular-offset contour</strong> of its "
            "outer: the pin (square+tail — the tail tip rises by 2.25/sin(halfAngle) ≈ 2.9 so the gap stays "
            "constant around the V), the plain square, the route kite (45° vertices rise 2.25·√2 ≈ 3.18; its "
            "side vertices land at y 17.2 because the kite's lower edges are steeper), and the symmetric "
            "fanned-out diamond.",
            "One number, four shapes — the square pin and the route diamond now carry identical-weight "
            "double rings, with visibly more air around the icon.",
            "Note: the fanned-out symmetric-diamond inner (<code>INNER_DIAMOND_SQUARE_PATH</code>) is a "
            "constant introduced by the still-uncommitted rbtp-parity workstream; its re-measured value is "
            "in the working tree and lands with that workstream's commit rather than this one.",
        ],
        "files": ["client-react/src/components/GraphLayer/voteTypeIcon.ts"],
    },
    {
        "id": "heattip",
        "tag": "Client · heat display",
        "title": "2 · An incandescent tip keeps the hottest votes distinguishable",
        "symptom": (
            "On busy maps the hottest parts of the heat display were indistinguishable — on /m/nyc-bikes "
            "(1.79M votes) every hot corridor in Manhattan painted the same flat gold, whether it carried "
            "46 votes or 617."
        ),
        "cause": [
            "The intensity scale is already logarithmic (log(votes+1)/log(max+1)) — but vote counts are "
            "heavy-tailed, so the log piles the busy corridors up near heat 1.0; the ramps then stopped "
            "resolving exactly there.",
            "The MapLibre block fill — the primary heat display on block maps like nyc-bikes — flat-topped "
            "its color ramp at heat 0.6 (<code>0.6 → peak, 1.0 → peak</code>): measured against the live "
            "data, everything from 46 to 617 votes (4,152 of the 35,044 lit blocks — 11.8%, i.e. precisely "
            "the hottest parts) rendered one identical color, with fill-opacity creeping only 0.42 → 0.58.",
            "The shared canvas/legend/pin ramp ended AT peak (stops 0/0.4/0.75/1), so its top quarter "
            "interpolated into peak with no headroom above — hot and hottest converged.",
        ],
        "fixes": [
            "New <code>heatTip(heat, basemap)</code> extends every ramp past peak into a computed "
            "incandescent tip: peak mixed 68% toward white on dark basemaps (additive blending — hotter = "
            "brighter) and toward near-black ink on light ones (multiply — hotter = darker).",
            "Shared ramp stops move to 0 / 0.35 / 0.65 / 0.85 / 1 with the tip at 1.0 — peak now sits at "
            "0.85, so the log-crushed top of the distribution resolves into hot → peak → white-hot instead "
            "of one flat band. Legend swatch, canvas heatmap, proposal-pin tint, and block fill still read "
            "the same color at a given intensity.",
            "Block fill interpolates warm → hot (0.35) → peak (0.7) → tip (1.0) across the FULL heat domain, "
            "with a wider opacity range (0.38 → 0.66) and a heat-driven outline width (1.1px → 2.0px) so the "
            "hottest blocks read etched, not just lit.",
            "The canvas heatmap's hottest-edge accent pass (norm > 0.7) strokes the tip color instead of "
            "flat peak, so the very top of the per-edge scale reads white-hot too.",
        ],
        "files": [
            "client-react/src/mapStyles.ts",
            "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
            "client-react/src/context/ThemeContext.tsx",
        ],
    },
]

VERIFY = [
    "Quantified the crush before designing the fix: pulled the live nyc-bikes block-vote distribution "
    "(35,044 lit blocks; p50 = 7, p90 = 54, p99 = 151, max = 617) and computed that the old flat-top "
    "painted every block ≥ 46 votes — 4,152 blocks — one identical color.",
    "Live on <code>/m/nyc-walkways</code>: selected a route diamond (card “ROUTE PROPOSAL · Improve "
    "sidewalk”) and a point pin; zoomed screenshots show both double borders slim and evenly spaced, and "
    "the live DOM confirms the new contour paths "
    "(<code>M17,6.2 L28,17.2 …</code> diamond, <code>M5.25,5.25 …</code> pin).",
    "Live on <code>/m/nyc-bikes</code>: block heat now resolves across the top — cross streets green → "
    "yellow-green, avenues gold, and the waterfront greenway visibly outburns them in pale white-gold "
    "(screenshots at city and neighborhood zoom). Legend gradient ends in the tip "
    "(<code>--heat-gradient</code> checked: … rgb(244,206,96) 85%, rgb(251,237,195)).",
    "No console errors on either map; <code>npx tsc --noEmit</code> clean; client suite green — 195/195.",
    "Commit hygiene: the working tree also carries the uncommitted rbtp-parity workstream in the same "
    "files, so the two commits were staged surgically (index-only HEAD+fix versions) — "
    "<code>git show e24a359 54c1a55</code> contain ONLY these fixes; parity edits remain untouched in the "
    "working tree.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>/m/nyc-walkways</a> and select a route-based "
    "(diamond) top proposal, then a point-based (square) one: each inner ring should sit just inside the "
    "outer with an even, matching gap on both marker kinds — no crowding of the glyph.",
    "Set a start ON a point proposal (teal is-start tint) and confirm the tinted double border reads the "
    "same slim weight.",
    "Open <a href='http://localhost:3000/m/nyc-bikes'>/m/nyc-bikes</a> (slow — big graph) and compare a "
    "busy avenue with the Hudson/East-River greenways: the hottest corridors should glow visibly paler / "
    "brighter than merely-busy gold streets, at both city and neighborhood zoom.",
    "Check the Votes legend swatch on a dark map: it should end in a pale (near-white) tip after the gold.",
    "Open a LIGHT-basemap map (e.g. a trees/terracotta/plum styled map): the hottest blocks should run "
    "DARKER past peak (toward ink), not lighter.",
    "Sanity-check a quiet map (few votes): it should still render proportionally cool — the "
    "HEAT_FULL_SCALE floor is untouched.",
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
    "client-react/src/components/GraphLayer/voteTypeIcon.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: heatmap canvas, top-proposal pins, route diamonds, cards"),
        "file": ("voteTypeIcon.ts", "~150 LOC — the divIcon factory: one SVG pin/square/diamond + inner ring + glyph + badges"),
        "outline": [
            ("VoteTypeIconOptions", "selected / tint / square / diamond / passthrough / heat opts", False),
            ("Shape geometry constants", "34×42 viewBox paths — outer + inner for pin, square, diamond", True),
            ("makeVoteTypeIcon", "class list, shape pick, glyph, badges, heat custom props", False),
        ],
        "blocks": [
            "inner-geometry comment — (6,6) 22×22 → (5.25,5.25) 23.5×23.5, 'hugging the outer'",
            "INNER_PATH — true 2.25px contour of the pin (tail tip rises ≈2.9)",
            "INNER_SQUARE_PATH — 2.25px contour of the plain square",
            "INNER_DIAMOND_PATH — 2.25px contour of the kite (was hand-placed ≈3.5-4.2px)",
        ],
    },
    "client-react/src/mapStyles.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · mapStyles", "the visual identity registry: basemap + accent + heat ramp per map style"),
        "file": ("mapStyles.ts", "~290 LOC — style table, ramp expansion, ramp sampling shared by canvas/legend/pins"),
        "outline": [
            ("HeatRamp / MapStyle types", "halo·warm·hot·peak + basemap/blend fields", False),
            ("MAP_STYLES table", "bikepaths / walkways / transit / … ramps", False),
            ("heatTip", "NEW — computed incandescent color above peak (white-hot / ink-hot)", True),
            ("heatGradientCss", "legend gradient — now 5 stops ending in the tip", True),
            ("buildHeatRampStops / sampleHeatRamp", "positioned stops 0/.35/.65/.85/1 — peak at .85, tip at 1", True),
        ],
        "blocks": [
            "heatTip(heat, basemap) — peak mixed 68% toward white (dark) or near-black ink (light)",
            "heatGradientCss(heat, basemap) — halo, warm 35%, hot 65%, peak 85%, tip",
            "buildHeatRampStops(heat, basemap) — 5th stop; positions restated in the doc comment",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "the GL underlay: raster basemap + PMTiles graph + the block-heat fill layer"),
        "file": ("MapLibreBackground.tsx", "~420 LOC — style build, Leaflet camera sync, feature-state heat/selection"),
        "outline": [
            ("blockFillPaint", "heat-driven fill color/opacity — the PRIMARY heat display on block maps", True),
            ("blockLinePaint", "the crisp outline tracing voted blocks", True),
            ("buildStyle", "raster + graph + blocks sources/layers", False),
            ("camera sync + zoom ride", "Leaflet move/zoomanim → jumpTo/transform", False),
            ("block votes feature-state effect", "log-normalized heat per block id", False),
        ],
        "blocks": [
            "blockFillPaint — warm→hot(.35)→peak(.7)→tip(1) over the FULL domain (was flat peak past .6); opacity .38→.66",
            "blockLinePaint — same ramp; heat-driven line-width 1.1→2.0px; opacity up to 1.0",
            "buildStyle call sites — pass the whole MapStyle (paint fns need basemap for the tip)",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the vote/proposal layer: heatmap canvas, top-proposal pins, route diamonds, cards"),
        "file": ("GraphLayer.tsx", "~3.4k LOC — topology load, canvas heatmap, winners, indicators, selection, cards"),
        "outline": [
            ("canvas redraw — ramp sampling", "per-edge log norm → sampleRamp; NEW tipColor", True),
            ("canvas pass 3 — hottest accent", "norm>0.7 accent stroke — now the tip, not flat peak", True),
            ("indicatorMarkers (PBTP pins)", "ramp-tinted pin heat — stops now include the tip", True),
            ("everything else", "selection resolve, cards, block broadcast — untouched", False),
        ],
        "blocks": [
            "buildHeatRampStops(heat, mapStyle.basemap) + tipColor = sampleRamp(1) in the canvas redraw",
            "ctx.strokeStyle = tipColor in the hottest-edge accent pass",
            "basemap added to the redraw + indicator memo deps; PBTP rampStops call updated",
        ],
    },
    "client-react/src/context/ThemeContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · context", "derives the active theme from the map and injects style custom properties"),
        "file": ("ThemeContext.tsx", "~45 LOC — data-basemap attr + --accent/--selection/--heat-gradient"),
        "outline": [
            ("ThemeProvider", "--heat-gradient now built with the basemap-aware tip", True),
        ],
        "blocks": ["heatGradientCss(style.heat, style.basemap) — legend swatch matches the extended ramp"],
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

  <p class="lede">Two aesthetic fixes, both verified live: the selected double border on top-proposal
  markers now hugs its outer ring — a true 2.25px contour on every shape, replacing the pin's 3px inset and
  the diamond's uneven hand-placed ≈3.5–4.2px one, so route diamonds and point pins finally carry
  identical-weight rings; and the heat display gained an incandescent tip above <code>peak</code> — the
  intensity scale was already logarithmic, but the block fill flat-topped its ramp at heat 0.6, painting
  every nyc-bikes block from 46 to 617 votes (4,152 blocks — exactly the hottest parts of the map) one
  identical gold. The ramp now keeps resolving through peak into white-hot (dark basemaps) or ink-dark
  (light ones), across the block fill, the canvas heatmap, the proposal-pin tint, and the legend.
  <br><br><em>Note: the working tree also carries the uncommitted rbtp-parity workstream in
  <code>voteTypeIcon.ts</code> / <code>GraphLayer.tsx</code>; these two commits were staged surgically and
  the diff below (captured from the commits) contains ONLY this workstream. Two entangled one-liners — the
  fanned-diamond inner contour value and the third <code>buildHeatRampStops</code> basemap arg, both on
  parity-added lines — live in the working tree and land with parity.</em></p>

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
    Generated from <code>changelog/changes-aesthetics.diff</code> by <code>changelog/build_aesthetics_report.py</code>.
    Regenerate after further edits with
    <code>git diff e24a359^..&lt;tip&gt; -- client-react &gt; changelog/changes-aesthetics.diff &amp;&amp; python changelog/build_aesthetics_report.py</code>.
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
