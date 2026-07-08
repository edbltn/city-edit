#!/usr/bin/env python3
"""Generate the API-latency-optimizations changelog report.

Run from repo root: python changelog/build_latency_opts_report.py
Reads changelog/changes-latency-opts.diff,
writes changelog/2026-07-07-latency-opts.html

Modeled on build_monitoring_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-latency-opts.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-latency-opts.html")

DATE = "2026-07-07"
TITLE = "API latency fixes — kill the per-request DB tax, unblock the gevent hub"


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
        elif raw.startswith("diff ") or raw.startswith("index "):
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
        "id": "dbtax",
        "tag": "Flask API · resolve_map",
        "title": "1 · The shared floor: a full-table vote count under every request",
        "symptom": (
            "Per-endpoint P99s (new log-based metric, 7-day baseline) showed a ~100–300&nbsp;ms floor "
            "under endpoints whose own work is sub-millisecond: reverse-geocode p50 320&nbsp;ms "
            "(a KD-tree query + ≤5-hop BFS!), and /api/maps tailing to 3.6&nbsp;s at p95."
        ),
        "cause": [
            "<code>resolve_map()</code> — the first line of every API endpoint — called "
            "<code>get_map(slug)</code>, and <code>_MAP_SELECT</code> LEFT JOINs "
            "<code>(SELECT map_slug, COUNT(*) FROM edge_votes GROUP BY map_slug)</code>: a full-table "
            "aggregate over ~1.8M vote rows, on the db-f1-micro, per request. The result "
            "(<code>voteCount</code>) isn't even read by resolve_map.",
        ],
        "fixes": [
            "<code>_MAP_SELECT</code> split three ways (same column list, so the row shape and "
            "<code>_map_row_to_dict</code> are untouched): <strong>list</strong> keeps the full-table "
            "aggregate (list_maps ranks by count), <strong>one</strong> uses a correlated "
            "<code>(SELECT COUNT(*) … WHERE map_slug = m.slug)</code> that walks "
            "<code>idx_edge_votes_map</code> for just that map, and <strong>light</strong> selects "
            "<code>0 AS vote_count</code> for callers that never read it.",
            "<code>get_map(slug, with_vote_count=False)</code> → light; the /api/maps/&lt;slug&gt; and "
            "by-subdomain config endpoints keep accurate counts via the indexed variant.",
            "On top of that, resolve_map's lookup is now behind a 30&nbsp;s in-process TTL cache "
            "(misses included — bots probe random slugs; bounded at 512 entries), so steady-state "
            "resolve_map costs no DB round-trip at all. Map-mutating endpoints (create, set-subdomain, "
            "promote-vote-types) invalidate their slug; cross-instance staleness is bounded by the TTL.",
        ],
        "files": ["server/app.py", "server/database.py"],
    },
    {
        "id": "hub",
        "tag": "Flask API · gevent",
        "title": "2 · One slow query froze the whole instance",
        "symptom": (
            "P99s across unrelated endpoints moved together: whenever the 3.6&nbsp;s maps aggregate (or "
            "any slow query) ran, every other in-flight request on the instance stalled behind it."
        ),
        "cause": [
            "Prod runs one gunicorn <strong>gevent</strong> worker, but psycopg2 is a C extension: "
            "without a wait callback it blocks the entire event-loop hub for the duration of every "
            "query. Concurrency existed in name only wherever Postgres was touched.",
            "The DB layer used a single shared connection — which was only safe <em>because</em> the hub "
            "blocked. Making psycopg2 yield without fixing that would let two greenlets interleave on "
            "one connection (\"another command is already in progress\").",
        ],
        "fixes": [
            "<code>psycogreen</code> installs psycopg2's gevent wait callback — guarded by "
            "<code>monkey.is_module_patched(\"socket\")</code> so plain <code>python app.py</code> local "
            "dev is untouched.",
            "The shared connection became a <code>ThreadedConnectionPool(1, 5)</code> (5 × 4 max "
            "instances stays under the f1-micro's ~25-connection ceiling), with a brief wait-and-retry "
            "when the pool is drained and drop-on-<code>OperationalError</code> so a dead connection "
            "isn't returned to the pool.",
            "Proved under a monkey-patched runtime: 32 greenlets — indexed reads, full-table "
            "aggregates, <code>pg_sleep</code> writers — completed interleaved in 0.63&nbsp;s with zero "
            "errors and the wait callback verified active.",
        ],
        "files": ["server/database.py", "server/requirements.in", "server/requirements.txt"],
    },
    {
        "id": "endpoints",
        "tag": "Flask API · endpoints",
        "title": "3 · Endpoint-level: shared maps-list cache, 304s that skip the graph load",
        "symptom": (
            "/api/maps re-ran the ranking aggregate for every distinct visitor (HTTP max-age=60 only "
            "helps repeat visitors), and /api/graph-votes p95 hit 15&nbsp;s: it called "
            "<code>ensure_loaded()</code> before the ETag check, so on a cold instance even a "
            "would-be-304 paid a 10&nbsp;s+ graph load."
        ),
        "cause": [
            "Browser caching is per-visitor; the server recomputed the same ranked list for each one.",
            "The graph-votes revision (its ETag input) lives in Redis — the graph itself is only needed "
            "to <em>build</em> a response body, never to conclude the client's copy is current.",
        ],
        "fixes": [
            "/api/maps keeps one enriched list per instance for 60&nbsp;s (same horizon as its "
            "Cache-Control), invalidated by map creation via <code>invalidate_map_cache</code>.",
            "/api/graph-votes computes rev + ETag from Redis first and answers 304 before touching the "
            "graph; <code>ensure_loaded()</code> moved after the conditional so only actual body builds "
            "load it.",
        ],
        "files": ["server/app.py"],
    },
]

VERIFY = [
    "Local end-to-end on the dev stack (Redis + Postgres with the 1.79M-row Lyft import + Flask): "
    "reverse-geocode 0.9&nbsp;ms, nearest-node 0.7&nbsp;ms, /api/maps ~1&nbsp;ms warm; responses "
    "byte-identical in shape (address string, 18 maps, voteCount 1,788,841 preserved on the list).",
    "Cold-instance graph-votes conditional GET returns 304 without its own graph load (the 30&nbsp;s "
    "observed locally was a concurrent browser tab triggering the load — the 304 itself served from "
    "Redis).",
    "Vote cast + un-cast round-trip through the pooled writer: success, cleared list correct.",
    "Gevent concurrency harness: 32 greenlets (readers, full-table aggregates, pg_sleep writers) "
    "through the 5-connection pool — 0 errors, 0.63&nbsp;s wall, psycopg2 wait callback confirmed set.",
    "Server test suite: unit 47/47, integration 2/2 green. requirements.txt diff is exactly "
    "<code>+psycogreen==1.0.2</code> (no other pin churn).",
    "NOT yet deployed to prod — needs an overlay deploy (code-only, ~2&nbsp;min) when you give the go.",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/nyc-walkways'>http://localhost:3000/m/nyc-walkways</a> — "
    "click around: address labels (reverse-geocode) should feel instant; routes, votes, and the "
    "landing grid all behave as before.",
    "Cast a vote and remove it — the block heat updates and the second cast still sees the first "
    "(the clear-then-cast semantics ride the pooled writer now).",
    "Create a throwaway map via Propose-a-Map — it should appear on the landing grid immediately "
    "(cache invalidation on create).",
    "Say the word and I'll overlay-deploy to prod (backup → cloudbuild.overlay → update-traffic), "
    "then watch the per-endpoint dashboard: reverse-geocode P99 should drop from ~1s to low tens of "
    "ms, /api/maps p95 from 3.6s to &lt;100ms warm.",
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


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/app.py": {
        "on": ["Flask API"],
        "module": ("Flask API · app.py", "all HTTP endpoints + resolve_map, the per-request map→city/graph/policy resolver"),
        "file": ("app.py", "~1900 LOC — routes, vote codepath, WS broadcast, admin; this diff touches the resolver and two endpoints"),
        "outline": [
            ("imports · config · helpers", "env, redis client, registries", False),
            ("ResolvedMap + resolve_map", "NEW 30s TTL map-config cache (+ invalidate_map_cache)", True),
            ("passcode gate", "auth attempts, _locked", False),
            ("maps endpoints", "list (NEW per-instance 60s cache) · create (NEW invalidation) · by-slug/subdomain", True),
            ("routes / vote endpoints", "OSRM routing, block-scoped voting", False),
            ("graph endpoints", "graph-votes: ETag/304 NOW before ensure_loaded", True),
            ("admin endpoints", "subdomain + promote-vote-types (NEW invalidation)", True),
        ],
        "blocks": [
            "_map_cache — {slug: (expiry, row|None)}, 30s TTL, 512-entry cap, misses cached too",
            "_cached_get_map / invalidate_map_cache — resolve_map reads through; mutations evict",
            "resolve_map — get_map(with_vote_count=False) via the cache",
            "maps_list — one enriched list per instance for 60s (server twin of max-age=60)",
            "map create / set-subdomain / promote-vote-types — invalidate_map_cache(slug)",
            "graph_votes — Redis rev → ETag → 304 first; ensure_loaded only for body builds",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API · database.py", "the Postgres persistence layer (votes, maps, vote-type lists)"),
        "file": ("database.py", "~1000 LOC — connection handling, schema migration, vote/map CRUD"),
        "outline": [
            ("imports", "NEW conditional psycogreen patch (gevent-patched runtimes only)", True),
            ("connection layer", "shared singleton → ThreadedConnectionPool(1,5) + retry/drop", True),
            ("schema migration", "edge_votes legacy → clean shape", False),
            ("vote CRUD", "record/remove/fetch votes", False),
            ("_MAP_SELECT family", "split: list (GROUP BY) · one (indexed correlated count) · light (0)", True),
            ("map CRUD", "get_map gains with_vote_count; by-subdomain → indexed count", True),
        ],
        "blocks": [
            "psycogreen guard — patch_psycopg() iff monkey.is_module_patched('socket')",
            "_get_pool / get_cursor — pooled autocommit cursors; PoolError wait-retry; drop dead conns",
            "_MAP_COLUMNS template + _MAP_SELECT / _MAP_SELECT_ONE / _MAP_SELECT_LIGHT",
            "get_map(slug, with_vote_count=True) — light path for resolve_map",
            "get_map_by_subdomain — correlated indexed count",
        ],
    },
    "server/requirements.in": {
        "on": ["Flask API"],
        "module": ("Flask API · deps", "hand-written top-level dependency list (uv two-file flow)"),
        "file": ("requirements.in", "19 lines — one addition"),
        "outline": [("deps", "+ psycogreen", True)],
        "blocks": ["psycogreen — psycopg2 gevent wait callback"],
    },
    "server/requirements.txt": {
        "on": ["Flask API"],
        "module": ("Flask API · deps", "uv-compiled lockfile"),
        "file": ("requirements.txt", "compiled from requirements.in"),
        "outline": [("pins", "+ psycogreen==1.0.2 (no other churn)", True)],
        "blocks": ["psycogreen==1.0.2"],
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">The per-endpoint latency dashboard exposed a shared floor under every API call: a
  full-table vote-count aggregate over 1.8M rows ran on the f1-micro Postgres for each request via
  <code>resolve_map</code>'s map lookup — 320&nbsp;ms medians on endpoints whose real work is
  sub-millisecond. This lands the fix set: the map query split so single lookups use an indexed count
  (or none), a 30&nbsp;s TTL cache that takes Postgres out of the hot path entirely, psycogreen + a real
  connection pool so one slow query no longer freezes the whole gevent worker, a per-instance maps-list
  cache, and graph-votes 304s that answer straight from Redis without loading a graph. Verified locally
  (sub-ms warm endpoints, tests green, 32-greenlet concurrency harness clean); prod deploy pending.</p>

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
    Generated from <code>changelog/changes-latency-opts.diff</code> by <code>changelog/build_latency_opts_report.py</code>.
    Regenerate with <code>git diff -- server/app.py server/database.py server/requirements.in server/requirements.txt &gt; changelog/changes-latency-opts.diff &amp;&amp; python changelog/build_latency_opts_report.py</code>.
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
