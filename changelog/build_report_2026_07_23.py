#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report_2026_07_23.py
Reads changelog/changes.diff, writes changelog/2026-07-23-perf-interim-borrows.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes.diff")
OUT_PATH = os.path.join(HERE, "2026-07-23-perf-interim-borrows.html")

DATE = "2026-07-23"
TITLE = "Borrowing perf from Brook's perf-interim PR #7 — early heat, cacheable tiles, and the races the review caught"


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


# Sections may override the default h3 labels (Symptom / Root cause /
# What changed) — the provenance section reads better as a triage table.
SECTIONS = [
    {
        "id": "provenance",
        "tag": "Provenance",
        "title": "1 · What was borrowed, skipped, and deferred from PR #7",
        "labels": ("Context", "Skipped or deferred — and why", "What we took (as 9 hand-ported commits)"),
        "symptom": (
            "Brook's <strong>perf-interim</strong> branch (PR #7) attacks the same cold-load and "
            "interaction latency we've been chipping at, but the branches diverged months ago — his is "
            "built on a pre-three-layer client and an older server. Nothing cherry-picks cleanly, so "
            "every borrow here is a <strong>hand-port</strong>: same idea, re-derived against our code, "
            "with his commit referenced where one maps 1:1 (e.g. the chunked index builds ← "
            "<code>a6bae355</code>)."
        ),
        "cause": [
            "<strong>Skipped as strict ancestors:</strong> his gzip-response work, WS fan-out batching, "
            "and Postgres connection pooling are earlier versions of what we already ship — the "
            "debounced-gzip snapshots + DeltaHub merged frames from the vote-path saturation round and "
            "the SWR/prewarm work in <code>800803b</code> subsume them. Porting them would be a "
            "regression.",
            "<strong>Deferred — too invasive for a borrow pass:</strong> the <em>Leaflet-deletion "
            "migration</em> (his client renders everything in MapLibre; ours keeps Leaflet as the "
            "camera owner and interaction layer — swapping that is its own workstream, and our "
            "camera-sync + selection model depend on it) and the <em>stable-eid registry</em> (a "
            "persistent edge-id scheme that would change the vote codec and every cache key — "
            "worthwhile, but it belongs with a migration plan, not a perf pass).",
            "<strong>Replaced rather than ported:</strong> his <code>/api/heat</code> endpoint (a "
            "server-rendered heat summary the client paints before topology) exists to get first heat "
            "up early. Our sparse <code>/api/graph-votes</code> body already carries the complete block "
            "heat in ~57KB, so we get the same effect with a client-only resequencing — no new "
            "endpoint, no second source of truth (§2).",
        ],
        "fixes": [
            "<code>36a6968</code> — early block-heat paint before topology decode + chunked "
            "(yield-to-main) spatial-index builds",
            "<code>5e7a12d</code> — retrying reverse-geocode, double-tap end guard, touch snap-marker "
            "guard, dev hosts follow the page hostname",
            "<code>f6ca922</code> — cacheable z/x/y block tiles + nginx tile cache + "
            "connection-ceiling knobs",
            "<code>ded3672</code> — frontend + WS load measurement harness (adapted to our endpoints)",
            "<code>c824b07</code> — SWR <code>map_get</code> enrichment bug (pre-existing prod bug "
            "found while verifying the tile descriptor reached clients)",
            "<code>333e480</code> — blocks source rebind to the z/x/y template on GL load",
            "<code>d40c300</code> — three race fixes the review of the port confirmed",
            "<code>02e5294</code> — tile endpoint hardening for prod (204 empties, x/y guard, hot-tile "
            "LRU, mtime_ns <code>?v</code>, atomic bake, coverage bounds)",
            "<code>30257f7</code> — keepalive alignment across nginx/gunicorn + tile-cache "
            "lock/timeouts",
        ],
        "files": [
            "(triage only — every file below belongs to one of the sections that follow)",
        ],
    },
    {
        "id": "early-heat",
        "tag": "Frontend",
        "title": "2 · Early heat paint — first heat before the topology decodes",
        "symptom": (
            "On a cold load nothing painted until the multi-MB topology finished downloading and "
            "decoding — even though the sparse vote body (~57KB, fetched in parallel since "
            "<code>800803b</code>) already held everything needed to light the block heat."
        ),
        "cause": [
            "Block heat is applied by MapLibre as <strong>feature-state keyed purely by block id</strong> "
            "on the blocks tile source — it needs no topology and no edge arrays, and the payload is "
            "retained and re-applied until the source exists. The old sequencing awaited topology "
            "anyway, serializing the paint behind the biggest download of the load.",
            "This is what Brook's <code>/api/heat</code> endpoint was for; the client-only resequencing "
            "gets the same first-heat win without a new endpoint (§1).",
        ],
        "fixes": [
            "<strong>Paint the moment votes + version probe land.</strong> The vote body is decoded "
            "once (<code>votesDecodedPromise</code>, shared with step 3) and its block heat broadcast "
            "immediately — <em>before</em> topology — gated on "
            "<code>voteData.blocks_version === </code> the probe's blocks version (block ids renumber "
            "on re-bake; a mid-deploy mismatch falls through to the topology-gated flow unchanged). "
            "Marked with <code>dlog(\"blocks\", \"first-heat-paint…\")</code>; edge-array "
            "reconciliation (<code>votesMatchTopology</code>) still happens only in step 3, and the "
            "<code>[MAPLOAD]</code> beacon / <code>setHeatmapLoaded()</code> are untouched so the "
            "dashboard P99 series stays comparable.",
            "<strong>Step 2 defers to the early paint.</strong> The (older-rev) cached vote snapshot "
            "no longer repaints its block heat over the authoritative body once "
            "<code>earlyHeatPainted</code> is set.",
            "<strong>Chunked spatial-index builds</strong> (← his <code>a6bae355</code>): the Flatbush "
            "node/edge index builds yield to the main thread every 120k insertions "
            "(<code>INDEX_YIELD_BATCH</code>) so the ~650k-edge NYC build doesn't jank the tile/heat "
            "paint happening at exactly that moment. Each ref is set only when its index is "
            "<em>complete</em> (stale refs nulled first — a mousemove landing in a yield gap must "
            "never pair an old index with new topology), and <code>hitTest</code> / the drag-snap "
            "nearest-node fallback return no result while an index is still building instead of "
            "brute-force scanning every edge or node per mousemove.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — early paint, votesDecodedPromise, async yielding index builds, index-only hitTest",
        ],
    },
    {
        "id": "tiles",
        "tag": "Backend + nginx + Frontend",
        "title": "3 · Cacheable z/x/y block tiles + nginx tile cache + hardening",
        "symptom": (
            "Every warm visit re-downloaded the whole viewport's block tiles (~1MB): browsers never "
            "HTTP-cache the <code>pmtiles://</code> protocol's range requests (206 responses)."
        ),
        "cause": [
            "The blocks archive was only reachable through byte-range reads of the whole "
            "<code>blocks.pmtiles</code> file — correct, but invisible to the browser cache, nginx, "
            "and any CDN. Discrete per-tile URLs are cacheable at every layer.",
            "A prod-worthy version of the endpoint also has to survive our operational quirks: "
            "<code>blocks.pmtiles</code> is re-baked <strong>in place</strong> (lazy re-bake), bots "
            "walk tile space with out-of-range x/y, blocks coverage stops at the street network so "
            "water/edge tiles are requested forever, and the single gevent worker pays ~5ms of "
            "unyielding pure-Python directory parsing per <code>Reader.get</code>.",
        ],
        "fixes": [
            "<strong><code>GET /api/tile/&lt;city&gt;/blocks/&lt;z&gt;/&lt;x&gt;/&lt;y&gt;.mvt</code></strong> "
            "serves tiles out of the city's blocks archive. tippecanoe stores tiles gzip-compressed, so "
            "the hot path passes stored bytes through with <code>Content-Encoding: gzip</code>; the "
            "reader cache is keyed on <code>st_mtime_ns</code> and reopens on re-bake; "
            "<code>city_id</code> is validated against the registry before touching the filesystem; "
            "strong per-tile ETag + <code>max-age=1y immutable</code> (safe because the URL carries a "
            "<code>?v=</code> cache-buster that rolls per bake).",
            "<strong>Hardening (<code>02e5294</code>):</strong> x/y range-checked "
            "(<code>zxy_to_tileid</code> raises past 2^z−1 — scanners got 500s); mid-re-bake reader "
            "errors degrade to 404; <strong>empty tiles return a cacheable 204</strong> (MapLibre: "
            "empty tile, no error, no parent probing — unlike an uncacheable 404); the gzip LRU also "
            "caches stored-gzip pass-through bodies (≤128KB) to blunt cold-nginx-cache bursts; "
            "<code>?v=</code> derives from <code>st_mtime_ns</code> (two bakes in one second shared a "
            "URL and could durably poison the immutable caches); the descriptor carries the coverage "
            "<code>bounds</code> so MapLibre never requests outside them; and the bake script writes "
            "to a temp file + atomic rename so readers never observe a half-written archive.",
            "<strong><code>cities.to_public()</code></strong> gains a <code>blockTiles</code> "
            "descriptor (template + min/max zoom + bounds read from the archive header, cached per "
            "mtime) next to <code>blocksVersion</code>.",
            "<strong>Client:</strong> <code>CONFIG.blockTiles</code> (devHost-aware), and MapLibre "
            "declares the blocks source as a z/x/y vector source when advertised, falling back to "
            "<code>pmtiles://</code> otherwise. Tile bytes are identical either way and the source "
            "keeps its <code>blocks</code> name, so feature-state heat/selection is untouched. "
            "<strong>Rebind on GL load (<code>333e480</code>):</strong> the GL map is created at mount "
            "but <code>CONFIG.blockTiles</code> resolves with the map-config fetch — when creation wins "
            "the race, <code>buildStyle</code> fell back to <code>pmtiles://</code> and never "
            "revisited. <code>rebindBlockTiles()</code> swaps the source via "
            "<code>VectorTileSource.setTiles()</code> on the map's <code>load</code> event; applied "
            "heat survives because feature-state lives on the source.",
            "<strong>nginx (local + Cloud Run):</strong> <code>proxy_cache</code> for "
            "<code>/api/tile/</code> (tmpfs-capped 128m on Cloud Run, <code>X-Tile-Cache</code> debug "
            "header), <code>proxy_cache_valid 200 204</code>, <code>proxy_cache_lock</code> to "
            "collapse cold-cache stampedes, 300s proxy timeouts (a lazy city graph load freezes the "
            "worker past the 60s default), <code>worker_connections</code> 1024→4096, upstream "
            "<code>keepalive 32</code> with HTTP/1.1.",
        ],
        "files": [
            "server/app.py — z/x/y tile endpoint + reader/gzip LRU caches",
            "server/cities.py — _blocks_header_info + blockTiles descriptor",
            "server/streetscape_blocks/build_city_blocks.sh — atomic bake (tmp + rename)",
            "client-react/src/config.ts — BlockTilesConfig + CONFIG.blockTiles",
            "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx — z/x/y source + rebindBlockTiles",
            "nginx.conf / deploy/nginx-cloudrun.conf — tile proxy_cache + /api/tile/ location + connection knobs",
        ],
    },
    {
        "id": "swr-bug",
        "tag": "Backend · prod bug",
        "title": "4 · The SWR enrichment bug — found while verifying the port",
        "symptom": (
            "While verifying that the new <code>blockTiles</code> descriptor reached clients: after the "
            "first 30s TTL expiry, every <code>/api/maps/&lt;slug&gt;</code> response served an "
            "<strong>empty <code>city</code></strong> — no bounds/center/blocksVersion/blockTiles and "
            "no searchVoteTypes. A pre-existing prod bug from the <code>800803b</code> SWR round, not "
            "something this port introduced."
        ),
        "cause": [
            "<code>_refresh_map_get</code> (the background SWR refresh) cached <code>get_map()</code> "
            "<strong>raw</strong>, while the inline path enriched the dict with the city public config "
            "before responding. First response: enriched. Every response after the first background "
            "refresh: raw.",
            "NYC maps masked it because the client bootstrap defaults to NYC — non-NYC cities lost "
            "their camera config, and the new z/x/y block-tile template never reached the client at "
            "all (which is how verification caught it).",
        ],
        "fixes": [
            "Factored the enrichment into <code>_enrich_map()</code> and both paths — inline "
            "<code>_map_response</code> and the background <code>_refresh_map_get</code> — now cache "
            "only <strong>enriched</strong> dicts, with a docstring invariant: every path that puts a "
            "map into <code>_map_get_cache</code> must cache the enriched dict.",
        ],
        "files": [
            "server/app.py — _enrich_map() + _refresh_map_get caches enriched",
        ],
    },
    {
        "id": "races",
        "tag": "Frontend",
        "title": "5 · Three races the review of the port confirmed",
        "symptom": (
            "Code review of the ported chunked-index work confirmed three real races: a multi-second "
            "freeze on warm NYC loads, a torn index/topology pairing that could throw into the "
            "ErrorBoundary (which purges the cache), and reverse-geocode refetching forever on "
            "no-address points."
        ),
        "cause": [
            "<code>redraw()</code> fell back to drawing <strong>ALL edges</strong> when the edge index "
            "wasn't ready — on NYC that's 1.97M strokes in one rAF task, and warm loads hit it "
            "whenever topology decoded from IndexedDB before the votes response landed (the chunked "
            "build made the no-index window much longer).",
            "The stale-topology recovery installed the fresh edge index across two awaits while "
            "<code>graphDataRef</code> still held the old arrays — a hover in that window paired "
            "out-of-range edge ids with old typed arrays (NaN coords → Leaflet throw → ErrorBoundary "
            "purge).",
            "<code>reverseGeocode</code> returned a bare address, so a <em>successful null</em> "
            "(unnamed street, every station-network point) was indistinguishable from failure and "
            "stayed uncached — refetched on every render, forever.",
        ],
        "fixes": [
            "<code>redraw()</code> <strong>skips the frame</strong> while the chunked build is in "
            "flight; index-build completion schedules the repaint, so nothing is lost.",
            "The recovery path (and the mount path) now <strong>build fresh indexes into locals</strong> "
            "and install every ref in one synchronous block after the <code>cancelled</code> checks — "
            "no window where a fresh index is live against old arrays.",
            "<code>reverseGeocode</code> returns <code>{ok, address}</code>; callers cache by "
            "<em>ok-ness</em>, not null-ness, so success-null is cached and only "
            "failure-after-retries stays retryable.",
        ],
        "files": [
            "client-react/src/components/GraphLayer/GraphLayer.tsx — skip-frame redraw, build-into-locals install",
            "client-react/src/utils/geocode.ts — {ok, address} result shape",
        ],
    },
    {
        "id": "transport",
        "tag": "Deploy + Frontend + Perf",
        "title": "6 · Transport knobs, the perf harness, and the mobile fixes",
        "symptom": (
            "Three supporting casts: sporadic 502s once upstream pooling landed, no way to measure "
            "whether any of this actually helped, and a handful of mobile paper cuts Brook had already "
            "fixed."
        ),
        "cause": [
            "<strong>Keepalive misalignment:</strong> the new nginx upstream pooling defaulted to "
            "gunicorn closing idle connections at 2s while nginx pooled them for 60s — a request "
            "racing the FIN got <em>upstream prematurely closed</em>, and POSTs (vote casts) are never "
            "retried.",
            "<strong>No measurement:</strong> his branch ships a Playwright + WS harness; ours had "
            "only the Locust vote loadtest.",
            "<strong>Mobile:</strong> reverse-geocode queued behind tile/topology downloads on slow "
            "connections and one silent timeout left a pin's address unresolved forever; placing the "
            "end point re-armed the Start tool so an accidental double-tap wiped the fresh route; the "
            "hover ghost froze at the last tap position on touch devices; and hardcoded "
            "<code>localhost</code> API hosts made phone-on-LAN testing impossible.",
        ],
        "fixes": [
            "<strong>Keepalive aligned (<code>30257f7</code>):</strong> gunicorn "
            "<code>--keep-alive 75</code> vs nginx upstream <code>keepalive_timeout 55s</code> — nginx "
            "always retires pooled connections first. <code>--worker-connections 2000</code> raises "
            "the gevent ceiling, and <code>Dockerfile.overlay</code> now ships "
            "<code>deploy/supervisord.conf</code> so the knobs actually land on overlay deploys.",
            "<strong>Perf harness (<code>ded3672</code>, adapted):</strong> "
            "<code>measure.mjs</code> (load→heatmap, pan/zoom frame stats, via an injected "
            "<code>instrument.js</code> that listens on our Leaflet camera owner and takes first heat "
            "from an explicit <code>__perf.mark('first-heat-apply')</code> in MapLibreBackground — "
            "feature-state isn't introspectable from outside), <code>loadtest.mjs</code> (WS delta "
            "fan-out understanding DeltaHub merged frames + cold-visitor bursts against our sparse "
            "votes / z/x/y tiles / binary topology endpoints; the strict rev+1 check relaxed to "
            "monotonic), <code>seed-votes.mjs</code> (decodes GTB1/GTB2, casts each chain as ONE "
            "edge_ids request — block-scoped clear-then-cast would wipe earlier edges cast "
            "one-by-one), <code>compare.mjs</code>. <code>perf/results/</code> gitignored with one "
            "force-added baseline.",
            "<strong>Mobile/DX (<code>5e7a12d</code>):</strong> <code>reverseGeocode()</code> with 3 "
            "attempts + backoff and a <code>priority:\"high\"</code> fetch hint; a 700ms end-placement "
            "cooldown in <code>useMapClick</code>; SnapMarker skipped on touch devices "
            "(<code>(hover: hover)</code> media query); dev API/WS/tile hosts follow "
            "<code>window.location.hostname</code> with <code>VITE_API_BASE</code>/<code>VITE_WS_BASE</code> "
            "keeping precedence.",
        ],
        "files": [
            "deploy/supervisord.conf + Dockerfile.overlay — gunicorn knobs shipped on overlays",
            "nginx.conf / deploy/nginx-cloudrun.conf — upstream keepalive_timeout 55s",
            "perf/ — measure.mjs, instrument.js, loadtest.mjs, seed-votes.mjs, compare.mjs, package.json",
            "client-react/src/utils/geocode.ts (new) + RouteContext.tsx + GraphLayer.tsx — retrying reverse-geocode",
            "client-react/src/hooks/useMapClick.ts — end-placement cooldown",
            "client-react/src/components/MapView/MapView.tsx — touch snap-marker guard",
            "client-react/src/config.ts — devHost-aware API/WS/tile bases",
        ],
    },
    {
        "id": "docs",
        "tag": "Docs (rode along)",
        "title": "7 · Rode along — hybrid dev-loop quickstart + staging parity plan",
        "labels": ("Context", "Why", "What changed"),
        "symptom": (
            "Two documentation commits share the diff window (they are <em>not</em> part of the PR #7 "
            "borrow, but land in the same range and are included here for completeness)."
        ),
        "cause": [
            "The perf work leaned hard on the hybrid loop (Docker deps + host Flask/Vite) — worth "
            "making it the canonical quickstart instead of tribal knowledge.",
            "Verifying transport knobs against prod-shaped nginx/gunicorn again raised the question of "
            "a real staging environment.",
        ],
        "fixes": [
            "<strong>README + CLAUDE.md + Makefile:</strong> <code>make deps</code>/<code>deps-down</code> "
            "start Redis/Postgres/OSRM in Docker (OSRM on host :5005 — AirPlay squats :5000), "
            "<code>make dev</code> chains graphs→tiles→deps→host Flask+Vite; <code>server/.env.example</code> "
            "documents <code>OSRM_URL=http://localhost:5005</code>.",
            "<strong>docs/staging-parity-plan.md (new):</strong> plan for an unguessable-URL staging "
            "stack mirroring prod topology with digest promotion to prod — respects the known deploy "
            "landmines (no blanket <code>terraform apply</code>, PAP, resnap-on-deploy).",
        ],
        "files": [
            "README.md — Quickstart rewritten around the hybrid loop",
            "CLAUDE.md — Local Development section updated to match",
            "Makefile — deps / deps-down / dev / clean targets",
            "server/.env.example — OSRM_URL",
            "docs/staging-parity-plan.md — new plan doc",
            ".gitignore — perf/results/ + perf/shots/",
        ],
    },
]

