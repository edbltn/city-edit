#!/usr/bin/env python3
"""Generate the 2026-07-27 heat-priority selection + disjoint-blocks-reship report.

Run from repo root: python changelog/build_heat_priority_report.py
Reads changelog/changes-heat-priority.diff, writes
changelog/2026-07-27-heat-priority-selection.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-heat-priority.diff")
OUT_PATH = os.path.join(HERE, "2026-07-27-heat-priority-selection.html")

DATE = "2026-07-27"
TITLE = "Hot blocks always win the hit: heat-priority selection + corridor-disjoint NYC blocks reshipped"

PILLS = ["nginx", "Flask API", "OSRM", "Redis", "React/Leaflet client"]

# Per-file hierarchical context: system pill → module → file summary → file map.
FILE_CONTEXT = {
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "system": "React/Leaflet client",
        "module": ("MapLibreBackground — the GL basemap + block-heat renderer; owns the "
                   "MapLibre instance and the point→block resolver GraphLayer calls on "
                   "every mousemove"),
        "file": "GL map bootstrap, block tile source/layers, heat feature-state apply, camera sync",
        "loc": 560,
        "map": [
            ("imports / zoom constants", False),
            ("BlockVotes / BlockSelect event contracts", False),
            ("blockAtResolver bridge (blockIdAtLatLng)", True),
            ("blockFillPaint / blockLinePaint heat ramps", False),
            ("map init + style + tile rebind", True),
            ("camera sync from Leaflet", False),
            ("heat feature-state apply (diffed writes)", False),
        ],
        "blocks": [
            ("import hottestBlockId", "pull in the pure overlap-winner rule"),
            ("blockAtResolver", "queryRenderedFeatures result now ranked by |feature-state heat| "
             "instead of taking feats[0] (render order) — a cool polygon overlapping a hot block "
             "no longer swallows every hover/click in the overlap region"),
        ],
    },
    "client-react/src/components/MapLibreBackground/hottestBlock.ts": {
        "system": "React/Leaflet client",
        "module": ("MapLibreBackground — the GL basemap + block-heat renderer"),
        "file": "NEW — pure overlap-winner rule: highest |heat| owns the point, ties keep render order",
        "loc": 31,
        "map": [
            ("BlockHitFeature shape", False),
            ("hottestBlockId()", True),
        ],
        "blocks": [
            ("hottestBlockId", "max |feature-state heat| wins; non-numeric ids skipped; "
             "all-zero-heat ties fall back to first = old render-order behavior"),
        ],
    },
    "client-react/src/components/MapLibreBackground/hottestBlock.test.ts": {
        "system": "React/Leaflet client",
        "module": "MapLibreBackground — unit tests",
        "file": "NEW — 7 vitest cases for the overlap-winner rule",
        "loc": 55,
        "map": [("hottestBlockId cases", True)],
        "blocks": [
            ("tests", "hotter-under-cooler wins; missing state = 0; ties keep render order; "
             "cold (negative) heat ranks by magnitude; non-numeric ids ignored"),
        ],
    },
    "cloudbuild.blocks-overlay.yaml": {
        "system": "nginx",
        "module": "Deploy tooling — Cloud Build configs",
        "file": "NEW — build config for Dockerfile.blocks-overlay (code+client+block artifacts on pinned base)",
        "loc": 27,
        "map": [("docker build w/ _BASE_IMAGE", True), ("push", False)],
        "blocks": [
            ("steps", "mirrors cloudbuild.overlay.yaml but builds Dockerfile.blocks-overlay so "
             ".blocks-staging/<city>/ artifacts ride along with the code/client overlay"),
        ],
    },
}

NARRATIVE = """
<h2>What happened</h2>
<p>On the <b>NYC Dangerous Intersections</b> map (<code>/m/nyc-intersections</code>, city
<code>nyc</code>, network <code>streets</code>), the orange heat block at Rockaway Parkway &amp;
Linden Boulevard was <b>impossible to hover or select</b>. Two independent causes:</p>
<ol>
<li><b>Prod served a pre-corridor-disjoint blocks bake.</b> The corridor-vs-corridor
disjointness pass (<code>a720309</code>, 2026-07-22) never shipped for NYC: prod's z14 tile at
that location carried <b>1,629 overlapping polygon pairs ≥1&nbsp;m²</b> (max 1,430&nbsp;m²), including a
913&nbsp;m² overlap between two consecutive Rockaway&nbsp;Parkway corridor blocks — exactly the
double-quad visible in the user's screenshot.</li>
<li><b>The point→block resolver took the topmost-rendered polygon.</b>
<code>blockIdAtLatLng</code> returned <code>queryRenderedFeatures(...)[0]</code> — tile/feature
order, not heat — so wherever a cool polygon overlapped a hot one, the cool one owned every
hover and click and the hot block underneath was unreachable.</li>
</ol>
<h2>The fix</h2>
<p><b>Client:</b> the resolver now ranks all polygons under the cursor by
<code>|feature-state heat|</code> and the hottest wins (<code>hottestBlockId</code>); ties — the
common all-zero case — keep render order. This also covers the residual ≤20&nbsp;m² slivers
tippecanoe's per-zoom simplification introduces at low zooms, which no geometry rebake can
eliminate.</p>
<p><b>Blocks:</b> NYC re-baked with the current graph-first builder (corridor cuts + duplicate
corridor merges + ship-frame audit: <code>residual_overlap_pairs: 0</code>) <b>against the serving
image's own graph</b> (etag <code>b0ca56f8…</code>, extracted via <code>docker cp</code> from the
digest-pinned prod image — prod graph vintage ≠ local), staged in
<code>.blocks-staging/nyc/</code>, and shipped with <code>Dockerfile.blocks-overlay</code> so the
client fix and artifacts land in one image. Staging-first, then the same digest promoted to prod.
The segmentation logic itself needed no new code — overlapping corridors were already merged/cut
by <code>a720309</code>; what was missing was a prod re-bake carrying it.</p>
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
