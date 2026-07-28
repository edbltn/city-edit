#!/usr/bin/env python3
"""Generate the HTML changelog report for the route-card vote-count flash fix.

Run from repo root: python changelog/build_routevotes_flash_report.py
Reads changelog/changes-routevotes-flash.diff,
writes changelog/2026-07-28-route-votes-flash.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-routevotes-flash.diff")
OUT_PATH = os.path.join(HERE, "2026-07-28-route-votes-flash.html")

DATE = "2026-07-28"
TITLE = "Route-card vote counts no longer flash inflated numbers"


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
        "id": "why",
        "tag": "React client",
        "title": "1 · Why the modal's counts flashed — two row sources racing",
        "symptom": (
            "Opening a route selection (including a Top Route Proposal diamond) painted one set of "
            "vote counts, then visibly <em>flashed down</em> to different, smaller numbers a beat "
            "later. Which one is right? <strong>The second — the server's distinct-voter counts.</strong>"
        ),
        "cause": [
            "The route-summary card has always had two row sources. First it renders the <strong>local "
            "block-grain sums</strong> (<code>selectionVoteRows</code> over the selection's touched "
            "blocks) — but a route cast fans one device's vote onto <em>every block it covers</em>, so "
            "these sums count the same person <strong>once per block</strong>. A 30-block corridor "
            "shows one voter as 30.",
            "Then <code>/api/route-votes</code> answers with the truth — DISTINCT devices per "
            "(vote&nbsp;type,&nbsp;direction) across the whole edge set — and the card swaps to it. "
            "That fetch sat behind a <strong>350&nbsp;ms debounce</strong> plus a round-trip, so the "
            "inflated stand-in was on screen long enough to read.",
            "A third glitch hid in the swap-over: <code>routeUniqueRows</code> was reset by an effect "
            "(post-paint), so switching from selection A to B could paint one frame of "
            "<em>A's</em> server rows on <em>B's</em> card.",
        ],
        "fixes": [
            "<strong>First fetch fires immediately.</strong> The debounce only ever existed to coalesce "
            "vote-burst refetches; an unseen selection now skips it, so truth replaces the stand-in "
            "after one round-trip (~50–150&nbsp;ms locally) instead of 350&nbsp;ms + round-trip.",
            "<strong>Resolved rows are cached per selection</strong> (order-insensitive FNV signature "
            "of the capped block-edge union — <code>routeVotesKey</code>, new in "
            "<code>blockSelection.ts</code>; bounded at 64 entries). Reopening a selection renders "
            "server truth with <em>no flash at all</em>, then refreshes stale-while-revalidate.",
            "<strong>Rows are key-matched to the current selection</strong> — a fetched result carries "
            "its selection signature, so selection A's rows can never appear on selection B's card.",
            "<strong>The diamond hover card reads the same cache</strong>, so hovering a proposal you've "
            "opened shows the same numbers its card settled on (previously hover always showed the "
            "inflated block-grain sums).",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — cache ref, routeEdgeUnion memo, immediate-first-fetch effect, key-matched rows, hover-card seeding",
            "client-react/src/utils/blockSelection.ts — routeVotesKey + ROUTE_VOTES_CACHE_MAX (pure, unit-testable)",
            "client-react/src/utils/routeVotesKey.test.ts — NEW: signature order-insensitivity / discrimination tests",
        ],
    },
]

VERIFY = [
    "Frontend: <code>tsc --noEmit</code> — clean; <code>vitest run</code> — 317 passed (5 new "
    "<code>routeVotesKey</code> tests), 1 skipped.",
    "Live dev (localhost:3000, nyc-walkways): start+end route on a voted corridor → the Route "
    "Proposal card rendered the server's distinct-voter row (<code>Improve bike lane · +4</code>) "
    "with <code>POST /api/route-votes → 200</code> firing immediately; Clear + in-app back restored "
    "the selection with the same rows (cache hit) and a debounced SWR refetch. No console errors.",
]

CHECKLIST = [
    "Open a Top Route Proposal on prod (nyc-walkways) and watch the card's counts as it opens — "
    "they should settle within ~a round-trip and no longer visibly drop from big numbers to small.",
    "Close and reopen the same proposal — the counts should appear instantly with no change at all.",
    "Hover the same proposal's diamond after opening it once — the hover card's rows should match "
    "the card you just saw.",
    "Cast a +/− on a route selection — the counts should update within ~a second (debounced refetch).",
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
      <h3>Cause</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


# ── Hierarchical "where does this block sit" context ──────────────────────────

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the canvas heatmap + proposal markers + the pinned/hover/route proposal cards"),
        "file": ("GraphLayer.tsx", "~4610 LOC — topology load, heat paint, hit-testing, and the ProposalCard portals for pinned points, routes, and hovers"),
        "outline": [
            ("Topology + votes + heat paint", "load, decode, canvas passes", False),
            ("Hover / hit-testing / selection", "nearest-edge snap, pinned target", False),
            ("hoverRbtpRows memo", "diamond hover rows — now seeded from the route-votes cache", True),
            ("route card rows plumbing", "routeEdgeUnion memo + cache + immediate-first-fetch effect + key-matched routeUniqueRows", True),
            ("Card portals (return JSX)", "rows={routeUniqueRows ?? routeVoteRows} — unchanged consumer", False),
        ],
        "blocks": [
            "routeVotesCacheRef — session Map<signature, VoteTypeRow[]> of resolved distinct-voter rows",
            "hoverRbtpRows — cache lookup by routeVotesKey(slug, capped blockEdgeIds) before local sums",
            "routeEdgeUnion — capped block-edge union + its signature, memoized from routeBlocks",
            "fetch effect — delay 0 for unseen signatures, ROUTE_VOTES_DEBOUNCE_MS for cached (SWR)",
            "routeUniqueRows — key-matched: fetched-for-this-key → cache → null (local stand-in)",
        ],
    },
    "client-react/src/utils/blockSelection.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "pure block-scoped selection helpers (docs §2.4) — no React/DOM, unit-testable"),
        "file": ("blockSelection.ts", "~112 LOC — materializeBlocks, selectionVoteRows, and now the route-votes cache signature"),
        "outline": [
            ("materializeBlocks", "selection → per-block edge lists", False),
            ("ROUTE_VOTES_CACHE_MAX + routeVotesKey", "NEW: FNV-1a signature of the sorted capped edge union", True),
            ("selectionVoteRows", "block-grain sums (the local stand-in rows)", False),
        ],
        "blocks": [
            "ROUTE_VOTES_CACHE_MAX = 64 — bounds a long browse session",
            "routeVotesKey(slug, edgeIds) — sort → FNV-1a over low+high 16 bits → `slug:len:hash`",
        ],
    },
    "client-react/src/utils/routeVotesKey.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "vitest unit tests beside the pure helpers"),
        "file": ("routeVotesKey.test.ts", "NEW ~30 LOC — signature behavior pinned down"),
        "outline": [
            ("order-insensitivity", "same set, any order → same key", True),
            ("discrimination", "different sets / lengths / slugs / high bits → different keys", True),
            ("purity", "input array not mutated", True),
        ],
        "blocks": [
            "5 tests — order-insensitive, set/length/slug discrimination, >16-bit ids, no mutation",
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
  @media (max-width: 680px) {{ h1 {{ font-size: 27px; }} }}
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
    <div class="dateline">{DATE} · branch <code>main</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">The route-summary modal opened with locally-summed vote counts that overcount one
  person per block, then flashed down to the server's distinct-voter truth. The first fetch now fires
  immediately, resolved counts are cached per selection (reopen = no flash), the rows are key-matched
  to the selection they belong to, and the diamond hover card shows the same settled numbers.
  The counts that persist — distinct devices — were always the accurate ones.</p>

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
    Generated from <code>changelog/changes-routevotes-flash.diff</code> by
    <code>changelog/build_routevotes_flash_report.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes-routevotes-flash.diff
    &amp;&amp; python changelog/build_routevotes_flash_report.py</code>.
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