VERIFY = [
    "Frontend: <code>tsc --noEmit</code> — clean; <code>vite build</code> — clean.",
    "Perf harness: <code>node --check</code> on all five scripts; <code>npm ci</code> in "
    "<code>perf/</code>; a live <code>measure.mjs</code> smoke against the dev stack — the "
    "<code>first-heat-apply</code> mark fired at <strong>973ms</strong> and the pan/zoom frame "
    "windows populated.",
    "Backend: <code>python -m py_compile app.py cities.py</code> — clean; tile endpoint exercised "
    "against dev (<code>curl -H 'Accept-Encoding: gzip' /api/tile/nyc/blocks/14/…</code> → 200 "
    "gzip + ETag; out-of-range x/y → 404; water tile → 204).",
    "The SWR enrichment bug was caught by exactly this verification: waiting out the 30s TTL and "
    "re-fetching <code>/api/maps/&lt;slug&gt;</code> showed the empty <code>city</code>; after the "
    "fix the enriched body (with <code>blockTiles</code>) survives background refreshes.",
    "Review pass over the ported code confirmed and closed the three §5 races before landing.",
]

CHECKLIST = [
    "Cold-load a street map (devtools → disable cache, open "
    "<code>http://localhost:3000/m/nyc-walkways?tab=perf</code>): block heat should appear "
    "<em>before</em> the streets/topology finish, and the console should show "
    "<code>[blocks] first-heat-paint: … (pre-topology)</code>.",
    "Immediately after load, hover the map: for ~a second hover simply does nothing (index still "
    "building), then lights up — with <strong>no freeze</strong> at any point, even on NYC.",
    "Check the tile path: <code>curl -sI -H 'Accept-Encoding: gzip' "
    "'http://localhost:5001/api/tile/nyc/blocks/14/4823/6160.mvt'</code> → 200 with "
    "<code>Content-Encoding: gzip</code>, an <code>ETag</code>, and "
    "<code>Cache-Control: … immutable</code>; a mid-river tile returns 204. Behind docker nginx, "
    "repeat a request and confirm <code>X-Tile-Cache: HIT</code>.",
    "Fetch <code>/api/maps/&lt;a-non-NYC-slug&gt;</code> twice, &gt;30s apart: both responses must "
    "carry a populated <code>city</code> (bounds/center/<code>blockTiles</code>) — the second is "
    "the SWR-refreshed cache entry that used to come back empty.",
    "On a phone on the same Wi-Fi, open the Network URL Vite prints: the map should load (API/WS "
    "follow the hostname), tapping start→end quickly must not wipe the route, no ghost pin lingers "
    "after a tap, and every dropped pin resolves an address (or a lat/lng fallback) without a "
    "permanent blank.",
    "Baseline vs now: <code>cd perf && node measure.mjs --label now --map nyc-walkways</code>, then "
    "<code>node compare.mjs results/loadtest-2026-07-22.json results/now.json</code>.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def section_html(s):
    l_symptom, l_cause, l_fixes = s.get("labels", ("Symptom", "Root cause", "What changed"))
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>{l_symptom}</h3>
      <p>{s['symptom']}</p>
      <h3>{l_cause}</h3>
      <ul>{li(s['cause'])}</ul>
      <h3>{l_fixes}</h3>
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
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/GraphLayer", "the canvas heatmap + topology loader + hit-testing + proposal renderer — the heart of the map"),
        "file": ("GraphLayer.tsx", "~5120 LOC — load topology+votes, build spatial indexes, paint the heat canvas, hit-test, render proposals"),
        "outline": [
            ("Module helpers — geometry + geocode", "pointToSegmentDist, haversine, resolveAddress (now via retrying reverseGeocode)", True),
            ("Tuning constants", "heat scale, cluster/spread, caps — + NEW INDEX_YIELD_BATCH / yieldToMain", True),
            ("Spatial index builders", "buildNodeIndex / buildEdgeIndex — now async, yielding every 120k inserts", True),
            ("hitTest + block helpers", "flatbush neighbors; brute-force no-index fallback DELETED", True),
            ("votesMatchTopology / rbtp helpers / IndicatorMarker", "dimension guard, proposal display positioning", False),
            ("GraphLayer — load effect (steps 0–3)", "version probe → EARLY HEAT PAINT → topology → chunked index install → votes reconcile", True),
            ("GraphLayer — redraw (heat passes)", "viewport-culled multi-pass paint; now skips frames while the index builds", True),
            ("GraphLayer — mousemove / drag-snap", "hover + nearest-node fallback — now index-only", True),
            ("Hover cards / ProposalCard", "pinned cards, vote tables, cluster fan-out", False),
        ],
        "blocks": [
            "import { reverseGeocode } from utils/geocode",
            "resolveAddress — cache by ok-ness ({ok, address}); success-null cached, only failure-after-retries retryable",
            "INDEX_YIELD_BATCH (120k) + yieldToMain; buildNodeIndex/buildEdgeIndex become async and yield per batch",
            "hitTest — delete the brute-force all-edges / all-nodes fallbacks (no hit while an index is building)",
            "load effect — votesDecodedPromise (decode once, shared) + earlyHeatPainted flag",
            "load effect step 1 — EARLY BLOCK-HEAT PAINT: broadcastBlockVotes pre-topology, gated on blocks_version === probe",
            "load effect step 1 — chunked index install: null stale refs, build into locals, install after cancelled checks, repaint",
            "load effect step 2 — skip repainting the older-rev cached snapshot over the early paint",
            "load effect step 3 — stale-topology recovery builds fresh indexes into locals, installs all refs in one synchronous block",
            "redraw — no index yet → skip the frame (was: draw ALL 1.97M edges in one rAF task)",
            "drag-snap nearest-node fallback — index-only (brute-force node scan deleted)",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapLibreBackground", "the MapLibre GL base map synced under Leaflet: block polygons + feature-state heat"),
        "file": ("MapLibreBackground.tsx", "~580 LOC — builds the GL style (blocks source), syncs the camera to Leaflet, diff-applies vote heat as feature-state"),
        "outline": [
            ("pmtiles protocol + zoom offset", "Protocol registration; leaflet−1 zoom convention", False),
            ("blockIdAtLatLng + paint builders", "queryRenderedFeatures probe; fill/line paint per map style", False),
            ("rebindBlockTiles", "NEW — swap the blocks source onto the z/x/y template via setTiles() on GL load", True),
            ("buildStyle", "GL style spec — blocks source now z/x/y vector source when advertised, pmtiles:// fallback", True),
            ("MapLibreBackground — map create + load", "creates the GL map; load handler now calls rebindBlockTiles", True),
            ("Camera sync (Leaflet → GL)", "move/zoomanim ride-along", False),
            ("Heat diff-apply", "feature-state writes; first apply now stamps __perf.mark('first-heat-apply')", True),
        ],
        "blocks": [
            "import BlockTilesConfig type",
            "rebindBlockTiles() — NEW: setTiles() to the template on GL load (feature-state lives on the source, heat survives)",
            "buildStyle — blocksSource: z/x/y vector source (template/minzoom/maxzoom/bounds) or pmtiles:// fallback",
            "map creation — pass CONFIG.blockTiles into buildStyle",
            "load handler — rebindBlockTiles(map) (closes the config-fetch vs map-creation race)",
            "heat apply — guarded window.__perf?.mark('first-heat-apply') on the first diff-apply (perf harness hook)",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · components/MapView", "the Leaflet map shell: panes, click handling, markers, camera plumbing"),
        "file": ("MapView.tsx", "~910 LOC — MapContainer + panes, cursor/drag trackers, SnapMarker ghost, zoom control"),
        "outline": [
            ("Panes / trackers", "MapPanes, MapViewTracker, MapDragCursor, MapClickHandler, CursorTracker", False),
            ("SnapMarker", "the follow-the-cursor hover ghost — now skipped on touch devices", True),
            ("MapView component", "MapContainer, GraphLayer wiring, markers, URL/camera sync", False),
            ("MapBridge / ZoomControl", "map handle export; custom zoom buttons", False),
        ],
        "blocks": [
            "SnapMarker — return null when !matchMedia('(hover: hover)') (no persistent cursor → the ghost froze at the last tap and read as a phantom pin)",
        ],
    },
    "client-react/src/config.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · src/", "runtime config: API/WS/tile URLs, camera bounds, per-city rebinding"),
        "file": ("config.ts", "~165 LOC — CONFIG object + CityConfig/applyCityConfig (per-city URL + camera rebinding)"),
        "outline": [
            ("Env detection", "isLocalDev, wsProtocol — + NEW devHost/devApiBase (follow window.location.hostname)", True),
            ("CONFIG object", "map slug, camera, tile/API/WS URLs — + NEW blockTiles slot; localhost → devHost", True),
            ("BlockTilesConfig / CityConfig interfaces", "NEW z/x/y descriptor type; city payload gains blockTiles", True),
            ("applyCityConfig", "rebinds camera + tile URLs per city — now also CONFIG.blockTiles (devHost-aware)", True),
        ],
        "blocks": [
            "devHost/devApiBase — dev hosts follow the page hostname (phone-on-LAN testable); VITE_API_BASE still wins",
            "CONFIG.graphTilesUrl / blockTilesUrl / apiUrl / wsUrl — localhost → devApiBase/devHost",
            "CONFIG.blockTiles — NEW z/x/y source slot (null → pmtiles:// fallback)",
            "BlockTilesConfig interface — template (?v= mtime_ns), minzoom/maxzoom, coverage bounds",
            "applyCityConfig — honor VITE_API_BASE for tile URLs too; populate CONFIG.blockTiles from the city payload",
        ],
    },
    "client-react/src/context/RouteContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · context/", "the canonical Selection state: waypoints, route resolution, vote casting"),
        "file": ("RouteContext.tsx", "~1700 LOC — RouteProvider: selection reducer, OSRM route fetch, waypoint addresses, cast plumbing"),
        "outline": [
            ("Geometry helpers", "haversine, projectOntoSegment, splitGeometryAtPoint", False),
            ("RouteContextValue interface", "the context surface", False),
            ("RouteProvider — geocodeInto", "waypoint address resolution — now via retrying reverseGeocode", True),
            ("RouteProvider — selection/route/cast logic", "reducer wiring, OSRM calls, castVotes", False),
            ("useRoute", "context hook", False),
        ],
        "blocks": [
            "import { reverseGeocode } from utils/geocode",
            "geocodeInto — raw fetch → reverseGeocode(lat, lng) (3 attempts + backoff; patch only on a real address)",
        ],
    },
    "client-react/src/hooks/useMapClick.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · hooks/", "map-click → start/end/point placement state machine"),
        "file": ("useMapClick.ts", "~145 LOC — handleMapClick: tool arming, bounds check, start/end placement"),
        "outline": [
            ("Option interfaces", "MapClickState, UseMapClickOptions", False),
            ("useMapClick — handleMapClick", "placement logic — + NEW 700ms end-placement cooldown", True),
        ],
        "blocks": [
            "END_PLACEMENT_COOLDOWN_MS (700) + lastEndPlacementRef",
            "handleMapClick — ignore clicks inside the cooldown (double-tap wiped the fresh route and dropped a start pin on the end)",
            "record lastEndPlacementRef after onUpdateEnd",
        ],
    },
    "client-react/src/utils/geocode.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "NEW — shared reverse-geocode helper with retries"),
        "file": ("geocode.ts", "NEW FILE, 37 LOC — reverseGeocode(): 3 attempts + backoff, priority:'high' hint, {ok, address} result"),
        "outline": [
            ("ReverseGeocodeResult", "ok distinguishes server-answered-null from failed-after-retries", True),
            ("reverseGeocode", "retry loop with backoff; high-priority fetch hint", True),
        ],
        "blocks": [
            "Whole file is new — one shared resolver for RouteContext.geocodeInto and GraphLayer.resolveAddress",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WebSocket routes, vote/response caches, graph + OSRM registries"),
        "file": ("app.py", "~2400 LOC — every API/WS route plus the vote caches, saturation valve, and city registries"),
        "outline": [
            ("Setup — logging / Flask / Redis / registries", "app, CORS, sock, redis client, GraphRegistry", True),
            ("Map resolution + passcode gate", "resolve_map, token check + lockout", False),
            ("Vote response cache + saturation valve", "bounded LRU, single-flight, shed valve, metrics", False),
            ("Maps API", "maps_list, _enrich_map (NEW), _map_response, _refresh_map_get, map_get", True),
            ("WS / Routes / Vote APIs", "delta hub, /api/routes, cast_vote, my-votes, route-votes", False),
            ("Geocode + /api/tiles static", "photon search, pmtiles passthrough", False),
            ("z/x/y block tiles", "NEW — /api/tile/<city>/blocks/<z>/<x>/<y>.mvt out of blocks.pmtiles", True),
            ("Previews / graph data APIs / admin", "/previews, graph-topology, graph-votes, admin stats", False),
        ],
        "blocks": [
            "import pmtiles Reader/MmapSource + Compression",
            "_enrich_map() — factored city + searchVoteTypes enrichment; docstring invariant: cache only ENRICHED dicts",
            "_refresh_map_get — SWR background refresh caches _enrich_map(m) (was raw get_map(); served empty city after first TTL)",
            "_blocks_tile_reader() — mmap reader cache keyed on st_mtime_ns (archive re-baked IN PLACE); purges the gzip LRU per bake",
            "blocks_tile() — registry-validated city_id, zoom + x/y range guards (scanners got 500s), strong ETag + immutable",
            "blocks_tile() — 204 cacheable empty tiles (water/edge tiles otherwise 404 per pan, ~5ms unyielding directory parse each)",
            "blocks_tile() — stored-gzip pass-through + hot-tile LRU (≤128KB) blunting cold-nginx-cache Reader.get bursts",
        ],
    },
    "server/cities.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "the per-city registry: bboxes, camera defaults, data dirs, public config"),
        "file": ("cities.py", "~240 LOC — City dataclass + CITIES registry; to_public() feeds the client's CityConfig"),
        "outline": [
            ("_blocks_header_info", "NEW — cached (zoom range, coverage bounds) from the blocks.pmtiles header", True),
            ("_env_osrm_host", "per-city OSRM host override", False),
            ("City — to_public / _block_tiles / _blocks_version", "public payload — now advertises the z/x/y blockTiles descriptor", True),
            ("Registry — CITIES / get_city / all_cities", "the city table", False),
        ],
        "blocks": [
            "import pmtiles Reader/MmapSource; _blocks_header_cache keyed per (path, version)",
            "_blocks_header_info() — ((minzoom, maxzoom), [minLon, minLat, maxLon, maxLat]) from the archive header",
            "to_public() — blockTiles descriptor next to blocksVersion",
            "_block_tiles() — template with ?v=st_mtime_ns (unique per BAKE, not per second), zooms + coverage bounds; None → client falls back to pmtiles://",
        ],
    },
    "server/streetscape_blocks/build_city_blocks.sh": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/streetscape_blocks", "bakes a city's block polygons GeoJSON → blocks.pmtiles"),
        "file": ("build_city_blocks.sh", "~60 LOC — runs the block builder then tippecanoe with the feature-id flags the client needs"),
        "outline": [
            ("Inputs + block build", "city arg, python builder, FINAL_FILE", False),
            ("tippecanoe bake", "now writes to blocks.pmtiles.tmp + atomic mv (readers see whole archives only)", True),
        ],
        "blocks": [
            "tippecanoe -o …tmp + mv -f — the archive is mmap-read IN PLACE by the running server; tippecanoe writes a SQLite journal mid-bake",
        ],
    },
    "nginx.conf": {
        "on": ["nginx"],
        "module": ("nginx · repo root", "the local/docker-compose reverse proxy (3 Flask replicas behind it)"),
        "file": ("nginx.conf", "~85 LOC — gzip, upstream, static + /api/ + /ws locations"),
        "outline": [
            ("events", "worker_connections 1024 → 4096", True),
            ("gzip", "JSON/api compression", False),
            ("upstream flask", "+ keepalive 32, keepalive_timeout 55s (retire before gunicorn's 75s)", True),
            ("proxy_cache_path tiles", "NEW — 512m disk cache for /api/tile/", True),
            ("locations /assets/, /", "immutable assets; SPA shell no-cache", False),
            ("location /api/tile/", "NEW — proxy_cache + 200/204 valid + cache_lock + 300s timeouts + X-Tile-Cache", True),
            ("locations /api/, /health, /ws", "plain proxying", False),
        ],
        "blocks": [
            "worker_connections 4096",
            "upstream keepalive 32 / keepalive_timeout 55s — nginx retires pooled connections BEFORE gunicorn (502 fix)",
            "proxy_cache_path tiles (512m, 7d)",
            "location /api/tile/ — cache 200+204, proxy_cache_lock (stampede collapse), 300s timeouts, X-Tile-Cache debug header",
        ],
    },
    "deploy/nginx-cloudrun.conf": {
        "on": ["nginx"],
        "module": ("Deploy · deploy/", "the single-container Cloud Run nginx (brotli, baked pmtiles, previews fallback)"),
        "file": ("nginx-cloudrun.conf", "~160 LOC — prod nginx: brotli+gzip, upstream, donate redirect, pmtiles alias, previews, /api/"),
        "outline": [
            ("Modules + brotli/gzip", "brotli preferred, gzip fallback (binary topology under the 32MB cap)", False),
            ("upstream flask", "+ keepalive 32, keepalive_timeout 55s vs gunicorn --keep-alive 75", True),
            ("proxy_cache_path tiles", "NEW — /tmp tmpfs cache capped 128m (counts against instance memory)", True),
            ("donate.cityedit.org server", "301 to donorbox", False),
            ("locations /assets/, /, /previews/", "immutable assets, SPA shell, previews + baked fallback", False),
            ("pmtiles alias", "baked archives served by nginx range support", False),
            ("location /api/tile/", "NEW — proxy_cache + 204 valid + cache_lock + 300s timeouts", True),
            ("locations /api/, /health, /ws", "300s timeouts for lazy graph loads; WS passthrough", False),
        ],
        "blocks": [
            "upstream keepalive 32 / keepalive_timeout 55s",
            "proxy_cache_path /tmp/nginx-tiles (tmpfs-capped 128m)",
            "location /api/tile/ — cache 200+204 7d, proxy_cache_lock 300s, 300s read/send timeouts, X-Tile-Cache",
        ],
    },
    "deploy/supervisord.conf": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · deploy/", "process supervisor for the single Cloud Run container (nginx + gunicorn)"),
        "file": ("supervisord.conf", "~40 LOC — supervisord + nginx + gunicorn program blocks"),
        "outline": [
            ("supervisord / nginx programs", "foreground supervisor, nginx", False),
            ("gunicorn program", "+ --worker-connections 2000 --keep-alive 75 (keeps --timeout 600)", True),
        ],
        "blocks": [
            "gunicorn command — --worker-connections 2000 (gevent ceiling) + --keep-alive 75 (outlives nginx's 55s pool retirement)",
        ],
    },
    "Dockerfile.overlay": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · repo root", "the fast overlay image: new code + client over the base image's baked graphs"),
        "file": ("Dockerfile.overlay", "~40 LOC — copy server code, client dist, nginx conf over the base image"),
        "outline": [
            ("Base + server/client copy", "FROM base, COPY server + dist + nginx conf", False),
            ("supervisord copy", "NEW — without it overlay deploys silently keep the base image's gunicorn command line", True),
        ],
        "blocks": [
            "COPY deploy/supervisord.conf — the --worker-connections/--keep-alive knobs actually land on overlay deploys",
        ],
    },
    "perf/measure.mjs": {
        "on": ["React / Leaflet client"],
        "module": ("Perf harness · perf/", "NEW (adapted from perf-interim) — Playwright frontend measurement: load→heatmap + pan/zoom frame stats"),
        "file": ("measure.mjs", "NEW FILE, ~240 LOC — drives Chromium at 2x DPR, injects instrument.js, records frame windows per gesture"),
        "outline": [
            ("Args + viewport/gesture constants", "--map/--label/--reps/--cpu; /m/<slug>?lat&lng&z URL shape", True),
            ("pct / frameStats", "percentile + per-window frame statistics", True),
            ("main", "inject instrument → wait first-heat mark → scripted pans/zooms → results JSON", True),
        ],
        "blocks": [
            "Whole file is new — adaptations: our /m/<slug> URL params, first heat from the explicit client mark (feature-state isn't introspectable), Leaflet camera owner",
        ],
    },
    "perf/instrument.js": {
        "on": ["React / Leaflet client"],
        "module": ("Perf harness · perf/", "NEW (adapted) — page-init instrumentation injected before any app code"),
        "file": ("instrument.js", "NEW FILE, ~55 LOC — records rAF frames, the first-heat mark, and zoom events off window.__lmap"),
        "outline": [
            ("__perf state + mark()", "frames, mapEvents, marks; app-side mark('first-heat-apply') hook", True),
            ("rAF + zoom listeners", "attaches to the Leaflet camera owner (window.__lmap, dev-only), legacy zoomanim-* names", True),
        ],
        "blocks": [
            "Whole file is new — Leaflet (__lmap) not his __mlMap; first heat via explicit mark instead of polling a GL source",
        ],
    },
    "perf/loadtest.mjs": {
        "on": ["Flask API", "Redis"],
        "module": ("Perf harness · perf/", "NEW (adapted) — WS fan-out + cold-visitor API burst load test"),
        "file": ("loadtest.mjs", "NEW FILE, ~170 LOC — 4 phases: connect WS clients, vote fan-out latency, concurrent cold-visitor bursts, sustained ordered votes"),
        "outline": [
            ("Args + tile math", "base/map/mode/city, clients/visitors/votes; z13–15 tiles from a --view center", True),
            ("Phase 1 — WS connect", "N sockets on our /ws", True),
            ("Phase 2 — fan-out latency", "one vote → delta arrival percentiles", True),
            ("Phase 3 — cold-visitor bursts", "sparse graph-votes + z/x/y tiles + binary topology, sockets held open", True),
            ("Phase 4 — sustained votes", "DeltaHub merged {type:'deltas'} frames; rev check relaxed to monotonic (revs legitimately skip)", True),
        ],
        "blocks": [
            "Whole file is new — endpoints, frame format, and rev semantics adapted to our stack",
        ],
    },
    "perf/seed-votes.mjs": {
        "on": ["Flask API"],
        "module": ("Perf harness · perf/", "NEW (adapted) — seeds realistic vote chains for load tests"),
        "file": ("seed-votes.mjs", "NEW FILE, ~140 LOC — decodes GTB1/GTB2 binary topology, walks edge chains, casts each chain as ONE request"),
        "outline": [
            ("Args + topology magics", "api/map/mode, chains/maxVotes; GTB1/GTB2 (street maps no longer serve JSON)", True),
            ("decodeTopologyEnds", "binary header → edge-ends arrays", True),
            ("main", "chain walk + single edge_ids cast per chain (block-scoped clear-then-cast would wipe earlier edges cast one-by-one)", True),
        ],
        "blocks": [
            "Whole file is new — binary topology decode + one-request-per-chain casting are the two stack-specific adaptations",
        ],
    },
    "perf/compare.mjs": {
        "on": [],
        "module": ("Perf harness · perf/", "NEW — before/after table over two measure.mjs result files"),
        "file": ("compare.mjs", "NEW FILE, ~37 LOC — prints deltas for load→heatmap, pan/zoom FPS + p95 frame, jank, blackout"),
        "outline": [
            ("Metric rows + table print", "higher/lower-is-better aware percentage deltas", True),
        ],
        "blocks": ["Whole file is new."],
    },
    "perf/package.json": {
        "on": [],
        "module": ("Perf harness · perf/", "NEW — harness dependencies"),
        "file": ("package.json", "NEW FILE — playwright + ws"),
        "outline": [("dependencies", "playwright ^1.61, ws ^8.21", True)],
        "blocks": ["Whole file is new (perf/package-lock.json committed too, excluded from this report's diff)."],
    },
    "server/.env.example": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/", "documented env template for local dev"),
        "file": (".env.example", "~20 LOC — Redis/OSRM/DB knobs"),
        "outline": [
            ("REDIS_HOST", "default localhost", False),
            ("OSRM_URL", "NEW — http://localhost:5005 (docker-compose.osrmport.yml; AirPlay squats :5000)", True),
            ("DATABASE_URL + legacy OSRM vars", "local postgres; host/port overrides", False),
        ],
        "blocks": [
            "OSRM_URL=http://localhost:5005 — the hybrid loop's merged-OSRM base URL (dockerized Flask overrides via compose)",
        ],
    },
    "Makefile": {
        "on": [],
        "module": ("Dev tooling · repo root", "make targets for the hybrid dev loop, builds, deploys, load tests"),
        "file": ("Makefile", "~190 LOC — dev/deps/graphs/tiles/docker/deploy/tf/loadtest targets"),
        "outline": [
            ("Vars + .PHONY", "registry paths; redis target replaced by deps/deps-down", True),
            ("help", "rewritten around the hybrid loop", True),
            ("deps / deps-down / flask / client", "Docker backing services (redis, postgres, osrm on :5005) + host processes", True),
            ("graphs / tiles", "per-city builds", False),
            ("dev", "tiles → deps → host flask + vite (was: daemonized local redis)", True),
            ("docker / deploy / tf / loadtest / screenshot", "unchanged", False),
            ("clean", "deps-down + kill host flask/vite (was pkill redis + bun)", True),
        ],
        "blocks": [
            "deps: docker compose (+osrmport overlay) up -d redis postgres osrm; deps-down: compose stop",
            "dev: tiles deps → host flask (background) + vite (foreground)",
            "clean: deps-down + pkill python app.py / vite",
        ],
    },
    "README.md": {
        "on": [],
        "module": ("Docs · repo root", "the public-facing repo README"),
        "file": ("README.md", "~180 LOC — quickstart, architecture, config, testing, deploy"),
        "outline": [
            ("Quickstart", "rewritten: one-time setup + the hybrid daily loop (make deps → host flask → host vite)", True),
            ("Architecture", "component diagram + data flow", False),
            ("Configuration / Everything in Docker", "env table; full-Docker alternative clarified", True),
            ("Testing / Deploy to GCP", "unchanged", False),
        ],
        "blocks": [
            "Quickstart — hybrid loop is canonical (Docker deps, host Flask/Vite, OSRM on :5005)",
            "Configuration — OSRM_URL documented; Docker section demoted to 'alternative'",
        ],
    },
    "CLAUDE.md": {
        "on": [],
        "module": ("Docs · repo root", "the project's agent + architecture instructions"),
        "file": ("CLAUDE.md", "~870 LOC — agent instructions, architecture overview, runbooks, style guides"),
        "outline": [
            ("Agent instructions / changelog / dev-preview conventions", "unchanged", False),
            ("Local Development", "rewritten to the hybrid loop (make deps + host Flask/Vite; make dev)", True),
            ("Prod DB / architecture / style guides", "unchanged", False),
        ],
        "blocks": [
            "Local Development — make deps (redis/postgres/osrm in Docker) + host flask + host vite; make dev shortcut",
        ],
    },
    "docs/staging-parity-plan.md": {
        "on": [],
        "module": ("Docs · docs/", "NEW — plan for a prod-parity staging stack"),
        "file": ("staging-parity-plan.md", "NEW FILE, 199 lines — unguessable-URL staging + digest promotion to prod"),
        "outline": [
            ("Goal / current prod topology", "what parity means; what we mirror", True),
            ("Proposed staging topology + the unguessable URL", "separate service, secret hostname", True),
            ("Code + Terraform changes", "small diffs; respects the deploy landmines (no blanket tf apply, PAP)", True),
            ("Data seeding / deploy workflow / costs / rollout / open questions", "the parity payoff", True),
        ],
        "blocks": ["Whole file is new — plan only, no code changes."],
    },
    ".gitignore": {
        "on": [],
        "module": ("Dev tooling · repo root", "repo ignore rules"),
        "file": (".gitignore", "~90 LOC"),
        "outline": [
            ("Server / client artifacts", "env, osm_data, dist…", False),
            ("Perf harness output", "NEW — perf/results/ + perf/shots/ (reference baselines force-added)", True),
            ("Locust output", "loadtest env + runs", False),
        ],
        "blocks": ["Ignore perf/results/ and perf/shots/ (per-run output; loadtest-2026-07-22.json force-added as baseline)."],
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

    missing = [name for name, _ in files if name not in FILE_CONTEXT]
    if missing:
        print("WARNING — no FILE_CONTEXT for:", ", ".join(missing))

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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · commits <code>800803b..30257f7</code> (9 hand-ports + 2 ride-along docs commits)</div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">A borrow pass over Brook's perf-interim PR #7: we hand-ported the chunks that are
  ahead of our stack (early first heat, cacheable z/x/y block tiles behind an nginx tile cache,
  connection-ceiling knobs, a measurement harness, four mobile fixes), skipped the chunks our recent
  work already subsumes, deferred the two that need their own workstreams — and, in the process of
  reviewing and verifying the port, found one pre-existing prod bug and closed three real races.</p>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Click any file to expand. Green is added, red removed.
    (Excludes <code>changelog/</code>, <code>perf/results/</code>, and <code>perf/package-lock.json</code>.)</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes.diff</code> by <code>changelog/build_report_2026_07_23.py</code>.
    Regenerate after further edits with <code>git diff 800803b..HEAD -- ':!changelog' ':!perf/results' ':!perf/package-lock.json'
    &gt; changelog/changes.diff &amp;&amp; python changelog/build_report_2026_07_23.py</code>.
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
