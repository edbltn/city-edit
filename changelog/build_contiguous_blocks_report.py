#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-07-29-slug-redirects-src-tracking.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-08-02-contiguous-blocks.html")

DATE = "2026-08-02"
TITLE = "One block, one place — the fused park ribbons that voted in two spots at once"
LEDE = (
    "A block is supposed to be one street segment you can click and vote on. At John V. Lindsay East River Park "
    "one block was two ribbons 72&nbsp;m apart, so a single vote landed on both sides of the park — and 2,346 nyc "
    "blocks were like it. The merge rule that fuses a street&rsquo;s two sidewalks with its roadway was testing "
    "only that the corridors share both endpoint junctions, which a park loop&rsquo;s two arms do just as well. "
    "It now measures the distance too, anchored on the roadway between them; and a new final pass gives the shipped "
    "geometry the last word, splitting any block whose pieces sit more than 20&nbsp;m apart. Across all seven maps "
    "that count goes 4,748 &rarr; 0, with 100% edge coverage and zero polygon overlap untouched."
)


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


def colorize(diff_chunk: str) -> str:
    out = []
    for raw in diff_chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith("diff ") or raw.startswith("index ") or raw.startswith("new file") or raw.startswith("similarity"):
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
        "id": "fused",
        "tag": "CORRECTNESS / LAYER 2",
        "title": "Two ribbons 72 m apart were one block",
        "symptom": "On <code>/m/nyc-proposals</code>, selecting the block at John V. Lindsay East River Park lit "
                   "<strong>two disconnected ribbons</strong> — the riverside esplanade and the park&rsquo;s inland "
                   "path, ~72 m of grass between them — as a single &quot;PROPOSAL&quot; target. Any vote cast there "
                   "landed in both places. It was not a one-off: <strong>2,346 nyc blocks</strong> (0.8%) shipped "
                   "with pieces more than 20 m apart, the worst spanning <strong>367 m</strong>.",
        "cause": [
            "The graph-first builder&rsquo;s §3 fixpoint has a rule A: <em>corridors sharing the same two "
            "endpoint junction clusters merge</em>. That is exactly right for the two sidewalks and the roadway of "
            "ONE street segment — they all run between the same pair of intersections",
            "But sharing two junctions makes corridors <em>candidates</em>, not duplicates. A park loop&rsquo;s two "
            "arms share their endpoints too. So do a street and the service road that rejoins it, and the paths "
            "either side of a median. Rule A merged them all, <strong>with no distance test whatsoever</strong>",
            "The 2026-07-29 oversized-block split (§4b) capped block SIZE, not this. It cuts a long block "
            "perpendicular to its principal axis — across BOTH arms of a fused pair — so each piece kept both "
            "ribbons. East River Park&rsquo;s block was only 274 m across and never even reached the 400 m threshold",
            "Downstream geometry could fragment a block after grouping was settled too: a grade-separated "
            "crossing&rsquo;s tube slicing a corridor, a junction cell eating its middle, a ship-frame repair cut "
            "taking a bite — all shipped as extra far-flung pieces of an otherwise sane block",
        ],
        "fixes": [
            "<strong>Rule A now measures.</strong> Each same-endpoints group is resolved <em>anchor-first</em>: its "
            "longest corridor claims only those that never stray more than <code>PARALLEL_MAX_SEP_M</code> (30 m) "
            "from it; whatever is left re-anchors and becomes its own block. Anchoring on the longest — the roadway, "
            "where there is one — measures each sidewalk against the carriageway <em>between</em> them, so the bound "
            "is half a right-of-way, not a whole one",
            "Straying is a directed Hausdorff distance, sampled every 5 m so a single long straight edge cannot hide "
            "its own middle from the probe; the MINIMUM of the two directions is used, so a sidewalk covering only "
            "part of a segment still hugs the roadway it runs beside",
            "<strong>§5 contiguity, the backstop.</strong> After every geometry pass is finished, each "
            "block&rsquo;s polygon parts are bucketed into runs no more than <code>PART_GAP_M</code> (20 m) apart and "
            "<strong>every bucket becomes its own block</strong>, members following their own geometry. Buckets are "
            "disjoint subsets of an already-audited polygon, so no split can create an overlap",
            "A bucket left holding no member edge is dropped — a shard with no edge in it can neither carry a vote "
            "nor light up, and keeping it only re-attaches a detached shape to a block that isn&rsquo;t there "
            "(0.02–0.3% of total block area across the cities)",
            "<code>scan_contiguity.py</code> makes the invariant checkable from outside the builder: it measures each "
            "block&rsquo;s <em>spread</em> (the widest edge of a minimum spanning tree over its pieces — precisely "
            "the gap at which it would fall apart) and exits non-zero if any block exceeds the threshold",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py",
                  "server/streetscape_blocks/scan_contiguity.py"],
    },
    {
        "id": "calibration",
        "tag": "CALIBRATION",
        "title": "Why 30 m, and how we know it is not cutting real streets",
        "symptom": "A distance threshold that is too tight splits a wide avenue&rsquo;s two sidewalks into separate "
                   "blocks; too loose and the park ribbons stay fused. The number needed evidence, not a guess.",
        "cause": [
            "Park Avenue is ~43 m building line to building line — the widest right-of-way in the NYC set — so a "
            "sidewalk-to-sidewalk test would have to allow 43 m and would then also swallow the 72 m park pair only "
            "narrowly. Anchoring on the roadway instead halves the distance being measured to ~21 m",
            "The builder now stamps <code>parallel_gap_hist_10m</code> into the mapping meta: every rule-A candidate "
            "bucketed by how far it strayed from its anchor, first round only",
        ],
        "fixes": [
            "Every city shows the same shape — a dense mass under 30 m and a thin tail beyond, with the valley "
            "exactly where the cut lands. nyc: <code>[175597, 64351, 7497, 1289, 300, 156, 148, 98, 31, 20, 11, "
            "18]</code> — <strong>7,497</strong> candidates in the 20–30 m bucket against <strong>1,289</strong> in "
            "30–40 m, a 5.8× drop, and only 0.8% of candidates land beyond the cut at all",
            "chicago <code>[109312, 42193, 3880, 549, …]</code>, sf <code>[16566, 7275, 1075, 191, …]</code>, "
            "philly <code>[59264, 12272, 2287, 735, …]</code>, dc <code>[57843, 19099, 2997, 497, …]</code>, "
            "test-cp <code>[2465, 1942, 410, 9, …]</code> — 98.5–99.4% of all candidates sit below the cut",
            "Both knobs are env-overridable (<code>PARALLEL_MAX_SEP_M</code>, <code>PART_GAP_M</code>) and stamped "
            "into <code>edge_blocks_&lt;network&gt;.json</code>, so any bake states the rules it was built under",
            "A new <code>BLOCKS_DATA_DIR</code> override sends a bake&rsquo;s <code>.npy/.json</code> somewhere other "
            "than the city&rsquo;s live <code>osm_data/</code> — parameter sweeps and A/B runs can no longer leave "
            "the local server serving a mapping nobody meant to ship (this one bit during calibration)",
        ],
        "files": ["server/streetscape_blocks/build_blocks_graph_first.py"],
    },
    {
        "id": "aggregation",
        "tag": "REFACTOR",
        "title": "Block aggregation: carry the slot, stop parsing the key back apart",
        "symptom": "Unrelated to the bug, requested alongside it. <code>block_votes.py</code> built Redis key strings "
                   "and then recovered the values from them with <code>bdk.split(&quot;:&quot;)</code> in two "
                   "different places, and the &quot;which counters move&quot; rules were written twice.",
        "cause": [
            "Both Redis structures are keyed by the same triple — <code>(block_id, vt_id, direction bit)</code> — but "
            "only the <code>bd:</code> key string was passed around, so the aggregate field had to be reconstructed "
            "by splitting that string back up (once in the batched write path, once in the rebuild)",
            "<code>apply_block_delta</code> (the reference implementation the tests use as an oracle) and "
            "<code>apply_block_deltas_batch</code> (the pipelined one prod calls) each derived the +1/-1 transitions "
            "from prev/new direction independently — the subtle half, duplicated",
            "<code>rebuild_from_db</code> derived the aggregate with an unpipelined <code>HLEN</code> per group — one "
            "full round trip per (block, vote type, direction) in the map",
        ],
        "fixes": [
            "A <code>Slot</code> is now the unit of currency: <code>_transitions()</code> returns "
            "<code>(slot, ±1)</code> moves, and <code>_slot_bd_key</code> / <code>_slot_bagg_field</code> derive "
            "whichever encoding a call site needs. No code recovers a slot from a key string any more",
            "Both write paths share <code>_transitions()</code> — they now differ only in HOW they talk to Redis "
            "(await-each-move vs two-phase pipeline), which is exactly what the equivalence test is there to check, "
            "so the oracle keeps its value",
            "<code>rebuild_from_db</code> pipelines the HLEN reads as well as the writes",
            "Behaviour-preserving: the full server suite (77 tests, including the 13 block-vote tests that assert "
            "the batched path is identical to the per-edge loop) passes unchanged",
        ],
        "files": ["server/block_votes.py"],
    },
]

