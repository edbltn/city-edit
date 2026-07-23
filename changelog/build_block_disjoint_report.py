#!/usr/bin/env python3
"""Generate the all-class block-disjointness changelog report (2026-07-22).

Run from repo root: python changelog/build_block_disjoint_report.py
Reads changelog/changes-block-disjoint.diff (captured with:
  git diff a720309~1 a720309 -- server/streetscape_blocks/build_blocks_graph_first.py \
    server/streetscape_blocks/scan_overlaps.py > changelog/changes-block-disjoint.diff),
writes changelog/2026-07-22-block-disjoint-all-classes.html

Modeled on build_junction_disjoint_report.py (same styles + context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-block-disjoint.diff")
OUT_PATH = os.path.join(HERE, "2026-07-22-block-disjoint-all-classes.html")

DATE = "2026-07-22"
TITLE = "Blocks never overlap, for real this time — corridor cuts, ship-frame audit, all-class guarantee"


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
  <thead><tr><th>bake</th><th>CC pairs</th><th>JJ pairs</th><th>CJ pairs</th><th>invalid geoms</th></tr></thead>
  <tbody>
    <tr><td>test-mid (pre-fix, Jul 9)</td><td>3,345 (max 4,660 m²)</td><td>126</td><td>0</td><td>189</td></tr>
    <tr><td>test-cp (post-07-13 junction fix!)</td><td>3,838 (max 4,113 m²)</td><td>0</td><td>1</td><td>239</td></tr>
    <tr><td><strong>every city, new builder</strong></td><td><strong>0</strong></td><td><strong>0</strong></td><td><strong>0</strong></td><td><strong>0</strong></td></tr>
  </tbody>
</table>
"""

