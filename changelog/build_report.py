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
OUT_PATH = os.path.join(HERE, "2026-08-01-vote-type-links-zigzag.html")

DATE = "2026-08-01"
TITLE = "Vote-type location links ([#n]) + the East River hairpin diagnosis"


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
        "id": "links",
        "tag": "CLIENT + SERVER + TOOLS",
        "title": "Per-vote-type proposal links ([a] [b] [c]) in proposal cards",
        "symptom": "Vote types on nyc-proposals are distilled from official DOT proposals, but a card row gave no way to reach the actual city proposals behind it \u2014 we wanted small lettered links next to each vote type in the listed votes.",
        "cause": [
            "Vote-type rows carry only {label, up, down}; there was no per-map, per-vote-type metadata channel for locations",
            "Imported vote types are cast-created (no maps.custom_vote_types row), so links must resolve by label, like iconForLabel",
        ],
        "fixes": [
            "maps.vote_type_links JSONB ({label: [{url, title?, lat?, lng?}]}) \u2192 emitted as voteTypeLinks on the map config only when set",
            "Admin endpoint POST/DELETE /api/admin/maps/&lt;slug&gt;/vote-type-links (X-Admin-Token, whole-mapping replace) + set_map_vote_type_links() with shape validation",
            "ProposalCard renders at most 3 links as small lettered [a] [b] [c] anchors on a line under the label; each points at the proposal's SOURCE page on nycdotprojects.info (target=_blank, so the map is never navigated away)",
            "Which 3: the proposals NEAREST the card's anchor (rowLinks, equirectangular ranking) \u2014 a Lower East Side card offers the EV/LES study, a Coney Island card offers the Coney Island + Canarsie studies, out of the same 48-entry 'Street redesign' list. Every card site passes an anchor: pinned point, both hover cards, and the route summary",
            "tools/nyc_proposals/set_vote_type_links.py builds the mapping from a plan JSONL (url = the DOT project page, title = project title, lat/lng = the proposal's location \u2014 corridors anchored at their midpoint) and installs it \u2014 60 proposals across 6 vote types on nyc-proposals locally",
            "invalidate_map_cache now also clears the /api/maps/&lt;slug&gt; SWR cache \u2014 admin mutations used to serve the pre-mutation config for up to 30s",
        ],
        "files": ["server/database.py", "server/app.py", "client-react/src/map/runtime.ts",
                  "client-react/src/components/GraphLayer/GraphLayer.tsx",
                  "client-react/src/styles/globals.css", "tools/nyc_proposals/set_vote_type_links.py"],
    },
    {
        "id": "zigzag",
        "tag": "DATA / TOOLS",
        "title": "The East River hairpin: why the top route proposal zig-zagged",
        "symptom": "nyc-proposals' top route proposal (fa8b70e11, 'Add traffic calming', 157 blocks) ran down the East River esplanade, around Corlears Hook, and back north up the parallel inland streets \u2014 both waterfront stretches selected.",
        "cause": [
            "NOT the block bake: recomputing proposals from prod data shows the 438 voted edges form a single simple path (2 endpoints, 0 branches) and zero blocks span both rails",
            "One DOT corridor cast IS the hairpin: 'EV/LES Waterfront Access Study' parsed as 'FDR Drive: Montgomery St \u2192 14th St', and geocoding put one endpoint inside John V. Lindsay East River Park",
            "The park esplanade is a stranded stub in the current OSM walk graph (ESCR-era closures): its only exit is at the far south end \u2014 reaching a point 330m north takes a 5.4km walk",
            "So the genuine shortest path to the corridor's other endpoint is a 7.75km hairpin (2.6\u00d7 the 3km crow distance); it passed the \u22648km and \u2264600-edge guards and cast 438 net-1 votes along the detour, which then outscored every clean corridor by construction (score = sum of edge nets)",
        ],
        "fixes": [
            "cast_entry gains DETOUR_RATIO_MAX = 1.8: a routed corridor longer than 1.8\u00d7 its crow distance demotes to the point-kind catch-all pin (every legitimate corridor today is \u2264 1.34)",
            "Local repair executed: direction=0 cleared the voter's 438 hairpin votes, recast as a 'Street redesign' pin at Delancey &amp; the esplanade; prod repair script staged (scratchpad, needs go-ahead)",
            "scripts/analyzeZigzag.ts: Node harness that recomputes route proposals from any live API and dumps the top corridor's per-edge nets/blocks/geometry",
        ],
        "files": ["tools/nyc_proposals/import_to_map.py", "client-react/scripts/analyzeZigzag.ts"],
    },
]



