#!/usr/bin/env python3
"""Generate the HTML changelog report for the 2026-08-01 workstream:
test maps removed from prod + staging environment decommissioned.

Run from repo root: python changelog/build_staging_decommission_report.py
Reads changelog/changes.diff (git show f27bea6 + git show 0724358),
writes changelog/2026-08-01-test-maps-staging-decommission.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-08-01-test-maps-staging-decommission.html")

DATE = "2026-08-01"
TITLE = "Test maps off the prod landing page · staging decommissioned"

SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client", "Terraform / GCP"]


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
        elif raw.startswith("diff ") or raw.startswith("index ") or raw.startswith("new file") or raw.startswith("deleted file") or raw.startswith("similarity"):
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
        "id": "testmaps",
        "tag": "SERVER / DATA",
        "title": "Test: Midtown & Test: Central Park kept resurfacing on prod",
        "symptom": "The two block-pipeline playground maps showed as public cards on the cityedit.org landing grid. Deleting their DB rows would not have stuck: they reappear on the next boot.",
        "cause": [
            "<code>seed_presets()</code> upserts every <code>PRESET_MAPS</code> entry unconditionally at startup, in every environment — the playground entries added for the block-pipeline experiments therefore self-heal into any DB, prod included",
            "<code>/api/maps</code> has no hidden/archived filter: every row in <code>maps</code> renders on the landing grid",
        ],
        "fixes": [
            "The two playground presets are now opt-in: appended to <code>PRESET_MAPS</code> only when <code>SEED_TEST_MAPS=1</code> (never set on prod), so the seeder can no longer recreate them there",
            "Deleted their <code>maps</code> rows on prod (both had 0 votes, no redirects); purged the two orphaned <code>bver:*</code> Redis keys — the only test-map keys in prod Redis",
            "Existing local DBs keep their playground rows untouched (the seeder only upserts); a fresh local DB wants <code>SEED_TEST_MAPS=1</code>",
            "Shipped as overlay digest <code>d64843f6…</code> (base: the serving <code>f0bf4262…</code>) → prod rev <code>00121-2h6</code>, after the mandatory DB backup (<code>~/city-edit-prod-backups/20260801-224221/</code>)",
        ],
        "files": ["server/presets.py"],
    },
    {
        "id": "staging",
        "tag": "INFRA / TERRAFORM",
        "title": "Staging environment torn down — deploys now go straight to prod",
        "symptom": "The ce-stg-* parity twin (shipped 2026-07-23) cost ~$35/mo in Memorystore plus deploy overhead, and traffic never justified the rehearsal step. Decision: prod is the only serving environment.",
        "cause": [
            "Staging duplicated the state-bearing pieces (own Redis, <code>votes_staging</code> DB, three staging secrets) purely to rehearse digest promotions — value that scales with traffic the site does not have",
        ],
        "fixes": [
            "All 14 staging resources destroyed via <em>targeted</em> terraform (plan verified <strong>0 add / 0 change / 14 destroy</strong> before applying — a blanket apply remains a prod landmine): Cloud Run service + public IAM, Memorystore <code>desire-path-staging</code>, <code>votes_staging</code> DB + <code>app_staging</code> user (the shared prod Cloud SQL instance untouched), 3 secrets + versions + accessor grants",
            "Two destroy-order gotchas: <code>votes_staging</code> failed once with “being accessed by other users” (the draining service still held connections — retry after it is gone), and <code>app_staging</code> cannot drop until the database holding its owned objects is dropped",
            "Repo cleanup: <code>terraform/staging.tf</code> and <code>docs/staging-parity-plan.md</code> deleted (git history keeps them), <code>make stage-refresh</code> removed, <code>staging_*</code> vars stripped from the gitignored tfvars",
            "Deploy docs + CLAUDE.md rewritten to the direct-to-prod flow: mandatory backup → digest deploy → verify (asset hash + <code>/api/maps</code>)",
            "The <code>IS_STAGING</code> runtime path (staging ribbon, redirect gate, noindex) stays in code — inert without <code>APP_ENV=staging</code>; removing it would force another image build for dead code",
        ],
        "files": ["terraform/staging.tf", "docs/staging-parity-plan.md", "Makefile",
                  "docs/gcp-deployment.md", "CLAUDE.md"],
    },
]

VERIFY = [
    "Targeted destroy plan reviewed before apply: <strong>0 to add, 0 to change, 14 to destroy</strong> — all staging-named; <code>terraform state list</code> afterwards shows no staging resources.",
    "Staging URL now returns 404 from Google’s frontend; <code>gcloud run services list</code> shows only <code>desire-path-mapper</code> + <code>desire-path-osrm</code> serving City Edit.",
    "<code>https://cityedit.org/api/maps</code> returns 13 maps, zero <code>test-*</code> slugs; the rendered landing grid (checked in Chrome) lists no “Test:” cards.",
    "Prod Redis scanned for <code>*test-central-park*</code> / <code>*test-midtown*</code>: only two <code>bver:</code> stamps existed, both deleted, rescan clean.",
    "Smoke on the new digest before promotion: <code>/api/maps/nyc-walkways</code> config, sparse <code>graph-votes</code> 200, landing 200.",
    "<code>npx tsc -b</code> clean; overlay built from a clean worktree of <code>f27bea6</code> (the working tree ships).",
    "Fresh prod backup taken first: <code>~/city-edit-prod-backups/20260801-224221/</code> (full dump + per-table CSVs + checksums).",
]

CHECKLIST = [
    "Open <code>https://cityedit.org/</code> — the grid should show 13 cards and no “Test: Midtown” / “Test: Central Park” (hard-refresh; the list is cached <code>max-age=60</code>).",
    "Run <code>cd terraform && terraform state list | grep -i staging</code> — expect no output.",
    "Confirm next month’s GCP bill drops the <code>desire-path-staging</code> Memorystore line (~$35/mo).",
    "If you ever want the playground maps locally on a fresh DB, boot Flask with <code>SEED_TEST_MAPS=1</code>.",
    "Skim <code>docs/gcp-deployment.md#deploying-changes</code> — deploys are now backup → digest → prod, no staging hop; shout if any step reads wrong.",
]

FILE_CONTEXT = {
    "server/presets.py": {
        "on": ["Flask API"],
        "module": ("Flask API · seeding", "canonical preset vote-type lists + preset maps; seed_presets() upserts these at every boot"),
        "file": ("presets.py", "~130 LOC — PRESET_LISTS (bikes/trees/walkways), PRESET_MAPS (the landing-grid presets)"),
        "outline": [
            ("PRESET_LISTS", "unchanged", False),
            ("PRESET_MAPS", "playground entries moved out of the unconditional list", True),
            ("SEED_TEST_MAPS gate", "new: appends test-central-park / test-midtown only when the env flag is set", True),
        ],
        "blocks": [
            "import os — the module now reads one env flag",
            "if os.environ.get(\"SEED_TEST_MAPS\", ...) in (\"1\",\"true\",\"yes\"): PRESET_MAPS += [test-central-park, test-midtown]",
        ],
    },
    "terraform/staging.tf": {
        "on": ["Terraform / GCP"],
        "module": ("Terraform · staging", "the entire staging stack — DELETED (recoverable from git history)"),
        "file": ("staging.tf", "302 lines — vars, data-source OSRM ref, Redis, DB+user, secrets, app service, outputs"),
        "outline": [
            ("staging_* variables", "deleted", True),
            ("google_redis_instance.cache_staging", "deleted (the ~$35/mo Memorystore twin)", True),
            ("votes_staging DB + app_staging user", "deleted — lived on the shared prod Cloud SQL instance", True),
            ("*-staging secrets + IAM", "deleted", True),
            ("google_cloud_run_service.app_staging + public IAM", "deleted (ce-stg-<token>, the URL-is-the-secret design)", True),
            ("staging_url / staging_redis_host outputs", "deleted", True),
        ],
        "blocks": [
            "whole file removed; the live resources were destroyed FIRST with -target (plan: 0/0/14)",
        ],
    },
    "docs/staging-parity-plan.md": {
        "on": [],
        "module": ("Docs · infra", "the staging design/rationale doc — DELETED with the environment"),
        "file": ("staging-parity-plan.md", "~260 lines — parity goals, CT-log URL rationale, seeding plan"),
        "outline": [
            ("whole document", "deleted; git history keeps the design if staging is ever resurrected", True),
        ],
        "blocks": [
            "removed alongside terraform/staging.tf so no doc points at infrastructure that does not exist",
        ],
    },
    "Makefile": {
        "on": [],
        "module": ("Tooling · dev loop", "deps/dev/test/deploy targets"),
        "file": ("Makefile", "~150 lines — docker deps, dev stack, tests, deploy helpers"),
        "outline": [
            ("deps / dev / test targets", "unchanged", False),
            ("stage-refresh", "deleted — restored prod dumps into votes_staging over two bastion tunnels", True),
        ],
        "blocks": [
            "stage-refresh target + its tunnel preflights removed",
        ],
    },
    "docs/gcp-deployment.md": {
        "on": [],
        "module": ("Docs · deployment", "the prod GCP runbook: deploys, secrets, DB access, backups"),
        "file": ("gcp-deployment.md", "~230 lines — deploy options, digest workflow, tunnels, snapshot recipe"),
        "outline": [
            ("Deploying Changes intro", "staging-first note → decommission note + direct-to-prod rule", True),
            ("Staging (deploy here FIRST) section", "deleted wholesale", True),
            ("Digest promotion workflow", "rewritten: build → backup → prod digest → asset check", True),
            ("Environment & Secrets / DB access & backups", "unchanged", False),
        ],
        "blocks": [
            "decommission note dated 2026-08-01 with git-history pointers for resurrection",
            "3-step prod digest workflow replaces the 4-step staging promotion",
        ],
    },
    "CLAUDE.md": {
        "on": [],
        "module": ("Docs · agent instructions", "project instructions checked into the repo"),
        "file": ("CLAUDE.md", "~470 lines — architecture, workflows, style guides"),
        "outline": [
            ("Deploy STAGING-FIRST bullet", "→ deploy straight to prod (backup → digest → verify)", True),
            ("everything else", "unchanged", False),
        ],
        "blocks": [
            "the staging-first instruction replaced; backup-before-deploy stays mandatory",
        ],
    },
}


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
    nav = "\n".join(f'<a href="#{s["id"]}">{s["title"].split("—")[0].split("kept")[0].strip()}</a>' for s in SECTIONS)

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
    <div class="dateline">{DATE} · branch <code>main</code> · commits <code>f27bea6</code>, <code>0724358</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Two block-pipeline playground maps (“Test: Midtown”, “Test: Central Park”) were showing on the public cityedit.org landing grid — the boot-time preset seeder recreated them in every environment, so row deletion alone could never stick. They are now opt-in via <code>SEED_TEST_MAPS=1</code> and their prod rows and Redis stamps are gone. In the same session the staging environment was decommissioned entirely (not enough users to justify the parity copy): all 14 staging terraform resources destroyed with targeted applies, the staging files removed from the repo, and the deploy runbook rewritten to the direct-to-prod flow — backup → digest → verify.</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed. The two deleted files (staging.tf, staging-parity-plan.md) show as all-red.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_staging_decommission_report.py</code>.
    Regenerate after further edits with <code>git show f27bea6 0724358 --format= &gt; changelog/changes.diff &amp;&amp; python changelog/build_staging_decommission_report.py</code>.
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