VERIFY = [
    "Every city A/B&rsquo;d against the SAME graph, old rules vs new: the invariant holds exactly and the audits are "
    "untouched — <strong>100% edge coverage and 100% member-edge∩polygon overlap</strong> everywhere, "
    "<strong>0 overlapping polygon pairs ≥1 m²</strong> everywhere.",
    "Blocks spread wider than 20 m, <strong>before → after</strong>: nyc <strong>2,346 → 0</strong> "
    "(worst 367 m → 19.9 m), dc <strong>1,139 → 0</strong> (321 m), chicago <strong>864 → 0</strong> "
    "(274 m), philly <strong>825 → 0</strong> (160 m), sf <strong>302 → 0</strong> (376 m), "
    "test-mid <strong>105 → 0</strong> (143 m), test-cp <strong>69 → 0</strong> (85 m).",
    "Block counts barely move — the fix separates a small tail, it does not shred the grid: sf 44,678 → 44,926 "
    "(+0.6%), chicago 138,140 → 138,683 (+0.4%), philly 144,796 → 145,647 (+0.6%), dc 148,471 → 149,198 "
    "(+0.5%).",
    "The rule-A guard is <em>not</em> redundant with the §5 backstop: on sf, running §5 alone produced "
    "44,697 blocks against 44,846 with both, i.e. the guard separates ~149 pairs whose tubes TOUCH and which no "
    "geometric split could ever tell apart.",
    "Verified in the running app on <code>/m/test-midtown</code> with the new mapping installed: block selection, the "
    "proposal card and the vote counters all behave, and the selection highlight is a single contiguous shape.",
    "Every staged mapping&rsquo;s <code>topology_etag</code> was checked against the serving image&rsquo;s own graph "
    "before shipping — the loader hard-rejects a mismatch, and local graphs are a different vintage from prod&rsquo;s.",
]

