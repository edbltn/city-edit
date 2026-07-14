#!/usr/bin/env python3
"""Generate the route-proposal straightness changelog report (2026-07-14).

Run from repo root: python changelog/build_routeprop_report.py
Reads changelog/changes-routeprop.diff (captured with:
  git add -N client-react/scripts/routepropStats.ts && \
  git diff -- client-react/src/components/GraphLayer/routeProposals.ts \
    client-react/src/components/GraphLayer/routeProposals.test.ts \
    client-react/scripts/routepropStats.ts > changelog/changes-routeprop.diff),
writes changelog/2026-07-14-routeprop-straightness.html

Modeled on build_junction_disjoint_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-routeprop.diff")
OUT_PATH = os.path.join(HERE, "2026-07-14-routeprop-straightness.html")

DATE = "2026-07-14"
TITLE = "Route proposals prefer long straight lines — loop-back splitting + a min-blocks gate"


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


BASELINE_TABLE = """
<table class="stats">
  <thead><tr><th></th><th>before</th><th>after</th></tr></thead>
  <tbody>
    <tr><td>endpoint straightness &lt; 0.55 (comes back on itself)</td><td><strong>2 / 20</strong> (worst 0.22, 0.32)</td><td><strong>0 / 20</strong></td></tr>
    <tr><td>buried hairpin (worst 800 m window &lt; 0.4)</td><td><strong>6 / 20</strong> (worst 0.13)</td><td><strong>0 / 20</strong></td></tr>
    <tr><td>corridor length min / median / max (m)</td><td>882 / 3,065 / 10,499</td><td>881 / 3,961 / 10,497</td></tr>
    <tr><td>compute time (3.3 M-edge NYC graph)</td><td>934 ms</td><td>924 ms</td></tr>
    <tr><td>nyc-walkways stub proposal (100 m, 3 blocks)</td><td>shipped as a diamond pin</td><td>gated (still a PBTP)</td></tr>
  </tbody>
