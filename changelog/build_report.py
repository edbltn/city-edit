#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes.diff, writes changelog/2026-06-14-caching-concurrency-zoom.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-06-14-caching-concurrency-zoom.html")

DATE = "2026-06-14"
TITLE = "Backend caching + concurrency, and the zoom overhaul"


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
        "id": "crash",
        "tag": "Backend + Frontend",
        "title": "1 · Stale-cache crash on heatmap load",
        "symptom": (
            "On mobile (iOS Safari) the app showed <em>“a problem repeatedly occurred”</em> "
            "on heatmap load — reproduced on the NYC e-bikes (station-network) map. That message "
            "is Safari’s crash-reload loop: the page throws, Safari reloads, it throws again."
        ),
        "cause": [
            "The heatmap is painted by indexing per-edge / per-node vote arrays returned by "
            "<code>/api/graph-votes</code> against the graph <strong>topology</strong> the client "
            "holds. Topology is cached aggressively (IndexedDB + a day-long HTTP <code>max-age</code>); "
            "votes refresh every few seconds.",
            "When the two disagree on dimensions — a stale cached topology with a different node/edge "
            "count than the fresh votes (after a graph rebuild, or when the version probe fails so the "
            "cache-busting URL is skipped) — the render indexes <strong>past the end</strong> of the "
            "arrays, or the binary topology decoder allocates garbage from a corrupt blob. Either throws, "
            "and there was <strong>no error boundary</strong>, so the whole React tree died → Safari loop.",
        ],
        "fixes": [
            "<strong>Server stamps the topology dimensions onto the votes</strong> "
            "(<code>n_edges</code>, <code>n_nodes</code>, <code>topology_version</code>) so the client "
            "can detect a mismatch instead of trusting that the arrays line up. "
            "(<code>server/app.py</code> · <code>_build_graph_votes_body_locked</code>)",
            "<strong>Client validates before painting.</strong> <code>votesMatchTopology()</code> "
            "compares the stamped dimensions to the held topology; on mismatch it refetches the topology "
            "fresh — bypassing both the IndexedDB and the HTTP cache (<code>cache:&nbsp;\"reload\"</code>) — "
            "rebuilds the indices, and only then applies the votes. If it still can’t reconcile, it clears "
            "the persisted cache and bails rather than paint a crash.",
            "<strong>Hardened the binary topology decoder.</strong> <code>decodeTopologyBin()</code> now "
            "validates the <code>GTB1</code> magic + the exact byte length <em>before</em> allocating, "
            "and clamps out-of-range node indices — a corrupt/truncated cached blob throws cleanly and "
            "falls back to a fresh fetch instead of OOM-ing the tab. <code>buildEdgeIndex()</code> no "
            "longer dereferences a missing node.",
            "<strong>Added a React error boundary</strong> around the map. The first render crash in a "
            "session clears the (usually poisoned) graph cache and reloads <em>once</em>; a second crash "
            "shows a manual “Reload the map” fallback instead of looping. This is the definitive backstop "
            "for “a problem repeatedly occurred”, whatever the trigger.",
        ],
        "files": [
            "server/app.py — stamp n_edges / n_nodes / topology_version onto the votes body",
            "client-react/src/components/GraphLayer/GraphLayer.tsx — votesMatchTopology, fresh-topology refetch, decoder + edge-index hardening",
            "client-react/src/utils/graphCache.ts — clearGraphCache()",
            "client-react/src/components/ErrorBoundary/ErrorBoundary.tsx (+ .css) — recovery boundary",
            "client-react/src/App.tsx — wrap <MapView> in the boundary",
        ],
    },
    {
        "id": "concurrency",
        "tag": "Backend",
        "title": "2 · Concurrency — safe under >1 instance / many tenants",
        "symptom": (
            "The app crashed when more than one tenant used it at once, and couldn’t safely scale past "
            "a single Flask instance."
        ),
        "cause": [
            "Production runs a <strong>single gevent gunicorn worker</strong> (the NYC graph is too big "
            "to duplicate across workers). gevent greenlets don’t preempt on CPU, so the seconds-long, "
            "pure-Python build of the vote arrays (~2M NYC edges) <strong>froze the whole worker</strong> "
            "— concurrent tenants’ requests (and the health probe) stalled behind it. Worse, N concurrent "
            "first-requests for the same map each rebuilt the same body in series (head-of-line blocking).",
            "The in-memory <code>_vote_cache</code> was an <strong>unbounded dict</strong>: every "
            "(map, mode) ever requested kept its full JSON body resident forever — a steady memory leak "
            "that OOM-crashes a multi-tenant server with many maps. Its invalidation also only popped the "
            "bare slug, never the per-mode <code>&lt;slug&gt;:&lt;mode&gt;</code> keys it actually stored.",
            "The voter read-modify-write was guarded only by a <strong>per-process</strong> "
            "<code>threading.Lock</code> — meaningless across instances/workers, so two near-simultaneous "
            "votes from one voter could double-apply.",
        ],
        "fixes": [
            "<strong>Bounded LRU vote cache.</strong> <code>_vote_cache</code> is now a capped "
            "<code>OrderedDict</code> (<code>VOTE_CACHE_MAX</code>, default 64) guarded by a lock — memory "
            "stays flat no matter how many tenant maps exist.",
            "<strong>Correct, mode-aware invalidation.</strong> <code>_invalidate_vote_cache(slug)</code> "
            "drops the bare slug <em>and</em> every <code>&lt;slug&gt;:&lt;mode&gt;</code> variant; wired "
            "into the vote path, the pub/sub delta listener, and the graph-reload re-snap.",
            "<strong>Single-flight on the expensive build.</strong> A per-cache-key lock means one greenlet "
            "builds the arrays while the rest wait and take the result — killing the head-of-line stampede "
            "that stalled the worker under concurrent tenants.",
            "<strong>Cross-instance voter lock.</strong> <code>vote_store.voter_lock()</code> is a short "
            "Redis <code>SET NX</code> lock keyed by (slug, device) that serializes a voter’s "
            "read-modify-write <em>fleet-wide</em>; it auto-expires and fails open so a vote never hangs on "
            "Redis. Combined with the bounded shared-state design, this is what makes running more than one "
            "instance safe.",
        ],
        "files": [
            "server/app.py — bounded LRU + _vote_cache_get/put, _invalidate_vote_cache, _build_lock_for single-flight, voter_lock wiring",
            "server/vote_store.py — voter_lock() cross-instance Redis lock",
        ],
    },
    {
        "id": "zoom",
        "tag": "Frontend",
        "title": "3 · Zoom overhaul — heatmap scales instead of vanishing",
        "symptom": (
            "The heatmap disappeared while zooming and only snapped back after the zoom finished."
        ),
        "cause": [
            "The heatmap is a hand-managed <code>&lt;canvas&gt;</code> in a custom Leaflet pane. On "
            "<code>zoomstart</code> the canvas was <strong>cleared</strong>, and it was only repainted on "
            "<code>zoomend</code> — so for the whole ~250&nbsp;ms zoom animation it was blank. Unlike "
            "Leaflet’s own tile / canvas renderers, it never rode the zoom animation’s transform.",
        ],
        "fixes": [
            "Stopped clearing the heatmap canvas on <code>zoomstart</code> — the existing bitmap stays up.",
            "Gave both canvases the <code>leaflet-zoom-animated</code> class so Leaflet’s zoom-animation CSS "
            "transition applies to their <code>transform</code>.",
            "Added a <code>zoomanim</code> handler that sets each canvas’s transform to where its top-left "
            "geographic corner lands at the target zoom, scaled by the zoom ratio "
            "(<code>getZoomScale</code> + <code>_latLngToNewLayerPoint</code> — the exact mechanism "
            "<code>L.Canvas</code> uses). The browser tweens it, so the heatmap glides and scales with the "
            "map. <code>zoomend</code> then repaints crisply at the new resolution and line widths.",
            "The draw state (zoom + top-left lat/lng) is recorded at the end of every paint so the animation "
            "transform is always anchored to the right geography.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — drawStateRef, leaflet-zoom-animated class, handleZoomAnim, zoomstart no longer clears the heatmap",
        ],
    },
]

