#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes-proposal-selection.diff, writes
changelog/2026-08-09-proposal-selection-grain.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-proposal-selection.diff")
OUT_PATH = os.path.join(HERE, "2026-08-09-proposal-selection-grain.html")

DATE = "2026-08-09"
TITLE = "The pin and the card now agree on what you selected"


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
        "id": "grain",
        "tag": "THE BUG",
        "title": "The card and the pin asked different questions",
        "symptom": "Run a route through a point-based top proposal and the route card lists it, badges it as a top proposal, and folds its votes into the row counts \u2014 while the pin sitting right there on the map stays dark. Reported for route-based proposals running through point ones; it is really every selection.",
        "cause": [
            "The card asks at <strong>block</strong> grain: <code>topKindsFor</code> materializes the path into whole blocks and badges any winner whose edge is in that union",
            "The pin asked a much narrower question: is the winner's own edge <em>on the routed path</em> (direction twins forgiven)?",
            "A junction block is not one segment \u2014 it holds every stub radiating from the intersection, and a route crosses exactly two of them. On <code>nyc-proposals</code> the block under \u201cFix dangerous intersection\u201d holds <strong>38 edges / 19 distinct segments</strong>; the odds of the route traversing the one the vote landed on are about 2 in 19",
            "So the narrow test missed nearly every intersection proposal a route plainly ran through \u2014 exactly the proposals a point-kind vote type produces",
        ],
        "fixes": [
            "The ring now comes from <code>onSelectedBlockSet</code>: winners sitting on any block the selection touches \u2014 <strong>the same union the card badges</strong>",
            "Both sets are built in one pass over <code>winners</code>, so the pin and the badge can only ever be computed from the same inputs",
            "Reproduced and then re-checked in the running app: the card badged the row and <strong>0 of 21</strong> icons carried <code>is-selected</code>; after the change, exactly <strong>1</strong> does, and it is the badged type",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "passthrough",
        "tag": "THE CATCH",
        "title": "Widening the ring must not widen click-through",
        "symptom": "The obvious fix \u2014 point <code>onPath</code> at the block union \u2014 would have broken the pointer, quietly. That flag does two jobs at once.",
        "cause": [
            "<code>onPath</code> also drives <code>passthrough</code>: an on-path pin is <code>pointer-events: none</code> so the <em>route polyline underneath it</em> owns the gesture (drag \u2192 ghost mid, tap \u2192 restart from here)",
            "That is only safe where a polyline really is underneath. A proposal on a crossed junction block sits <em>beside</em> the route, not on it \u2014 made click-through, it would have become simply dead to the pointer",
        ],
        "fixes": [
            "Two sets, two jobs, documented as such at the declaration: <code>onPathEdgeSet</code> (exact, twin-forgiving) keeps click-through and the exploded-icon mid-drag; <code>onSelectedBlockSet</code> (block grain) drives the ring and <em>nothing else</em>",
            "<code>onSelectedBlock</code> is deliberately <code>!onPath &amp;&amp; \u2026</code> \u2014 the two are disjoint, so reading the code you can see which pins stay interactive",
            "A proposal the selection covers but does not run over therefore reads selected <em>and</em> stays clickable, which is the behaviour you want: it is the thing you would click next",
        ],
        "files": ["client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "corridor",
        "tag": "THE SIBLING",
        "title": "A corridor you traced most of read as untraced",
        "symptom": "Run a selection along part of a corridor without covering all of it: the point pins along the way light up, and the corridor's own diamond stays dark. Flagged in the original report as \u201cpossibly related\u201d \u2014 it is the same disagreement, one family over.",
        "cause": [
            "<code>isRouteCovered</code> demanded <strong>every</strong> block of the corridor hold a selected edge",
            "That looks symmetric with the point rule and is not: a point proposal has <em>one</em> block, so any overlap is already full coverage for it. Only corridors were ever held to the strict bar",
            "And full coverage barely survives the ends \u2014 a route along a corridor starts and stops mid-corridor, or OSRM clips a junction block at a turn",
        ],
        "fixes": [
            "Coverage becomes a ratio with a threshold: <code>ROUTE_SELECTED_MIN_COVERAGE = 0.6</code>. Ran along most of it \u2192 selected; merely brushed it \u2192 not",
            "One predicate is shared by all three surfaces that answer \u201cwhat does this selection stand for\u201d \u2014 the diamond's ring, the route card's header, and the card's route badge \u2014 so they cannot drift apart the way the pin and the card just did",
            "The header now takes the <strong>best-covered</strong> corridor rather than the first in rank order, because with a threshold several can qualify at once",
            "<code>isRouteCovered</code> keeps a <code>minRatio</code> parameter, so a caller that genuinely wants all-of-it can still ask for it",
        ],
        "files": [
            "client-react/src/components/GraphLayer/routeProposals.ts",
            "client-react/src/components/GraphLayer/GraphLayer.tsx",
        ],
    },
]