CHECKLIST = [
    "Open the reported link — <code>https://cityedit.org/m/nyc-proposals?z=15&amp;lat=40.72240&amp;lng=-73.98022"
    "&amp;slat=40.71931&amp;slng=-73.97336&amp;vt=Add+traffic+calming</code> — and confirm the selected block is "
    "now ONE ribbon, not two ribbons either side of the park.",
    "Click along East River Park: the esplanade and the inland path should now select independently, each lighting "
    "only itself.",
    "Spot-check a wide avenue (Park Avenue around 40.7540, -73.9760) — a normal street block should still cover "
    "the roadway AND both sidewalks as one shape; if avenues look split down the middle, "
    "<code>PARALLEL_MAX_SEP_M</code> is too tight.",
    "Confirm the block layer redrew rather than serving cached tiles: <code>curl -s "
    "https://cityedit.org/api/maps | grep -o '\"blocksVersion\":[0-9]*'</code> should differ from the pre-deploy "
    "value (it is the bake&rsquo;s sha, and the client appends it as <code>?v=</code>).",
    "Vote on a block and reload — the count must survive, confirming the <code>bd:</code>/<code>bagg:</code> "
    "rebuild-on-blocks-change self-heal ran against the new block ids.",
    "Re-run the audit any time with <code>python server/streetscape_blocks/scan_contiguity.py "
    "&lt;blocks_final_&lt;city&gt;.geojson&gt;</code> — it exits non-zero if any block spreads past 20 m.",
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


# ── Hierarchical "where does this block sit" context ──────────────────────────
# For every diff we render a recursively-summarized map: the SYSTEM (which of the
# app's components this file belongs to) → the MODULE (subsystem + one-line
# summary) → the FILE (summary + an outline of its top-level sections) → the
# changed BLOCKS. The file outline is a focus+context minimap: changed sections
# are highlighted, the rest are dimmed, so you see where the diff lands among its
# peers at every zoom level.

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

# label, summary, changed?
FILE_CONTEXT = {
    "server/streetscape_blocks/build_blocks_graph_first.py": {
        "on": ["Flask API"],
        "module": ("streetscape_blocks · the Layer-2 graph-first block builder",
                   "edges grouped topologically (junction clusters + corridors), polygons generated FROM the groups, "
                   "disjointness enforced by construction + audits"),
        "file": ("build_blocks_graph_first.py",
                 "one-pass builder: junction clustering → edge grouping → merge fixpoint → geometry "
                 "→ disjointness → contiguity → ship-frame audit · ~1355 LOC"),
        "outline": [
            ("module docstring", "stages 1–4 + NEW stage 5 (contiguity) stated as an invariant", True),
            ("knobs (cluster / stub / width / split)", "+ PARALLEL_MAX_SEP_M, HUG_SAMPLE_M, PART_GAP_M", True),
            ("UnionFind / split_oversized / _clean helpers", "unchanged", False),
            ("§1 junction clusters", "unchanged", False),
            ("§2 edge grouping", "unchanged", False),
            ("corridor centreline geometry (hoisted)", "edge_lines / corr_geom / member_lines / strays — one "
                                                       "cache, shared by §3 and §4", True),
            ("§3 degeneracy + equivalence fixpoint", "rule A becomes anchor-first + distance-guarded; cache "
                                                          "invalidated where V1/V2 move members", True),
            ("§4 geometry from membership", "duplicate edge_lines/member_lines removed — use the hoisted "
                                                 "pair", True),
            ("§4b oversized-corridor split", "unchanged", False),
            ("ship-frame finalize + audit + re-home", "unchanged", False),
            ("§5 contiguity split (NEW)", "bucket polygon parts, one block per bucket, drop member-less shards",
             True),
            ("§6 emit", "single staged (polygon, members, props) list feeds one loop for corridors AND cells",
             True),
            ("§7 audit + meta", "+ contiguity_* counters, parallel_gap_hist_10m, the two new knobs", True),
        ],
        "blocks": [
            "PARALLEL_MAX_SEP_M = 30 (env-overridable) — how far a corridor may stray from its group anchor and "
            "still count as the same street; measured against the roadway between the sidewalks, so the bound is half "
            "a right-of-way",
            "PART_GAP_M = 20 — wider than any gap a junction cell can open in a street corridor, far narrower "
            "than the separations that read as two places",
            "corr_geom(c) — (centrelines, sample points) cache; segmentize to 5 m so a long straight edge cannot "
            "hide its middle from a distance probe",
            "strays(c, anchor) — directed Hausdorff; rule A takes the MIN of the two directions so a part-length "
            "sidewalk still hugs its roadway",
            "rule A rewritten: sort the group by member length, longest anchors, claim only what hugs it, re-anchor "
            "the remainder — and record the first-round gap histogram",
            "lines_cache invalidated in V1 (stub → junction) and V2 (junction → corridor), which also move "
            "members mid-fixpoint",
            "§5: contiguous_buckets() union-finds polygon parts within PART_GAP_M; members follow midpoint, "
            "then line, then nearest; member-less buckets dropped",
            "§6 emit unified — corridors and junction cells stage into one (poly, members, extra) list, so "
            "the split pass and the feature writer are each written once",
            "BLOCKS_DATA_DIR — redirect the baked mapping away from the city's live osm_data/ for sweeps",
        ],
    },
    "server/streetscape_blocks/scan_contiguity.py": {
        "on": ["Flask API"],
        "module": ("streetscape_blocks · out-of-band audits",
                   "independent scanners over a shipped blocks geojson — scan_overlaps.py for disjointness, "
                   "scan_contiguity.py for one-block-one-place"),
        "file": ("scan_contiguity.py", "NEW · ~100 LOC — spread measurement + threshold gate"),
        "outline": [
            ("docstring", "what a legitimate multi-piece block is, and what must never ship", True),
            ("_pieces / spread_m", "MST over the block's polygon pieces; widest edge = the gap it splits at", True),
            ("scan", "percentiles, over-N-metres histogram, worst offenders with coordinates", True),
            ("main", "CLI over N geojsons, --max-spread, non-zero exit on any offender", True),
        ],
        "blocks": [
            "spread = widest MST edge over the pieces — exactly the threshold at which the block falls apart, so "
            "'spread ≤ PART_GAP_M for every block' is the shipped invariant",
            "offenders printed with lat,lon so a failure is directly clickable on the map",
            "exit status 1 when any block exceeds --max-spread (default 20 m) — usable as a bake gate",
        ],
    },
    "server/block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask · block-level vote display",
                   "a deduplicated projection of edge votes onto blocks: bd: device-multiplicity hashes + the bagg: "
                   "aggregate one HGETALL serves the block layer from"),
        "file": ("block_votes.py", "keys/packing, incremental write path, read path, rebuild · ~350 LOC"),
        "outline": [
            ("key helpers + packing", "→ 'Slots and keys': the (block, vt, dir) triple both structures share, "
                                      "plus _slot_bd_key / _slot_bagg_field / _transitions", True),
            ("apply_block_delta", "reference path — now drives off _transitions()", True),
            ("apply_block_deltas", "per-edge convenience wrapper, unchanged", False),
            ("apply_block_deltas_batch", "same two phases, keyed by slot instead of by key string", True),
            ("build_block_arrays", "unchanged", False),
            ("read_block_vt_counts", "field built via _slot_bagg_field", True),
            ("clear / rebuild_from_db", "tracks slots, and pipelines the HLEN derivation", True),
        ],
        "blocks": [
            "Slot = tuple[int, int, int] + the section comment explaining why the triple, not the key string, is what "
            "gets passed around",
            "_transitions(block, vt, prev, new) — the whole of 'which counters move', shared by both write paths",
            "apply_block_delta reduced to a loop over _transitions with the same await-each-move semantics",
            "apply_block_deltas_batch phases keyed by Slot — the two bdk.split(':') reconstructions are gone",
            "rebuild_from_db pipelines the HLEN reads (was one round trip per (block, vt, dir) group)",
        ],
    },
    "cloudbuild.blocks-artifacts-overlay.yaml": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Cloud Build configs",
                   "one config per deploy shape: full app, code overlay, arrays overlay, blocks overlay"),
        "file": ("cloudbuild.blocks-artifacts-overlay.yaml", "NEW · the config the artifacts-only Dockerfile "
                                                             "never had"),
        "outline": [
            ("steps", "docker build -f Dockerfile.blocks-artifacts-overlay + push", True),
            ("substitutions", "_REGION, _BASE_IMAGE (digest-pinned serving image)", True),
        ],
        "blocks": [
            "Dockerfile.blocks-artifacts-overlay already existed but had no Cloud Build config, so the most surgical "
            "deploy we have had to be driven by hand",
            "two layers only: the staged artifacts, nothing else — no server code, no client build, no nginx "
            "config",
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
        <details>
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
  .twocol {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 680px) {{ .twocol {{ grid-template-columns: 1fr; }} h1 {{ font-size: 27px; }} }}
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
  .ctx-tier {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; padding: 4px 0; }}
  .ctx-mod {{ padding-left: 18px; }} .ctx-file {{ padding-left: 36px; }}
  .ctx-tier {{ position: relative; }}
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
    <div class="dateline">{DATE} · branch <code>main</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">{LEDE}</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_report.py</code>.
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
