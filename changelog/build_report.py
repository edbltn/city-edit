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
OUT_PATH = os.path.join(HERE, "2026-08-02-screenshot-job-oom-cascade.html")

DATE = "2026-08-02"
TITLE = "The screenshot job that reported green while capturing nothing"


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
        "id": "cascade",
        "tag": "INFRA / JOBS",
        "title": "One OOM was failing every remaining map in the run",
        "symptom": "No preview existed for <code>nyc-proposals</code> (the Landing card 404'd outright), and philly-trees / philly-walkways served an 11 KB screenshot of the &quot;Loading\u2026&quot; splash dated 2026-07-09. Meanwhile the daily job reported a green <code>1/1 complete</code> every single morning.",
        "cause": [
            "capture.py drove all 13 maps through ONE shared Chromium page. Memory grew across sequential navigations of these heatmaps until the 2Gi container OOM'd \u2014 and because the page was never recreated, every map after the crash died instantly with <code>Page.goto: Page crashed</code>",
            "The crash point walked BACKWARDS as the heatmaps grew: map 12 (philly-bikes) on 07-19 and 07-22, map 1 (nyc-bikes) by 07-30, and mid-capture on map 1 by 08-02. Recent runs were capturing nothing at all",
            "nyc-proposals is 5th in the capture order and was only renamed from nyc-crossings on 07-30 \u2014 by which point the crash had already reached map 1, so it was NEVER captured once under its current slug",
            "No baked fallback exists for it either (client-react/public/previews has no nyc-proposals.png), so nginx's @baked_previews could not cover the gap and the card 404'd",
        ],
        "fixes": [
            "A throwaway browser per map (capture_map): bounds memory regardless of how heavy the maps get, and contains a crash to the map that caused it instead of failing the other twelve",
            "<code>--disable-dev-shm-usage</code> \u2014 containers give /dev/shm only 64 MB, which Chromium reports as an opaque renderer crash",
            "Job sizing follows the new shape: <strong>8Gi</strong> (nyc-trees still crashed the renderer at 4Gi even alone in the container), <strong>timeout 1800s</strong> (300s could not fit even the old shared-page run, which took ~5 min), and <strong>max_retries 0</strong> since capture failures are deterministic \u2014 a retry only doubled a long run",
        ],
        "files": ["screenshots/capture.py", "terraform/main.tf"],
    },
    {
        "id": "junk",
        "tag": "CORRECTNESS",
        "title": "Publishing a screenshot of the loading spinner over a good preview",
        "symptom": "philly-trees and philly-walkways were frozen on a picture of the app's &quot;Loading\u2026&quot; splash \u2014 11,011 bytes against ~950 KB for a real render \u2014 and had been since 2026-07-09.",
        "cause": [
            "On canvas-check timeout capture.py printed &quot;capturing anyway&quot; and published the result regardless, so a slow cold load overwrote a good preview with a spinner",
            "The canvas-content check CANNOT gate publication on its own: it waits for a non-transparent pixel on a Leaflet canvas, and maps with zero votes never paint heat. All three philly maps have 0 nonzero edges out of 1,177,930 \u2014 philly-bikes only looked fine because its basemap had painted by the time the 30s check gave up",
            "Nothing ever overwrote the bad image afterwards because the OOM cascade meant the run never reached maps 12 and 13 again",
        ],
        "fixes": [
            "Wait for the <code>.map-bootstrap</code> splash to clear (present-or-visible check, 60s) \u2014 the one reliable &quot;app is past loading&quot; signal that does not depend on vote data",
            "<code>MIN_PNG_BYTES = 60_000</code> publish gate: a 1400\u00d7900 basemap is never 11 KB, so an unpainted render is refused rather than overwriting a good preview",
            "Passcode-gated maps are skipped entirely \u2014 <code>nyc-ebike-charging</code> has <code>requiresPasscode: true</code> and can only ever render an unlock prompt; a public preview would defeat the gate",
            "Preset maps fall back to their slug route if the subdomain does not load",
        ],
        "files": ["screenshots/capture.py"],
    },
    {
        "id": "silent",
        "tag": "OBSERVABILITY",
        "title": "Why nobody noticed for six weeks",
        "symptom": "Every execution since at least 2026-07-19 reported <code>\u2714 1/1 complete</code>. The last fully-healthy run was 2026-07-22.",
        "cause": [
            "Each map was wrapped in a per-map try/except that printed to stderr and moved on, and main() never inspected the outcome \u2014 so a run where 12 of 13 maps failed still exited 0",
            "Cloud Run only surfaces the process exit code, so the scheduler, the console, and any alerting all saw a healthy job",
        ],
        "fixes": [
            "main() now tracks captured/failed, prints <code>Done. Captured N/M</code>, and exits non-zero when any map fails \u2014 a partial run shows up RED",
            "The very first post-fix run proved the point: 11/13 with a red X, which is exactly how the two genuinely-broken maps got found and fixed",
            "sync_to_gcs no longer swallows its own exceptions \u2014 a failed upload used to count as a success",
        ],
        "files": ["screenshots/capture.py"],
    },
]



