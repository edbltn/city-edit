#!/usr/bin/env python3
"""Generate the HTML changelog report for the icon-explode unification.

Run from repo root: python changelog/build_explode_report.py
Reads changelog/changes.diff, writes changelog/2026-06-17-unify-icon-explode.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-06-17-unify-icon-explode.html")

DATE = "2026-06-17"
TITLE = "Unifying the icon-explode model — one cluster fanned out at a time"


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
        "id": "model",
        "tag": "React / Leaflet client",
        "title": "1 · Collapse the two-map spread state into one",
        "symptom": (
            "Crowded proposal icons “explode” (fan out into a grid) so you can pick one from a stack. "
            "The old model kept <strong>two</strong> overlapping spread maps — a <code>lockedSpread</code> and a "
            "<code>transientSpread</code> — that were merged into a union on every render, deliberately so that "
            "<em>several</em> clusters could sit fanned out at once. That made the desired behavior "
            "(“only one cluster open at a time”) impossible and spread the bookkeeping across two pieces "
            "of state, two refs, a union <code>useMemo</code>, and a mirror effect."
        ),
        "cause": [
            "<code>lockedSpread</code> (a box was selected → persists, no timer) and "
            "<code>transientSpread</code> (hover/tap → snap-back timer) only ever differed in <em>whether a "
            "timer was running</em>. Their position maps were identical in structure, and the render only ever "
            "read their <strong>union</strong> (<code>spreadPositions</code>). The two-map split existed solely "
            "to allow multiple simultaneous fanouts.",
            "Because both <code>spreadCluster</code> and the locked-promotion path <em>merged</em> into the "
            "existing maps (<code>new Map(transientSpreadRef.current ?? [])</code>, then merge transient into "
            "locked), exploding a second cluster added to the open set rather than replacing it.",
        ],
        "fixes": [
            "Replaced <code>lockedSpread</code> + <code>transientSpread</code> (state) and their two refs with a "
            "single <code>spread</code> state, one <code>spreadRef</code>, and a boolean "
            "<code>spreadLockedRef</code> — “locked” is now just a flag on the one open spread, not a "
            "separate map. The union <code>useMemo</code> and its mirror effect are gone.",
            "Added one <code>applySpread(next)</code> helper that sets the state and mirrors the ref in lockstep "
            "(the hot-path hit-test <code>proposalIconAt</code> reads the ref every drag frame), so no call site "
            "can update one and forget the other.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — spread state → single map + lock flag + applySpread()",
        ],
    },
    {
        "id": "oneatatime",
        "tag": "React / Leaflet client",
        "title": "2 · One cluster fanned out at a time + DRY cluster detection",
        "symptom": (
            "Requested behavior: exploding one icon set should <strong>un-explode any other</strong> — only one "
            "set fanned out at once. Separately, the “is there a crowded stack here?” distance test was "
            "copy-pasted in three places (the path/drag exploder, the browse-click handler, and once inline for "
            "the anchor cluster), each re-deriving the same radius math."
        ),
        "cause": [
            "<code>spreadCluster</code> merged into the open transient spread, and selecting a box merged "
            "transient into locked — so a second fanout coexisted with the first by design.",
            "Three sites filtered <code>placed</code> by <code>CLUSTER_RADIUS_PX</code> with their own copy of "
            "the squared-distance loop.",
        ],
        "fixes": [
            "<strong>Replace, don’t merge.</strong> <code>spreadCluster</code> now builds a fresh "
            "<code>new Map()</code> and calls <code>applySpread(next)</code>, so fanning out any cluster "
            "automatically collapses whatever was open. Exploding set B un-explodes set A for free.",
            "<strong>Selecting a box just flips the lock.</strong> Picking a fanned-out icon sets "
            "<code>spreadLockedRef.current = true</code> and cancels the timer (the position map is unchanged, so "
            "no re-render) instead of rebuilding a merged locked map. A lone, non-fanned icon calls "
            "<code>collapseSpread()</code>.",
            "<strong>One cluster-detection helper.</strong> Extracted <code>clusterAround(anchor)</code> — "
            "the 2+ icons within <code>CLUSTER_RADIUS_PX</code> of a screen point — and routed both the path/"
            "drag exploder and the browse-click handler through it.",
            "<strong>One collapse path.</strong> <code>collapseTransient</code> + <code>collapseAll</code> "
            "collapsed into a single <code>collapseSpread()</code> (clear timer → unlock → clear spread "
            "→ schedule class clear) shared by the snap-back timer, pan/zoom, and deselect; "
            "<code>clearSpreadTimer()</code> replaces the duplicated <code>pauseSpreadTimer</code>.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — spreadCluster replaces; clusterAround(); collapseSpread()/clearSpreadTimer()",
        ],
    },
]

VERIFY = [
    "<code>npx tsc --noEmit</code> — clean (no type errors).",
    "<code>npx eslint src/components/GraphLayer/GraphLayer.tsx</code> — only the 4 pre-existing exhaustive-deps warnings; no new ones (the new deps <code>spread</code>, <code>collapseSpread</code>, <code>clearSpreadTimer</code>, <code>applySpread</code> are all listed).",
    "<code>grep</code> confirms zero remaining references to <code>lockedSpread</code>, <code>transientSpread</code>, <code>spreadPositions</code>, <code>collapseAll</code>, <code>collapseTransient</code>, <code>pauseSpreadTimer</code>.",
    "Client-only change — Vite hot-reloads it; no Flask restart needed.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-walkways</code> at a zoom where several proposal icons stack, and tap a crowded stack — it should fan out into a grid.",
    "With one cluster fanned out, tap a <em>different</em> crowded stack — the first should snap back as the second fans out (only one open at a time).",
    "Tap one of the fanned boxes — it should select (start a route / open the modal) and the spread should stay put (locked, no snap-back) until you pan, zoom, or clear the selection.",
    "Let a fanned cluster sit untouched — it should snap back after ~2.2s; hovering one of its icons should pause that countdown.",
    "Drag a waypoint over a crowded stack — it should fan out so you can drop on a specific icon, and dragging onto a different stack should re-fan the new one.",
    "On a station map (<code>/m/e-bikes-3</code>) confirm station markers still select/deselect normally (no spread there).",
]


# ── Hierarchical "where does this block sit" context ─────────────────────
SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the Leaflet overlay that draws the vote heatmap, the street/topology graph, and the per-vote-type “top proposal” icons (with their explode/fan-out interaction)"),
        "file": ("GraphLayer.tsx", "~3400 LOC — one big component; the change touches only the icon-spread (“explode”) state and handlers"),
        "outline": [
            ("hooks, refs & heatmap drawing", "canvas overlay, topology, votes, zoom — untouched", False),
            ("spread state + applySpread()", "single spread map + spreadLockedRef (was locked/transient union)", True),
            ("proposalIconAt (drag hit-test)", "reads spreadRef for each icon’s display position", True),
            ("selection-clear effect", "collapse the spread when the selection is cleared", True),
            ("collapseSpread / clearSpreadTimer / pan-zoom", "one collapse path + timer cancel (was collapseAll + collapseTransient)", True),
            ("indicatorMarkers useMemo", "builds the icon markers — hosts the explode logic", True),
            ("└ clusterAround / spreadCluster / armSpreadTimer", "NEW shared helper + replace-not-merge fanout", True),
            ("└ clusterExploderRef (path/drag entry)", "tap/drag over a stack → fan out; now via clusterAround", True),
            ("└ handleClick / activate / deactivate", "browse click fans or locks; hover pauses/arms the timer", True),
            ("tooltip + vote-cast plumbing", "hover card content, castProposalVote — untouched", False),
        ],
        "blocks": [
            "spread state: lockedSpread+transientSpread (+union useMemo +mirror effect) → single spread + spreadRef + spreadLockedRef + applySpread()",
            "proposalIconAt: spreadPositionsRef.current → spreadRef.current",
            "selection-clear effect: collapseAllRef → collapseSpreadRef",
            "scheduleSpreadClassClear / clearSpreadTimer / collapseSpread (was collapseTransient + collapseAll); pan-zoom uses collapseSpread",
            "armSpreadTimer + NEW clusterAround(); spreadCluster builds a fresh map (replace, not merge)",
            "clusterExploderRef: uses clusterAround; alreadyOpen checks the single spreadRef",
            "override lookup spread?.get; activate/deactivate gate timer on !locked && spreadRef.has",
            "handleClick: clusterAround for detection; pick a box → lock flag; lone icon → collapseSpread; deps array updated",
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
  details {{ border: 1px solid var(--hairline); border-radius: 10px; margin: 10px 0; background: #fff; overflow: hidden; }}
  summary {{ cursor: pointer; padding: 10px 14px; font-family: var(--font-mono); font-size: 13px;
    display: flex; justify-content: space-between; align-items: center; gap: 12px; user-select: none; }}
  summary:hover {{ background: #faf8f3; }}
  .fname {{ color: var(--ink); }} .stat {{ font-size: 12px; color: var(--muted); }}
  pre.diff {{ margin: 0; padding: 14px 16px; overflow-x: auto; background: #fcfbf7;
    border-top: 1px solid var(--hairline); font-family: var(--font-mono); font-size: 12px; line-height: 1.5; }}
  pre.diff span {{ display: block; white-space: pre; }}

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

  <p class="lede">Crowded proposal icons fan out (“explode”) into a grid so you can pick one from a stack.
  The state behind that was two overlapping maps merged on every render — built to keep several clusters open
  at once. This collapses them into a single spread (plus a lock flag), routes all cluster detection through one
  helper, and makes a fresh fanout <strong>replace</strong> the open one — so only one icon set is ever
  exploded at a time.</p>

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
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_explode_report.py</code>.
    Regenerate after further edits with <code>git diff client-react/src/components/GraphLayer/GraphLayer.tsx &gt; changelog/changes.diff &amp;&amp; python changelog/build_explode_report.py</code>.
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
