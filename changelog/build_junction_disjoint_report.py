#!/usr/bin/env python3
"""Generate the disjoint-junction-cells changelog report (2026-07-13).

Run from repo root: python changelog/build_junction_disjoint_report.py
Reads changelog/changes-junction-disjoint.diff (captured with:
  git diff 1f5f782 8fb0df3 -- server/streetscape_blocks/build_blocks_graph_first.py \
    Dockerfile.blocks-artifacts-overlay > changelog/changes-junction-disjoint.diff),
writes changelog/2026-07-13-junction-disjoint-blocks.html

Modeled on build_graph_first_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-junction-disjoint.diff")
OUT_PATH = os.path.join(HERE, "2026-07-13-junction-disjoint-blocks.html")

DATE = "2026-07-13"
TITLE = "Disjoint junction cells — the Voronoi trim stops asking permission, membership follows the cut"


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
        "id": "diagnosis",
        "tag": "Diagnosis · prod NYC",
        "title": "1 · Why intersection blocks overlapped in prod",
        "symptom": (
            "On the production NYC maps some intersection blocks visibly stack — two junction "
            "polygons drawn on top of each other, double-tinted, outlines crossing. Blocks were "
            "assumed to be “voronoi'd” into a partition."
        ),
        "cause": [
            "Blocks were never a global Voronoi partition: a junction cell is the convex hull of its "
            "member nodes (⊕ 8 m) ∪ its captured edges' tubes, and the only Voronoi-like step is a "
            "pairwise bisector trim between overlapping junction cells.",
            "That trim was <strong>vetoed</strong> whenever the cut would evict a member node or detach a "
            "captured edge (“the audit invariants win over aesthetics”). Big multi-node intersections "
            "always have some captured crosswalk/driveway stub past the bisector, so both directions of "
            "the cut got vetoed and the full pre-trim overlap shipped.",
            "Quantified in the then-shipped NYC bake (scan of <code>blocks_final_nyc.geojson</code>): "
            "<strong>4,880 junction-cell pairs overlapped by ≥ 1 m²</strong> — median 179 m², max "
            "~800 m² — involving 8,164 cells, 10.2% of the 80,199 junction cells. In ~3,800 pairs "
            "<em>neither</em> side had been cut.",
            "Not bikes-specific: blocks are baked per city+network and only <code>nyc:streets</code> "
            "has a bake, so every NYC streets map shares the same polygons. The vote model was never "
            "affected — <code>edge_blocks_streets.npy</code> maps each edge to exactly one block; the "
            "overlap was purely visual.",
        ],
        "fixes": [
            "Diagnosis only in this section — the scan lives at "
            "<code>scratchpad/diag_junction_overlaps.py</code> (session scratch; parameterized by "
            "<code>CITY</code>, streams the geojson, STRtree pairwise intersection areas, bisector-side "
            "classification).",
        ],
        "files": [],
    },
    {
        "id": "fix",
        "tag": "Pipeline · unconditional trim",
        "title": "2 · Cuts apply unconditionally; stranded membership is re-homed, not protected",
        "symptom": (
            "The veto protected the 100% edge∩polygon audit by refusing cuts — geometry was distorted "
            "to preserve membership, exactly opposite to the builder's own “membership follows the final "
            "geometry” rule used for corridor edges eaten by junction cells."
        ),
        "cause": [
            "The veto predates the corridor-edge reassignment pass. Junction-vs-junction was the one "
            "place where membership still overruled geometry.",
        ],
        "fixes": [
            "<strong>Unconditional bisector cuts</strong>: where two junction cells overlap by ≥ 1 m², "
            "each is cut at the perpendicular bisector of the two clusters' member centroids — no "
            "member/edge veto. A cell left with &lt; 1 m² on its own side of a bisector is geometrically "
            "one intersection with that neighbour and is <strong>absorbed</strong> into it (members, "
            "captured edges, geometry). Absorption grows the survivor, so the trim sweeps to a fixpoint "
            "(≤ 4 processing sweeps + a measure-only sweep; 2 suffice in practice).",
            "<strong>Stranded-edge re-home</strong> (new pass, after corridor geometry exists): a captured "
            "edge left outside its now-smaller cell moves to the cell or corridor containing its midpoint, "
            "falling back to any cell the line still touches — the same membership-follows-geometry rule "
            "corridor edges already used. 4,299 edges re-homed on the shipped NYC bake.",
            "<strong>Clipped tube grafts</strong>: an edge NO polygon holds gets its tube grafted back "
            "onto its own cell, clipped against neighbour cells (safe — no cell touches the line, else it "
            "would have re-homed), so a graft cannot re-stack junctions. 60 grafts on NYC; the residual "
            "overlap count is re-measured post-graft and stamped into the meta.",
            "Meta gains <code>cells_absorbed</code>, <code>residual_overlap_pairs</code>, "
            "<code>junction_edges_rehomed</code>, <code>junction_tube_grafts</code>.",
            "Results: test-cp — 869 cuts, 92 re-homed, 2 grafts, <strong>0 overlapping pairs</strong>, "
            "audit 100%/100%. NYC (against the serving image's graph, etag <code>b0ca56f8…</code>) — "
            "45k+ cuts, <strong>residual_overlap_pairs 0</strong>, audit 100% mapped / 100% "
            "edge∩polygon, independently confirmed by the geojson scan.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "api",
        "tag": "Pipeline · array-native CityGraph",
        "title": "3 · The builder works again on the compact-arrays graph runtime",
        "symptom": (
            "The builder crashed on load — <code>AttributeError: 'CityGraph' object has no attribute "
            "'nodes'</code> — for ANY city, before any block logic ran."
        ),
        "cause": [
            "The compact-arrays refactor (<code>9962396</code>) made CityGraph array-native "
            "(<code>node_coords</code>/<code>edge_ends</code>, per-edge name/highway no longer retained) "
            "and the builder was not adapted — a pre-existing break at HEAD, surfaced by this re-bake.",
        ],
        "fixes": [
            "Nodes/edges come from <code>g.node_coords</code>/<code>g.edge_ends</code>; per-edge "
            "road-class + name are re-read from <code>g.provider.get_graph_for_bbox(*city.bbox)</code> — "
            "the exact output the topology (and its etag) is built from, so edge order is the etag "
            "contract's order (length-mismatch guard included). Station networks keep working via "
            "<code>load_station_graph</code>.",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "deploy",
        "tag": "Deploy · artifacts-only overlay",
        "title": "4 · Shipping blocks while another agent ships code",
        "symptom": (
            "Prod moved mid-flight: the serving image gained a NEW NYC graph (etag "
            "<code>b0ca56f8…</code>, 3,305,042 edges) baked by parallel deploys the same afternoon, and "
            "the working tree carried another agent's uncommitted server edits that must not ship."
        ),
        "cause": [
            "A blocks bake is only valid against the exact graph the serving image loads — "
            "<code>graph_registry</code> hard-rejects a mapping whose stamped topology_etag doesn't match. "
            "And <code>Dockerfile.blocks-overlay</code> copies <code>server/*.py</code> + a fresh client "
            "build, which would sweep in-flight work into prod.",
        ],
        "fixes": [
            "New <code>Dockerfile.blocks-artifacts-overlay</code>: FROM the digest-pinned serving image, "
            "<code>COPY .blocks-staging/ ./osm_data/</code> — nothing else. The most surgical deploy the "
            "repo has.",
            "Procedure: pull the serving revision's image digest → <code>docker cp</code> its "
            "<code>osm_data/nyc</code> graph out → swap it under the local bake (local graph + bake "
            "backed up and restored after) → re-bake + tippecanoe → stage "
            "<code>.blocks-staging/nyc/</code> → build the overlay pinned to that digest → "
            "<code>gcloud run services update</code>.",
            "Prod DB snapshot taken first (<code>~/city-edit-prod-backups/20260713-234529/</code>, "
            "24 MB dump + sql.gz), per the always-backup rule.",
        ],
        "files": ["Dockerfile.blocks-artifacts-overlay"],
    },
]

VERIFY = [
    "Old-bake scan (the diagnosis): 4,880 pairs ≥ 1 m² / 8,164 cells (10.18%) / median 179 m², "
    "p90 322 m², max 797 m²; worst example blocks 282231/282232 at 40.644825, −73.974653.",
    "test-cp re-bake: 869 bisector cuts, 0 absorbed, 92 stranded edges re-homed, 2 clipped grafts — "
    "geojson scan: <strong>0 pairs ≥ 1 m²</strong> (was 3 before graft clipping); audit 72,924/72,924 "
    "mapped, 72,924/72,924 touching (100%/100%).",
    "NYC re-bake against the local graph (etag <code>a89beee7…</code>): 45,780 cuts, 4,299 re-homed, "
    "60 grafts, <code>residual_overlap_pairs 0</code>; independent scan of 290,138 features: "
    "<strong>0 overlapping pairs</strong> (14,529 pairs touch — shared Voronoi boundaries, as designed).",
    "NYC ship bake against the SERVING image's graph (etag <code>b0ca56f8…</code>, 3,305,042 edges — "
    "prod gained a new graph from parallel deploys the same afternoon, so the local-graph bake could not "
    "ship): 45,814 cuts, 4,339 re-homed, 63 clipped grafts, <code>residual_overlap_pairs 0</code>, "
    "audit 3,305,042/3,305,042 mapped + touching (100%/100%), blocks_sha256 <code>720da8e4…</code>, "
    "n_blocks 290,513; independent scan: <strong>0 pairs ≥ 1 m²</strong> across 80,308 cells "
    "(14,542 touching pairs — shared boundaries).",
    "Prod DB backed up first (<code>~/city-edit-prod-backups/20260713-234529/</code>). Deployed as "
    "revision <code>desire-path-mapper-00095-bj8</code> (100% traffic), overlay image "
    "<code>sha256:3501050b…</code> pinned on base <code>sha256:585184f1…</code> (revision 00094's exact "
    "digest, re-checked for drift just before the build; no Cloud Builds in flight).",
    "Post-deploy: <code>/api/graph-votes?map=nyc-walkways</code> serves n_blocks 290,513 + "
    "blocks_version <code>720da8e4…</code> with topology_version unchanged "
    "<code>b0ca56f8…</code> (no resnap needed); <code>/api/tiles/nyc/blocks.pmtiles</code> "
    "content-length 83,237,399 == the staged artifact byte-for-byte, mtime of the deploy.",
]

CHECKLIST = [
    "Open the prod NYC map at 40.644825, −73.974653 (the worst former overlap): the two intersection "
    "blocks should now share a straight boundary instead of stacking.",
    "Pan a few dense Manhattan intersections at z17–18: junction polygons should abut, never "
    "double-tint; corridor tubes unchanged.",
    "Click inside a former overlap zone: exactly one block rings, and the pinned card's block matches "
    "the polygon under the cursor.",
    "Cast a test vote on an NYC streets map: the block lights, and <code>/api/graph-votes?map=…</code> "
    "returns the new <code>blocks_version</code> with <code>n_blocks</code> matching the new bake.",
    "If any map shows an empty heatmap + revision-mismatch loop after the deploy, bump "
    "<code>vote_rev:&lt;slug&gt;</code> in prod Redis (the etag/body 304-desync trap).",
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
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "on": ["Flask API"],
        "module": ("blocks pipeline · THE builder", "one pass: topological grouping → polygons from the groups → baked mapping"),
        "file": ("build_blocks_graph_first.py", "~700 LOC — clusters, grouping, fixpoint, geometry, audit, emit"),
        "outline": [
            ("Docstring + imports", "stage-4 description rewritten; STATION_NETWORKS/load_station_graph imported", True),
            ("Graph load", "array-native CityGraph: node_coords/edge_ends + provider re-read for name/class", True),
            ("1 · junction clusters", "deg≥3 union-find within 18m (unchanged)", False),
            ("2 · edge grouping (total)", "same-cluster · ≤30m crosswalk stubs · corridors (unchanged)", False),
            ("3 · degeneracy fixpoint", "A/V1/V2/B rules (unchanged)", False),
            ("4 · geometry — Voronoi trim", "UNCONDITIONAL cuts · absorb-on-sliver · sweep to fixpoint · compaction", True),
            ("4 · geometry — corridor tubes", "minus junction cells; corridor-edge reassignment (unchanged)", False),
            ("4 · geometry — stranded re-home", "NEW pass: cell/corridor by midpoint → line-touch → clipped tube graft", True),
            ("5 · emit + meta", "cells_absorbed / residual_overlap_pairs / junction_edges_rehomed / junction_tube_grafts", True),
            ("6 · audit", "100% mapped + 100% edge∩polygon (unchanged, still passes)", False),
        ],
        "blocks": [
            "graph load — g.node_coords/g.edge_ends + provider.get_graph_for_bbox for rclass/names (etag-order contract)",
            "absorb(i, j) — fold a sliver cell's members/edges/geometry into its neighbour",
            "trim loop — unconditional cut at the member-centroid bisector; ≤4 sweeps + measure-only sweep",
            "re-home pass — stranded captured edges → midpoint cell/corridor → line-touch cell → clipped graft",
            "post-graft residual re-measure — meta reflects the shipped polygons",
        ],
    },
    "Dockerfile.blocks-artifacts-overlay": {
        "on": ["nginx", "Flask API"],
        "module": ("deploy · overlay images", "surgical prod deploys that reuse the serving image's baked graphs"),
        "file": ("Dockerfile.blocks-artifacts-overlay", "21 lines — FROM pinned base, COPY .blocks-staging/ only"),
        "outline": [
            ("Header contract", "artifacts must be baked against the BASE IMAGE's own graphs", True),
            ("FROM ${BASE_IMAGE}", "digest-pinned serving image — code/client/nginx untouched", True),
            ("COPY .blocks-staging/", "per-city edge_blocks npy/json + blocks.pmtiles over osm_data", True),
        ],
        "blocks": [
            "the whole file is new — the artifacts-only sibling of Dockerfile.blocks-overlay",
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

  <p class="lede">Some prod NYC intersection blocks rendered stacked on top of each other. Root cause: the
  graph-first builder's Voronoi trim between overlapping junction cells was vetoed whenever a cut would
  evict a member node or detach a captured edge — and at big multi-node intersections something always
  crossed the bisector, so 4,880 junction pairs (10% of NYC's cells) shipped overlapping. The fix inverts
  the priority, matching the builder's own corridor rule: geometry wins — cuts apply unconditionally,
  sliver cells are absorbed into their neighbour — and stranded membership is re-homed to whichever
  polygon actually holds it now (midpoint cell/corridor, line-touch fallback, clipped tube graft last).
  Result: zero overlapping junction pairs on test-cp and NYC, audit still 100% mapped / 100%
  edge∩polygon. Shipped to prod with a new artifacts-only overlay image (digest-pinned base, block
  artifacts only) because another agent was deploying code the same afternoon — their new NYC graph is
  exactly what the shipped bake is stamped against.</p>

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
    Generated from <code>changelog/changes-junction-disjoint.diff</code> by <code>changelog/build_junction_disjoint_report.py</code>.
    Regenerate after further edits with
    <code>git diff 1f5f782 8fb0df3 -- server/streetscape_blocks/build_blocks_graph_first.py Dockerfile.blocks-artifacts-overlay &gt; changelog/changes-junction-disjoint.diff &amp;&amp; python changelog/build_junction_disjoint_report.py</code>.
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
