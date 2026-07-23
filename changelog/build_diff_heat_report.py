#!/usr/bin/env python3
"""Generate the differential-block-heat changelog report (2026-07-23).

Run from repo root: python changelog/build_diff_heat_report.py
Reads changelog/changes-diff-heat.diff (captured with:
  git diff 53716b3^..53716b3 > changelog/changes-diff-heat.diff),
writes changelog/2026-07-23-differential-block-heat.html

Modeled on build_counter_cancel_report.py (same styles + hierarchical
context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-diff-heat.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-differential-block-heat.html")

DATE = "2026-07-23"
TITLE = "Differential block heat — net-against blocks go cold"


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
        "id": "why",
        "tag": "Diagnosis · heat semantics",
        "title": "1 · Activity heat can't tell support from opposition",
        "symptom": (
            "Block heat was TOTAL deduped activity (up + down): a block with 6 devices against and "
            "5 for glowed exactly as hot as one with 11 for. After the counter-vote work made "
            "net-against and cancelled blocks a real, meaningful state, the heatmap had no way to "
            "show them — opposition read as popularity."
        ),
        "cause": [
            "The heat value was <code>block_votes[b] = up + down</code> end to end: server "
            "aggregate, sparse wire format, and the MapLibre feature-state apply all spoke "
            "unsigned totals, and every theme ramp only had a warm arm.",
        ],
        "fixes": [
            "Heat is now the <strong>signed differential (up − down) of the block's top-ranked "
            "proposal</strong>, ranked BY differential — <code>topProposalDiffs()</code> takes the "
            "max differential across the block's vote types. Positive rides the existing warm "
            "ramp; zero (cancelled/contested signal) is invisible; negative descends a new cold "
            "arm. Client-only: derived from the per-type breakdowns already in every payload, so "
            "no server or wire change.",
        ],
        "files": ["client-react/src/components/GraphLayer/voteApply.ts",
                   "client-react/src/components/GraphLayer/voteApply.test.ts"],
    },
    {
        "id": "render",
        "tag": "client · MapLibre blocks",
        "title": "2 · Two-armed normalization and paint",
        "symptom": (
            "Negative differentials are structurally tiny next to bulk-import positives (organic "
            "downvotes vs thousands of imported rides): normalized against the shared ceiling "
            "they'd all wash out at a barely-visible tint."
        ),
        "cause": [
            "One log denominator can't serve both arms — the positive ceiling on a busy map is "
            "hundreds of votes, while ~10 net-against is already overwhelming opposition.",
        ],
        "fixes": [
            "Feature-state heat is now ∈ [−1, 1] with a log denominator per arm: positives "
            "normalize against the busy-map ceiling (<code>HEAT_FULL_SCALE</code> 50), negatives "
            "against their own much tighter floor (<code>NEG_HEAT_FULL_SCALE</code> 10), so "
            "net-against blocks actually reach deep cold.",
            "The fill/line interpolations pin the whole mild-negative range to <code>cold</code> "
            "with a stop at −0.001 — without it a −0.1 block would interpolate mostly toward "
            "<code>warm</code> and read as faint support. Opacity is symmetric about the invisible "
            "zero (the cold arm peaks slightly higher — cold fills fight the basemap harder).",
            "The diff-apply architecture survives intact: lit = nonzero differential, the "
            "full-rewrite trigger is now the (denomPos, denomNeg) pair, and blocks whose "
            "differential returns to zero cool back to invisible.",
        ],
        "files": ["client-react/src/components/MapLibreBackground/MapLibreBackground.tsx",
                   "client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "themes",
        "tag": "client · theme ramps",
        "title": "3 · A cold arm for every theme",
        "symptom": (
            "No theme had negative-arm colors, and the cold end must read as opposition without "
            "being confusable with the theme's positive spectrum."
        ),
        "cause": [
            "Each ramp was authored warm-only (halo/warm/hot/peak + computed incandescent tip).",
        ],
        "fixes": [
            "<code>HeatRamp</code> gains <code>cold</code> / <code>coldDeep</code>: bikes freezes "
            "steel blue → vivid near-pure blue (an overwhelmingly net-against block reads almost "
            "blue); transit and waterfront swing violet/indigo since their positives already climb "
            "the cool blues; the light multiply themes (trees, terracotta, plum) tint toward "
            "slate/steel blues that darken the paper coolly.",
            "The legend gradient (<code>heatGradientCss</code> → <code>--heat-gradient</code>) now "
            "spans the full spectrum, cold-deep on the left with zero sitting at 25%.",
        ],
        "files": ["client-react/src/mapStyles.ts"],
    },
]

VERIFY = [
    "Live on local nyc-bikes (rich pre-sweep negative state): feature-state holds 24,363 "
    "negative-heat + 17,881 positive-heat blocks, range exactly [−1, 1]; flip-countered avenues "
    "render blue against warm park/greenway desire-lines (screenshot-verified at two zooms).",
    "block-heat paint expression confirmed in the live style: −1 → rgb(70,105,255) (coldDeep), "
    "−0.001 → rgb(38,108,205) (cold), 0 → warm green, 1 → incandescent tip.",
    "Early heat paint (pre-topology, from the sparse body) still works: first-heat-paint at "
    "284,281 block slots, then a full apply of 42,244 lit blocks.",
    "258 client tests pass (5 new for topProposalDiffs: max-differential ranking, negative top, "
    "cancelled-to-zero, no-breakdown fallback, Int32Array/holey sparse shapes).",
    "tsc --noEmit clean.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-bikes</code> — avenues that were counter-flipped "
    "locally should read blue; Central Park loops and greenways stay warm green/gold.",
    "Click a blue block: the modal's top row should be net-against (downs &gt; ups), matching the "
    "cold rendering.",
    "Switch themes on a few maps (transit, trees, terracotta): negative blocks should read "
    "violet (transit) or steel blue (light themes) — never confusable with each theme's "
    "positive colors.",
    "Check the legend swatch: the gradient should now start in the cold colors before warming "
    "through the theme ramp.",
    "Cast an up-then-down vote on one block and watch it move warm → invisible → cold live "
    "(WS delta path recomputes the differential per apply).",
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
    diffs_h = f"<h3>Diffs — files touched (click to expand)</h3>{''.join(file_rows)}" if s["files"] else ""
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
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/voteApply.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer", "pure vote-application: mutates in-memory vote arrays, derives node/block projections"),
        "file": ("voteApply.ts", "~290 LOC — optimistic/authoritative edge SETs, block SETs, and now the signed block-heat derivation"),
        "outline": [
            ("applyMyVoteChange / applyEdgeVoteChange", "optimistic per-edge transitions", False),
            ("topProposalDiffs", "NEW: per-block signed heat — max (up − down) across the block's vote types, total fallback, both arm ceilings", True),
            ("applyBlockCounts", "authoritative per-block SET from WS deltas (comment updated: totals are the legacy fallback now)", True),
            ("applyAuthoritativeCounts", "authoritative per-edge SET", False),
        ],
        "blocks": [
            "topProposalDiffs — Int32Array over blocks; per block max differential across [legendIdx, up, down] entries; maxPos/maxNeg for the renderer",
            "applyBlockCounts comment — block_votes stays total activity, display heat is the differential derived downstream",
        ],
    },
    "client-react/src/components/GraphLayer/voteApply.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer", "unit tests for the pure vote-application core"),
        "file": ("voteApply.test.ts", "24 tests — 5 new for topProposalDiffs"),
        "outline": [
            ("edge/node/block apply suites", "unchanged", False),
            ("topProposalDiffs suite", "NEW: max-differential ranking, negative top, cancelled→0, fallback, sparse shapes", True),
        ],
        "blocks": [
            "five topProposalDiffs cases incl. Int32Array totals + holey breakdown arrays (the sparse-decode shapes)",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer", "the map brain: topology, votes, selection, proposals, heat broadcast"),
        "file": ("GraphLayer.tsx", "~5100 LOC — only the block-heat broadcast changes"),
        "outline": [
            ("constants", "NEG_HEAT_FULL_SCALE = 10 beside HEAT_FULL_SCALE = 50, with the why", True),
            ("broadcastBlockVotes", "now derives signed diffs via topProposalDiffs and ships both arm ceilings", True),
            ("everything else", "topology load, selection, proposals, canvas — untouched", False),
        ],
        "blocks": [
            "NEG_HEAT_FULL_SCALE — negatives get their own dynamic range (organic downs vs bulk-import ups)",
            "broadcastBlockVotes — {blockDiff, max, maxNeg} event detail replaces {blockVotes, max}",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · MapLibreBackground", "owns the GL map: block tiles, feature-state heat, selection states"),
        "file": ("MapLibreBackground.tsx", "~600 LOC — paint expressions + the heat apply loop change"),
        "outline": [
            ("BlockVotesDetail", "signed contract: blockDiff + max + maxNeg", True),
            ("blockFillPaint / blockLinePaint", "interpolations extended into the negative domain; −0.001 cold pin; symmetric opacity", True),
            ("apply()", "two log denominators; lit = nonzero; denomKey full-rewrite trigger", True),
            ("selection / tile rebind / camera sync", "untouched", False),
        ],
        "blocks": [
            "fill-color: −1 coldDeep → −0.001 cold → 0 warm → 0.35 hot → 0.7 peak → 1 tip",
            "fill/line-opacity symmetric about invisible zero (cold arm peaks slightly higher)",
            "signedHeat(v) = ±log(|v|+1)/denom per arm; denomKey change forces the rare full rewrite",
        ],
    },
    "client-react/src/mapStyles.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · theming", "per-style basemap + heat ramp definitions shared by canvas, blocks, pins, legend"),
        "file": ("mapStyles.ts", "~360 LOC — HeatRamp interface + all seven palettes + legend gradient"),
        "outline": [
            ("HeatRamp", "gains cold / coldDeep (the negative arm)", True),
            ("seven palettes", "per-theme cold colors chosen against each positive spectrum", True),
            ("heatGradientCss", "legend spans cold-deep → tip, zero at 25%", True),
            ("buildHeatRampStops / pin ramps", "positive-arm samplers — untouched", False),
        ],
        "blocks": [
            "bikes: cold rgb(38,108,205) → coldDeep rgb(70,105,255) — the almost-blue floor",
            "transit/waterfront swing violet (positives already cool); light themes tint slate/steel blue",
            "legend gradient with the negative arm on the left quarter",
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

  <p class="lede">Imported Citibike trips are routed through the foot profile on purpose — the divergence
  between how a pedestrian moves and where a bike is allowed to go IS the vote. But the ingest upvoted the
  whole pedestrianized path, so corridors that already carry bikes (and especially streets that already
  have bike lanes, densest near Citibike stations) got the same +1 as the genuinely un-bikeable stretches.
  The correction: a second, bike-legality OSRM dataset (stock v5.25.0 bicycle profile flattened to
  shortest-legal-path, pushing-the-bike disabled) re-routes every ingested ride pinned to its own voted
  corridor via via-points; every upvoted edge lying bodily inside the resulting route's 20 m corridor gets
  a <code>direction=-1</code> cast from the ride's own voter identity. What survives upvoted is exactly
  what can't be ridden: park and plaza paths, stairs, and one-way streets taken against the flow.</p>

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
    Generated from <code>changelog/changes-counter-lyft.diff</code> by <code>changelog/build_counter_lyft_report.py</code>.
    Regenerate after further edits with
    <code>git diff dc81a6c -- osrm/bicycle-flat.lua scripts/build_bike_osrm.sh server/counter_lyft.py server/tests/unit/test_counter_lyft.py server/app.py server/database.py .gcloudignore &gt; changelog/changes-counter-lyft.diff &amp;&amp; python changelog/build_counter_lyft_report.py</code>.
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
