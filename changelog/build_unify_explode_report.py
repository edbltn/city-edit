#!/usr/bin/env python3
"""Generate the cluster-explode unification changelog report (2026-07-14).

Run from repo root: python changelog/build_unify_explode_report.py
Reads changelog/changes-unify-explode.diff (captured with:
  git diff -- client-react/src/components/GraphLayer/GraphLayer.tsx \
    > changelog/changes-unify-explode.diff),
writes changelog/2026-07-14-unify-cluster-explode.html

Modeled on build_routeprop_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-unify-explode.diff")
OUT_PATH = os.path.join(HERE, "2026-07-14-unify-cluster-explode.html")

DATE = "2026-07-14"
TITLE = "Click-to-explode works for route-based top proposals — one cluster engine for both pin kinds"


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
        "id": "divergence",
        "tag": "Diagnosis · why the two pin kinds diverged",
        "title": "1 · The exploder lived inside the point-pin memo",
        "symptom": (
            "Clicking a crowded stack of proposal pins fans it out into a pickable grid "
            "(“click to explode”) — but only when a point-based top proposal (PBTP square) "
            "existed. On a map whose top proposals are all route-based (RBTP diamonds — e.g. "
            "chicago-bikes, whose 714 K imported votes are all route-kind), clicking a stack of "
            "overlapping diamonds selected whichever sat on top, blind."
        ),
        "cause": [
            "The cluster machinery — the <code>clusterables</code> list, <code>clusterAround()</code>, "
            "<code>spreadCluster()</code>, and the <code>explodeClusterAt()</code> gate — was born with "
            "the PBTP squares and lived <em>inside</em> the <code>indicatorMarkers</code> memo. When "
            "RBTPs were added, they were wired in parasitically: the PBTP memo pushed the diamonds "
            "into its <code>clusterables</code>, and published the gate on two refs "
            "(<code>internalExploderRef</code> for the diamond markers' own clicks, "
            "<code>clusterExploderRef</code> for the host's path taps and drag-hover).",
            "But the host memo early-returns <strong>before</strong> those refs are assigned: "
            "<code>if (source.length === 0) return null</code> — no point-based winners, no markers "
            "to render, so also <em>no exploder for anyone</em>. Diamond clicks fell through their "
            "<code>internalExploderRef.current?.()</code> guard (still <code>null</code>) straight "
            "to corridor selection, and bare-map taps over a stack placed a start pin instead.",
            "Worse than never-installed: <em>stale</em>. When winners transiently empty (a theme/mode "
            "switch resets them; votes reload), the refs kept the previous closure — an exploder frozen "
            "over the OLD clusterables at their OLD positions — so explode behavior after a mode switch "
            "depended on what the last non-empty render happened to see.",
            "So the observed rule “explode only works when a point pin is around” was exactly the "
            "code structure: the shared gesture was owned by one of the two marker kinds.",
        ],
        "fixes": [],
        "files": [],
    },
    {
        "id": "engine",
        "tag": "Fix · GraphLayer.tsx",
        "title": "2 · One clusterEngine memo, owned by neither pin kind",
        "symptom": (
            "The fan-out machinery needed a home that exists whenever <em>either</em> pin kind exists."
        ),
        "cause": [
            "Extraction, not rewrite: the cluster functions were already kind-agnostic (spread keys "
            "carry the kind: <code>e&lt;edgeIdx&gt;</code> vs <code>r&lt;id&gt;</code>); only their "
            "placement gated them on squares.",
        ],
        "fixes": [
            "New <code>clusterEngine</code> <code>useMemo</code>, hoisted above both marker memos. It "
            "computes <code>placed</code> (each square/station resolved to its edge midpoint) and "
            "<code>clusterables</code> (squares <em>and</em> diamonds at their settled display "
            "positions), and defines <code>clusterAround</code> / <code>spreadCluster</code> / "
            "<code>explodeClusterAt</code> — all moved verbatim.",
            "The exploder refs are assigned <strong>unconditionally</strong> on every engine run — "
            "with zero clusterables the gate still exists and simply returns <code>false</code> — so "
            "they can never be null-on-a-diamond-map or stale-after-a-mode-switch again.",
            "<code>indicatorMarkers</code> (the PBTP memo) now consumes "
            "<code>{ placed, clusterAround, spreadCluster }</code> from the engine and keeps only "
            "rendering: heat ranking (now computed from <code>placed</code>), icons, role tinting, "
            "its <code>handleClick</code> — whose cluster-detection body is unchanged.",
            "The diamond markers and the host (<code>MapView</code>) are untouched: they already "
            "called the gate through the refs; the refs just finally always point at a live one. "
            "Dependency arrays tightened accordingly (<code>winners</code>/<code>routeProposals</code>/"
            "<code>applySpread</code>/<code>stationLabel</code> moved to the engine; the marker memo "
            "keys off <code>clusterEngine</code>).",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
]

VERIFY = [
    "<code>tsc --noEmit</code> clean.",
    "Driven in the running dev app (Vite + host Flask + Redis, Chrome). Repro map "
    "<strong>chicago-bikes</strong>: 20 RBTP diamonds, <strong>0</strong> PBTP squares — the exact "
    "state where the exploder was previously never installed.",
    "Diamond-stack click → the stack fanned out (container got <code>votes-spreading</code>; 3 "
    "markers lifted into the 500000+ fanned z-band in a 38 px grid) and the click was consumed — "
    "no blind corridor selection. Console: <code>[proposals] diamond click 2fb8434d override=false</code>.",
    "Picking a fanned diamond → selected that corridor: URL gained the forced-corridor token "
    "(<code>…,f2fb8434d;…</code>), Start/End populated, the “TOP ROUTE PROPOSAL · Improve bike "
    "lane · Selects 236 blocks” card opened.",
    "Bare-map tap just off a diamond-only stack (the <code>clusterExploderRef</code> path through "
    "MapView) → exploded 4 diamonds, and did NOT place a start pin.",
    "Transient snap-back intact: unhovered spread collapsed after ~2.2 s "
    "(<code>SPREAD_DURATION_MS</code>), <code>votes-spreading</code> cleared, 0 fanned markers.",
    "Regression, mixed cluster on <strong>nyc-bikes</strong> (8 squares + 20 diamonds): clicking an "
    "overlapping square+diamond stack fanned out 3 icons — 1 diamond + 2 squares — together in one grid.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/chicago-bikes</code> (all-route map): click any overlapping "
    "pair of diamond pins — they should fan out into a grid instead of opening a corridor.",
    "Click one of the fanned diamonds — the corridor should select (start/end pins at its anchors, "
    "route-proposal card open).",
    "With no route active, click the map just beside (not on) a diamond stack — it should still fan "
    "out rather than drop a start pin.",
    "On <code>http://localhost:3000/m/nyc-bikes</code>, click a stack where a square and a diamond "
    "overlap — both kinds should fan out together in one grid (no regression).",
    "Switch modes on a multi-mode map and immediately click a stack — it should explode at the "
    "current positions (the stale-exploder case).",
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
      {f"<h3>What changed</h3><ul>{li(s['fixes'])}</ul>" if s['fixes'] else ""}
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer · proposal pins + voting UI", "renders PBTP squares & RBTP diamonds over the Leaflet map; owns hover/selection/cluster-fan-out gestures"),
        "file": ("GraphLayer.tsx", "~4600 LOC — topology/vote wiring, spread state, drag gestures, the two marker memos, cards"),
        "outline": [
            ("Constants + rbtpDisplayPos + spread keys", "CLUSTER_RADIUS_PX 26 · SPREAD_CELL_PX 38 · e<edge>/r<id> keys", False),
            ("Spread state + exploder refs", "spread/spreadRef/spreadLockedRef; internalExploderRef comment now points at the engine", True),
            ("Winners recompute + selection/hover effects", "selectTopProposals scan, deep-link reconciliation (unchanged)", False),
            ("Drag effects", "drag-hover explode via clusterExploderRef (unchanged, comment updated)", True),
            ("collapseSpread / armSpreadTimer", "snap-back timer machinery (unchanged)", False),
            ("clusterEngine memo", "NEW HOME: placed + clusterables (BOTH kinds) + clusterAround/spreadCluster/explodeClusterAt + unconditional ref assignment", True),
            ("indicatorMarkers memo (PBTP squares)", "now renders from engine.placed; cluster logic consumed, not owned", True),
            ("routeIndicatorMarkers memo (RBTP diamonds)", "unchanged — its internalExploderRef gate finally always fires", False),
            ("Pinned/hover ProposalCards + portals", "unchanged", False),
        ],
        "blocks": [
            "internalExploderRef comment — assigned by clusterEngine, for BOTH kinds",
            "drag-explode effect comment — ref assigned during the clusterEngine render",
            "clusterEngine useMemo — placed/clusterables built for squares+stations+diamonds; clusterAround, spreadCluster, explodeClusterAt moved verbatim; refs re-assigned unconditionally",
            "indicatorMarkers — destructures { placed, clusterAround, spreadCluster }; heat rank from placed; early returns are rendering-only now",
            "indicatorMarkers deps — winners/routeProposals/applySpread/stationLabel replaced by clusterEngine",
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

  <p class="lede">Clicking a crowded stack of proposal pins is supposed to fan it out into a pickable
  grid before any side effect — but the entire fan-out machinery lived inside the point-pin
  (<code>indicatorMarkers</code>) memo, which early-returns when a map has no point-based winners.
  On all-route maps (chicago-bikes: 20 diamonds, 0 squares) the exploder was therefore never
  installed: diamond clicks selected the top-of-stack corridor blind, and bare-map taps over a stack
  dropped a start pin. The fix extracts the machinery into a <code>clusterEngine</code> memo owned by
  neither pin kind, which assigns the exploder refs unconditionally (also killing a stale-closure
  hazard after mode switches empty the winners list). Both marker memos now consume the same engine;
  the diamond markers and MapView are untouched. Verified live: diamond-only stacks explode, fanned
  picks select, bare-map taps near stacks no longer place starts, and mixed square+diamond stacks
  still fan out together.</p>

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
    Generated from <code>changelog/changes-unify-explode.diff</code> by <code>changelog/build_unify_explode_report.py</code>.
    Regenerate after further edits with
    <code>git diff -- client-react/src/components/GraphLayer/GraphLayer.tsx &gt; changelog/changes-unify-explode.diff &amp;&amp; python changelog/build_unify_explode_report.py</code>.
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