VERIFY = [
    "Backend: <code>python -m py_compile app.py vote_store.py</code> — clean.",
    "Backend: <code>pytest tests/unit/test_hydration.py tests/unit/test_vote_counts.py</code> — 10 passed (these exercise the vote cache + build path).",
    "Frontend: <code>tsc -b &amp;&amp; vite build</code> — clean production build.",
    "Frontend: <code>vitest run</code> — 101 passed.",
    "Frontend: <code>eslint</code> on the changed files — 0 errors (4 pre-existing exhaustive-deps warnings on mount-once effects).",
]

CHECKLIST = [
    "Open the NYC e-bikes map on a phone (or iOS Simulator Safari). It should load the station heatmap without the “a problem repeatedly occurred” loop. To simulate a poisoned cache, in devtools set IndexedDB <code>desire-path-cache</code> to garbage — the error boundary should clear it and recover on one reload.",
    "Zoom in and out (scroll, pinch, and the +/- control). The heatmap should scale smoothly <em>with</em> the map, not blink out and reappear.",
    "Cast a few votes from two browsers/devices at once on the same map — counts should stay correct (no double-count), and the heatmap should update for both.",
    "Hit <code>/api/graph-votes?map=&lt;slug&gt;&amp;mode=&lt;mode&gt;</code> and confirm the JSON now includes <code>n_edges</code>, <code>n_nodes</code>, and <code>topology_version</code>.",
    "Leave the app open across many maps/modes for a while and watch the worker’s memory — it should stay flat (bounded vote cache) rather than climbing.",
    "Optionally bump the deploy to more than one Cloud Run instance and load-test concurrent voting; the Redis voter lock keeps the read-modify-write correct across instances.",
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
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WebSocket routes, the per-map vote cache, graph + OSRM registries, startup warmup"),
        "file": ("app.py", "~1660 LOC — every API/WS route plus the vote-response cache and city registries"),
        "outline": [
            ("Flask / Redis / registries setup", "app, CORS, sock, redis client, GraphRegistry", False),
            ("Map resolution — resolve_map", "slug → city / graph / OSRM / policy", False),
            ("Passcode gate", "private-map token check + lockout", False),
            ("Per-map vote response cache", "bounded LRU · single-flight build · mode-aware invalidation · stamps topology dims", True),
            ("Startup warmup + pub-sub listeners", "Postgres→Redis replay; delta listener invalidates cache", True),
            ("Vote API — /api/vote", "directional voting; now wraps the read-modify-write in a cross-instance Redis lock", True),
            ("Graph data APIs", "/api/graph-topology, /api/graph-votes, /api/graph-version", False),
            ("Admin APIs", "subdomain, refresh-osm, stats", False),
        ],
        "blocks": [
            "import OrderedDict",
            "Bounded-LRU _vote_cache + _vote_cache_get/put + _invalidate_vote_cache + single-flight _build_lock_for",
            "_build_graph_votes_body → single-flight wrapper + _build_graph_votes_body_locked (stamps n_edges / n_nodes / topology_version)",
            "pub-sub delta listener + _resnap_city_maps → _invalidate_vote_cache (was pop(slug))",
            "cast_vote → with vote_store.voter_lock(…), _proposal_vote_lock",
        ],
    },
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "the Redis vote-cache layer: bit-packed fields, read/write path, derived arrays"),
        "file": ("vote_store.py", "~390 LOC — packs votes into Redis fields, builds the heatmap arrays, vote-type cache"),
        "outline": [
            ("Cross-instance voter lock", "NEW — Redis SET-NX lock per (slug, device) serializing a voter fleet-wide", True),
            ("Keys / mode enum", "hash_key, channel_key, revision_key, MODE_IDS", False),
            ("Vote-type cache", "label↔id cache backed by Postgres", False),
            ("Bit packing", "pack / unpack / redis_field (45-bit field)", False),
            ("Write path", "apply_directional, publish_delta", False),
            ("Read path", "read_all, read_edge_vt_counts, build_arrays", False),
            ("Coordinate → edge mapping", "coords_to_edge_ids, osm_nodes_to_edge_ids", False),
        ],
        "blocks": [
            "import contextlib / os / time",
            "voter_lock() — @contextmanager Redis SET-NX lock, auto-expiring, fails open",
        ],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the canvas heatmap + topology loader + proposal-marker renderer — the heart of the map"),
        "file": ("GraphLayer.tsx", "~3340 LOC — one big component: load topology+votes, paint the heat canvas, hit-test, render proposals"),
        "outline": [
            ("Module helpers", "decodeTopologyBin, buildEdgeIndex, votesMatchTopology, buildNodeAdj", True),
            ("Component state & refs", "canvas / topology / graphData refs, projCacheRef, drawStateRef", True),
            ("Canvas init", "create heat + hover canvases, attach to graphPane, leaflet-zoom-animated", True),
            ("Topology + vote loading", "version → IndexedDB cache → network fetch → reconcile dims → paint", True),
            ("redraw — heat passes", "viewport-culled multi-pass canvas paint; captures draw-state", True),
            ("Map event handlers (pan / zoom)", "zoomanim transform; zoomstart no longer clears the heatmap", True),
            ("Hover / hit-testing", "nearest-edge snap, tooltips, pinned cards", False),
            ("Indicator markers", "vote winners / station markers / cluster fan-out", False),
        ],
        "blocks": [
            "decodeTopologyBin — validate GTB1 magic + byte length before allocating; clamp bad node indices",
            "buildEdgeIndex — index a degenerate box instead of dereferencing a missing node",
            "votesMatchTopology() — NEW dimension guard",
            "drawStateRef — records {zoom, top-left latlng} each paint",
            "canvas init — add `leaflet-zoom-animated` to both canvases",
            "load effect — fetchTopologyFromNetwork(forceReload) + stale-topology refetch on mismatch",
            "fetchVotes — skip applying a mid-session dimension mismatch",
            "redraw — capture drawStateRef at end of paint",
            "zoom handlers — handleZoomAnim (transform) + don't clear heat on zoomstart",
        ],
    },
    "client-react/src/utils/graphCache.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "IndexedDB persistence for topology + vote arrays, keyed by graph version"),
        "file": ("graphCache.ts", "~175 LOC — get/set cached topology (JSON + binary) and votes; version-busting"),
        "outline": [
            ("DB open / upgrade", "desire-path-cache, DB_VERSION=2 clears stale store", False),
            ("idbGet / idbSet", "best-effort key/value helpers", False),
            ("Topology cache", "getCachedTopology / Bin + setters", False),
            ("Votes cache", "getCachedVotes / setCachedVotes (version-scoped)", False),
            ("clearGraphCache", "NEW — wipe every entry; recovery path for a poisoned cache", True),
        ],
        "blocks": [
            "clearGraphCache() — clears the object store so a poisoned cache can't survive a reload",
        ],
    },
    "client-react/src/components/ErrorBoundary/ErrorBoundary.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/ErrorBoundary", "NEW — render-crash recovery boundary around the map"),
        "file": ("ErrorBoundary.tsx", "NEW FILE — class boundary: clear the graph cache + reload once, then a manual fallback"),
        "outline": [
            ("getDerivedStateFromError", "flip to the crashed state", True),
            ("componentDidMount", "clear the recovery flag after ~12s of health", True),
            ("componentDidCatch", "first crash → clear cache + reload once; second → manual fallback", True),
            ("render", "fallback card with a Reload button", True),
        ],
        "blocks": ["Whole file is new — the safety net for “a problem repeatedly occurred”."],
    },
    "client-react/src/components/ErrorBoundary/ErrorBoundary.css": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/ErrorBoundary", "NEW — styles for the fallback card"),
        "file": ("ErrorBoundary.css", "NEW FILE — full-screen centered fallback, editorial paper/ink palette"),
        "outline": [(".error-boundary*", "overlay, card, title, body, button", True)],
        "blocks": ["New stylesheet for the fallback UI."],
    },
    "client-react/src/components/index.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/", "barrel re-exporting the component public surface"),
        "file": ("index.ts", "~12 LOC — barrel exports"),
        "outline": [("exports", "TopBar, MapView, …, + ErrorBoundary", True)],
        "blocks": ["export { ErrorBoundary } from \"./ErrorBoundary/ErrorBoundary\""],
    },
    "client-react/src/App.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · src/", "app root: providers, map bootstrap, the #app shell"),
        "file": ("App.tsx", "app root — resolves the map, then mounts the map subtree"),
        "outline": [
            ("FullScreenLoader", "the ASCII-spinner splash", False),
            ("AppContent", "shell: TopBar, <main> map, toasts — now wraps MapView in ErrorBoundary", True),
            ("MapApp / providers", "resolve map config, provider tree", False),
        ],
        "blocks": [
            "import ErrorBoundary",
            "wrap <MapView /> in <ErrorBoundary>",
        ],
    },
    "CLAUDE.md": {
        "on": [],
        "module": ("Docs · repo root", "the project’s agent + architecture instructions"),
        "file": ("CLAUDE.md", "agent instructions, architecture overview, runbooks"),
        "outline": [
            ("In-progress note", "this workstream summary", True),
            ("Changelog + dev-server conventions", "standing instructions added this turn", True),
            ("Architecture / runbooks", "unchanged", False),
        ],
        "blocks": [
            "In-progress workstream note (links the changelog)",
            "New standing instructions: changelog diagrams + redeploy to localhost:3000",
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">Three fixes in one pass: the stale-cache crash that loops mobile Safari on heatmap load,
  the concurrency limits that broke the app under multiple tenants, and the heatmap that vanished mid-zoom.</p>

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