VERIFY = [
    "Root cause reproduced from the job's own logs: the 2026-07-22 run shows <code>Out-of-memory event detected in "
    "container</code> followed by <strong>12 consecutive <code>Page.goto: Page crashed</code></strong> errors \u2014 "
    "and the Cloud Run retry restarted at map 1 and OOM'd again immediately.",
    "The crash point demonstrably walked backwards: canonical bucket objects are dated <strong>07-22</strong> for most "
    "slugs, <strong>07-30</strong> for nyc-bikes alone, and <strong>07-09</strong> for philly-trees/walkways.",
    "The junk-preview mechanism confirmed by data, not inference: all three philly maps report "
    "<strong>0 nonzero edges out of 1,177,930</strong> via /api/graph-votes, so the canvas check can never pass there.",
    "New code exercised locally against prod BEFORE building the image: philly-trees went "
    "<strong>11,011 \u2192 955,310 bytes</strong> (a real Philadelphia street network, not a spinner) and nyc-proposals "
    "captured at 1,209,969 bytes.",
    "The reject path was tested rather than assumed \u2014 with MIN_PNG_BYTES monkeypatched to 1e9 the run refused to "
    "write the file, printed <code>Failed: philly-trees</code>, and <strong>exited 1</strong>.",
    "Deploy discipline: prod DB backed up first (<code>~/city-edit-prod-backups/20260802-221945/prod-full.dump</code>, "
    "27 MB), and terraform applied with <code>-target</code> \u2014 plan showed <strong>0 to add, 1 to change, "
    "0 to destroy</strong>, touching only memory/timeout/max_retries.",
    "First post-fix prod run: <strong>11/13, red X</strong> \u2014 no cascade, and the honest exit code immediately "
    "surfaced the two real failures (nyc-trees renderer crash at 4Gi; nyc-ebike-charging is passcode-gated).",
    "Final prod run after 8Gi + private-map skip: <strong>Done. Captured 12/12</strong>, job green, and all 12 public "
    "<code>/previews/*.png</code> serve real renders (nyc-proposals 1,209,835 b; philly-trees 956,372 b). "
    "nyc-ebike-charging still 404s \u2014 correct, it is private.",
]