</table>
"""

SECTIONS = [
    {
        "id": "diagnosis",
        "tag": "Diagnosis · real prod-scale data",
        "title": "1 · Why top proposals got long and loopy (and some tiny)",
        "symptom": (
            "After the corridor length budget was tripled (<code>cf1d576</code>, 2026-07-13: base "
            "900→2700 m, growth 220→660 m·√score, ceiling 3500→10500 m), the top route proposals on "
            "the imported-data maps read as multi-kilometre snakes — some visibly looping back on "
            "themselves — while other maps still surfaced 100 m, 3-block stubs as route pins."
        ),
        "cause": [
            "The peel was always able to snake: <code>greedyHeaviestPath</code> extends by the heaviest "
            "unvisited arc with <em>no regard for direction</em>, so in a dense net-positive region it "
            "wanders. The old 3.5 km ceiling just hid it — the budget window was too short to hold a "
            "full loop. At 10.5 km the best-weight window happily contains the whole double-back.",
            "The bulk-imported vote data is what makes regions dense enough to snake: nyc-bikes holds "
            "1.79 M votes across 182,803 net-positive edges, so a single connected component can span "
            "boroughs and the heaviest path through it is effectively unconstrained.",
            "Measured on real data (the new <code>routepropStats.ts</code> harness, running the actual "
            "client pipeline): 2 of the top-20 nyc-bikes corridors ended ~2 km from their start after "
            "9–10 km of path (endpoint straightness 0.22 / 0.32), and 6 of 20 contained a buried "
            "hairpin — an 800 m stretch that came back to within 120–300 m of where it began "
            "(worst-window straightness down to 0.13). Three corridors sat pinned at the 10,497–10,499 m "
            "ceiling.",
            "At the other end, nothing stopped a hot 2-edge stub from becoming a “route”: nyc-walkways "
            "shipped a 100 m, 3-block corridor whose votes already surface as a point pin (PBTP).",
        ],
        "fixes": [
            "No hard max-distance change — long corridors are the point. The fix is shape-aware: "
            "split where a corridor turns back on itself (§3) and require a route to span ≥ 5 blocks "
            "(§2). Before/after on the same data:" + BASELINE_TABLE,
        ],
        "files": [],
    },
    {
        "id": "gate",
        "tag": "Client clustering · min-distance gate",
        "title": "2 · A route must span at least 5 blocks",
        "symptom": (
            "Hot micro-corridors (2–4 blocks, sometimes &lt; 100 m) surfaced as diamond route pins, "
            "duplicating the point-proposal pin that the same votes already earn."
        ),
        "cause": [
            "The activity gates only checked score (≥ 3) and path <em>edges</em> (≥ 2) — nothing "
            "expressed minimum <em>distance</em>. Edges are a poor proxy anyway: twin directions and "
            "crosswalk stubs mean many edges can still be one street corner.",
        ],
        "fixes": [
            "New gate <code>MIN_ROUTE_BLOCKS = 5</code> (option <code>minRouteBlocks</code>): after the "
            "path is projected onto blocks, a corridor spanning fewer than 5 distinct blocks is dropped. "
            "Blocks are the unit the UI selects and votes in, so “5 selected blocks” is exactly the "
            "min-distance the request asked for (~400–500 m in Manhattan).",
            "Nothing is lost: a dropped stub's votes still surface as a PBTP square pin — the gate only "
            "removes the redundant route pin.",
            "The gate runs AFTER loop-back splitting and budget-capping, so fragments and trimmed "
            "windows are judged by what will actually be shown.",
        ],
        "files": [],
    },
    {
        "id": "split",
        "tag": "Client clustering · splitLoopyPath",
        "title": "3 · Corridors are split where they turn back on themselves",
        "symptom": (
            "A single peeled path could snake through a hot region — out one avenue, back the parallel "
            "one — and ship as one absurd proposal instead of the two straight corridors a person "
            "would draw."
        ),
        "cause": [
            "Peeling maximizes weight, and the budget trim (<code>capPathToLengthBudget</code>) "
            "maximizes weight-within-meters. Neither ever looks at geometry, so a weight-dense loop "
            "beats a straight line every time.",
        ],
        "fixes": [
            "New <code>splitLoopyPath()</code>, run on every peeled path before budget-capping. "
            "Straightness of a stretch = crow-flies(endpoints) / arc length (1.0 = ruler, ~0.71 = "
            "L-corner or grid staircase, ~0.33 = U-turn).",
            "<strong>Two triggers</strong>: a sliding 800 m window whose straightness falls below 0.4 "
            "(a hairpin buried in an otherwise straight corridor — invisible to the endpoint measure), "
            "split at the excursion apex; and a whole stretch below 0.55 (it ends near where it began), "
            "split where the weaker half is straightest. Fragments recurse (depth ≤ 6) until straight "
            "or too short to judge (&lt; 4 edges).",
            "Each fragment then earns its OWN length budget from its own support and passes the "
            "activity gates independently — so one snake becomes several rankable straight corridors, "
            "and weak residue dies at the gates. On nyc-bikes the split fragments now fill ranks 9–15 "
            "(scores 700–2,900) that were previously occupied by sub-100-score stubs.",
            "Deterministic by construction: pure arithmetic over node coordinates (equirectangular "
            "meters), strict-inequality comparisons keep the earliest index on ties — the "
            "byte-identical-across-clients contract is preserved (determinism test included).",
            "Thresholds are calibrated so grid staircases (0.71), L-corners (0.71) and half-circle "
            "arcs (0.64) survive whole — only genuine double-backs split.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/routeProposals.test.ts",
        ],
    },
    {
        "id": "harness",
        "tag": "Diagnostics · real-data harness",
        "title": "4 · A stats harness that runs the real pipeline on real data",
        "symptom": (
            "Proposal-shape regressions were only visible by eyeballing the map — there was no way to "
            "measure length / straightness / block-count distributions against real vote data."
        ),
        "cause": [
            "The unit tests run on micro-graphs; the perf test only times. Neither sees the shapes that "
            "1.79 M imported votes produce.",
        ],
        "fixes": [
            "New <code>client-react/scripts/routepropStats.ts</code> (vite-node): fetches the live "
            "local Flask's binary topology (GTB2) + vote arrays, runs the REAL "
            "<code>computeRouteProposals</code> with the map's own kind resolver and block index, and "
            "prints per-proposal score / edges / blocks / length / endpoint straightness / "
            "worst-800 m-window straightness plus distribution summaries.",
            "Usage: <code>cd client-react &amp;&amp; node_modules/.bin/vite-node "
            "scripts/routepropStats.ts [slug ...]</code> (defaults to nyc-bikes + nyc-walkways).",
        ],
        "files": ["client-react/scripts/routepropStats.ts"],
    },
]

VERIFY = [
    "Baseline (before the change), nyc-bikes top-20: straightness &lt; 0.55 on 2 corridors (0.22, "
    "0.32); buried hairpin (worst 800 m window &lt; 0.4) on 6; three corridors pinned at the "
    "10.5 km ceiling; nyc-walkways shipped a 100 m / 3-block route pin.",
    "After: 0 / 20 below either straightness bar on nyc-bikes; every top corridor reads as a line "
    "(endpoint straightness 0.61–0.97). The former #3 snake (straight 0.32) became two straight "
    "corridors (0.87 / 0.91) that BOTH rank top-5; the stub route pins are gone from nyc-walkways.",
    "Median corridor length rose 3,065 → 3,961 m — splitting doesn't shorten the list, it replaces "
    "sub-100-score stubs at ranks 9–15 with 700–2,900-score straight fragments of the former snakes.",
    "Compute time on the 3.3 M-edge NYC graph unchanged: 924 ms vs 934 ms baseline (the splitter is "
    "O(path · depth) on peeled paths only).",
    "Unit tests: 10 new (min-blocks gate: default drop &lt; 5 blocks, blocks-not-edges, override; "
    "splitLoopyPath: straight kept whole, hairpin apex split, buried-detour window rule, fragment "
    "weight recomputation, &lt; 4-edge passthrough, determinism; plus a topology-level U-corridor "
    "→ two straight proposals test). Full client suite 241 passed / 1 skipped; <code>tsc -b</code> clean.",
    "Existing 53 route-proposal tests untouched semantically — the micro-graph helper pins "
    "<code>minRouteBlocks: 1</code> so they keep probing clustering mechanics, not the gate.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-bikes</code> and hover the top few diamond pins: each "
    "highlighted corridor should read as one long, roughly straight line — no corridor that runs out "
    "an avenue and comes back the parallel one.",
    "On <code>http://localhost:3000/m/nyc-walkways</code>, confirm the tiny “Add bike lane” route "
    "diamond (a ~100 m stub near the Improve-sidewalk corridors) is gone; its edge should still show "
    "a square PBTP pin if it's the hottest of its type.",
    "Click a diamond pin and confirm selection/voting still covers every block of the corridor "
    "(the auto-select ring and the vote cast are unchanged code paths).",
    "Reload the page twice: the same pins in the same order (determinism — ids are content-derived).",
    "Optionally re-run the numbers: <code>cd client-react &amp;&amp; node_modules/.bin/vite-node "
    "scripts/routepropStats.ts nyc-bikes</code> — expect 0/20 under both straightness bars.",
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
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · route proposals (RBTP)", "client-side deterministic clustering — hot corridors from (topology, vote state), no server round-trip"),
        "file": ("routeProposals.ts", "~1000 LOC — wire parsing, marker shape, coverage, corridor geometry, the clustering pipeline"),
        "outline": [
            ("Wire parsing + marker shape", "parseRouteProposal, diamond vs square", False),
            ("Coverage / dedupe / corridor geometry", "isRouteCovered, corridorCoordinates, anchor ordering", False),
            ("Pipeline constants + gates", "MIN_NET, PEEL_*, MIN_ROUTE_SCORE/EDGES — NEW: MIN_ROUTE_BLOCKS = 5", True),
            ("Corridor length budget", "routeLengthBudgetM — 2700 + 660·√score, cap 10500 (unchanged)", False),
            ("Loop-back splitting", "NEW: splitLoopyPath — window rule (hairpins) + endpoint rule (U-turns), recursive", True),
            ("capPathToLengthBudget", "best-weight window within the meter budget (unchanged)", False),
            ("netsByType / buildTypeAdj / components / peeling", "per-type net-positive subgraph → heaviest simple paths (unchanged)", False),
            ("createRouteProposalJob · step()", "peel → SPLIT → cap → gates (score, edges, NEW blocks) → blocks → dedupe", True),
        ],
        "blocks": [
            "MIN_ROUTE_BLOCKS = 5 — the min-distance gate, in BLOCK units (what the UI selects/votes)",
            "ROUTE_STRAIGHTNESS_MIN 0.55 / ROUTE_WINDOW_M 800 / ROUTE_WINDOW_STRAIGHTNESS_MIN 0.4 / ROUTE_SPLIT_MAX_DEPTH 6",
            "splitLoopyPath() — planar-meter coords, worst-window scan, apex split, weaker-half split, recursion",
            "RouteProposalOptions.minRouteBlocks — override for tests/tuning",
            "step(): splitLoopyPath around the cap; fragments earn their own budget; blocks.length gate after groupBlocks",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · route proposals (RBTP)", "unit tests for the clustering pipeline (ported from the old server suite)"),
        "file": ("routeProposals.test.ts", "~850 LOC — wire/coverage/pipeline suites on micro-graphs"),
        "outline": [
            ("Wire / shape / coverage suites", "unchanged", False),
            ("compute() helper", "now pins minRouteBlocks: 1 — micro-graphs probe mechanics, not the gate", True),
            ("Clustering suites (nets, peeling, dedupe, budget)", "unchanged, all still pass", False),
            ("min-blocks gate suite", "NEW: default drops < 5 blocks; counts BLOCKS not edges; override works", True),
            ("splitLoopyPath suite", "NEW: straight/hairpin/buried-detour/weights/short/determinism", True),
            ("U-corridor integration test", "NEW: a hot U on a real topology becomes ≥ 2 leg proposals", True),
        ],
        "blocks": [
            "compute() pins minRouteBlocks: 1 (single spot — 53 existing tests unchanged)",
            "computeDefault() — direct call so the REAL 5-block default is what's tested",
            "mkPath() — synthetic meter-space paths with injectable per-edge weights",
            "hairpin / buried-detour geometry fixtures with fragment-partition assertions",
        ],
    },
    "client-react/scripts/routepropStats.ts": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("diagnostics · route-proposal stats", "runs the real client pipeline against a live local Flask and prints shape distributions"),
        "file": ("routepropStats.ts", "~140 LOC — fetch GTB2 + votes, computeRouteProposals, per-proposal metrics"),
        "outline": [
            ("Metric helpers", "haversine, endpoint straightness, worst-800 m-window straightness", True),
            ("Fetch + decode", "?format=bin GTB2 → decodeTopologyBin; /api/graph-votes; map kindOf from /api/maps", True),
            ("Run + report", "console.table per proposal + min/med/p90/max + threshold counts", True),
        ],
        "blocks": [
            "the whole file is new — vite-node script, no app code imported beyond the pure pipeline modules",
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
  table.stats {{ border-collapse: collapse; margin: 10px 0 2px; font-size: 13.5px; }}
  table.stats th, table.stats td {{ border: 1px solid var(--hairline); padding: 6px 10px; text-align: left; }}
  table.stats th {{ font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); background: #f7f5ef; }}
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

  <p class="lede">Yesterday's 3× corridor length budget (<code>cf1d576</code>) exposed what the greedy
  peel always did: it extends by weight with no regard for direction, so on the imported-data maps the
  top route proposals shipped as multi-kilometre snakes — 2 of nyc-bikes' top-20 ended ~2 km from their
  start after 9–10 km, and 6 more hid an 800 m hairpin — while other maps still surfaced 100 m,
  3-block stubs as route pins. No hard max distance is added (long corridors are the point). Instead the
  pipeline becomes shape-aware: a new <code>splitLoopyPath()</code> splits every peeled path where it
  turns back on itself (a sliding-window rule for buried hairpins, an endpoint rule for U-turns), each
  fragment earns its own support-based length budget, and a new <code>MIN_ROUTE_BLOCKS = 5</code> gate
  drops corridors that don't span at least 5 blocks (their votes still show as point pins). Verified
  against real data with a new vite-node stats harness: 0/20 loopy corridors on nyc-bikes (was 8/20 by
  either measure), stubs gone, determinism contract intact, compute time unchanged.</p>

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
    Generated from <code>changelog/changes-routeprop.diff</code> by <code>changelog/build_routeprop_report.py</code>.
    Regenerate after further edits with
    <code>git diff -- client-react/src/components/GraphLayer/routeProposals.ts client-react/src/components/GraphLayer/routeProposals.test.ts client-react/scripts/routepropStats.ts &gt; changelog/changes-routeprop.diff &amp;&amp; python changelog/build_routeprop_report.py</code>.
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
