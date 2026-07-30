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
OUT_PATH = os.path.join(HERE, "2026-07-30-nyc-proposals-import.html")

DATE = "2026-07-30"
TITLE = "nyc-proposals: map rename, official DOT proposal import + weekly job, per-map proposal floor"


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
        "id": "rename",
        "tag": "DATA / ROUTING",
        "title": "nyc-crossings → nyc-proposals (rename + QR chain)",
        "symptom": "The dangerous-intersections map was being broadened to cover ALL city proposals; the printed QR posters point at /m/nyc-intersections and had to keep resolving.",
        "cause": [
            "Slug renames are data-driven (map_redirects) since 2026-07-29 — no code change needed",
            "rename_map_slug's chain flattening repoints any redirect targeting the old slug, so nyc-intersections → nyc-proposals kept its src=qr-poster retro-tag",
        ],
        "fixes": [
            "rename_map.py nyc-crossings nyc-proposals run against prod, staging, and local (73/73/2 votes moved; Redis rebuilt under the new slug)",
            "Display name/subtitle updated to \"NYC Proposals\" / \"Every proposal to improve the city, in one place\"",
            "Redirect inventory rows updated in docs/url-routing.md",
        ],
        "files": ["docs/url-routing.md"],
    },
    {
        "id": "floor",
        "tag": "CLIENT + SERVER",
        "title": "Per-map top-proposal support floor override",
        "symptom": "The same-day TOP_PROPOSAL_MIN_NET=100 floor (536bf76) would hide every imported proposal: one vote per DOT project means net 1, and block heat only paints top proposals — the import would have been invisible.",
        "cause": [
            "The floor is the right default for crowdsourced maps but wrong for curated/imported ones",
            "It gates BOTH families: PBTP winner selection and RBTP minRouteScore",
        ],
        "fixes": [
            "maps.top_proposal_min_net (nullable INT) → served as topProposalMinNet on the map config only when set",
            "GraphLayer resolves getCurrentMap()?.topProposalMinNet ?? TOP_PROPOSAL_MIN_NET and threads it through both proposal families",
            "nyc-proposals rows set to 0 on local/staging/prod (data change; composes with the floor workstream rather than reverting it)",
        ],
        "files": ["server/database.py", "client-react/src/map/runtime.ts",
                  "client-react/src/components/GraphLayer/GraphLayer.tsx"],
    },
    {
        "id": "import",
        "tag": "TOOLS / INFRA",
        "title": "Official DOT proposals imported as votes (60 cast) + weekly job",
        "symptom": "Get every official NYC street-change proposal onto the map, one vote each, and keep it fresh weekly.",
        "cause": [
            "nycdotprojects.info is the freshest proposal source (docs/nyc-proposal-data-sources.md) but mixes real project pages with outreach/blog posts under bare slugs",
            "Route-kind vote types only surface as corridors — a point cast of one would be invisible by design",
            "Same-named streets across boroughs make naive geocoding cast cross-borough 'corridors' (5th Ave & 57 St hit Sunset Park)",
        ],
        "fixes": [
            "import_to_map.py: plan (geocode via the app's /api/geocode + classify + two-sided junk filter) → cast (public /api/vote, voter dotproj:<sha1(url)>, ip_from_voter)",
            "Corridor projects route via /api/routes and cast on the edge ids (8 corridors); guards: ≤8km endpoint spread, ≤600 edges, Manhattan-first retry for E/W-numbered cross streets",
            "Route-kind entries without parseable endpoints demote to point-kind 'Street redesign' so they still pin (52 points)",
            "weekly_import.py + Dockerfile + terraform/dot-import-job.tf: Cloud Run job dot-proposals-import-prod, scheduler Mondays 07:00 NY, 8-day overlapping window, idempotent",
            "Shipped via overlay deploy (digest 9ed734b9…) staging-first, then 60 proposals cast on staging and prod (1550 voted edges, 9 vote types)",
        ],
        "files": ["tools/nyc_proposals/import_to_map.py", "tools/nyc_proposals/weekly_import.py",
                  "tools/nyc_proposals/Dockerfile", "terraform/dot-import-job.tf",
                  "docs/nyc-proposal-data-sources.md"],
    },
]



