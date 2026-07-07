#!/usr/bin/env python3
"""Generate the monitoring-MVP changelog report.

Run from repo root: python changelog/build_monitoring_report.py
Reads changelog/changes-monitoring.diff,
writes changelog/2026-07-07-monitoring-mvp.html

Modeled on build_zoomdrift_report.py (same styles + hierarchical context diagrams).
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-monitoring.diff")
OUT_PATH = os.path.join(HERE, "2026-07-07-monitoring-mvp.html")

DATE = "2026-07-07"
TITLE = "Monitoring MVP — system-health dashboard, uptime check, and 8 alert policies"

DASHBOARD_URL = (
    "https://console.cloud.google.com/monitoring/dashboards/builder/"
    "4528dcba-e7d1-4c41-b385-48e2ed44162a?project=google-mpf-ywspom2sxeey"
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
        "id": "dashboard",
        "tag": "GCP · Cloud Monitoring",
        "title": "1 · The dashboard: a system diagram you can read health off",
        "symptom": (
            "There was no single place to answer “is prod OK, and what is it doing right now?” — memory "
            "of the 8Gi Flask service, whether OSRM's pinned instance is alive, Redis fill against its "
            "1GB LRU cap, request latency percentiles. Each answer meant a console spelunk into a "
            "different product page."
        ),
        "cause": [
            "The GCP project had <em>zero</em> monitoring config: no dashboard, no uptime check, no alert "
            "policies, no notification channel. Every incident so far (OSRM OOM at 2Gi, the app OOM loop, "
            "Redis evictions) was discovered by using the site or reading logs after the fact.",
        ],
        "fixes": [
            "New <a href='" + DASHBOARD_URL + "'>“City Edit — System Health”</a> Cloud Monitoring dashboard "
            "(48-col mosaic), laid out top-to-bottom as the architecture: a markdown header showing the "
            "traffic path (cityedit.org → app → OSRM · Redis · Cloud SQL), then one <strong>health "
            "scorecard per component</strong> — uptime gauge, app P99 latency sparkline, app/OSRM/Redis "
            "memory gauges, SQL CPU gauge — each with yellow/red thresholds, then a metrics section per "
            "component.",
            "App section: requests/s stacked by response class (5xx pops out visually), P50/P95/P99 "
            "latency, per-revision memory (with the 0.9 OOM-threshold line) and CPU, autoscaler instance "
            "count (active vs idle, max 4), and P99 request concurrency.",
            "OSRM: requests by class, P50/P99 latency (sub-ms MLD queries — any drift is a red flag), "
            "memory & CPU on one chart. Redis: usage ratio vs the 0.85 line, connected clients, "
            "commands/s. Cloud SQL: CPU+memory, active connections, disk vs the 0.85 line.",
        ],
        "files": ["terraform/monitoring.tf"],
    },
    {
        "id": "alerts",
        "tag": "GCP · alerting",
        "title": "2 · Uptime check + 8 alert policies, thresholds from measured behavior",
        "symptom": (
            "Silent failure modes we've already been bitten by: the app OOM-looping, OSRM's single pinned "
            "instance dying (routing breaks fleet-wide while the site still looks up), Redis evicting vote "
            "keys past its LRU cap, Postgres going read-only on a full disk."
        ),
        "cause": [
            "Each component has a known cliff, measured in past incidents: app steady-state is "
            "~5.5–6.0Gi of 8Gi (warmup transient OOMs past 90%); OSRM holds ~1.6GB of MLD data in a 4Gi "
            "limit and OOM'd once at 2Gi; Redis serves the heatmap <em>exclusively</em>, so eviction = "
            "missing votes until a rebuild; db-f1-micro disk is small.",
        ],
        "fixes": [
            "Uptime check on <code>https://cityedit.org/health</code> every 60s from 6 global probe "
            "regions. <code>/health</code> 503s when Flask can't reach Redis, so one check covers the "
            "app container, the domain mapping, and the Redis dependency.",
            "8 policies, all emailing <code>eric.didier.bolton@gmail.com</code>, each with a runbook-style "
            "<code>documentation</code> block naming the likely culprit: /health failing (2 min), app 5xx "
            "rate &gt; 0.05/s, app P99 &gt; 5s (5 min), app memory &gt; 90%, OSRM memory &gt; 90%, "
            "<strong>OSRM instance count &lt; 1</strong> (with "
            "<code>evaluation_missing_data = ACTIVE</code> — the metric stops reporting entirely when the "
            "service is down, so “no data” must count as a violation), Redis memory &gt; 85%, SQL disk "
            "&gt; 85%.",
        ],
        "files": ["terraform/monitoring.tf"],
    },
    {
        "id": "applysafety",
        "tag": "Terraform · apply safety",
        "title": "3 · Applying without touching the running app",
        "symptom": (
            "The full plan wanted to “update in-place” the app Cloud Run service — stripping "
            "<code>run.googleapis.com/client-name/client-version</code> annotations left behind by a past "
            "gcloud deploy. Harmless-looking, but any template change mints a new revision of the 8Gi "
            "service (a ~10-min graph warmup) for zero benefit."
        ),
        "cause": [
            "Known drift pattern on this project: the app service gets updated out-of-band via gcloud "
            "(image swaps, overlay deploys), so terraform's view of the template metadata lags reality.",
            "Separately, <code>google_monitoring_dashboard</code> showed a perpetual diff: the API echoes "
            "back an <code>etag</code>, an empty <code>style: {}</code> on text widgets, and strips "
            "proto3 zero-valued fields (<code>xPos: 0</code>, <code>yPos: 0</code>, "
            "<code>lowerBound: 0</code>) — so config and state JSON never matched.",
        ],
        "fixes": [
            "Applied with an explicit 12-resource <code>-target</code> list (the monitoring resources "
            "only); the app service was never touched. Monitoring config is pure Cloud Monitoring API "
            "state — safe to target-apply independently, noted in the file header.",
            "Normalized the dashboard JSON to what the API stores: added the empty <code>style</code> "
            "object, dropped all zero-valued position/bound fields. <code>terraform plan</code> is now "
            "completely clean (“No changes”), so the next full apply won't churn the dashboard either.",
            "Prod DB backed up first per the deploy rule (23MB <code>pg_dump -Fc</code> snapshot via the "
            "bastion tunnel into <code>~/city-edit-prod-backups/</code>) even though nothing here touches "
            "the DB.",
        ],
        "files": ["terraform/monitoring.tf"],
    },
]

SECTIONS.append(
    {
        "id": "perendpoint",
        "tag": "GCP · log-based metrics",
        "title": "4 · P99 by request type, grouped by backing store",
        "symptom": (
            "The service-level P99 says <em>something</em> is slow but not <em>what</em>: a vote should be "
            "a Redis round-trip (tens of ms), a route is an OSRM hop, an address lookup is an in-process "
            "KD-tree query — one number over all of them is unreadable. And reverse-geocode was reported "
            "“a bit slow”."
        ),
        "cause": [
            "Cloud Run's built-in <code>request_latencies</code> metric has no URL label, so it can't be "
            "split by endpoint. The request <em>logs</em> carry <code>httpRequest.latency</code> + "
            "<code>requestUrl</code> for every request — the standard fix is a log-based distribution "
            "metric with the endpoint extracted as a label.",
            "Measured baseline from 7 days of logs (per-endpoint p50/p95/p99): "
            "<code>/api/reverse-geocode</code> 320ms/935ms/1.1s(!), <code>/api/maps</code> "
            "123ms/3.6s/5.5s, <code>/api/graph-votes</code> 183ms/14.9s/19s, "
            "<code>/api/graph-topology</code> 2.6s/3.2s/6.1s, <code>/api/routes</code> 65ms/312ms, "
            "<code>/health</code> 6ms. <code>/api/vote</code> had zero traffic in the window.",
            "Root cause found while digging: <code>resolve_map()</code> → <code>get_map()</code> runs "
            "<code>_MAP_SELECT</code> — which LEFT JOINs a full-table "
            "<code>COUNT(*) … GROUP BY map_slug</code> over <code>edge_votes</code> — against the "
            "db-f1-micro Postgres on <strong>every API request</strong>. That's the shared ~100–300ms "
            "floor under reverse-geocode/routes/vote and the multi-second tail on /api/maps. The "
            "endpoint's own work (KD-tree + ≤5-hop BFS) is sub-millisecond.",
            "Bot scanners constantly probe <code>/api/.env</code>, <code>/api/wp-config.php</code> etc. — "
            "an unfiltered URL label would blow up the metric's cardinality with junk endpoints.",
        ],
        "fixes": [
            "New log-based distribution metric <code>cityedit_api_latency</code> (seconds, 48 exponential "
            "buckets from 1ms): filter whitelists the real API surface + <code>/health</code>; labels "
            "<code>endpoint</code> (path prefix — subpaths like <code>/api/maps/&lt;slug&gt;</code> "
            "collapse into their parent) and <code>method</code>.",
            "New dashboard section “API latency by endpoint”, one chart per backing store so a slow group "
            "points at its dependency: <strong>Redis/Postgres</strong> (vote, graph-votes, my-votes, "
            "route-votes, maps, health), <strong>OSRM</strong> (/api/routes with P50/P95/P99), "
            "<strong>in-process graph provider</strong> (reverse-geocode, nearest-node, graph-topology, "
            "graph-version), <strong>external + static</strong> (geocode/Photon, tiles/PMTiles).",
            "Verified live end-to-end: generated real traffic against prod, and the metric split into 7 "
            "endpoint series (e.g. routes p99 219ms, reverse-geocode 162ms cold / ~10ms warm, "
            "nearest-node 8ms). Note log-based metrics record from creation forward only.",
        ],
        "files": ["terraform/monitoring.tf"],
    }
)

VERIFY = [
    "Targeted apply completed clean: 12 resources created (1 project service, 1 notification channel, "
    "1 uptime check, 8 alert policies, 1 dashboard); the app service was excluded and untouched.",
    "Listed back from the live APIs: the dashboard, the 60s uptime check, and all 8 policies "
    "(<code>enabled: True</code>) exist under project <code>google-mpf-ywspom2sxeey</code>.",
    "Queried real time-series through the Monitoring API: app memory P99 = 0.77 (matches the known "
    "~6Gi/8Gi steady state) and 28/28 uptime probes passing across all 6 regions in the first 20 min.",
    "<code>curl https://cityedit.org/health</code> → 200 in ~100ms.",
    "<code>terraform plan</code> after the JSON normalization: “No changes” — no perpetual dashboard "
    "diff, no app churn pending from this work.",
    "Round 2 (per-endpoint): pulled 2,225 request-log entries over 7 days and computed per-endpoint "
    "percentiles to set the baseline; created the log-based metric + dashboard section via a second "
    "targeted apply (fresh DB snapshot taken first); generated live traffic and read back 7 endpoint "
    "series from the new metric with sensible values.",
]

CHECKLIST = [
    "Open the <a href='" + DASHBOARD_URL + "'>System Health dashboard</a>: the scorecard row should be "
    "all green (uptime gauge ~1.0, app memory gauge ~0.77, Redis/SQL well under thresholds).",
    "Check the App latency chart: P50 should sit far below P99; note the current P99 baseline for future "
    "reference.",
    "Cloud Console → Monitoring → Alerting: 8 policies listed, all enabled, notification channel "
    "“Eric (email)” attached.",
    "You should receive NO alert emails — if one arrives in the next hours, the threshold is telling us "
    "something real (most likely app memory brushing 90% during a warmup).",
    "Optional fire drill: in Alerting, open “City Edit: /health failing” and use “Test notification "
    "channel” to confirm the email lands in your inbox and not spam.",
    "Scroll to “API latency by endpoint”: after a bit of real traffic each chart should show one line "
    "per endpoint (data begins at metric creation — history before that shows nothing).",
    "Use the app normally for a day, then read the Redis/Postgres chart: /api/vote should sit in the "
    "tens of ms; if it tracks /api/maps upward instead, the per-request map-lookup DB query is the "
    "culprit (see the improvement notes in the session summary).",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Before</h3>
      <p>{s['symptom']}</p>
      <h3>Context</h3>
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
    "terraform/monitoring.tf": {
        # Monitoring watches the server-side components; the client is out of scope.
        "on": ["nginx", "Flask API", "OSRM", "Redis"],
        "module": ("terraform · GCP infra", "declarative prod infrastructure (Cloud Run, Memorystore, Cloud SQL, scheduler, bastion) — this file adds the observability layer over all of it"),
        "file": ("monitoring.tf", "~1100 LOC (new) — Cloud Monitoring dashboard-as-code, uptime check, notification channel, log-based endpoint-latency metric, 8 alert policies"),
        "outline": [
            ("google_project_service.monitoring", "enable monitoring.googleapis.com", True),
            ("locals — metric filters", "app / osrm / redis / sql resource-filter strings, built once", True),
            ("notification_channel.email_eric", "email channel for every policy", True),
            ("uptime_check_config.app_health", "GET cityedit.org/health every 60s, 6 regions", True),
            ("logging_metric.api_latency", "per-endpoint latency distribution from request logs, bot-scanner whitelist", True),
            ("alert_policy × 8", "uptime · 5xx · P99 · app mem · OSRM mem · OSRM instances · Redis mem · SQL disk", True),
            ("dashboard.system_health", "48-col mosaic: diagram header → scorecards → app → endpoint-latency → OSRM/Redis/SQL", True),
            ("output dashboard_url", "console deep-link derived from the dashboard id", True),
        ],
        "blocks": [
            "project service — monitoring.googleapis.com enabled",
            "locals — one filter string per monitored resource (cloud_run_revision × 2, redis_instance, cloudsql_database)",
            "email notification channel — eric.didier.bolton@gmail.com",
            "uptime check — /health via HTTPS, 10s timeout, 60s period",
            "app_uptime policy — check_passed FRACTION_TRUE < 0.5 for 2 min",
            "app_5xx_rate policy — 5xx request rate > 0.05/s for 5 min",
            "app_p99_latency policy — ALIGN_PERCENTILE_99 > 5000ms for 5 min",
            "app_memory policy — memory/utilizations P99 > 0.9 (steady state 0.70-0.75)",
            "osrm_memory policy — same shape, 4Gi service",
            "osrm_no_instances policy — instance_count < 1, missing-data counts as violation",
            "redis_memory policy — usage_ratio > 0.85 (allkeys-lru evicts serving keys past 1.0)",
            "sql_disk policy — disk/utilization > 0.85 (postgres goes read-only when full)",
            "logging metric cityedit_api_latency — DELTA distribution (s), endpoint+method labels, whitelist filter",
            "dashboard — jsonencode'd mosaic: text diagram, 6 scorecards, 16 charts across app/endpoints/OSRM/Redis/SQL",
            "endpoint-latency charts — 4 tiles grouped by backing store (Redis+PG · OSRM · in-process graph · external)",
            "dashboard_url output — https://console.cloud.google.com/monitoring/dashboards/builder/<id>",
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

  <p class="lede">Prod gets its first observability layer, all as Terraform: a Cloud Monitoring
  dashboard laid out as the system diagram (traffic path header, one health gauge per component, then
  per-component metric sections), a 60-second uptime check on <code>/health</code>, and 8 alert policies
  with thresholds set from this system's measured cliffs — the app's 90%-of-8Gi OOM line, OSRM's pinned
  single instance, Redis's 1GB LRU eviction point, Postgres's read-only-on-full disk. Applied with a
  targeted <code>-target</code> list so the drifted app service was never touched.</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Green is added, red removed. One new file.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes-monitoring.diff</code> by <code>changelog/build_monitoring_report.py</code>.
    Regenerate after further edits with <code>git diff -- terraform/monitoring.tf &gt; changelog/changes-monitoring.diff &amp;&amp; python changelog/build_monitoring_report.py</code>.
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