VERIFY = [
    "Prod reproduction: <code>analyzeZigzag.ts</code> against cityedit.org rebuilt the exact live proposal set "
    "(top = <code>#a8b70e11</code>, score 438, 157 blocks, 1 ghost) \u2014 and its component is a simple path: "
    "<strong>2 endpoints, 0 branch nodes</strong>; block check: <strong>0 blocks</strong> span both rails.",
    "Router probes: start \u2192 +330m north = <strong>5.4km</strong> route (stranded stub confirmed); "
    "start \u2192 corridor end = 7.75km vs 3.01km crow (ratio 2.57). All 8 corridor casts ratio-checked: "
    "culprit 2.57, every other \u2264 1.34 \u2014 the 1.8 threshold separates cleanly.",
    "Links round-trip: admin POST \u2192 <code>/api/maps/nyc-proposals</code> serves <code>voteTypeLinks</code> \u2192 "
    "exactly 3 anchors render (10px, opacity .5, <code>target=_blank rel=noopener noreferrer</code>), hrefs are the "
    "nycdotprojects.info project pages (spot-checked 4 \u2192 HTTP 200).",
    "Proximity ranking is location-sensitive: the Delancey card lists EV/LES + N Williamsburg; the Coney Island card "
    "lists Coney Island + Canarsie \u2014 same vote type, same 48-entry list.",
    "Local repair verified: 438 votes cleared (rev bump), 'Add traffic calming' total 915 \u2192 477, recast pin "
    "becomes the block's top proposal.",
    "<code>npx tsc -b</code> clean; all 327 client unit tests pass; touched Python compiles.",
]