VERIFY = [
    "droast over all 10 Dockerfiles: <strong>exit 0</strong> — one intentional info remains (no EXPOSE on the "
    "screenshots batch job, which has no port).",
    "<code>docker build --check</code> (BuildKit lint) on all 10: clean except two pre-existing, by-design "
    "warnings (ARG BASE_IMAGE deliberately has no default; osrm's amd64 base on an arm64 Mac).",
    "Real builds: client-react image (validates the explicit COPY set through <code>tsc -b && vite build</code>) "
    "and server image — both green.",
    "Prod DB backed up pre-deploy: <code>pg_dump -Fc</code> via the bastion tunnel (:5433) into "
    "<code>~/city-edit-prod-backups/</code> (27 MB).",
    "Overlay image built from a CLEAN worktree of HEAD (the working tree carried unrelated in-flight edits to "
    "GraphLayer.tsx / runtime.ts / database.py that must not ship), base = the serving digest "
    "<code>f38b2659…</code> — no graph rebuild, no edge-id shift, no resnap.",
    "Staging-first: new digest deployed to <code>ce-stg-*</code>, verified (/health, graph-votes fields, served "
    "asset hash), then the SAME digest promoted to <code>desire-path-mapper</code> and re-verified.",
]

CHECKLIST = [
    "Run <code>droast</code> from the repo root — it should exit 0 with only the screenshots EXPOSE info.",
    "<code>docker compose build flask nginx</code> — both images should build; check <code>docker compose ps</code> "
    "shows flask healthy (compose probe) and nginx healthy (new image probe).",
    "Confirm a context upload shrank: <code>docker build -f Dockerfile.overlay --build-arg BASE_IMAGE=python:3.13-slim .</code> "
    "should transfer a context WITHOUT server/osm_data or node_modules (watch the “transferring context” line).",
    "Open the prod map and click a top-proposal diamond — corridor + badges restore (this deploy also shipped "
    "536fb76's floor/badges/ghost-waypoint work to prod for the first time).",
    "Check prod logs for a clean boot: <code>gcloud run services logs read desire-path-mapper --limit=50</code>.",
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
    "docs/url-routing.md": {
        "on": [],
        "module": ("Docs · routing", "URL/slug/subdomain architecture + the redirect inventory (source of truth)"),
        "file": ("url-routing.md", "~215 lines — address space, slug redirects, rename runbook, redirect inventory"),
        "outline": [
            ("address space / resolution", "unchanged", False),
            ("redirect inventory", "nyc-intersections row repointed; nyc-crossings row added", True),
        ],
        "blocks": [
            "nyc-intersections → nyc-proposals (src=qr-poster, chain-flattened) · nyc-crossings → nyc-proposals",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API · persistence", "Postgres schema + maps/vote/redirect queries"),
        "file": ("database.py", "~1300 LOC — schema init, map CRUD, vote rows, rename/redirect txn"),
        "outline": [
            ("schema init", "ALTER maps ADD top_proposal_min_net INT", True),
            ("_map_row_to_dict / _MAP_COLUMNS", "unpack + emit topProposalMinNet only when set", True),
            ("rename_map_slug / votes / redirects", "unchanged", False),
        ],
        "blocks": [
            "ALTER TABLE maps ADD COLUMN IF NOT EXISTS top_proposal_min_net INT",
            "**({\"topProposalMinNet\": v} if v is not None else {}) — absent key = client default",
        ],
    },
    "client-react/src/map/runtime.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · map/", "map-config resolution: slug/subdomain → /api/maps → MapConfig"),
        "file": ("runtime.ts", "~200 LOC — MapConfig type, resolveMapConfig, slugRedirectUrl"),
        "outline": [
            ("MapConfig interface", "gains topProposalMinNet?: number", True),
            ("resolveMapConfig / redirects", "unchanged", False),
        ],
        "blocks": [
            "topProposalMinNet?: number — per-map floor override, absent ⇒ TOP_PROPOSAL_MIN_NET",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · GraphLayer/", "vote heat, PBTP/RBTP proposal selection, pin rendering"),
        "file": ("GraphLayer.tsx", "~1700 LOC — topology load, heat paint, proposal recompute jobs"),
        "outline": [
            ("map flags", "topProposalMinNet resolved from getCurrentMap()", True),
            ("recomputeTopProposals (PBTP)", "floor now the per-map value", True),
            ("route-proposal job (RBTP)", "minRouteScore: topProposalMinNet + 1", True),
            ("hover/selection/markers", "unchanged", False),
        ],
        "blocks": [
            "const topProposalMinNet = getCurrentMap()?.topProposalMinNet ?? TOP_PROPOSAL_MIN_NET",
            "isStationNetwork ? 0 : topProposalMinNet — station networks keep their existing exemption",
            "minRouteScore: topProposalMinNet + 1 — both families share one bar",
        ],
    },
    "tools/nyc_proposals/import_to_map.py": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("Tools · nyc_proposals/", "official-DOT-proposals → votes pipeline (plan/cast CLI)"),
        "file": ("import_to_map.py", "~300 LOC — filters, classifier, geocoding, corridor guards, casting"),
        "outline": [
            ("SKIP_TITLE / PROJECT_TITLE / looks_like_project", "two-sided junk filter", True),
            ("CLASSIFY_RULES + POINT_FALLBACK_LABEL", "title → vote type + kind", True),
            ("CORRIDOR_RE + street_name()", "endpoint parsing, trailing-cap-run street trim", True),
            ("plan_project / cast_entry", "geocode+guards → /api/routes → /api/vote", True),
        ],
        "blocks": [
            "MAX_CORRIDOR_KM = 8.0 / MAX_CORRIDOR_EDGES = 600 — cross-borough geocode guard",
            "voter_id = dotproj:<sha1(url)[:16]> + ip_from_voter — idempotent, per-IP-cap-safe",
            "route-kind without endpoints → 'Street redesign' (point) — stays visible",
        ],
    },
    "tools/nyc_proposals/weekly_import.py": {
        "on": ["Flask API"],
        "module": ("Tools · nyc_proposals/", "Cloud Run job entrypoint: fetch → plan → cast weekly"),
        "file": ("weekly_import.py", "~70 LOC — 8-day overlapping window against BASE_URL"),
        "outline": [
            ("env config", "BASE_URL / MAP_SLUG / WINDOW_DAYS", True),
            ("main loop", "changed pages → plan_project → cast_entry, stats", True),
        ],
        "blocks": [
            "8-day window + idempotent casts: overlap is safe, missed weeks self-heal on the next run",
        ],
    },
    "tools/nyc_proposals/Dockerfile": {
        "on": ["Flask API"],
        "module": ("Deploy · tools/nyc_proposals/", "the dot-import job image"),
        "file": ("Dockerfile", "python:3.13-slim + the three stdlib-only scripts"),
        "outline": [
            ("image", "no deps to install; HEALTHCHECK NONE (one-shot job)", True),
        ],
        "blocks": [
            "CMD [\"python\", \"weekly_import.py\"]",
        ],
    },
    "terraform/dot-import-job.tf": {
        "on": ["Flask API"],
        "module": ("Terraform · prod", "weekly import job: SA + Cloud Run v2 job + scheduler"),
        "file": ("dot-import-job.tf", "~100 lines — mirrors the map-screenshot job pattern"),
        "outline": [
            ("dot_import_sa + invoker IAM", "job-scoped SA", True),
            ("google_cloud_run_v2_job.dot_import", "512Mi / 1800s / max_retries 1", True),
            ("google_cloud_scheduler_job.dot_import", "0 7 * * 1 America/New_York", True),
        ],
        "blocks": [
            "apply with -target only — blanket applies remain a landmine (docs/gcp-deployment.md)",
        ],
    },
    "docs/nyc-proposal-data-sources.md": {
        "on": [],
        "module": ("Docs · data sources", "where official street-change proposals live + scrape recipes"),
        "file": ("nyc-proposal-data-sources.md", "~180 lines — nycdotprojects/SIPs/nyc.gov + import pipeline"),
        "outline": [
            ("sources + recipes", "unchanged (authored by the research agent)", False),
            ("importing proposals as votes", "new section: pipeline, guards, weekly job", True),
        ],
        "blocks": [
            "documents the corridor/point cast split, the floor-0 override, and the weekly job runbook",
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
