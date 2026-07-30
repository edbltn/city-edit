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
OUT_PATH = os.path.join(HERE, "2026-07-30-droast-dockerfile-hygiene.html")

DATE = "2026-07-30"
TITLE = "droast Dockerfile hygiene pass + the ForcedCorridor build-breaker (overlay deploy)"


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
        "id": "buildfix",
        "tag": "React client · build",
        "title": "1 · A missing type import silently broke every Docker client build",
        "symptom": (
            "Building the client image (and therefore ANY overlay deploy) failed at <code>tsc -b</code> with "
            "<code>TS2304: Cannot find name 'ForcedCorridor'</code> ×2 in MapView.tsx. Localhost looked perfectly "
            "healthy the whole time — Vite's dev server transpiles without type-checking, so the error only "
            "existed at image-build time. Surfaced by the droast validation builds, not by any dev workflow."
        ),
        "cause": [
            "<code>536fb76</code> (ghost-waypoint corridors) added <code>ForcedCorridor</code> type annotations to "
            "MapView's <code>corridorChainOf</code>/<code>corridorChainFor</code> without importing the type — every "
            "other user (RouteContext, reducer) already imported it from <code>selection/types</code>.",
            "Nothing in the dev loop runs <code>tsc</code>; the first consumer to notice would have been the next "
            "overlay deploy's Cloud Build, mid-deploy.",
        ],
        "fixes": [
            "One line: <code>import type {{ ForcedCorridor }} from \"../../selection/types\";</code> — same style as "
            "RouteContext's import.",
            "Verified by host <code>tsc -b</code> (clean) and a full <code>docker build</code> of "
            "client-react/Dockerfile (image builds through <code>vite build</code>).",
        ],
        "files": [
            "client-react/src/components/MapView/MapView.tsx",
        ],
    },
    {
        "id": "droast",
        "tag": "Docker · all images",
        "title": "2 · droast lint pass: 1 error / 12 warnings / 27 infos → clean",
        "symptom": (
            "Ran <a href=\"https://github.com/immanuwell/dockerfile-roast\">droast</a> 1.4.11 over all 10 "
            "Dockerfiles: 1 error (osrm CMD referenced an undeclared <code>$PORT</code>), 12 warnings (no USER, "
            "no CMD in overlays, COPY-everything, single-stage), 27 infos (no .dockerignore anywhere, unpinned "
            "apt, missing Python env vars, missing healthchecks). No build context in the repo had a "
            ".dockerignore, so local docker builds could sweep secrets (<code>server/.env</code>), "
            "<code>server/osm_data</code> graphs, and node_modules into context uploads."
        ),
        "cause": [
            "The Dockerfiles accreted per-workstream (main bake, 5 surgical overlays, 3 service images, 1 batch "
            "job) without a shared hygiene pass; <code>screenshots/Dockerfile</code> even used bare "
            "<code>pip</code> against the project's own uv mandate.",
        ],
        "fixes": [
            "<strong>.dockerignore per build context</strong> (root + server + client-react + osrm + screenshots). "
            "The root one feeds the app image AND all 5 overlays, so it excludes secrets/graphs/node_modules while "
            "preserving every COPY'd path (client-react/, server/*.py + data/, deploy/, .arrays-staging/, "
            ".blocks-staging/).",
            "<strong>osrm</strong>: the one ERROR — <code>ENV PORT=5000</code> declares the var the CMD "
            "interpolates (Cloud Run still overrides with 8080); plus <code>EXPOSE 5000</code> and an explicit "
            "<code>HEALTHCHECK NONE</code> (the runtime image ships no curl/wget — same rationale already "
            "documented on the compose service).",
            "<strong>Main image</strong>: <code>--no-install-recommends</code>; a status-only HEALTHCHECK probing "
            "<code>/health</code> with a 900s start period + 15 retries, mirroring deploy/healthcheck.sh's "
            "graph-warmup tolerance (Cloud Run ignores Docker HEALTHCHECK — this is for compose runs).",
            "<strong>server</strong>: <code>PYTHONUNBUFFERED</code>/<code>PYTHONDONTWRITEBYTECODE</code>, "
            "<code>--no-install-recommends</code>, explicit <code>HEALTHCHECK NONE</code> — the image is shared "
            "with the osm-refresh sidecar (whose command runs no server), so compose owns the flask probe.",
            "<strong>client-react</strong>: explicit COPY of the six build inputs instead of <code>COPY . .</code> "
            "(unrelated files no longer bust the npm build cache) + a busybox-wget healthcheck on the nginx stage.",
            "<strong>screenshots</strong>: bare <code>pip</code> → <code>uv pip install --system</code> (CLAUDE.md "
            "mandate), Python env vars, <code>HEALTHCHECK NONE</code> (one-shot batch job).",
            "<strong>droast.toml</strong> records the four deliberate global skips with rationale — DF005 apt "
            "pinning (Debian point-release churn breaks rebuilds; we pin base images instead), DF020 USER (each "
            "image has a concrete root requirement), DF011 single-stage (slim images carry no toolchain), DF036 "
            "no-CMD (overlays inherit CMD from the digest-pinned <code>${{BASE_IMAGE}}</code>). Future runs lint "
            "clean: <code>droast</code> exits 0.",
        ],
        "files": [
            "Dockerfile",
            "server/Dockerfile",
            "client-react/Dockerfile",
            "osrm/Dockerfile",
            "screenshots/Dockerfile",
            ".dockerignore",
            "server/.dockerignore",
            "client-react/.dockerignore",
            "osrm/.dockerignore",
            "screenshots/.dockerignore",
            "droast.toml",
        ],
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
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · MapView/", "the map shell: tools, click/drag handlers, corridor threading"),
        "file": ("MapView.tsx", "~800 LOC — placement handlers, chain threading (536fb76), marker render"),
        "outline": [
            ("imports", "gains the type import 536fb76 forgot", True),
            ("corridorChainOf / corridorChainFor", "the ForcedCorridor-annotated helpers that failed tsc", False),
            ("click/drop handlers, marker wiring", "unchanged", False),
        ],
        "blocks": [
            "import type { ForcedCorridor } from \"../../selection/types\" — matches RouteContext's import style",
        ],
    },
    "Dockerfile": {
        "on": ["nginx", "Flask API", "React / Leaflet client"],
        "module": ("Deploy · repo root", "the full app image bake: client build + graphs + PMTiles + nginx/supervisor"),
        "file": ("Dockerfile", "~65 LOC — 2-stage: node client build → python+nginx with in-image graph bakes"),
        "outline": [
            ("client-builder stage", "npm ci + vite build", False),
            ("apt install", "now --no-install-recommends", True),
            ("uv deps / graph bakes / pmtiles", "unchanged (takes effect next FULL rebuild only)", False),
            ("EXPOSE + HEALTHCHECK + CMD", "status-only /health probe, 900s start period ×15 retries", True),
        ],
        "blocks": [
            "apt-get install -y --no-install-recommends nginx …",
            "HEALTHCHECK --interval=60s --start-period=900s --retries=15 CMD curl -fsS :5001/health — mirrors deploy/healthcheck.sh's warmup tolerance; Cloud Run ignores it",
        ],
    },
    "server/Dockerfile": {
        "on": ["Flask API"],
        "module": ("Deploy · server/", "the local-dev flask image (compose flask + osm-refresh sidecar)"),
        "file": ("Dockerfile", "~35 LOC — python:3.13-slim + uv deps + app code"),
        "outline": [
            ("ENV", "PYTHONUNBUFFERED + PYTHONDONTWRITEBYTECODE", True),
            ("apt curl", "now --no-install-recommends", True),
            ("uv deps / code copy", "unchanged", False),
            ("HEALTHCHECK NONE", "explicit — image shared with the serverless osm-refresh sidecar; compose owns the flask probe", True),
        ],
        "blocks": [
            "ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1",
            "apt-get install -y --no-install-recommends curl",
            "HEALTHCHECK NONE + rationale comment",
        ],
    },
    "client-react/Dockerfile": {
        "on": ["nginx", "React / Leaflet client"],
        "module": ("Deploy · client-react/", "the standalone client image (compose nginx service)"),
        "file": ("Dockerfile", "~20 LOC — node build stage → nginx:alpine serve stage"),
        "outline": [
            ("build stage COPY", "COPY . . → explicit six build inputs (cache-friendly, silences DF007)", True),
            ("serve stage", "gains a busybox-wget healthcheck", True),
        ],
        "blocks": [
            "COPY index.html vite.config.ts tsconfig*.json ./ + public/ src/ scripts/",
            "HEALTHCHECK … wget -q --spider http://127.0.0.1/",
        ],
    },
    "osrm/Dockerfile": {
        "on": ["OSRM"],
        "module": ("Deploy · osrm/", "the merged-OSRM image: osmium merge → extract/partition/customize → serve"),
        "file": ("Dockerfile", "~50 LOC — 3-stage; dataset baked at build"),
        "outline": [
            ("merger + builder stages", "unchanged", False),
            ("runtime ENV/EXPOSE/HEALTHCHECK", "PORT declared (fixes the DF087 ERROR), EXPOSE 5000, HEALTHCHECK NONE", True),
            ("CMD", "unchanged — ${PORT:-5000} still honors Cloud Run's 8080", False),
        ],
        "blocks": [
            "ENV OSRM_DATASET=… PORT=5000 — CMD no longer interpolates an undeclared var",
            "EXPOSE 5000; HEALTHCHECK NONE (runtime image ships no curl/wget — compose comment's rationale, now in-image)",
        ],
    },
    "screenshots/Dockerfile": {
        "on": [],
        "module": ("Deploy · screenshots/", "the daily map-preview capture job (playwright + chromium)"),
        "file": ("Dockerfile", "~20 LOC — python:3.13-slim + playwright, one-shot CMD"),
        "outline": [
            ("ENV + uv", "Python env vars; bare pip → uv pip --system (CLAUDE.md mandate)", True),
            ("playwright install / CMD", "unchanged", False),
            ("HEALTHCHECK NONE", "one-shot batch job", True),
        ],
        "blocks": [
            "COPY --from=ghcr.io/astral-sh/uv:latest + uv pip install --system --no-cache",
            "ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1; HEALTHCHECK NONE",
        ],
    },
    ".dockerignore": {
        "on": ["nginx", "Flask API", "React / Leaflet client"],
        "module": ("Deploy · repo root", "NEW — governs the root build context (app image + all 5 overlays)"),
        "file": (".dockerignore", "~40 lines — excludes secrets/graphs/node_modules, preserves every COPY'd path"),
        "outline": [
            ("secrets & env", "**/.env (keep .env.example), server/env", True),
            ("heavy artifacts", "server/osm_data, **/node_modules, client-react/dist", True),
            ("repo material", ".git, docs, changelog, data, tools, sibling contexts", True),
        ],
        "blocks": [
            "header documents the exact COPY surface of the 6 root-context builds",
            "patterns anchored so server/data (needed) survives while root data/ (analysis files) is excluded",
        ],
    },
    "server/.dockerignore": {
        "on": ["Flask API"],
        "module": ("Deploy · server/", "NEW — context for compose's build: ./server"),
        "file": (".dockerignore", "10 lines"),
        "outline": [("env/.env/osm_data/tests/__pycache__", "excluded; build COPYs only requirements.txt + *.py", True)],
        "blocks": ["env, .env (compose injects it at runtime via env_file), osm_data (volume-mounted), tests"],
    },
    "client-react/.dockerignore": {
        "on": ["React / Leaflet client"],
        "module": ("Deploy · client-react/", "NEW — context for the standalone client image"),
        "file": (".dockerignore", "8 lines"),
        "outline": [("node_modules/dist/coverage/.env*", "excluded", True)],
        "blocks": ["node_modules, dist, coverage, .env*, logs"],
    },
    "osrm/.dockerignore": {
        "on": ["OSRM"],
        "module": ("Deploy · osrm/", "NEW — context for the OSRM image"),
        "file": (".dockerignore", "5 lines"),
        "outline": [("local OSM/OSRM artifacts", "*.osm.pbf / *.osrm* kept out of uploads", True)],
        "blocks": ["*.osm.pbf, *.osrm*, .DS_Store — only the .lua profiles + build-merged.sh ship"],
    },
    "screenshots/.dockerignore": {
        "on": [],
        "module": ("Deploy · screenshots/", "NEW — context for the capture job"),
        "file": (".dockerignore", "6 lines"),
        "outline": [("capenv venv + captures", "excluded", True)],
        "blocks": ["capenv (a full venv sat in this context), *.png, __pycache__"],
    },
    "droast.toml": {
        "on": [],
        "module": ("Deploy · repo root", "NEW — droast project policy: the deliberate deviations, with rationale"),
        "file": ("droast.toml", "~25 lines — comments + a 4-rule skip list"),
        "outline": [
            ("rationale comments", "one block per skipped rule", True),
            ("skip list", "DF005 / DF011 / DF020 / DF036", True),
        ],
        "blocks": [
            "DF005 apt pinning (mirror churn), DF011 single-stage (slim images), DF020 USER (documented root needs), DF036 overlay CMD inheritance",
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
