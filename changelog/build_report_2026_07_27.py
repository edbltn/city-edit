#!/usr/bin/env python3
"""Generate the HTML changelog report for the feedback/about/donate header work.

Run from repo root: python changelog/build_report_2026_07_27.py
Reads changelog/changes-feedback-buttons.diff,
writes changelog/2026-07-27-feedback-about-buttons.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-feedback-buttons.diff")
OUT_PATH = os.path.join(HERE, "2026-07-27-feedback-about-buttons.html")

DATE = "2026-07-27"
TITLE = "feedback.cityedit.org + the header link row (Feedback · About · Donate)"


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
        "id": "feedback-subdomain",
        "tag": "nginx + terraform + static page",
        "title": "1 · feedback.cityedit.org — a form that emails the team",
        "symptom": (
            "There was no way for users to reach us. Donate already had the pattern: a "
            "<code>*.cityedit.org</code> subdomain mapped to the same Cloud Run service and answered "
            "at the nginx layer (donate 301-redirects to Donorbox). Feedback follows it, but serves a "
            "real page: a form whose submissions land in "
            "<code>eric@sphericalharmonics.org</code> + <code>brook@sphericalharmonics.org</code>."
        ),
        "cause": [
            "The server has no SMTP/email stack, and adding one (plus a queue, spam handling, secrets) "
            "for a feedback form is overkill. <strong>FormSubmit.co</strong> is a no-backend "
            "form-to-email relay: the static page POSTs to "
            "<code>formsubmit.co/eric@sphericalharmonics.org</code> with a hidden <code>_cc</code> to "
            "Brook, a honeypot field for bots, and a <code>_next</code> redirect back to the page "
            "(<code>?sent=1</code> shows the thank-you banner).",
            "The page itself is <code>client-react/public/feedback.html</code> — Vite copies "
            "<code>public/</code> into <code>dist/</code> verbatim, so it ships inside the existing "
            "client build with zero new build steps. It reuses the app's terminal aesthetic "
            "(Red Hat Mono, #0d0d0d paper, hairline borders, amber accent).",
            "nginx answers the subdomain before the catch-all SPA server: every path on "
            "<code>feedback.cityedit.org</code> lands on <code>/feedback.html</code>.",
        ],
        "fixes": [
            "<strong>New static form page</strong> <code>client-react/public/feedback.html</code> — "
            "name (optional), reply-to email (optional), message; posts to FormSubmit with "
            "<code>_cc</code>, <code>_subject</code>, honeypot, and a success banner on return.",
            "<strong>nginx server block</strong> for <code>feedback.cityedit.org</code> in "
            "<code>deploy/nginx-cloudrun.conf</code>, mirroring the donate block's placement.",
            "<strong>Terraform</strong>: <code>feedback.cityedit.org</code> added to "
            "<code>custom_domains</code>, so a targeted apply creates the Cloud Run domain mapping "
            "(the app service itself is never applied via terraform — see the deploy-landmines "
            "runbook).",
            "<strong>One manual step</strong>: a Namecheap DNS CNAME <code>feedback → "
            "ghs.googlehosted.com</code> (DNS for cityedit.org is registrar-hosted, not in terraform).",
            "<strong>One activation step</strong>: FormSubmit sends a one-time activation email to "
            "eric@sphericalharmonics.org on the first submission; until it's clicked, submissions "
            "aren't relayed.",
        ],
        "files": [
            "client-react/public/feedback.html — NEW: the form page",
            "deploy/nginx-cloudrun.conf — feedback.cityedit.org server block",
            "terraform/main.tf — custom_domains += feedback.cityedit.org",
        ],
    },
    {
        "id": "header-row",
        "tag": "React client",
        "title": "2 · Header rework — Feedback · About · Donate on one line",
        "symptom": (
            "The map-page header had How-it-Works + Donate sharing a grid cell. Wanted: Feedback, "
            "About (→ sphericalharmonics.org), and Donate together on one line, Donate colored by the "
            "active map theme, and How-it-Works moved down to the Cast/Clear row. Same trio on the "
            "landing footer."
        ),
        "cause": [
            "<code>.topbar-actions</code> is a strict 2×2 grid (Mode / header links over Vote / "
            "actions), so the three links stay wrapped in <code>.header-btn-group</code> to keep 4 "
            "grid children, and How-it-Works simply moves into <code>.actions-group</code> (the "
            "Cast/Clear cell).",
            "On mobile (≤1080px) the second grid row used to collapse to 0 height until a selection "
            "existed — it held only the then-hidden Cast/Clear controls. With the always-visible "
            "How-it-Works button living there, the collapse would hide it, so the row now stays open.",
            "Theme coloring: <code>ThemeContext</code> already injects the active map style's accent "
            "as <code>--accent</code> on the document root (with an amber fallback pre-JS). Every "
            "theme accent is a bright mid-tone, so near-black text keeps ≥4.4:1 contrast on all of "
            "them — the Donate button just paints <code>background: var(--accent)</code>.",
        ],
        "fixes": [
            "<strong>TopBar.tsx</strong>: header cell now renders Feedback (→ feedback.cityedit.org), "
            "About (→ sphericalharmonics.org), Donate — all external links; the How-it-Works "
            "<code>&lt;button&gt;</code> moved to the front of <code>.actions-group</code> on the "
            "Cast/Clear row (its modal state stays in TopBar).",
            "<strong>TopBar.css</strong>: <code>.btn-donate</code> gets the accent background, accent "
            "border, near-black bold label, and a brightness hover; links in the group share "
            "<code>text-decoration: none</code>.",
            "<strong>globals.css</strong>: the ≤1080px second-row collapse "
            "(<code>grid-template-rows … 0</code> + <code>.has-selection</code> override) replaced by "
            "an always-open second row, since How-it-Works now lives there.",
            "<strong>Landing.tsx</strong>: footer now reads About · Feedback · Donate (the old "
            "<code>sphericalharmonics.org</code> label became About).",
        ],
        "files": [
            "client-react/src/components/TopBar/TopBar.tsx — link trio + How-it-Works move",
            "client-react/src/components/TopBar/TopBar.css — themed .btn-donate",
            "client-react/src/styles/globals.css — mobile row-2 always open",
            "client-react/src/components/Landing/Landing.tsx — footer About/Feedback/Donate",
        ],
    },
]

VERIFY = [
    "Frontend: <code>tsc --noEmit</code> — clean; <code>vite build</code> — clean, and "
    "<code>dist/feedback.html</code> present in the bundle.",
    "nginx: config syntax-checked in a Debian container (<code>nginx -t</code>, brotli module lines "
    "stripped for the check) — OK.",
    "Live dev (localhost:3000): walkways map shows Feedback / About / Donate on one line with an "
    "amber Donate; How-it-Works renders on the Cast/Clear row; landing footer shows "
    "About · Feedback · Donate; /feedback.html renders the styled form.",
]

CHECKLIST = [
    "<strong>Click the FormSubmit activation link</strong> — submit the form once on "
    "feedback.cityedit.org, then open eric@sphericalharmonics.org's inbox and confirm the "
    "FormSubmit activation email; until it's clicked no feedback is relayed. Then submit again and "
    "confirm both eric@ and brook@ receive it.",
    "<strong>Add the DNS record at Namecheap</strong>: CNAME <code>feedback</code> → "
    "<code>ghs.googlehosted.com</code> (same as donate). Until then feedback.cityedit.org won't "
    "resolve; the buttons will 404 at the DNS level.",
    "On a map page, confirm the Donate button picks up each theme's accent (amber on walkways, green "
    "on bikes) and the label stays readable.",
    "On a phone-width window, confirm How-it-Works is visible below the mode row <em>before</em> "
    "any selection is made, and that Cast/Clear appear next to it after selecting a route.",
    "Open the How-it-Works modal from its new spot; confirm it still opens/closes.",
    "On the landing page footer, click About / Feedback / Donate and confirm the three targets.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Goal</h3>
      <p>{s['symptom']}</p>
      <h3>Design</h3>
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
    "client-react/public/feedback.html": {
        "on": ["nginx", "React / Leaflet client"],
        "module": ("Client static assets · client-react/public/", "files Vite copies verbatim into dist/ — favicons, logo, previews, and now the feedback page"),
        "file": ("feedback.html", "NEW FILE ~230 LOC — standalone form page in the app's terminal aesthetic; no JS framework"),
        "outline": [
            ("<head> — fonts + inline styles", "Red Hat Mono, dark paper palette, form styling", True),
            ("Header + lede", "← CITY EDIT link, page intro", True),
            ("Success banner", "hidden div shown when ?sent=1", True),
            ("The form", "FormSubmit POST: _cc, _subject, honeypot, name/email/message", True),
            ("Footer + script", "Spherical Harmonics / Donate links; ?sent=1 banner toggle", True),
        ],
        "blocks": ["Whole file is new — the feedback.cityedit.org page."],
    },
    "deploy/nginx-cloudrun.conf": {
        "on": ["nginx"],
        "module": ("Deploy · deploy/", "the Cloud Run container's nginx: subdomain answers, SPA fallback, tile + preview caching"),
        "file": ("nginx-cloudrun.conf", "~200 LOC — http block: donate/feedback subdomain servers, then the default SPA server"),
        "outline": [
            ("worker/env + brotli modules", "gzip+brotli, staging robots map", False),
            ("tile proxy cache", "/tmp keys_zone for block tiles", False),
            ("donate.cityedit.org server", "301 → Donorbox", False),
            ("feedback.cityedit.org server", "NEW — every path serves /feedback.html", True),
            ("default SPA server", "try_files → index.html, asset caching, previews proxy", False),
        ],
        "blocks": [
            "server { server_name feedback.cityedit.org; try_files /feedback.html =404; } — placed with the donate block, before the catch-all",
        ],
    },
    "terraform/main.tf": {
        "on": [],
        "module": ("Infra · terraform/", "GCP project: Cloud Run service, SQL, Redis, domain mappings, schedulers"),
        "file": ("main.tf", "~700 LOC — all prod infra; custom_domains drives one google_cloud_run_domain_mapping per subdomain"),
        "outline": [
            ("Cloud Run service + IAM", "the app resource (never applied blind — env drift)", False),
            ("custom_domains variable", "cityedit.org + subdomains → feedback added", True),
            ("google_cloud_run_domain_mapping.custom", "for_each over custom_domains", False),
            ("outputs", "domain_mapping_dns lists registrar records", False),
            ("OSM refresh scheduler", "unchanged", False),
        ],
        "blocks": [
            'custom_domains default += "feedback.cityedit.org" — apply with -target=google_cloud_run_domain_mapping.custom',
        ],
    },
    "client-react/src/components/TopBar/TopBar.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/TopBar", "the map page header: legend, mode/vote switchers, cast controls, header links"),
        "file": ("TopBar.tsx", "~310 LOC — one component rendering the whole topbar grid"),
        "outline": [
            ("Logo + mobile banner", "landing links", False),
            ("topbar-legend", "start/end tools, selection, votes swatch", False),
            ("mode-switcher-group", "Mode: dropdown", False),
            ("header-btn-group", "now Feedback · About · Donate links", True),
            ("vote-switcher-group", "Vote: selector", False),
            ("actions-group", "How-it-Works (moved here) + calculating + Cast/Clear", True),
            ("HowItWorksModal", "state unchanged, still owned here", False),
        ],
        "blocks": [
            "header-btn-group → three external <a> links (feedback / sphericalharmonics / donate)",
            "actions-group → How-it-Works <button> prepended before the calculating indicator",
        ],
    },
    "client-react/src/components/TopBar/TopBar.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/TopBar", "topbar-specific styles (logo, header link group)"),
        "file": ("TopBar.css", "~100 LOC — logo, mobile banner, header button group"),
        "outline": [
            ("logo + mobile banner", "incl. light-basemap invert", False),
            (".header-btn-group", "flex cell; links share text-decoration:none", True),
            (".btn-donate", "NEW look — accent bg/border, near-black bold label, brightness hover", True),
            ("loading pulse", "unchanged", False),
        ],
        "blocks": [
            ".header-btn-group .btn-header — + text-decoration:none (all three are links now)",
            ".btn-donate — background/border var(--accent), color #111, font-weight 600, hover brightness(1.12)",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · styles/", "design tokens + all shared layout (topbar grid, buttons, modals, breakpoints)"),
        "file": ("globals.css", "~1900 LOC — tokens, topbar grid, controls, media queries"),
        "outline": [
            ("Design tokens", "structural + palette (--accent injected per map)", False),
            (".topbar-actions grid", "2×2: Mode/links over Vote/actions", False),
            ("buttons / cast / legend", "unchanged", False),
            ("≤1080px media query", "second row no longer collapses to 0 pre-selection", True),
            ("≤400px + landscape queries", "unchanged", False),
        ],
        "blocks": [
            "≤1080px .topbar-actions — grid-template-rows always two control-height rows; .has-selection override removed (How-it-Works lives in row 2 now)",
        ],
    },
    "client-react/src/components/Landing/Landing.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/Landing", "the apex-domain map picker (cards grid + footer)"),
        "file": ("Landing.tsx", "~230 LOC — search, propose-a-map, map cards, footer"),
        "outline": [
            ("search + cards grid", "unchanged", False),
            ("footer", "© line + About · Feedback · Donate", True),
        ],
        "blocks": [
            "footer links — sphericalharmonics.org relabeled About; + Feedback → feedback.cityedit.org",
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

  <p class="lede">A way for users to reach the team: feedback.cityedit.org serves a form whose
  submissions email eric@ and brook@sphericalharmonics.org, and the map-page header grew a
  Feedback · About · Donate link row (Donate in the map theme's accent) with How-it-Works moved
  down beside Cast/Clear.</p>

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
    Generated from <code>changelog/changes-feedback-buttons.diff</code> by
    <code>changelog/build_report_2026_07_27.py</code>.
    Regenerate after further edits with <code>git diff … &gt; changelog/changes-feedback-buttons.diff
    &amp;&amp; python changelog/build_report_2026_07_27.py</code>.
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
