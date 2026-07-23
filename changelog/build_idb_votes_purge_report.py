#!/usr/bin/env python3
"""Generate the pre-sparse dense-vote-cache purge changelog report (2026-07-23).

Run from repo root: python changelog/build_idb_votes_purge_report.py
Reads changelog/changes-idb-votes-purge.diff (captured with:
  git diff -- client-react/src/utils/graphCache.ts > changelog/changes-idb-votes-purge.diff),
writes changelog/2026-07-23-idb-dense-votes-purge.html

Modeled on build_block_disjoint_report.py (same styles + context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-idb-votes-purge.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-idb-dense-votes-purge.html")

DATE = "2026-07-23"
TITLE = "The crash loop the sparse fix couldn't reach — purging pre-sparse dense vote entries (IndexedDB v4)"


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
        "id": "crashloop",
        "tag": "Mobile Safari · nyc-bikes",
        "title": "1 · Why one device kept crashing after format=sparse shipped",
        "symptom": (
            "bikepaths.cityedit.org on a phone that had used the map before the 2026-07-22 sparse "
            "rollout: the map loads, the heatmap blinks on, the page reloads itself, then Safari gives "
            "up with &ldquo;a problem repeatedly occurred&rdquo;. Fresh devices — anyone who first "
            "visited after the rollout — load fine, which made it look device-specific."
        ),
        "cause": [
            "Pre-sparse clients persisted the <strong>decoded dense</strong> vote arrays into "
            "IndexedDB — on NYC maps ~9M boxed JS values (3.3M edge votes + 3.3M edge vote-type "
            "arrays + 2×1.35M node slots + 2×290K block slots).",
            "The sparse rollout deliberately kept old entries readable (&ldquo;old dense cache entries "
            "still work&rdquo;) and did NOT bump <code>DB_VERSION</code> — so the poisoned entry "
            "survived on every device that had one.",
            "Merely <em>reading</em> that entry back (<code>getCachedVotes</code>, GraphLayer step 2) "
            "structured-clones the whole thing onto the JS heap <em>before</em> any format or "
            "blocks-version check can reject it. On top of the ~71&nbsp;MB topology + MapLibre, that "
            "re-creates the exact pre-sparse memory peak → jetsam kills WebContent.",
            "The kill lands <em>before</em> the authoritative sparse fetch finishes and overwrites the "
            "cache entry (GraphLayer step 3) — so the dense entry is never replaced and the device "
            "crash-loops <strong>forever</strong>. Self-reinforcing: the crashier the device, the "
            "stickier the poison.",
            "The existing recovery paths can't fire: the React error boundary needs a JS error (a "
            "jetsam kill raises none), and <code>clearGraphCache</code> runs only on a detected "
            "topology/vote mismatch.",
        ],
        "fixes": [
            "<code>DB_VERSION</code> 3 → 4. The v3→v4 upgrade deletes every <code>votes:*</code> key "
            "with a single <strong>key-range delete</strong> — "
            "<code>IDBKeyRange.bound(\"votes:\", \"votes:\\uffff\")</code> — which touches only the "
            "keys and never materializes the huge values (a cursor over values, or a read-and-check, "
            "would itself OOM the device we're rescuing).",
            "The cached binary topology (<code>topology-bin</code>, a raw ArrayBuffer) and the boxed "
            "station-network topology (<code>topology</code>) are format-unchanged and survive — the "
            "warm-topology first paint, the biggest P99 win, is preserved.",
            "Pre-v3 upgrades keep the old behavior (drop the whole store); the handler now branches "
            "on <code>event.oldVersion</code>.",
            "Self-healing on the crashing device: the upgrade runs inside <code>openDb()</code> on "
            "first cache access, before any vote read is possible — cheap enough to complete even on "
            "the constrained device, after which the app fetches and persists the tiny sparse payload.",
        ],
        "files": ["client-react/src/utils/graphCache.ts"],
    },
]

VERIFY = [
    "<code>npx tsc --noEmit</code> — clean.",
    "<code>npx vitest run src/utils/sparseVotes.test.ts src/utils/voteStore.test.ts</code> — 30/30 pass.",
    "Real-browser upgrade test (puppeteer-core + Chrome-for-Testing 148 against local dev): seeded a "
    "v3 <code>desire-path-cache</code> with a <code>topology</code> sentinel, <code>topology-bin</code>, "
    "and two dense <code>votes:*</code> entries, then loaded <code>/m/nyc-walkways</code> so the real "
    "bundle ran the upgrade. Result: DB version 4, both seeded votes entries gone, both topology "
    "entries intact, and the app then wrote a fresh sparse <code>votes:walkways</code> entry.",
    "Prod serving state confirmed before the fix: rev 00102 serves the sparse bundle "
    "(<code>format=sparse</code> in the served JS, ETag <code>…-sp-gz</code> from /api/graph-votes, "
    "381&nbsp;KB gzip for nyc-bikes) and [MAPLOAD] beacons are flowing into "
    "<code>cityedit_map_load_ms</code>.",
]

CHECKLIST = [
    "On the phone that crash-looped: open bikepaths.cityedit.org fresh. First load may still be "
    "heavier (it purges, then cold-fetches votes) but must NOT reload itself; the second load should "
    "paint the heatmap near-instantly from cache.",
    "If it somehow still crash-loops: Settings → Safari → Advanced → Website Data → remove "
    "cityedit.org, then retry once — and tell me, because that points at hypothesis 2 (raw memory "
    "headroom), not the cache.",
    "On a healthy device: reload any NYC map twice — the heatmap should still paint instantly on the "
    "second load (topology cache survived the purge).",
    "Watch the System Health dashboard's map-load row for a day: P99 should drop as poisoned devices "
    "self-heal, and cached_topo=1 loads should stay fast.",
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
            file_rows.append(file_detail_html(f, chunk, open_=True))
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
    "client-react/src/utils/graphCache.ts": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer persistence", "IndexedDB cache so repeat loads skip the download AND the parse: topology (binary + boxed), per-map vote snapshots"),
        "file": ("graphCache.ts", "~190 LOC — versioned DB open, get/set for topology / topology-bin / votes:*, clearGraphCache recovery"),
        "outline": [
            ("header comment + DB_VERSION", "v2/v3 history; NEW v4 rationale (dense-entry read = jetsam kill)", True),
            ("openDb / onupgradeneeded", "was: drop store unconditionally; now branches on oldVersion, v3→v4 key-range votes purge", True),
            ("idbGet / idbSet", "best-effort promise wrappers (unchanged)", False),
            ("topology accessors", "getCachedTopology(+Bin)/set — version-matched (unchanged)", False),
            ("votes accessors", "getCachedVotes/set keyed votes:<slug> (unchanged — reads are safe once dense entries can't exist)", False),
            ("clearGraphCache", "mismatch/crash recovery wipe (unchanged)", False),
        ],
        "blocks": [
            "DB_VERSION = 4 + v4 comment — why reading a dense entry is itself the crash",
            "onupgradeneeded — oldVersion<3 drops store; else delete(IDBKeyRange.bound(\"votes:\", \"votes:\\uffff\"))",
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

  <p class="lede">The 2026-07-22 format=sparse rollout fixed the nyc-bikes mobile crash for every device
  except the ones already poisoned: a phone that had visited before the rollout still held the DECODED
  dense vote arrays (~9M boxed values) in IndexedDB, and merely reading that entry back on load — which
  the client does before any format check can reject it — re-created the old memory peak and jetsam-killed
  the tab before the sparse refetch could ever overwrite it. A permanent, self-reinforcing crash loop
  (&ldquo;a problem repeatedly occurred&rdquo;) that no error boundary can catch, because a jetsam kill
  raises no JS error. The fix bumps the cache to v4 and purges every votes:* entry in the upgrade with a
  key-range delete that never materializes the values, while the warm topology cache — the big first-load
  win — survives. Verified end-to-end in a real Chrome against the dev bundle.</p>

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
    Generated from <code>changelog/changes-idb-votes-purge.diff</code> by <code>changelog/build_idb_votes_purge_report.py</code>.
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
