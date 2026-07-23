#!/usr/bin/env python3
"""Generate the counter-cancel changelog report (2026-07-23).

Run from repo root: python changelog/build_counter_cancel_report.py
Reads changelog/changes-counter-cancel.diff (captured with:
  git diff 7ec4a37^..7ec4a37 > changelog/changes-counter-cancel.diff),
writes changelog/2026-07-23-counter-cancel-negative-blocks.html

Modeled on build_counter_lyft_report.py (same styles + hierarchical
context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-counter-cancel.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-counter-cancel-negative-blocks.html")

DATE = "2026-07-23"
TITLE = "Counter-votes go cancel-only — negative blocks eliminated"


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
        "id": "why",
        "tag": "Diagnosis · block layer",
        "title": "1 · The tally floor fixed edges — but blocks count devices",
        "symptom": (
            "A week after the 2026-07-21 tally floor (every net-negative (edge, vote_type) tally "
            "clipped to zero), prod block pins still showed net-negative vote breakdowns. Probing "
            "the served <code>/api/graph-votes</code> payloads: nyc-bikes had <strong>1,171 "
            "(block, vote_type) entries with more downs than ups</strong> (sf 61, chicago 22) — "
            "while only ONE edge in the entire map was net-negative. The floor demonstrably held at "
            "edge level and demonstrably didn't at block level."
        ),
        "cause": [
            "Block counts are not sums of edge tallies. <code>block_votes.py</code> dedupes "
            "<strong>per device</strong>: a block's count for (vote_type, direction) is the number "
            "of distinct devices holding at least one row of that direction inside the block. A "
            "flipped import device with any surviving −1 row therefore counted as one “against” on "
            "that block — no matter how the per-edge sums balanced out.",
            "The floor deleted only the <em>excess</em> down-rows (enough to lift each (edge,vt) "
            "sum to zero). 308,597 counter-cast −1 rows legitimately survived it (302,916 nyc / "
            "3,754 sf / 1,927 chicago) — each one still marking its device “against” at block "
            "level, and each one silently eating one <em>spared</em> upvote on shared corridors "
            "(a flip is a −2 swing: the device's own +1 was already removed by clear-then-cast, so "
            "the −1 subtracts from someone else's surviving signal).",
        ],
        "fixes": [
            "Recognize the correct end-state: the correction should <strong>cancel imported "
            "signal, not manufacture opposition</strong>. A flipped device's own upvote is already "
            "gone — its −1 row carries no information except “subtract one from whoever else "
            "voted here”. Delete them all (§2) and stop casting them (§3).",
        ],
        "files": [],
    },
    {
        "id": "sweep",
        "tag": "Prod ops · 2026-07-23",
        "title": "2 · Sweep: delete every counter-cast −1 row in prod",
        "symptom": (
            "308,597 counter-cast down-rows across the three bike maps, identified by the import "
            "marker (<code>ip_hash = device_id</code> — every counter cast used "
            "<code>ip_from_voter</code>) and <code>direction = -1</code>."
        ),
        "cause": [
            "Safe-to-delete precondition verified first: <strong>zero devices hold both directions "
            "on any (edge, vote_type)</strong> in all three maps — block-scoped clear-then-cast "
            "removed each flipped device's +1 when its −1 landed, so deleting the −1 leaves that "
            "device's contribution at exactly zero. Human downvotes "
            "(<code>ip_hash ≠ device_id</code>) are untouched by the predicate.",
        ],
        "fixes": [
            "Fresh prod snapshot first (<code>~/city-edit-prod-backups/20260723T051700Z/"
            "prod-full-pre-downsweep.dump</code>, 32 MB).",
            "One <code>DELETE</code> per map over the 5433 tunnel (302,916 nyc / 3,754 sf / "
            "1,927 chicago), then per map: Redis aggregate rebuilt "
            "(<code>rebuild_redis_for_map</code>), block state purged (<code>bd:/bagg:/bver</code> "
            "— prod lazily rebuilds from the corrected rows), <code>vote_rev</code> bumped +1000 so "
            "every cached client invalidates.",
            "Verified on the live API: negative (block, vote_type) entries "
            "<strong>nyc 1171 → 0, sf 61 → 1, chicago 22 → 0</strong>. The sf survivor is a single "
            "organic human downvote (real signal, preserved by design), as are the 2 remaining "
            "block downs on nyc and the 1/1/0 net-negative edges.",
            "Side effect, intended: spared upvotes that surviving −1 rows had been eating now show "
            "through — covered corridors read slightly warmer, at their true spared-signal level.",
        ],
        "files": [],
    },
    {
        "id": "cancel",
        "tag": "server · counter_lyft.py",
        "title": "3 · Cancel mode is the new default — flips demoted to --flip",
        "symptom": (
            "Any future counter pass in flip mode would recreate the block-level negatives the "
            "sweep just removed: the NetGate keeps <em>edge</em> tallies ≥ 0, but every flip still "
            "registers its device “against” at block level."
        ),
        "cause": [
            "Flip semantics conflate two operations: removing the device's own credit (correct) "
            "and adding opposition (unwanted). Only removal is needed — and removal is a primitive "
            "the vote API already has: <code>direction=0</code>, block-scoped clear of the "
            "device's own votes.",
        ],
        "fixes": [
            "<code>counter_lyft.py</code> now casts <strong>direction=0 on the covered edges</strong> "
            "by default: the device's own votes on the covered blocks are removed. A removal can "
            "only subtract what the device itself cast, so no (vote_type, edge) tally can go "
            "negative and no device is ever counted “against” — structurally, without a gate.",
            "The tally-gate query (<code>load_net_counts</code>, a full-map aggregate) is skipped "
            "in cancel mode; the legacy behavior stays available behind <code>--flip</code> with "
            "the NetGate guard unchanged. All 15 unit tests pass (NetGate tests still cover the "
            "flip path).",
            "Idempotency preserved: a re-run finds no remaining votes on the covered blocks and "
            "plans no-ops.",
        ],
        "files": ["server/counter_lyft.py"],
    },
    {
        "id": "june",
        "tag": "Prod data · June 2026",
        "title": "4 · Fresh Citibike data: the June 1–7 window, imported and countered",
        "symptom": (
            "Citibike publishes monthly with a lag — June 2026 (<code>202606</code>) is the newest "
            "zip, and prod's only prior sample was ~10k rides from June 24–29 (verified by hashing "
            "sampled ride ids per month against prod device ids: April/May live only in local "
            "imports). July isn't published yet."
        ),
        "cause": [
            "“More data” therefore means a fresh window of June: 10,000 rides sampled "
            "(seed 71) from the 1,283,561 rides of June 1–7.",
        ],
        "fixes": [
            "Imported via a scratch Flask wired to prod (serving-image graphs from digest "
            "<code>e1299fcb…</code>, topology + blocks_version verified identical to live; DB over "
            "the 5433 tunnel, Memorystore over 6380; foot-OSRM :5005): "
            "<strong>10,000/10,000 rides, 0 failures, 1,365,070 segments upvoted</strong> in 66 min.",
            "Counter pass in the new cancel mode over all 20,616 matched rides (new batch + the "
            "already-treated late-June batch, which mostly no-ops): <strong>14,831 rides "
            "countered, 1,119,982 covered upvotes cancelled, 0 downvotes cast</strong>, 0 route "
            "failures, 1 transient vote failure (~0.005%, idempotent rerun would catch it).",
            "End state on the live API: nyc-bikes serves <strong>106,769 net-positive edges</strong> "
            "(was ~37k) with <strong>0 negative block entries</strong> — the new week's "
            "desire-lines are in, and only the pedestrian/counter-one-way stretches got credit.",
        ],
        "files": [],
    },
]

VERIFY = [
    "Live API, all three bike maps: negative (block, vote_type) entries now 0 / 1 / 0 "
    "(nyc / sf / chicago) — the sf entry is one organic human downvote. Net-negative edges 1 / 1 / 0, "
    "all organic.",
    "Precondition held before the sweep: zero devices holding both directions on any (edge, vote_type) "
    "in any of the three maps.",
    "Scratch Flask served prod state exactly before any cast: rev 28128, n_edges 3,305,042, "
    "blocks_version ea883f4121e4ecf8 — all equal to the live endpoint.",
    "June import: 10,000/10,000 rides ok, 0 failed. Counter pass: 0 route failures, 1 vote failure "
    "out of 20,616 rides.",
    "Unit tests: 15/15 pass (tests/unit/test_counter_lyft.py — NetGate coverage now exercises the "
    "--flip path).",
]

CHECKLIST = [
    "Open <code>/m/nyc-bikes</code> in prod, tap several block pins along big avenues (e.g. 1st/2nd Ave "
    "protected-lane corridors): no breakdown should show more downs than ups.",
    "Hard-refresh (or open a private window) if a stale cached body is suspected — the sweep bumped "
    "<code>vote_rev</code> by +1000 per map, so any etag-pinned client re-fetches.",
    "Compare overall heat: covered corridors read slightly warmer than last week (spared upvotes no "
    "longer eaten by surviving −1 rows); pedestrian/park/counter-one-way desire-lines remain the "
    "brightest.",
    "Spot-check new June 1–7 signal: pick a park path (Central Park loop, Hudson River Greenway gaps) "
    "and confirm fresh positive votes.",
    "Optional rerun to converge the 1 failed cast: "
    "<code>DATABASE_URL=&lt;tunnel&gt; python counter_lyft.py --city nyc --map nyc-bikes "
    "--graph-dir &lt;prod-graphs&gt;/nyc --api-base http://localhost:5002</code> (idempotent).",
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
      <h3>What changed</h3>
      <ul>{li(s['fixes'])}</ul>
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/counter_lyft.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/", "bulk-import tooling: import_lyft.py casts the votes, counter_lyft.py cancels the over-credit"),
        "file": ("counter_lyft.py", "~700 LOC — DB join → ride matching → via-guided bike routing → corridor coverage → /api/vote direction=0"),
        "outline": [
            ("Docstring", "the correction model — now stated as cancellation; flip mode documented as legacy", True),
            ("device_of / load_upvotes / load_net_counts", "identity join + the tally the flip gate runs on", False),
            ("NetGate", "running (vote_type, edge) tally guard — now exercised only under --flip", False),
            ("voted_path_vias / covered_edges", "via ordering + 20 m corridor test — untouched", False),
            ("counter_one", "per-vt cast decision: cancel (direction 0) by default, gated flips under --flip", True),
            ("main()", "--flip flag; gate query skipped in cancel mode", True),
        ],
        "blocks": [
            "docstring — cancellation is the contract: remove the device's own votes, never manufacture opposition",
            "counter_one — args.flip ? gate.take(...) : (flips=[], zeros=overlap): covered edges become direction-0 removals",
            "main() — --flip argument; NetGate({}) placeholder in cancel mode (no full-map tally query)",
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

  <p class="lede">Imported Citibike trips are routed through the foot profile on purpose — the divergence
  between how a pedestrian moves and where a bike is allowed to go IS the vote. But the ingest upvoted the
  whole pedestrianized path, so corridors that already carry bikes (and especially streets that already
  have bike lanes, densest near Citibike stations) got the same +1 as the genuinely un-bikeable stretches.
  The correction: a second, bike-legality OSRM dataset (stock v5.25.0 bicycle profile flattened to
  shortest-legal-path, pushing-the-bike disabled) re-routes every ingested ride pinned to its own voted
  corridor via via-points; every upvoted edge lying bodily inside the resulting route's 20 m corridor gets
  a <code>direction=-1</code> cast from the ride's own voter identity. What survives upvoted is exactly
  what can't be ridden: park and plaza paths, stairs, and one-way streets taken against the flow.</p>

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
    Generated from <code>changelog/changes-counter-lyft.diff</code> by <code>changelog/build_counter_lyft_report.py</code>.
    Regenerate after further edits with
    <code>git diff dc81a6c -- osrm/bicycle-flat.lua scripts/build_bike_osrm.sh server/counter_lyft.py server/tests/unit/test_counter_lyft.py server/app.py server/database.py .gcloudignore &gt; changelog/changes-counter-lyft.diff &amp;&amp; python changelog/build_counter_lyft_report.py</code>.
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