VERIFY = [
    "<strong>Structural evidence first.</strong> A throwaway <code>vite-node</code> harness loaded the live topology + votes and ran the client's own <code>selectTopProposals</code>: on <code>nyc-proposals</code> and <code>sac-proposals</code>, PBTP blocks hold up to <strong>22 distinct segments</strong> (44 edges) \u2014 so an exact-edge test on a crossed block is a coin flip with 20 sides.",
    "<strong>Reproduced in the running app</strong> before touching anything: a route through 40.7573,-73.9897 gave a card reading <em>Fix dangerous intersection \u00b7 1/21 blocks</em> with the square top-proposal badge, and <code>document.querySelectorAll('.vote-type-indicator.is-selected')</code> returning <strong>empty</strong> across all 21 pins.",
    "<strong>Same probe after the fix:</strong> one <code>vote-type-indicator is-selected</code>, and the badged row is still the only badged row \u2014 pin count and badge count now match.",
    "<strong>Partial-corridor case driven end to end:</strong> a generated deep link tracing the middle ~70% of a 72-block \u201cAdd bus lane\u201d corridor (both anchors strictly interior, so full coverage is impossible) now returns <code>vote-type-indicator is-selected is-diamond</code> and a diamond-badged row. Rule (b) cannot account for it \u2014 <code>selectedRbtpId</code> is only ever set by a tap or a drop, never by a deep link.",
    "<code>npx tsc -b</code> clean for every file in this change; the only errors in the tree are in another session's in-flight <code>routeVoteRows.ts</code>, which this change does not touch.",
    "<code>npx vitest run</code>: <strong>401 passed</strong>, 1 skipped. <code>routeProposals.test.ts</code> gained coverage-ratio cases and its <code>isRouteCovered</code> cases were rewritten for the threshold rule (including one pinning the strict <code>minRatio = 1</code> escape hatch).",
    "<code>python3 scripts/check_algorithm_docs.py</code> still OK \u2014 the route-proposals dossier's <code>isRouteCovered</code> anchor is intentionally preserved rather than renamed.",
    "Staged by explicit path only \u2014 four files \u2014 since other sessions are mid-flight in neighbouring GraphLayer modules. <strong>Path-level staging was not enough for the shared doc:</strong> <code>docs/three-layer-model.md</code> also carried another session's uncommitted edits to \u00a72 and the file table, and those rode along into the commit. They were left there deliberately once found: that session has since committed three times on top, so unpicking the hunk would have meant rebasing live work to reverse a change the tree now depends on. <strong>The diff shown below is filtered to this workstream's own hunks.</strong>",
]


CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-proposals?w=40.754000,-73.990500;40.761000,-73.988000</code> \u2014 the route card should badge <strong>Fix dangerous intersection</strong> with a square, and the pin near 42nd St should now carry the selected ring.",
    "Click that ringed pin. It must still respond \u2014 selecting it and opening its modal. If it feels dead, the passthrough split has regressed.",
    "Drag the route line where it passes <em>over</em> a proposal pin: you should still pull out a ghost mid with a dotted trail. That path is driven by the narrow set and should be unchanged.",
    "Trace a route along most of a corridor without reaching either end \u2014 its diamond should light, and the card header should name that corridor.",
    "Now brush a corridor with a short crossing selection. The diamond should <em>not</em> light: 0.6 is the line, and it is one constant (<code>ROUTE_SELECTED_MIN_COVERAGE</code>) if it feels wrong in daily use.",
    "Clear the route \u2014 every ring should drop at once, with no pin left lit.",
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
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 GraphLayer", "The map's proposal layer: it owns the top-proposal pins (squares and diamonds), their hover/pin cards, the selection highlight and the cast controls"),
        "file": ("GraphLayer.tsx", "~5.1k LOC \u2014 this change touches the selected-state derivation and three places that ask \u201cis this proposal in the selection?\u201d"),
        "outline": [
            ("imports", "+2 \u2014 routeCoverageRatio, ROUTE_SELECTED_MIN_COVERAGE", True),
            ("waypoint matching (start/end/mid edge ids)", "unchanged", False),
            ("onPathEdgeSet / onSelectedBlockSet state", "the two grains, newly split and documented", True),
            ("the on-path effect", "now also builds the block union and the block-grain winner set", True),
            ("hover / pinned resolution, cluster engine", "unchanged", False),
            ("coveredRouteProposal (card header)", "threshold coverage; picks the BEST-covered corridor", True),
            ("topKindsFor (row badges)", "comment only \u2014 now names the shared sets/predicate", True),
            ("indicatorMarkers (PBTP squares)", "onSelectedBlock added to isSelected; passthrough left alone", True),
            ("routeIndicatorMarkers (RBTP diamonds)", "comment only \u2014 rule (a) is now MOST of its blocks", True),
            ("ProposalCard row badge tooltip", "\u201cfully inside\u201d \u2192 \u201cmostly inside\u201d", True),
        ],
        "blocks": [
            "onSelectedBlockSet is built from materializeBlocks over pathEdgeIds - byte for byte the union topKindsFor badges",
            "Both sets come out of ONE loop over winners, so they cannot be computed from different snapshots",
            "onSelectedBlock is gated !onPath: disjoint sets, so the passthrough rule below reads unambiguously",
            "passthrough still keys off onPath alone - click-through is only safe where a polyline is underneath",
            "isSelected gains onSelectedBlock; the mid/drop-target/pinned arms are untouched",
            "coveredRouteProposal switched from .find to a max-by-ratio scan: with a threshold, several corridors can qualify",
            "The indicatorMarkers memo gains onSelectedBlockSet as a dep - a stale dep here silently freezes rings",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 GraphLayer / route proposals", "Pure logic for route-based top proposals (RBTPs): computing corridors from the vote graph, and the block-grained predicates the UI asks about them"),
        "file": ("routeProposals.ts", "~1.4k LOC \u2014 this change rewrites the coverage predicate near the top of the file"),
        "outline": [
            ("RouteProposal type + wire parsing", "unchanged", False),
            ("marker shape (diamond vs square)", "unchanged", False),
            ("routeBlockEdges (highlight + vote set)", "unchanged", False),
            ("routeCoverageRatio", "new \u2014 share of blocks the selection reaches", True),
            ("ROUTE_SELECTED_MIN_COVERAGE", "new \u2014 the 0.6 threshold, with the reasoning", True),
            ("isRouteCovered", "now a threshold test; keeps a minRatio parameter", True),
            ("expandSelectionToUndirected", "unchanged \u2014 still applied before any coverage test", False),
            ("dropPointsCoveredByRoutes", "unchanged", False),
            ("corridor growth / peeling / ghost waypoints", "unchanged", False),
        ],
        "blocks": [
            "routeCoverageRatio returns 0 for a corridor with no blocks, so an empty corridor can never read as covered",
            "The Set conversion happens once, inside the ratio - isRouteCovered delegates rather than duplicating it",
            "The threshold carries its rationale in the docstring: why not 1.0, and what breaks in each direction",
            "isRouteCovered keeps its NAME on purpose - the algorithm dossier binds to that anchor, and this is still the same question",
            "minRatio is a parameter, not a hardcode, so a caller wanting the strict rule has one",
        ],
    },
    "client-react/src/components/GraphLayer/routeProposals.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client \u00b7 GraphLayer / tests", "Vitest coverage for the pure route-proposal logic \u2014 87 cases over parsing, coverage, corridor growth and dedupe"),
        "file": ("routeProposals.test.ts", "this change replaces the coverage block and adds a ratio block"),
        "outline": [
            ("parseRouteProposal / shape class / block edges", "unchanged", False),
            ("routeCoverageRatio", "new describe \u2014 counting, and the empty-corridor floor", True),
            ("isRouteCovered", "rewritten for the threshold rule", True),
            ("expandSelectionToUndirected", "unchanged \u2014 its twin case still asserts coverage", False),
            ("growth / recovery / dedupe suites", "unchanged", False),
        ],
        "blocks": [
            "2/3 covered now asserts TRUE - the behaviour change is pinned by a test, not left implicit",
            "1/3 asserts FALSE, so 'merely brushed it' stays out",
            "One case passes minRatio = 1 explicitly, keeping the strict rule alive and exercised",
        ],
    },
    "docs/three-layer-model.md": {
        "on": [],
        "module": ("Docs \u00b7 three-layer model", "The canonical description of graph / blocks / proposals, and the rules the client derives from them"),
        "file": ("three-layer-model.md", "\u00a73.3 Selection behavior is the section this change rewrites"),
        "outline": [
            ("\u00a71\u20132 graph and block layers", "unchanged", False),
            ("\u00a73.1\u20133.2 proposal families and corridor growth", "unchanged", False),
            ("\u00a73.3 rule (1) coverage", "now the threshold, with why it is not 1.0", True),
            ("\u00a73.3 rule (2) explicit tap", "unchanged", False),
            ("\u00a73.3 PBTP selection", "new bullet \u2014 the two grains and which one drives what", True),
            ("\u00a74 vote semantics", "unchanged", False),
        ],
        "blocks": [
            "The doc now states which surfaces share the predicate, so the next change has to keep them together",
            "It records WHY full coverage was wrong rather than only that it changed - a point has one block, so overlap already was full coverage for it",
            "The PBTP bullet spells out that the narrow path test survives only to decide click-through",
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

  <p class="lede">Two surfaces described the same selection and disagreed about it. Run a route through a point-based top proposal and the card listed it, badged it and counted its votes — while its pin stayed dark, because the card asks at <strong>block</strong> grain and the pin asked whether the winner’s own edge was on the routed path. A junction block holds dozens of stubs and a route crosses two, so the pin’s test missed nearly every intersection proposal a route ran through. The ring now comes from the same block union the card badges; the narrow test survives for one job only — deciding click-through, which is safe only where a polyline really lies underneath. The corridor diamonds had the same disagreement one family over: they demanded <em>full</em> block coverage, which looks symmetric with the point rule but is not — a point has one block, so any overlap is already full coverage for it. Coverage is now a threshold behind one shared predicate, so the pin, the card header and the row badge cannot drift apart again.</p>

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
    Generated from <code>changelog/changes-proposal-selection.diff</code> by <code>changelog/build_proposal_selection_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes.diff &amp;&amp; python changelog/build_proposal_selection_report.py</code>.
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