SECTIONS = [
    {
        "id": "diagnosis",
        "tag": "Diagnosis · every city",
        "title": "1 · Why blocks still overlapped after the July-13 fix",
        "symptom": (
            "Prod still showed stacked block polygons (reported at 7th Ave / W 53rd on nyc-bikes: two "
            "polygons under one click, the lower one impossible to select). The 2026-07-13 "
            "junction-disjoint fix was assumed to have ended overlaps."
        ),
        "cause": [
            "The July-13 fix made JUNCTION-vs-JUNCTION cells disjoint — and only measured junction "
            "pairs. <strong>Corridor-vs-corridor overlap was never handled anywhere</strong>: two "
            "corridors' tubes overlap wherever distinct streets run closer than the sum of their "
            "half-widths — parallel service roads and footways, grade-separated crossings (no shared "
            "node → no junction cell between them), wide-class buffers (motorway 18 m) spilling over "
            "a neighbouring path. Thousands of pairs per city.",
            "Junction tube GRAFTS (the last-resort patch for a stranded captured edge) were clipped "
            "against neighbour cells but NOT against corridors — a small corridor-vs-junction leak.",
            "~200–700 polygons per city shipped <strong>invalid</strong> (self-touching rings from "
            "difference chains). A consumer running make_valid can inflate a self-crossed ring into a "
            "large false lobe — my own scanner measured a 1,636 m² phantom overlap that way.",
            "Numbers (independent geojson scan):" + BASELINE_TABLE,
        ],
        "fixes": [],
        "files": [],
    },
    {
        "id": "corridors",
        "tag": "Builder · corridor disjointness",
        "title": "2 · Corridor overlaps: claimed by the street that runs there, cut from the other, merged when swallowed",
        "symptom": (
            "Corridor tubes are built independently per corridor (union of member-edge buffers minus "
            "junction cells) — nothing ever compared two corridors."
        ),
        "cause": [
            "The builder's own “geometry wins” rule (from the junction trim) had never been extended "
            "to the corridor-corridor class.",
        ],
        "fixes": [
            "New corridor-disjointness sweep, before membership reassignment: every overlap region "
            "≥ 1 m² is <strong>claimed by the corridor whose own member edges carry more length "
            "inside it</strong> (its street actually runs there; tie → longer street, then lower id) "
            "and cut from the other. A loser left with &lt; 1 m² is geometrically a duplicate and "
            "<strong>merges into the winner</strong> (members + geometry) — the requested merge "
            "behaviour, applied exactly where merging is right (containment), while crossings get a "
            "cut, not a merge (an overpass must not fuse with the street below it).",
            "Cuts only shrink and merges only union, so the sweep converges (≤ 4 mutating sweeps + a "
            "measure-only one). NYC: 209,876 cuts, 5,993 duplicate corridors merged.",
            "Junction tube grafts now clip against corridors too; corridor members stranded by cuts "
            "re-home to the polygon that holds their midpoint (cell first, then corridor), clipped "
            "tube graft as last resort.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "shipframe",
        "tag": "Builder · ship-frame finalize",
        "title": "3 · The disjointness contract is enforced in the frame that ships",
        "symptom": (
            "With the metres-frame pipeline fully clean (0 pairs), the EMITTED lon/lat geojson still "
            "scanned dirty: hundreds of invalid rings and threshold-grazing overlaps that the "
            "builder's own audit could not see."
        ),
        "cause": [
            "Geometry is built in a local equirectangular metres frame and transformed to lon/lat at "
            "emit. Cut seams carry near-coincident collinear vertices; under the frame scaling their "
            "relative float error grows and rings self-cross — polygons flip invalid IN TRANSIT "
            "(696 on test-mid, 41,545 on nyc). An audit in the build frame certifies geometry nobody "
            "ships.",
        ],
        "fixes": [
            "Finalization moved into the shipped frame: transform FIRST, snap every coordinate to a "
            "1e-7° grid (≈ 1 cm — shared cut seams land on identical grid points), make_valid "
            "(hardened: strip to polygonal parts before make_valid, which throws on mixed-dimension "
            "collections; buffer(0) fallback), THEN measure ALL-CLASS pairwise overlap and repair "
            "residual pairs (junction beats corridor, else larger area keeps; repair at ≥ 0.5 m² so "
            "the ≥ 1 m² promise holds under any external measuring frame).",
            "Global ship-frame membership re-home: any member edge whose line no longer touches its "
            "polygon (cuts can strand members up to 23 m away) moves to the polygon holding its "
            "midpoint. Coverage and edge∩polygon audits stay 100%.",
            "Meta: <code>residual_overlap_pairs</code> is now the ALL-CLASS count plus "
            "<code>residual_overlap_detail</code> {CC, CJ, JJ}, <code>corridor_cuts</code>, "
            "<code>corridor_duplicates_merged</code>, <code>invalid_geoms_fixed</code>.",
            "New <code>scan_overlaps.py</code>: independent post-hoc scanner over an emitted "
            "blocks_final geojson — classified pair counts, worst offenders with coordinates. The "
            "verifier that caught both the transform-invalid flip and the threshold-grazing pairs.",
        ],
        "files": ["server/streetscape_blocks/scan_overlaps.py"],
    },
    {
        "id": "ship",
        "tag": "Deploy · all seven cities",
        "title": "4 · Re-baked everywhere, shipped via the artifacts-only overlay",
        "symptom": (
            "Only nyc had ever been re-baked since the junction fix; sf/dc/chicago/philly prod bakes "
            "predated even that. Four cities' prod graph vintages differ from local, so their bakes "
            "must be against the SERVING image's own graphs."
        ),
        "cause": [
            "Blocks are stamped with the graph topology etag; graph_registry hard-rejects a mismatch.",
        ],
        "fixes": [
            "All 7 cities re-baked with the new builder. nyc/sf/dc/chicago baked twice: once against "
            "local graphs (dev) and once against graphs docker-cp'd out of the digest-pinned serving "
            "image (ship), with an etag gate refusing to stage a mismatched bake.",
            "Shipped with Dockerfile.blocks-artifacts-overlay (digest-pinned base + block artifacts "
            "only) — NOT a full Cloud Build, which would rebuild the graphs in-image, shift every "
            "edge id/etag, invalidate these very bakes, and force a prod-wide vote resnap.",
            "Prod DB backed up first (~/city-edit-prod-backups/, 32 MB dump), per the standing rule.",
        ],
        "files": [],
    },
]

VERIFY = [
    "Independent scanner on every new bake: 0 overlapping pairs ≥ 1 m² (all classes), 0 invalid "
    "geometries — test-mid, test-cp, sf, philly, dc, chicago, nyc.",
    "Coverage audits: 100% mapped / 100% edge∩polygon everywhere (nyc 3,299,040/3,299,152 mapped "
    "= 100.00%; the tail is members of polygons emptied by ship-frame repair, left visible in meta).",
    "The reported prod URLs re-checked against the new bake: 287 blocks in a ±400 m window around "
    "7th Ave / W 53rd, 0 overlapping pairs; the two click points resolve to exactly one block each "
    "(footway block + West 53rd Street roadway block).",
    "Live app check (local dev, docker OSRM + host Flask + Vite): [topo] ready with 284,281 blocks "
    "(the new bake), [blocks] select: [200072] and [494] at the two formerly-stacked URLs — one "
    "unambiguous block per click.",
    "The builder's own final ship-frame audit prints and stamps 0 pairs; the external scan agrees "
    "because repair runs at ≥ 0.5 m² (hysteresis absorbs measuring-frame differences).",
]

CHECKLIST = [
    "Open the two originally-reported prod URLs (nyc-bikes, 7th Ave / W 53rd): the two selections "
    "should ring two DIFFERENT single blocks — no stacked polygon, both clickable.",
    "Pan dense areas on each city map (sf-bike-lanes, chicago-bikes, dc-bikes, philly-bikes): "
    "parallel street/sidewalk tubes should abut with a seam, never double-tint.",
    "Check a grade-separated crossing (e.g. a park overpass): bridge and street below should be "
    "separate blocks with a clean cut, not merged.",
    "Cast a test vote on each re-baked map and confirm the block lights and the count sticks "
    "(bagg rebuilds on first hit after blocks_version changes).",
    "If any map shows an empty heatmap + revision-mismatch loop after deploy, bump "
    "vote_rev:&lt;slug&gt; in prod Redis (the etag/body 304-desync trap).",
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
    fixes_h = f"<h3>What changed</h3><ul>{li(s['fixes'])}</ul>" if s["fixes"] else ""
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Symptom</h3>
      <p>{s['symptom']}</p>
      <h3>Root cause</h3>
      <ul>{li(s['cause'])}</ul>
      {fixes_h}
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · THE builder", "one pass: topological grouping → polygons from the groups → baked mapping"),
        "file": ("build_blocks_graph_first.py", "~1000 LOC — clusters, grouping, fixpoint, geometry, disjointness, ship-frame audit, emit"),
        "outline": [
            ("Docstring + _clean/_polyparts", "hardened validity helpers (mixed-dimension make_valid throw)", True),
            ("1-3 · clusters, grouping, fixpoint", "unchanged", False),
            ("4 · junction cells + Voronoi trim", "unchanged (unconditional cuts since 07-13)", False),
            ("4 · corridor tubes", "minus junction cells (unchanged)", False),
            ("4 · NEW corridor disjointness sweep", "claim by member length in region → cut; <1 m² loser merges; fixpoint", True),
            ("4 · membership reassignment", "extended: midpoint → cell OR corridor; clipped corridor graft", True),
            ("4 · junction re-home + grafts", "grafts now clip against corridors too", True),
            ("NEW ship-frame finalize", "to_ll FIRST → 1e-7° snap → make_valid → all-class audit → repair (≥0.5 m²)", True),
            ("NEW global ship-frame re-home", "strays (up to 23 m after cuts) move to their holding polygon", True),
            ("5-6 · emit + audit", "geometry already in ll; touch measured in ship frame; all-class meta", True),
        ],
        "blocks": [
            "_polyparts/_clean — strip to polygons BEFORE make_valid; buffer(0) fallback",
            "corridor sweep — member_lines claim, merge_corridor, ≤4 sweeps + measure",
            "graft clipping vs corridors (the CJ leak)",
            "_finalize — transform, set_precision(1e-7°) with re-validate retry",
            "audit/repair rounds — J beats C, larger area keeps; repaired members re-homed",
            "meta — residual_overlap_pairs now ALL-CLASS + detail {CC,CJ,JJ} + invalid_geoms_fixed",
        ],
    },
    "server/streetscape_blocks/scan_overlaps.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · independent verifier", "post-hoc scan of an emitted blocks_final geojson"),
        "file": ("scan_overlaps.py", "~90 LOC — STRtree pairwise intersection, classified counts, worst offenders"),
        "outline": [
            ("load + make_valid count", "invalid features are themselves a finding", True),
            ("pairwise scan", "≥1 m² pairs by class (CC/CJ/JJ), median/max areas", True),
            ("worst offenders", "areas + block ids + lat/lon for eyeballing", True),
        ],
        "blocks": ["the whole file is new — usage: python scan_overlaps.py <geojson> [...]"],
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

  <p class="lede">Blocks still overlapped in prod (reported at 7th Ave / W 53rd on nyc-bikes) because the
  July-13 disjointness fix only covered junction-vs-junction cells: corridor-vs-corridor overlap was
  never handled at all — thousands of pairs per city wherever parallel streets, footways, or
  grade-separated crossings sit inside each other's tube buffers — and an overlapped block cannot be
  clicked. The builder now extends its own geometry-wins rule to every pair class (overlap regions go
  to the corridor whose member edges actually run there; fully-swallowed corridors merge into the
  winner), and — after discovering that polygons were flipping INVALID in the metres→degrees transform
  — enforces the whole contract in the frame that ships: transform, snap to a 1 cm grid, validate,
  audit every polygon pair of every class, repair the stragglers. Every city re-baked: 0 overlapping
  pairs ≥ 1 m², 0 invalid geometries, 100% coverage, independently re-scanned. Shipped to prod for all
  seven cities via the artifacts-only overlay (bakes for nyc/sf/dc/chicago made against the serving
  image's own graphs, etag-gated).</p>

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
    Generated from <code>changelog/changes-block-disjoint.diff</code> by <code>changelog/build_block_disjoint_report.py</code>.
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
