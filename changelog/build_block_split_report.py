#!/usr/bin/env python3
"""Generate the 2026-07-29 oversized-block thinnest-cut split report.

Run from repo root: python changelog/build_block_split_report.py
Reads changelog/changes-block-split.diff, writes
changelog/2026-07-29-oversized-block-split.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-block-split.diff")
OUT_PATH = os.path.join(HERE, "2026-07-29-oversized-block-split.html")

DATE = "2026-07-29"
TITLE = "Oversized blocks cut at their thinnest crossing — Central Park's 4.3 km vote target becomes street-scale pieces"

PILLS = ["nginx", "Flask API", "OSRM", "Redis", "React/Leaflet client"]

FILE_CONTEXT = {
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "system": "Flask API",
        "module": ("streetscape_blocks — the Layer-2 graph-first block builder: edges grouped "
                   "topologically (junction clusters + corridors), polygons generated FROM the "
                   "groups, disjointness enforced by construction + audits"),
        "file": ("one-pass builder: junction clustering → edge grouping → merge fixpoint → "
                 "geometry → disjointness → ship-frame audit"),
        "loc": 1200,
        "map": [
            ("knobs (cluster/stub/width)", True),
            ("UnionFind / split_oversized / _clean helpers", False),
            ("§1 junction clusters", False),
            ("§2 edge grouping", False),
            ("§3 degeneracy + equivalence fixpoint", False),
            ("§4 geometry from membership (trim, corridor disjointness, re-home)", False),
            ("§4b oversized-corridor split (NEW)", True),
            ("ship-frame finalize + audit + re-home", False),
            ("§5 emit + §6 audit + meta", True),
        ],
        "blocks": [
            ("SPLIT_* knobs", "SPLIT_MAX_EXTENT_M=400 (env-overridable; ≈p99.5 of the nyc "
             "corridor-extent distribution — a long Manhattan block face is ~280 m), middle-band "
             "0.35–0.65, 13 sampled stations, depth cap 8"),
            ("§4b split pass", "corridors whose bbox diagonal exceeds the threshold are cut "
             "recursively along the thinnest crossing: PCA principal axis from the polygon's "
             "exterior rings, perpendicular chord sampled at 13 stations across the middle band, "
             "narrowest measured width wins (a gap between parts measures 0 and splits free); "
             "member edges follow their midpoints, member-less pieces melt into the nearest "
             "member-holding piece; pieces meet only along the cut line so every disjointness "
             "audit downstream is unaffected"),
            ("meta stamp", "oversized_blocks_split / split_pieces / split_gaveup / "
             "split_max_extent_m recorded in edge_blocks_<network>.json"),
        ],
    },
}

NARRATIVE = """
<h2>What happened</h2>
<p>On <b>/m/nyc-intersections</b> (city <code>nyc</code>, network <code>streets</code>) the
merge rules in the graph-first builder sometimes produced <b>enormous blocks</b> — reported at
the Central Park reservoir, where the running-track loop and its feeder paths fused into one
corridor with a <b>4.3&nbsp;km bounding-box diagonal</b> (264 edges, 52k&nbsp;m² — block
<code>56216</code> in the 07-27 bake). One hover lit kilometres of park; one vote covered all
of it.</p>
<p>Root cause: the §3 fixpoint's merge rules have <b>no size cap</b>. Rule&nbsp;A merges
corridors sharing the same two endpoint junctions — exactly what a park loop's two arms do —
and a big connected path network between few junctions is already ONE corridor component by
construction. NYC had <b>1,013 corridors over 400&nbsp;m</b> (p99 of the corridor-extent
distribution is ~319&nbsp;m): greenways, bridge paths, park loops.</p>
<h2>The fix — §4b, cut at the thinnest crossing</h2>
<p>A new pass after all disjointness/membership passes (still in the metric frame, before the
ship-frame finalize): any corridor whose bbox diagonal exceeds
<code>SPLIT_MAX_EXTENT_M</code> (default <b>400&nbsp;m</b>) is cut in two along its
<b>thinnest crossing</b> — the perpendicular to its PCA principal axis, placed at whichever of
13 stations across the middle band (35–65% of the span) measures the least polygon width
(length of the polygon∩chord intersection; a gap between disconnected parts measures 0 and
splits free). Each half is re-checked recursively (depth cap 8). Member edges follow their
midpoints; a piece holding no members melts into the nearest member-holding piece — an empty
piece could never hold or display a vote. Pieces meet only along the cut line (zero shared
area), so the all-class pairwise disjointness audit and the coverage audit downstream are
unaffected.</p>
<h2>Results</h2>
<ul>
<li><b>test-cp</b>: 15 corridors → 56 pieces, 0 give-ups; ship-frame audit 0 overlapping
pairs; 100% coverage, 100% member-edge∩polygon touch. Verified visually on
<code>/m/test-central-park</code> — the reservoir ring renders as a chain of street-scale
blocks with clean seams.</li>
<li><b>nyc (serving-graph bake, etag <code>b0ca56f8…</code>)</b>: 1,021 corridors → 2,452
pieces, 0 give-ups (525&nbsp;s into the 883&nbsp;s bake); 286,102 blocks emitted; ship-frame
audit <code>residual_overlap_pairs: 0</code>; 3,304,932/3,304,932 member edges touch their
polygon (100%). The 4,350&nbsp;m Central Park reservoir corridor is gone — the reservoir area
now has <b>zero</b> corridors over 400&nbsp;m. Citywide the &gt;400&nbsp;m tail dropped
1,013&nbsp;→&nbsp;74 (max 4,350&nbsp;→&nbsp;907&nbsp;m); the stragglers are 2-edge corridors
whose single OSM edge spans 400–900&nbsp;m (bridge spans, parkway stretches) — an edge belongs
to exactly one block, so the member-less half melts back by design and edge granularity is the
split floor.</li>
</ul>
<h2>Ship</h2>
<p>Baked against the serving image's own graph (<code>walk_graph_arrays.npz</code> extracted
via <code>docker cp</code> from the digest-pinned prod image — prod graph vintage ≠ local),
staged in <code>.blocks-staging/nyc/</code>, shipped with
<code>Dockerfile.blocks-artifacts-overlay</code> (artifacts ONLY) pinned to the serving digest
(<code>a7e3a33c…</code>) → new digest <code>973ea9d6…</code>, prod revision
<code>desire-path-mapper-00114</code>. Prod DB snapshotted to
<code>~/city-edit-prod-backups/20260729-203632/</code> before the deploy; staging-first
(verified blocksVersion, tile content at the reservoir, and the bagg self-heal), then the same
digest promoted to prod and re-verified.</p>
<h2>Full-rebuild follow-up (same evening)</h2>
<p>The layer-cap reset happened hours later: a full <code>cloudbuild.app.yaml</code> rebuild
(fresh Geofabrik PBFs, ~2h20m — the first attempt hit the 2h config timeout in the last
PMTiles step) produced a ~20-layer base with new graphs for all five cities (nyc
<code>6a07332e</code>, +8.3k edges of fresh OSM), then blocks were re-baked per city against
the new graphs — the split pass reached every city for the first time (nyc 1,020 / dc 584 /
philly 579 / chicago 216 / sf 137 corridors split; all audits 0 overlaps, 100% coverage) —
staged with the test-city dirs (absent from a fresh image) and shipped as an artifacts overlay
(digest <code>f38b2659</code>, prod rev <code>00116</code>). Staging rehearsed the
<code>graph_reload</code> resnap against the full prod-sized vote set; prod then resnapped
868,774/868,793 votes (19 expected dedup merges) pinned to one instance.
<b>New landmine found:</b> right after a deploy, the DRAINING old revision still subscribes to
<code>graph_reload</code> (PUBSUB NUMSUB showed 2) and would resnap with its OLD graph —
last-writer-wins on edge ids. Wait for <code>PUBSUB NUMSUB graph_reload</code> = 1 before
publishing, then publish again to be safe (anchors make the resnap idempotent).</p>
<p><b>Why the first ship was artifacts-only, and the (now-reset) layer landmine:</b> the full
<code>Dockerfile.blocks-overlay</code> (code + client + artifacts) failed twice. First the
source tarball raced live edits in the working tree (<code>App.tsx</code> was saved mid-upload,
so the remote saw imports without their usages — build from a clean <code>git archive HEAD</code>
export, never the dirty tree). Then Docker's <b>127-layer cap</b>: the serving image is at
<b>119 layers</b> after weeks of stacked overlays, and the 8-layer code overlay exceeds it —
only the 2-layer artifacts overlay still fits. <b>The next code deploy must be a full rebuild
(<code>cloudbuild.app.yaml</code> + full-bake flow) to reset the layer depth</b>; further
code overlays on this base will fail with <code>max depth exceeded</code>.</p>
"""


def split_by_file(diff_text):
    files = []
    current_name, current_lines = None, []
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


def colorize(chunk):
    out = []
    for raw in chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith(("+++", "---")):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith(("diff ", "index ", "new file", "similarity")):
            cls = "d-meta"
        elif raw.startswith("+"):
            cls = "d-add"
        elif raw.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        out.append(f'<span class="{cls}">{esc or "&nbsp;"}</span>')
    return "\n".join(out)


def context_diagram(path):
    ctx = FILE_CONTEXT.get(path)
    if not ctx:
        return ""
    pills = "".join(
        f'<span class="pill{" on" if p == ctx["system"] else ""}">{html.escape(p)}</span>'
        for p in PILLS
    )
    fmap = "".join(
        f'<div class="fm{" hot" if hot else ""}">{html.escape(name)}</div>'
        for name, hot in ctx["map"]
    )
    blocks = "".join(
        f'<li><b>{html.escape(name)}</b> — {html.escape(desc)}</li>'
        for name, desc in ctx["blocks"]
    )
    return f"""