CHECKLIST = [
    "Open <code>http://localhost:3000/m/nyc-proposals?w=40.715500%2C-73.976500&vt=Street+redesign</code>, click the "
    "pin block \u2014 the card should show 'Street redesign' with [a] [b] [c]; hover for the project titles and click one "
    "to open the DOT page in a new tab.",
    "Confirm the hairpin is gone locally: the East River loop no longer paints, and the top route proposal is now "
    "Linden Boulevard (<code>#128218e4</code>).",
    "Decide on the prod repair: run <code>repair_prod_hairpin.py</code> (scratchpad) after the usual DB backup \u2014 "
    "clears the 438 prod hairpin votes and drops the replacement pin.",
    "Deploy: overlay deploy (backup \u2192 digest \u2192 verify; staging is decommissioned as of 2026-08-01) ships the "
    "[#n] client rendering + admin endpoint; then run "
    "<code>set_vote_type_links.py --base &lt;prod&gt; --token &lt;ADMIN_TOKEN&gt;</code> with the plan JSONL.",
    "Weekly job: next Monday's run now demotes any future detour corridor (watch for <code>ok-detour-pin</code> in its log).",
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
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API \u00b7 persistence", "Postgres schema + maps/vote/redirect queries"),
        "file": ("database.py", "~1350 LOC \u2014 schema init, map CRUD, vote rows, admin setters"),
        "outline": [
            ("schema init", "ALTER maps ADD vote_type_links JSONB", True),
            ("_map_row_to_dict / _MAP_COLUMNS", "unpack + emit voteTypeLinks only when set", True),
            ("set_map_vote_type_links", "whole-mapping replace with shape validation", True),
            ("votes / redirects / rename", "unchanged", False),
        ],
        "blocks": [
            "ALTER TABLE maps ADD COLUMN IF NOT EXISTS vote_type_links JSONB",
            "voteTypeLinks emitted only when set \u2014 absent key = no links",
            "set_map_vote_type_links(slug, links): {label: [{url, title?}]}, Json() adapter, rowcount guard",
        ],
    },
    "server/app.py": {
        "on": ["Flask API"],
        "module": ("Flask API \u00b7 app", "routes, caches, admin endpoints"),
        "file": ("app.py", "~2400 LOC \u2014 vote/route/graph endpoints, SWR caches, admin surface"),
        "outline": [
            ("invalidate_map_cache", "now also clears the /api/maps SWR cache", True),
            ("admin endpoints", "+ POST/DELETE /api/admin/maps/<slug>/vote-type-links", True),
            ("vote / graph-votes / routes", "unchanged", False),
        ],
        "blocks": [
            "_map_get_cache.pop(slug) in invalidate_map_cache \u2014 admin mutations no longer stale for 30s",
            "admin_set_vote_type_links: X-Admin-Token gated, whole-mapping replace, invalidates on success",
        ],
    },
    "client-react/src/map/runtime.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client \u00b7 map/", "map-config resolution: slug/subdomain \u2192 /api/maps \u2192 MapConfig"),
        "file": ("runtime.ts", "~215 LOC \u2014 MapConfig type, resolveMapConfig, slugRedirectUrl"),
        "outline": [
            ("VoteTypeLink + MapConfig", "voteTypeLinks?: Record<label, VoteTypeLink[]>", True),
            ("resolveMapConfig / redirects", "unchanged", False),
        ],
        "blocks": [
            "VoteTypeLink { url, title?, lat?, lng? } \u2014 lat/lng drive the nearest-3 pick",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client \u00b7 GraphLayer/", "vote heat, PBTP/RBTP proposals, proposal cards"),
        "file": ("GraphLayer.tsx", "~4800 LOC \u2014 topology load, heat paint, ProposalCard"),
        "outline": [
            ("rowLinks helper", "nearest MAX_ROW_LINKS=3 by equirectangular distance", True),
            ("ProposalCard", "linkAnchor prop; [a] [b] [c] external anchors per row", True),
            ("card render sites", "all four pass an anchor (pinned/hover/RBTP/route)", True),
            ("hover/selection/markers", "unchanged", False),
        ],
        "blocks": [
            "links resolve by row.label \u2014 covers cast-created vote types with no custom_vote_types row",
            "anchor per link: href = the DOT project page, target=_blank rel=noopener noreferrer, title = project title",
        ],
    },
    "client-react/src/styles/globals.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client \u00b7 styles", "global stylesheet (cards, rows, vote controls)"),
        "file": ("globals.css", "~2300 lines \u2014 proposal card + row styles live around :1950"),
        "outline": [
            (".graph-proposal-row", "flex-wrap so the links line can wrap under", True),
            (".graph-proposal-row-links / -link", "10px, opacity .5, quiet until hover", True),
        ],
        "blocks": [
            "flex: 1 0 100% \u2014 the links line never squeezes the label or the \u00b1 control",
        ],
    },
    "tools/nyc_proposals/set_vote_type_links.py": {
        "on": ["Flask API"],
        "module": ("Tools \u00b7 nyc_proposals/", "plan JSONL \u2192 vote-type location links installer"),
        "file": ("set_vote_type_links.py", "~80 LOC \u2014 stdlib only, --dry-run, admin POST"),
        "outline": [
            ("link_for", "url = DOT project page \u00b7 lat/lng = proposal location (corridor midpoint)", True),
            ("main", "group by vote_type, print, POST /api/admin/\u2026/vote-type-links", True),
        ],
        "blocks": [
            "idempotent: the whole mapping is replaced on every run; dedupes (vote_type, url)",
        ],
    },
    "tools/nyc_proposals/import_to_map.py": {
        "on": ["Flask API"],
        "module": ("Tools \u00b7 nyc_proposals/", "official-DOT-proposals \u2192 votes pipeline (plan/cast CLI)"),
        "file": ("import_to_map.py", "~360 LOC \u2014 filters, classifier, geocoding, corridor guards, casting"),
        "outline": [
            ("corridor guards", "+ DETOUR_RATIO_MAX = 1.8 (routed vs crow distance)", True),
            ("cast_entry", "detour \u2192 demote to point-kind catch-all pin, status ok-detour-pin", True),
            ("plan / classify / geocode", "unchanged", False),
        ],
        "blocks": [
            "routed_km > max(0.4, 1.8 \u00d7 crow_km) \u21d2 the 'corridor' is a hairpin, not the project's street",
            "demotion mirrors the unparseable-endpoints fallback: vote_type='Street redesign', kind=point, pin at start",
        ],
    },
    "client-react/scripts/analyzeZigzag.ts": {
        "on": ["React / Leaflet client", "Flask API"],
        "module": ("Client scripts", "one-off diagnostics driven through the client's own algorithms"),
        "file": ("analyzeZigzag.ts", "~100 LOC \u2014 vite-node; fetches cfg/topology/votes, recomputes proposals"),
        "outline": [
            ("proposal recompute", "computeRouteProposals with the map's own floor + kindOf", True),
            ("dump", "top corridor per-edge nets/blocks/geometry + all voted edges of that type", True),
        ],
        "blocks": [
            "API_BASE=https://cityedit.org \u2026 vite-node scripts/analyzeZigzag.ts nyc-proposals",
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