CHECKLIST = [
    "Open <code>https://cityedit.org/</code> and confirm the <strong>NYC Proposals</strong> card now shows a map "
    "(terracotta corridors + proposal pins over Manhattan) instead of a broken image.",
    "Check the two previously-frozen cards \u2014 <strong>philly-trees</strong> and <strong>philly-walkways</strong> "
    "should show the Philadelphia street grid, not a &quot;Loading\u2026&quot; spinner.",
    "Run <code>gcloud run jobs executions list --job=map-screenshot-prod --region=us-central1 "
    "--project=google-mpf-ywspom2sxeey --limit=1</code> \u2014 expect a green \u2714, and note that from now on a "
    "partial run will show a RED X rather than a misleading 1/1 complete.",
    "Confirm the log ends with <code>Done. Captured 12/12</code> and <code>Skipping 1 private map(s): "
    "nyc-ebike-charging</code> \u2014 that line is the new expected steady state, not a failure.",
    "Sanity-check tomorrow's 06:00 America/New_York scheduled run lands the same 12/12 unattended.",
    "Identical byte sizes across philly-* (956,372) and dc-* (911,284) are EXPECTED, not a bug: voteless maps of the "
    "same city render the same basemap at the same center and zoom.",
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
    "screenshots/capture.py": {
        "on": ["React / Leaflet client"],
        "module": ("Jobs \u00b7 screenshots/", "Playwright/Chromium capture of one preview PNG per map, synced to GCS"),
        "file": ("capture.py", "~200 LOC \u2014 map discovery, per-map capture, GCS sync, CLI"),
        "outline": [
            ("constants", "+ MIN_PNG_BYTES, BOOTSTRAP_TIMEOUT_MS, BROWSER_ARGS", True),
            ("fetch_maps", "unchanged \u2014 /api/maps with a preset fallback", False),
            ("map_url \u2192 map_urls", "returns candidates: subdomain first, slug as fallback", True),
            ("capture_url", "+ wait for the .map-bootstrap splash to clear", True),
            ("sync_to_gcs", "no longer swallows upload failures", True),
            ("capture_map (new)", "throwaway browser per map, walks the candidate URLs", True),
            ("main", "private-map skip, size gate, captured/failed tally, non-zero exit", True),
        ],
        "blocks": [
            "MIN_PNG_BYTES = 60_000 \u2014 a 1400x900 basemap is never 11 KB, so an unpainted render is refused",
            "BROWSER_ARGS = --disable-dev-shm-usage \u2014 the 64 MB container /dev/shm reads as an opaque renderer crash",
            "map_urls: preset maps keep the subdomain but gain the slug route as a fallback",
            "wait_for_function on .map-bootstrap (absent OR offsetParent === null) \u2014 handles unmount and hide alike",
            "capture_map: fresh browser per map; a crash is contained to the map that caused it",
            "requiresPasscode maps skipped \u2014 they render an unlock prompt, never a .leaflet-container",
            "sys.exit(1) when any map failed \u2014 kills the misleading green 1/1 complete",
        ],
    },
    "terraform/main.tf": {
        "on": ["Flask API"],
        "module": ("Terraform \u00b7 prod infra", "Cloud Run services + jobs, Cloud SQL, Redis, schedulers, buckets"),
        "file": ("main.tf", "~815 lines \u2014 the screenshot job block sits around :745"),
        "outline": [
            ("services / SQL / Redis", "unchanged", False),
            ("google_cloud_run_v2_job.screenshot", "memory 2Gi \u2192 8Gi, timeout 300s \u2192 1800s, max_retries 1 \u2192 0", True),
            ("scheduler / IAM / outputs", "unchanged", False),
        ],
        "blocks": [
            "memory 2Gi \u2192 8Gi \u2014 nyc-trees still crashed the renderer at 4Gi even alone in the container",
            "timeout 300s \u2192 1800s \u2014 300s could not fit even the old run (~5 min for 13 maps)",
            "max_retries 1 \u2192 0 \u2014 capture failures are deterministic; the retry only doubled a long run",
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

  <p class="lede">Three fixes to Top Proposals: a pin now takes real support (&gt;100 net votes, both point and route families); every modal badges the vote types that are CURRENT top proposals for what it shows (square = point, diamond = route fully inside the selection); and route corridors are now grown ROUTING-CONSISTENTLY — an extension either keeps the segment a shortest path or pins a ghost waypoint (max 3), and the whole waypoint chain lands in the URL, so a shared top-proposal link keeps routing into its corridor long after the proposal itself has churned away.</p>

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