<div class="ctx">
  <div class="lvl"><span class="lbl">System</span><div class="pills">{pills}</div></div>
  <div class="lvl"><span class="lbl">Module</span><div>{html.escape(ctx["module"])}</div></div>
  <div class="lvl"><span class="lbl">File</span><div>{html.escape(ctx["file"])} · ~{ctx["loc"]} LOC</div></div>
  <div class="lvl"><span class="lbl">File map</span><div class="fmap">{fmap}</div></div>
  <div class="lvl"><span class="lbl">Changed blocks</span><ol class="blk">{blocks}</ol></div>
</div>"""


def main():
    with open(DIFF_PATH) as f:
        diff = f.read()
    sections = []
    for path, chunk in split_by_file(diff):
        sections.append(f"""
<section class="file">
<h3><code>{html.escape(path)}</code></h3>
{context_diagram(path)}
<pre class="diff">{colorize(chunk)}</pre>
</section>""")
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{html.escape(TITLE)}</title>
<style>
body {{ font: 15px/1.55 -apple-system, "Segoe UI", sans-serif; color: #1c1c1a; background: #fbfbf8;
       max-width: 1000px; margin: 2rem auto; padding: 0 1.2rem; }}
h1 {{ font-size: 1.5rem; }} h2 {{ margin-top: 2rem; }}
code {{ background: #f0efe9; padding: 1px 4px; border-radius: 3px; }}
.date {{ color: #777; }}
.ctx {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; margin: 10px 0; background: #fff; }}
.lvl {{ display: flex; gap: 12px; margin: 6px 0; align-items: baseline; }}
.lbl {{ flex: 0 0 110px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #999; }}
.pills .pill {{ display: inline-block; border: 1px solid #ccc; border-radius: 999px; padding: 1px 10px;
                margin-right: 6px; font-size: 12px; color: #aaa; }}
.pills .pill.on {{ border-color: #b3552e; color: #b3552e; font-weight: 600; background: #fdf1ea; }}
.fmap {{ display: flex; flex-wrap: wrap; gap: 4px; }}
.fm {{ font-size: 12px; padding: 2px 8px; border-radius: 4px; background: #f2f1ec; color: #b6b4ac; }}
.fm.hot {{ background: #fdf1ea; color: #b3552e; font-weight: 600; }}
.blk {{ margin: 4px 0 2px 18px; padding: 0; font-size: 13.5px; }}
.diff {{ font: 12px/1.45 ui-monospace, Menlo, monospace; background: #14140f; color: #ddd;
         border-radius: 8px; padding: 12px 14px; overflow-x: auto; display: block; white-space: pre; }}
.d-add {{ color: #7fce6c; display: block; }} .d-del {{ color: #e06c60; display: block; }}
.d-hunk {{ color: #6cb6e0; display: block; }} .d-meta {{ color: #888; display: block; }}
.d-ctx {{ color: #bbb; display: block; }}
section.file {{ margin: 2.2rem 0; }}
</style></head><body>
<h1>{html.escape(TITLE)}</h1>
<p class="date">{DATE} · City Edit changelog</p>
{NARRATIVE}
<h2>Diffs</h2>
{''.join(sections)}
</body></html>"""
    with open(OUT_PATH, "w") as f:
        f.write(body)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    main()
