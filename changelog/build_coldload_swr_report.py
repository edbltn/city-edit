#!/usr/bin/env python3
"""Generate the cold-load latency changelog report (2026-07-23).

Run from repo root: python changelog/build_coldload_swr_report.py
Reads changelog/changes-coldload-swr.diff (captured with:
  git diff -- server/app.py client-react/src/components/GraphLayer/GraphLayer.tsx \
    > changelog/changes-coldload-swr.diff),
writes changelog/2026-07-23-coldload-swr.html

Modeled on build_block_disjoint_report.py (same styles + context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-coldload-swr.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-coldload-swr.html")

DATE = "2026-07-23"
TITLE = "Cutting the cold load: prewarm the key clients actually request, revalidate in the background, fetch in parallel"


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
        "id": "waterfall",
        "tag": "Measured · prod nyc-bikes",
        "title": "1 \u00b7 Where the 9 seconds actually went",
        "symptom": (
            "Cold first loads of bikepaths.cityedit.org measured 8.5\u20139.9s (three headless-Chrome runs, "
            "fresh profile; the [MAPLOAD] beacon agrees), while warm loads are ~950ms. The waterfall showed "
            "three fat, fully SERIALIZED terms: /api/maps/nyc-bikes taking 0.05\u20132s for a 2KB response "
            "and blocking everything (the map subtree only mounts after config resolves), the topology "
            "download (~15MB brotli, 0.7\u20131.3s), and \u2014 dominating every run \u2014 "
            "/api/graph-votes at 5.8\u20136.3s, which could not even START until the topology had finished."
        ),
        "cause": [
            "<strong>The prewarm warmed a key nobody requests.</strong> Instance startup built the "
            "MODELESS vote snapshot (cache key <code>slug</code>), but every client requests "
            "<code>mode=&lt;map.mode&gt;</code> (key <code>slug:bikepaths</code>) \u2014 so the first "
            "visitor per instance\u00d7map\u00d7mode always paid the full ~6s array build inline. "
            "Confirmed by curl: first request 5.7s TTFB, subsequent 105ms.",
            "<strong>Stale-while-revalidate wasn't.</strong> When a snapshot went rev-stale past the 2s "
            "debounce, every OTHER concurrent caller served the stale body \u2014 but the one who won the "
            "build lock rebuilt INLINE on the request path, +6s for that unlucky visitor. On an actively "
            "voted map this is a steady tax on P99.",
            "<code>/api/maps/&lt;slug&gt;</code> has a 30s in-memory TTL; on expiry one request pays "
            "get_map's Postgres round-trip (it includes a vote count \u2014 1\u20132s on the small prod "
            "instance) inline, as the FIRST request of the page load with everything queued behind it.",
            "The client fetched votes only AFTER the topology download+decode completed, serializing the "
            "two biggest requests for no reason \u2014 the votes request depends only on slug+mode.",
        ],
        "fixes": [
            "Prewarm builds the mode-scoped snapshot (<code>_build_graph_votes_body(rmap, rmap.mode)</code>) "
            "\u2014 the key clients actually hit \u2014 so a fresh instance serves its first visitor from cache.",
            "graph_votes: a request holding a stale snapshot now ALWAYS serves it immediately and hands the "
            "rebuild to a background OS thread (<code>_rebuild_votes_snapshot</code>, single-flight via "
            "non-blocking lock acquire entirely inside the worker). Only the nothing-cached-at-all case "
            "(fresh instance past prewarm, LRU eviction, blocks re-bake) still builds inline \u2014 there is "
            "nothing to serve stale. Staleness stays bounded by the build duration, exactly what concurrent "
            "losers already got; clients reconcile forward from WS deltas.",
            "map_get: same pattern \u2014 an expired-but-present config serves stale immediately and "
            "refreshes on a background thread (<code>_refresh_map_get</code>); a map that vanished or turned "
            "passcode-protected drops out of the cache on refresh.",
            "GraphLayer kicks the vote fetch off in step 0, concurrent with the version probe and topology "
            "download/decode; step 3 just awaits the already-in-flight promise. Safe because MapApp applies "
            "the map config BEFORE mounting the map subtree, so getMapSlug() is always resolved here.",
        ],
        "files": ["server/app.py", "client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
]

VERIFY = [
    "Measured prod cold loads pre-change: 9.9s / 8.5s / 9.0s (headless Chrome, fresh profile each run); "
    "warm 0.9\u20131.1s. curl isolated the votes term: first hit 5.67s TTFB, cached 0.105s.",
    "Local SWR exercise: bump <code>vote_rev:nyc-walkways</code>, wait past the 2s debounce \u2014 the next "
    "request served the STALE body in 4ms with the old rev's ETag; 8s later the fresh rev's ETag was "
    "serving (background rebuild completed); no failure lines in the Flask log.",
    "Local waterfall after the client change: graph-votes now starts at the same millisecond as "
    "graph-version, in parallel with the topology fetch (was: strictly after topology completed).",
    "<code>npx tsc --noEmit</code> clean; full client suite 253/253 passing. Server "
    "<code>tests/unit</code> has 7 pre-existing failures (stale numpy-vs-list expectations around the "
    "vectorized build_arrays \u2014 identical with the diff stashed; none touch these handlers).",
    "getMapSlug()-at-step-0 safety: App.tsx's MapApp resolves + applies the map config before the map "
    "subtree mounts, so the early vote fetch can never target the default map by racing config load.",
]

CHECKLIST = [
    "Open bikepaths.cityedit.org in a private/incognito window (cold): the loader should clear in roughly "
    "3\u20134s now, not ~9s.",
    "Reload normally (warm): still ~1s, heatmap painting from cache.",
    "Cast a vote on any map, wait ~5s, reload: counts correct after the WS delta reconciles \u2014 the "
    "snapshot you loaded may be seconds stale by design.",
    "Watch the System Health map-load row over a few days: cold-load P50/P99 (cached_topo=0) should drop "
    "by roughly the 6s build term.",
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
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("API entrypoint", "routes, vote snapshot cache, prewarm, WS delta hub wiring"),
        "file": ("app.py", "~2250 LOC \u2014 map resolution/caches, vote snapshot LRU + single-flight, graph-votes/topology routes, admin"),
        "outline": [
            ("map config caches", "_map_cache (resolve_map) unchanged; map_get cache gains SWR", True),
            ("vote snapshot cache", "LRU + debounce + single-flight locks (unchanged)", False),
            ("_build_graph_votes_body(_locked)", "dense+sparse twin build (unchanged)", False),
            ("NEW _rebuild_votes_snapshot", "background revalidation worker \u2014 lock acquired AND released in-thread", True),
            ("_prewarm", "now builds the MODE-SCOPED snapshot clients actually request", True),
            ("routes: maps", "map_get \u2014 NEW _refresh_map_get + serve-stale-on-expiry branch", True),
            ("routes: graph-votes", "stale entry \u2192 serve now + background rebuild; empty cache \u2192 inline build", True),
            ("routes: topology / admin / WS", "unchanged", False),
        ],
        "blocks": [
            "_rebuild_votes_snapshot \u2014 non-blocking single-flight, rev re-check inside the lock",
            "_prewarm \u2014 _build_graph_votes_body(rmap, rmap.mode)",
            "_refresh_map_get + map_get SWR branch \u2014 stale config serves while get_map's 1-2s Postgres trip runs off-path",
            "graph_votes \u2014 the not-servable fork: entry\u2260None spawns _PrewarmThread; entry=None builds inline behind the lock",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("GraphLayer", "topology + votes loading, heatmap paint, WS delta application, proposals"),
        "file": ("GraphLayer.tsx", "~2600 LOC \u2014 the mount effect loads topology (IDB-first) then votes, then reconciles deltas"),
        "outline": [
            ("NEW step 0 \u00b7 early vote fetch", "fires with the version probe, parallel to topology", True),
            ("step 1 \u00b7 version probe + topology", "IDB-first, version-busted URLs (unchanged)", False),
            ("step 2 \u00b7 paint from cached votes", "unchanged (v4-purged cache is sparse-only)", False),
            ("step 3 \u00b7 authoritative votes", "awaits the step-0 promise instead of fetching here", True),
            ("delta application / proposals", "unchanged", False),
        ],
        "blocks": [
            "votesFetchPromise \u2014 started before the topology await; noop catch silences unhandled rejection if topology fails first",
            "step 3 \u2014 `const voteRaw = await votesFetchPromise` replaces the serialized fetch",
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

  <p class="lede">Warm loads are already ~1s; cold loads measured 8.5&ndash;9.9s, and the waterfall said why:
  every big step ran one-after-another, and the biggest one — the ~6s vote-array build — ran on the request
  path far more often than it should have. The instance-start prewarm was building the MODELESS snapshot
  key while every real client requests mode=&lt;map.mode&gt;, so first visitors always built inline; a
  rev-stale snapshot served instantly to everyone EXCEPT the one caller who won the build lock and paid the
  rebuild inline; /api/maps/&lt;slug&gt; stalled page starts for 1&ndash;2s on TTL expiry; and the client
  wouldn't even start fetching votes until the 15MB topology had finished downloading. Four fixes: prewarm
  the right key, always serve stale + rebuild on a background thread (votes AND map config), and fire the
  vote fetch in parallel with topology. Expected cold path: ~9s &rarr; ~3&ndash;4s, with the remaining time
  dominated by the topology download itself.</p>

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
    Generated from <code>changelog/changes-coldload-swr.diff</code> by <code>changelog/build_coldload_swr_report.py</code>.
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
