#!/usr/bin/env python3
"""Generate the staging-parity changelog report (2026-07-23).

Run from repo root: python changelog/build_staging_parity_report.py
Reads changelog/changes-staging-parity.diff (captured with:
  git diff 7ec4a37..HEAD -- server/app.py deploy/nginx-cloudrun.conf \
    client-react/src/App.tsx client-react/src/map/runtime.ts \
    client-react/src/styles/globals.css terraform/staging.tf Makefile \
    docs/gcp-deployment.md CLAUDE.md docs/staging-parity-plan.md \
    > changelog/changes-staging-parity.diff),
writes changelog/2026-07-23-staging-parity.html

NOTE: the staging URL/token is a SECRET — it must never appear in this
builder, the diff, or the generated report.

Modeled on build_block_disjoint_report.py (same styles + context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-staging-parity.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-staging-parity.html")

DATE = "2026-07-23"
TITLE = "Staging ↔ prod parity: an unguessable staging twin, and digest promotion as the new deploy path"


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
        "id": "envflag",
        "tag": "Code · same image, two behaviors",
        "title": "1 · One env flag, three effects: redirect gate, noindex, ribbon",
        "symptom": (
            "Staging runs the EXACT image digest prod runs — that's the parity point — so every "
            "staging-specific behavior must be env/host-keyed, never baked in. Without a gate, opening any "
            "preset map on the staging host would hit the canonical-subdomain redirect in MapApp and bounce "
            "the tester straight to prod (e.g. /m/nyc-walkways → walkways.cityedit.org). And an "
            "unguessable URL stays unguessable only if it is never indexed after an accidental link."
        ),
        "cause": [
            "<strong>Server:</strong> <code>IS_STAGING</code> (env <code>APP_ENV=staging</code>, set only "
            "by the staging terraform) adds <code>staging: true</code> to every map config via "
            "<code>_enrich_map</code> — the single choke point all config endpoints flow through — "
            "and an <code>after_request</code> hook stamps <code>X-Robots-Tag: noindex, nofollow</code> on "
            "every Flask response.",
            "<strong>Client:</strong> MapApp skips <code>subdomainRedirectUrl</code> when the resolved "
            "config carries the flag; a fixed STAGING ribbon (bottom-left, hazard-striped, "
            "pointer-events: none) renders so screenshots can never be mistaken for prod.",
            "<strong>nginx:</strong> Flask can't header the statics nginx serves itself (SPA shell, "
            "/assets, pmtiles), and the config is identical in both environments — so a "
            "<code>map $host</code> keys the robots header off the <code>ce-stg-</code> hostname prefix. "
            "nginx drops an <code>add_header</code> whose value is empty, so prod hosts are a structural "
            "no-op. The header is repeated inside every location that declares its own add_header, because "
            "nginx add_header inheritance is all-or-nothing.",
        ],
        "fixes": [
            "Flag OFF (prod, local dev): no <code>staging</code> key in configs, no robots header, "
            "redirect behavior unchanged — verified by diffing responses with and without "
            "<code>APP_ENV=staging</code> locally, and by a headless prod check post-deploy "
            "(cityedit.org/m/nyc-bikes still lands on bikepaths.cityedit.org).",
        ],
        "files": [
            "server/app.py",
            "client-react/src/App.tsx",
            "client-react/src/map/runtime.ts",
            "client-react/src/styles/globals.css",
            "deploy/nginx-cloudrun.conf",
        ],
    },
    {
        "id": "terraform",
        "tag": "Infra · 14 adds, 0 changes, 0 destroys",
        "title": "2 · The staging stack: duplicate state, share the stateless",
        "symptom": (
            "Staging needs to predict prod, which means its own copies of everything that carries state "
            "— and shared everything that doesn't. Duplicated: the Cloud Run app service (same "
            "8Gi/4CPU/concurrency-200 shape; minScale 0 / maxScale 2), a second Memorystore Basic 1GB "
            "(Redis keys aren't env-namespaced, sharing prod's is impossible; ~$35/mo, the main cost), a "
            "<code>votes_staging</code> database + user co-located on the prod Cloud SQL instance, and "
            "staging-scoped admin-token/secret-key secrets. Shared: OSRM (stateless, the default compute "
            "SA already holds run.invoker), the previews bucket (read-only), and the image registry."
        ),
        "cause": [
            "<strong>The URL is the secret.</strong> The service name carries a random token "
            "(<code>ce-stg-&lt;token&gt;</code>, from gitignored terraform.tfvars) — its run.app URL "
            "rides Google's shared wildcard cert, so the hostname is never published. A "
            "<code>stg.cityedit.org</code> domain mapping was rejected: per-hostname managed certs land in "
            "Certificate Transparency logs, where anyone watching <code>%.cityedit.org</code> reads the "
            "“unguessable” name within hours. Rotation = new token, targeted apply, delete old.",
            "<strong>OSRM via data source, not resource reference:</strong> referencing "
            "<code>google_cloud_run_service.osrm</code> drags prod OSRM into every -target closure — "
            "and its gcloud-stamped client-name annotation drift meant the first “staging-only” "
            "plan wanted to roll a new prod OSRM revision. A <code>data</code> lookup keeps staging plans "
            "strictly additive (the re-plan: 14 add / 0 change / 0 destroy).",
            "<strong>terraform stays out of the deploy loop:</strong> the image is seeded from "
            "<code>:latest</code> once; <code>lifecycle.ignore_changes</code> on the image lets gcloud "
            "push digests (staging first, then prod) without the next plan fighting them.",
        ],
        "fixes": [
            "Applied with -target only, after verifying the plan showed adds only — the blanket-apply "
            "landmines (secret wipe, ebikes domain mapping) still stand and are re-documented in "
            "staging.tf's header.",
        ],
        "files": ["terraform/staging.tf"],
    },
    {
        "id": "workflow",
        "tag": "Process · deploy staging-first",
        "title": "3 · Seeding, stage-refresh, and digest promotion",
        "symptom": (
            "Today's cold-load work was measured live on prod because there was nowhere else to measure "
            "it. The payoff of the twin: build the overlay → deploy the digest to staging → "
            "verify (load waterfall, smoke checks, [MAPLOAD] beacons filtered to the staging service "
            "name) → fresh prod backup → promote the SAME digest to prod → asset-hash check."
        ),
        "cause": [
            "<strong>Seed:</strong> pg_restore of the newest prod dump into <code>votes_staging</code> "
            "through the existing bastion tunnel (same instance, different database). No resnap needed: "
            "same image ⇒ same graphs ⇒ same edge ids — the invariant that already powers "
            "overlay deploys.",
            "<strong>Redis hydrates itself:</strong> <code>_populate_redis</code> at boot and "
            "<code>_hydrate_map_redis</code> on each map's first request replay Postgres → Redis, so "
            "an initial seed needs no restart at all.",
            "<strong>make stage-refresh:</strong> restore newest dump + FLUSHALL staging Redis (needs the "
            ":5433 Cloud SQL and :6380 staging-Redis bastion tunnels). The flush matters on RE-refresh: "
            "the hydration paths only fire on empty/underpopulated hashes, so stale staging Redis data "
            "would silently survive a reseed without it.",
        ],
        "fixes": [
            "docs/gcp-deployment.md gains the “Staging (deploy here FIRST)” section + promotion "
            "commands; CLAUDE.md tells future sessions to deploy staging-first and to treat the staging "
            "URL as a secret (looked up via <code>terraform output -raw staging_url</code>, never "
            "committed).",
        ],
        "files": ["Makefile", "docs/gcp-deployment.md", "CLAUDE.md"],
    },
]

VERIFY = [
    "Targeted terraform plan re-run after the OSRM data-source fix: 14 to add, 0 to change, 0 to destroy; "
    "apply completed cleanly and the service went Ready on the first revision.",
    "Seeded votes_staging from the 2026-07-23 16:15Z prod dump: 869,139 edge_votes / 16 maps / 58 "
    "vote_types (the only restore error was the benign <code>SET transaction_timeout</code> — a "
    "newer-pg_dump directive Postgres 15 ignores).",
    "Staging serves real data end-to-end: /api/maps/nyc-walkways voteCount 28,979; sparse graph-votes "
    "with 8,244 edge pairs + 10 legend entries (lazy Redis hydration from the seeded DB, no restart "
    "needed).",
    "Headless staging run: cold loader-dismiss 3.9s / warm 1.0s (prod's post-00105 profile), host stays "
    "on staging (no prod bounce despite nyc-walkways having a canonical subdomain), STAGING ribbon "
    "renders, heatmap painted.",
    "X-Robots-Tag present on staging API responses AND nginx-served statics (SPA shell, hashed assets); "
    "absent everywhere on prod. <code>staging</code> key absent from prod configs.",
    "Digest promotion exercised for real: the overlay digest was deployed to staging, verified, then "
    "promoted to prod (rev 00107) after a fresh backup — both now serve identical asset hashes "
    "(<code>index-C4L56-9s.js</code>). Headless prod check: the subdomain redirect still fires.",
    "<code>npx tsc --noEmit</code> clean; nginx config validated with <code>nginx -t</code> in docker; "
    "app.py parses.",
]

CHECKLIST = [
    "Open the staging URL (get it: <code>cd terraform &amp;&amp; terraform output -raw staging_url</code>) "
    "— the map should load with the yellow STAGING ribbon bottom-left and NOT redirect to prod.",
    "Cast a test vote on staging, reload — it should persist there; then check the same street on "
    "prod — it must NOT appear (separate Redis + separate database).",
    "Open cityedit.org/m/nyc-bikes in a fresh tab — confirm it still redirects to "
    "bikepaths.cityedit.org (prod behavior unchanged).",
    "Spot-check prod after rev 00107: heatmap loads, votes cast fine, no STAGING ribbon anywhere.",
    "Next deploy: follow the new staging-first flow in docs/gcp-deployment.md and see whether it catches "
    "anything before prod does.",
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
        "on": ["Flask API"],
        "module": ("API entrypoint", "routes, map-config enrichment, vote snapshot cache, prewarm"),
        "file": ("app.py", "~2400 LOC — map resolution/caches, graph-votes/topology routes, admin, WS hub"),
        "outline": [
            ("imports / env config", "DATABASE_URL, ADMIN_TOKEN … now also APP_ENV", False),
            ("vote snapshot cache + prewarm", "unchanged (rev 00105 SWR shape)", False),
            ("NEW IS_STAGING + after_request", "flag + X-Robots-Tag on every Flask response", True),
            ("_enrich_map", "city config + searchVoteTypes … now also staging:true", True),
            ("map_get / by-subdomain / list", "all flow through _enrich_map (unchanged)", False),
            ("graph-votes / topology / admin", "unchanged", False),
        ],
        "blocks": [
            "IS_STAGING = os.environ.get(\"APP_ENV\") == \"staging\" + _staging_noindex after_request hook",
            "_enrich_map — if IS_STAGING: m[\"staging\"] = True (single choke point for all config endpoints)",
        ],
    },
    "client-react/src/App.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("App shell", "map resolution → providers → map subtree; landing vs map routing"),
        "file": ("App.tsx", "~160 LOC — FullScreenLoader, AppContent, MapApp (config resolve + redirect), App"),
        "outline": [
            ("FullScreenLoader", "splash + spinner", False),
            ("AppContent", "topbar/map/toasts … now also the STAGING ribbon", True),
            ("MapApp · config resolve", "subdomain redirect now gated on !resolved.staging", True),
            ("MapApp · passcode flow", "unchanged", False),
            ("App", "landing-vs-map routing (unchanged)", False),
        ],
        "blocks": [
            "if (resolved?.subdomain && !resolved.staging) — the redirect gate",
            "getCurrentMap()?.staging && <div class=\"staging-ribbon\">STAGING</div>",
        ],
    },
    "client-react/src/map/runtime.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Runtime map resolution", "URL → map config fetch → CONFIG rebind"),
        "file": ("runtime.ts", "~250 LOC — MapConfig type, resolveMapConfig, applyMap"),
        "outline": [
            ("MapConfig interface", "gains optional staging?: boolean", True),
            ("resolve/fetch/apply", "unchanged", False),
        ],
        "blocks": [
            "staging?: boolean — server-driven flag; typed so the gate and ribbon read it safely",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("Global styles", "design tokens, layout, map chrome"),
        "file": ("globals.css", "~2250 LOC — tokens, topbar/sidebar, graph-vote chips, animations"),
        "outline": [
            ("tokens / layout / components", "unchanged", False),
            ("NEW .staging-ribbon", "fixed bottom-left hazard-striped badge, pointer-events none", True),
            ("animations", "unchanged", False),
        ],
        "blocks": [
            ".staging-ribbon — repeating-linear-gradient hazard stripes, z-index 3000, non-interactive",
        ],
    },
    "deploy/nginx-cloudrun.conf": {
        "on": ["nginx"],
        "module": ("Prod nginx", "static serving, API proxy, tile cache, brotli"),
        "file": ("nginx-cloudrun.conf", "~180 LOC — compression, upstream, tile cache, locations"),
        "outline": [
            ("compression / upstream / tile cache", "unchanged", False),
            ("NEW map $host $staging_robots", "ce-stg-* hosts → \"noindex, nofollow\", else empty", True),
            ("server block + static locations", "add_header X-Robots-Tag $staging_robots always (×5)", True),
            ("proxied /api locations", "unchanged — Flask adds the header itself on staging", False),
        ],
        "blocks": [
            "map $host $staging_robots — empty value on prod hosts means nginx omits the header entirely",
            "add_header repeated in /assets/, /, @baked_previews, pmtiles — add_header inheritance is all-or-nothing",
        ],
    },
    "terraform/staging.tf": {
        "on": ["Flask API", "Redis"],
        "module": ("Infrastructure", "the staging environment, additively beside prod's main.tf"),
        "file": ("staging.tf (NEW)", "~300 LOC — Redis, DB, secrets, Cloud Run service, IAM, outputs"),
        "outline": [
            ("header — apply rules", "-target only; adds-only; deploy via gcloud", True),
            ("data osrm_live", "URL lookup without dragging prod OSRM into the plan", True),
            ("cache_staging (Redis 1GB)", "allkeys-lru twin — the ~$35/mo item", True),
            ("votes_staging db + user", "co-located on the prod Cloud SQL instance", True),
            ("staging secrets ×3 + IAM", "database-url / admin-token / secret-key -staging", True),
            ("app_staging service", "prod shape, minScale 0/maxScale 2, APP_ENV=staging, ignore_changes image", True),
            ("outputs", "staging_url (sensitive — the URL IS the secret), staging_redis_host", True),
        ],
        "blocks": [
            "data.google_cloud_run_service.osrm_live — keeps staging plans strictly additive",
            "google_cloud_run_service.app_staging — name ce-stg-${var.staging_token}",
            "lifecycle { ignore_changes = [ …containers[0].image ] } — gcloud owns the digest",
        ],
    },
    "Makefile": {
        "on": ["Flask API", "Redis"],
        "module": ("Dev/ops targets", "deps, dev loop, deploy, tests, terraform"),
        "file": ("Makefile", "~170 LOC"),
        "outline": [
            ("deps / dev / test targets", "unchanged", False),
            ("NEW stage-refresh", "newest dump → votes_staging + FLUSHALL staging Redis", True),
            ("terraform / monitoring targets", "unchanged", False),
        ],
        "blocks": [
            "stage-refresh — guards on both tunnels (:5433 SQL, :6380 staging Redis), pg_restore --clean --if-exists, FLUSHALL",
        ],
    },
    "docs/gcp-deployment.md": {
        "on": ["Flask API"],
        "module": ("Ops docs", "deploy guide, DB access, domains, troubleshooting"),
        "file": ("gcp-deployment.md", "deploy guide"),
        "outline": [
            ("Deploying Changes", "now points at staging-first", True),
            ("NEW Staging section", "URL secrecy, digest promotion, seeding, tunnels", True),
            ("Environment & Secrets / backups / domains", "unchanged", False),
        ],
        "blocks": [
            "\"Staging (deploy here FIRST)\" — the promotion workflow + stage-refresh tunnels",
        ],
    },
    "CLAUDE.md": {
        "on": ["Flask API"],
        "module": ("Project instructions", "session-level rules for Claude"),
        "file": ("CLAUDE.md", "project instructions"),
        "outline": [
            ("deploy rules", "backup-first unchanged; NEW staging-first bullet", True),
            ("everything else", "unchanged", False),
        ],
        "blocks": [
            "Deploy STAGING-FIRST bullet — promote the same digest; the URL is a secret, never committed",
        ],
    },
    "docs/staging-parity-plan.md": {
        "on": ["Flask API"],
        "module": ("Planning doc", "the full plan this workstream executed"),
        "file": ("staging-parity-plan.md (NEW)", "goal, topology, URL options, costs, rollout order"),
        "outline": [
            ("plan", "written first, then executed step-by-step this session", True),
        ],
        "blocks": [
            "The CT-log argument for a randomized run.app name over a vanity subdomain lives here",
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

  <p class="lede">City Edit now has a staging twin: the exact image digest prod runs, its own Redis and
  database seeded from a fresh prod dump, at a random-token run.app URL that never appears in any
  public record (Certificate Transparency killed the pretty-subdomain option). One env flag makes the
  shared image behave: staging maps skip the canonical-subdomain redirect that would eject testers to
  prod, every response is noindexed, and a hazard-striped STAGING ribbon marks the screenshots. The
  deploy path is now build &rarr; staging &rarr; verify &rarr; promote the same digest to prod &mdash;
  exercised end-to-end today: the staging-flag overlay itself was verified on staging before landing on
  prod as rev 00107.</p>

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
    Generated from <code>changelog/changes-staging-parity.diff</code> by <code>changelog/build_staging_parity_report.py</code>.
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
