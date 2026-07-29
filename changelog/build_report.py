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
OUT_PATH = os.path.join(HERE, "2026-07-29-slug-redirects-src-tracking.html")

DATE = "2026-07-29"
TITLE = "Slug redirects + ?src= visit tracking — and the nyc-crossings rename"


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
        "id": "src",
        "tag": "Frontend + Backend + Monitoring",
        "title": "1 · ?src= — campaign visit-source tracking",
        "symptom": (
            "There was no way to answer <em>“how many people came from the QR posters?”</em> "
            "(or any future campaign). The map-load beacon already flowed client → "
            "<code>/api/client-timing</code> → the <code>[MAPLOAD]</code> log line → the "
            "<code>cityedit_map_load_ms</code> log-based metric — but it carried no attribution, "
            "so a poster scan and a bookmarked visit were indistinguishable."
        ),
        "cause": [
            "Nothing in the URL scheme reserved a campaign tag, and the client rewrites the URL "
            "aggressively (RouteContext’s selection sync drops unknown params; the "
            "canonical-subdomain redirect is a full page load on another host) — a naïve "
            "<code>?src=</code> param would be <strong>dropped before anything could read it</strong>, "
            "or worse, <strong>survive into re-shared links</strong> and poison the attribution.",
        ],
        "fixes": [
            "<strong>New <code>utils/sourceTag.ts</code></strong> captures <code>?src=&lt;tag&gt;</code> "
            "<em>once</em> at map boot (first call wins — StrictMode-safe), validates it "
            "(<code>[a-zA-Z0-9_-]</code>, max 32), then <strong>strips it from the address bar</strong> "
            "via <code>history.replaceState</code> so a re-shared link doesn’t inherit the campaign. "
            "<code>App.tsx</code> calls <code>captureSourceTag()</code> at the top of the map-resolution "
            "effect, before any redirect or URL rewrite can eat it.",
            "<strong>The beacon reports it.</strong> <code>loadTelemetry.ts</code> adds "
            "<code>src</code> to the existing <code>POST /api/client-timing</code> payload; absent for "
            "direct visits.",
            "<strong>The server sanitizes + logs it.</strong> <code>client_timing()</code> "
            "(<code>server/app.py</code>) whitelists the tag and appends "
            "<code>src=&lt;tag|direct&gt;</code> to the <code>[MAPLOAD]</code> line.",
            "<strong>The metric grows a <code>src</code> label</strong> "
            "(<code>terraform/monitoring.tf</code>, <code>REGEXP_EXTRACT</code> like the existing "
            "map/cached/nav labels) — “visits by source” is now a Metrics Explorer group-by on "
            "<code>cityedit_map_load_ms</code>’s count. Starting a new campaign = minting a new tag in "
            "the printed URL; zero code changes.",
            "<strong>The tag survives the canonical-subdomain hop.</strong> "
            "<code>withSourceTag()</code> re-attaches the captured (already-stripped) tag to the "
            "<code>subdomainRedirectUrl</code> target in <code>App.tsx</code>, so e.g. "
            "<code>cityedit.org/m/nyc-bikes?src=…</code> → <code>bikepaths.cityedit.org</code> still "
            "attributes.",
        ],
        "files": [
            "client-react/src/utils/sourceTag.ts — NEW: captureSourceTag / getSourceTag / withSourceTag",
            "client-react/src/App.tsx — capture at boot; withSourceTag across the subdomain redirect",
            "client-react/src/utils/loadTelemetry.ts — src in the map-load beacon",
            "server/app.py — client_timing sanitizes + logs src=<tag|direct>",
            "terraform/monitoring.tf — src label on cityedit_map_load_ms",
        ],
    },
    {
        "id": "redirects",
        "tag": "Backend + Frontend",
        "title": "2 · Slug redirects — renamed maps keep their old links",
        "symptom": (
            "Renaming a map’s slug would kill every printed/shared link to the old name "
            "(<code>GET /api/maps/&lt;old&gt;</code> → 404 → the client falls back to the default map), "
            "and — worse — Propose-a-Map could <strong>re-issue the retired slug</strong> to a "
            "stranger’s map, silently rerouting printed QR codes to someone else’s content."
        ),
        "cause": [
            "The slug was the map’s <em>only</em> address: a miss on the <code>maps</code> table was "
            "terminal, and <code>slug_available()</code> only consulted <code>maps</code> — the moment "
            "a slug was freed by a rename it was up for grabs.",
            "A rename also isn’t one UPDATE: <code>edge_votes.map_slug</code> is a denormalized TEXT "
            "copy (nothing cascades), and the heatmap serves <strong>only from Redis</strong>, keyed by "
            "slug — a half-applied rename strands votes under a dead slug.",
        ],
        "fixes": [
            "<strong>New <code>map_redirects</code> table</strong> (<code>from_slug</code> PK, "
            "<code>to_slug</code>, <code>append_query</code>, <code>note</code>) created in "
            "<code>database.init_db()</code>. <code>append_query</code> is the retro-tagging hook: a "
            "query string merged into the target URL by the client.",
            "<strong><code>map_get</code> serves a redirect stub instead of 404.</strong> On a maps-table "
            "miss it consults <code>get_map_redirect(slug)</code> and returns "
            "<code>{slug, redirect: {toSlug, appendQuery}}</code> — a <strong>200 with "
            "<code>Cache-Control: max-age=300</code></strong>, not a 30x, because nginx serves the SPA "
            "shell before any slug lookup, so only the client can act on it (exactly like the "
            "canonical-subdomain redirect). Not stored in <code>_map_get_cache</code> (enriched map "
            "dicts only); the lookup is a single PK read.",
            "<strong>The client follows it.</strong> <code>App.tsx</code> checks "
            "<code>resolved?.redirect?.toSlug</code> first and <code>location.replace</code>s to "
            "<code>slugRedirectUrl()</code> (<code>map/runtime.ts</code>): same-origin "
            "<code>/m/&lt;toSlug&gt;</code>, <strong>keeping every current query param</strong> (QR deep "
            "links carry <code>?w=</code>/<code>?vt=</code>), dropping <code>?map=</code>, and merging "
            "<code>append_query</code> <em>without overriding</em> params already present.",
            "<strong>Retired slugs are reserved forever.</strong> <code>slug_available()</code> now "
            "checks <code>maps</code> UNION <code>map_redirects</code>, so Propose-a-Map (and "
            "<code>rename_map_slug</code> itself) can never re-issue one.",
            "<strong>Atomic rename.</strong> <code>rename_map_slug()</code> does the whole move in one "
            "explicit BEGIN/COMMIT (get_cursor is autocommit): <code>maps.slug</code>, the denormalized "
            "<code>edge_votes.map_slug</code> rows, the redirect row, and <strong>chain flattening</strong> "
            "(existing redirects targeting the old slug are re-pointed, so a→b then b→c becomes a→c — "
            "clients never hop twice).",
            "<strong>New CLI <code>server/rename_map.py</code></strong> wraps the transaction, then "
            "rebuilds Redis under the new slug (<code>vote_migration.rebuild_redis_for_map</code> — "
            "ev: replay from Postgres) and purges the old slug’s keys (<code>ev:</code>, "
            "<code>vote_rev:</code>, <code>bd:</code>/<code>bagg:</code>). Fails soft if Redis is down "
            "(DB renamed; rerun to serve votes).",
        ],
        "files": [
            "server/database.py — map_redirects table, get_map_redirect, list_map_redirects, rename_map_slug, slug_available checks both tables",
            "server/app.py — map_get redirect stub (200 + max-age=300)",
            "server/rename_map.py — NEW CLI: rename + Redis rebuild + old-key purge",
            "client-react/src/map/runtime.ts — MapConfig.redirect + slugRedirectUrl()",
            "client-react/src/App.tsx — follow the redirect before the subdomain check",
        ],
    },
    {
        "id": "rename",
        "tag": "Ops + Scripts + Docs",
        "title": "3 · nyc-intersections → nyc-crossings — retro-tagging the printed posters",
        "symptom": (
            "The July QR poster campaign went to print linking "
            "<code>cityedit.org/m/nyc-intersections</code> with <strong>no analytics tag</strong> — "
            "the posters are already hanging, so the URL on them can’t change, and their scans were "
            "about to be indistinguishable from organic traffic."
        ),
        "cause": [
            "The two features above exist precisely to fix this: a redirect row whose "
            "<code>append_query</code> stamps <code>src=qr-poster</code> onto every visit through the "
            "old printed URL turns the rename itself into the retro-tagging mechanism.",
        ],
        "fixes": [
            "<strong>Ran the rename</strong>: <code>python rename_map.py nyc-intersections "
            "nyc-crossings --append-query \"src=qr-poster\"</code> — one transaction moved the map + its "
            "votes, left the redirect row, rebuilt Redis under <code>nyc-crossings</code>, purged the "
            "old keys. Every scan of an already-printed poster now lands on "
            "<code>/m/nyc-crossings</code> with pin + vote type intact <em>and</em> reports "
            "<code>src=qr-poster</code>.",
            "<strong>Future prints are tagged at the source.</strong> "
            "<code>scripts/generate_posters.py</code>: <code>BASE_URL</code> now points at "
            "<code>nyc-crossings</code>, and a new <code>QR_SRC</code>/<code>--src</code> flag bakes an "
            "explicit <code>&amp;src=qr-poster</code> into every QR URL (vary per campaign/print run); "
            "the poster-book checklist copy updated. <code>scripts/seed_poster_votes.py</code> default "
            "map updated.",
            "<strong>Docs.</strong> <code>docs/url-routing.md</code> gains “Visit-source tracking "
            "(?src=)”, “Slug redirects (renamed maps)”, an “Admin runbook — rename a map slug”, and a "
            "<strong>Redirect inventory</strong> table that is now the source of truth for EVERY "
            "redirect/rewrite in the system (donate. nginx 301, feedback. rewrite, canonical-subdomain "
            "client redirect, retired-slug redirects, the staging opt-out) plus the live "
            "<code>map_redirects</code> rows. <code>README.md</code> and <code>docs/README.md</code> "
            "link to it.",
        ],
        "files": [
            "scripts/generate_posters.py — BASE_URL → nyc-crossings; QR_SRC + --src baked into QR URLs",
            "scripts/seed_poster_votes.py — default --map → nyc-crossings",
            "docs/url-routing.md — ?src= section, slug-redirects section, rename runbook, redirect inventory",
            "docs/README.md + README.md — pointers to the redirect inventory",
        ],
    },
]

VERIFY = [
    "Rename CLI (local): <code>python rename_map.py nyc-intersections nyc-crossings "
    "--append-query \"src=qr-poster\"</code> — one transaction, <strong>2 votes moved</strong>, Redis "
    "rebuilt under <code>nyc-crossings</code>, old <code>ev:</code>/<code>vote_rev:</code>/"
    "<code>bd:</code>/<code>bagg:</code> keys purged.",
    "<code>GET /api/maps/nyc-intersections</code> → <code>{\"slug\": \"nyc-intersections\", "
    "\"redirect\": {\"toSlug\": \"nyc-crossings\", \"appendQuery\": \"src=qr-poster\"}}</code> "
    "(200, <code>Cache-Control: public, max-age=300</code>).",
    "<code>GET /api/maps/check-slug</code> — <strong>both</strong> <code>nyc-intersections</code> "
    "(reserved by the redirect) and <code>nyc-crossings</code> (taken by the map) report unavailable.",
    "Browser, the printed QR URL shape: "
    "<code>http://localhost:3000/m/nyc-intersections?z=17&amp;lat=…&amp;slat=…</code> landed on "
    "<code>/m/nyc-crossings</code> with the selection intact, and the Flask log shows "
    "<code>[MAPLOAD] map=nyc-crossings ms=30105 cached_topo=0 nav=navigate src=qr-poster</code> — "
    "the redirect’s <code>append_query</code> → client capture → beacon → log-label pipeline, "
    "end to end.",
]

CHECKLIST = [
    "Visit <code>http://localhost:3000/m/nyc-crossings?src=test-tag</code> — the map loads, "
    "<code>?src=</code> vanishes from the address bar, and once the loader dismisses the Flask log "
    "shows <code>[MAPLOAD] … src=test-tag</code>. A plain visit logs <code>src=direct</code>.",
    "Open a poster deep link through the OLD slug — "
    "<code>http://localhost:3000/m/nyc-intersections?w=&lt;lat&gt;,&lt;lon&gt;&amp;vt=Fix+dangerous+intersection</code> "
    "— you should land on <code>/m/nyc-crossings</code> with the pin set and the vote type "
    "preselected, and the log line should carry <code>src=qr-poster</code> (merged by the redirect).",
    "In Propose-a-Map, try to claim the slug <code>nyc-intersections</code> — it must report "
    "unavailable (reserved by the redirect row).",
    "Regenerate one poster (<code>python scripts/generate_posters.py --limit 1</code> or similar) and "
    "scan its QR: the URL should read <code>…/m/nyc-crossings?w=…&amp;vt=…&amp;src=qr-poster</code>.",
    "After the next prod deploy + <code>terraform apply</code> of the monitoring change: in Metrics "
    "Explorer, group <code>logging/user/cityedit_map_load_ms</code>’s count by <code>src</code> and "
    "confirm <code>qr-poster</code> vs <code>direct</code> series appear.",
    "Skim <code>docs/url-routing.md</code>’s Redirect inventory — every redirect you know about "
    "should have a row (donate., feedback., canonical subdomain, retired slugs, staging opt-out).",
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
    "README.md": {
        "on": [],
        "module": ("Docs · repo root", "the front-door README: quickstart, architecture, deploy pointers"),
        "file": ("README.md", "~182 LOC — quickstart, architecture overview, config, testing, GCP deploy"),
        "outline": [
            ("Quickstart", "hybrid dev loop: deps in Docker, Flask + Vite on the host", False),
            ("Architecture", "components + doc pointers — the url-routing pointer now sells the redirect inventory & ?src=", True),
            ("Configuration", "server/.env variables", False),
            ("Everything in Docker (alternative)", "prod-shaped + dev-mode compose", False),
            ("Testing", "test taxonomy pointer", False),
            ("Deploy to GCP", "cloudbuild pointer", False),
        ],
        "blocks": [
            "Architecture § — url-routing.md pointer expanded: redirect inventory + ?src= campaign visit tracking",
        ],
    },
    "client-react/src/App.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("React client · src/", "app root: providers, map bootstrap (resolve slug → config → redirects), the #app shell"),
        "file": ("App.tsx", "~180 LOC — resolves the map, follows redirects, then mounts the map subtree"),
        "outline": [
            ("FullScreenLoader", "the ASCII-spinner splash", False),
            ("AppContent", "shell: TopBar, <main> map in ErrorBoundary, toasts", False),
            ("MapApp — map-resolution effect", "captureSourceTag → resolveMapConfig → slug redirect → subdomain redirect → applyMap", True),
            ("App (providers)", "provider tree", False),
        ],
        "blocks": [
            "imports — slugRedirectUrl (map/runtime), captureSourceTag/withSourceTag (utils/sourceTag)",
            "captureSourceTag() at the top of the map-resolution effect — before any redirect/rewrite drops ?src",
            "follow resolved.redirect.toSlug via location.replace(slugRedirectUrl(…)) — before the subdomain check",
            "subdomain redirect target wrapped in withSourceTag(…) — the tag survives the host hop",
        ],
    },
    "client-react/src/map/runtime.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · map/", "map-config runtime: resolve slug/subdomain → MapConfig, apply it, passcode auth"),
        "file": ("runtime.ts", "~274 LOC — MapConfig types, slug detection, config fetch/resolve/apply, passcode helpers"),
        "outline": [
            ("MapVoteType / MapConfig interfaces", "the config shape — now with the redirect?: {toSlug, appendQuery} field", True),
            ("getCurrentMap / getMapSlug / detectMapSlugFromUrl", "current-map accessors + /m/<slug> · ?map= parsing", False),
            ("slugRedirectUrl", "NEW — retired-slug target URL: keep query, drop ?map, merge appendQuery (no overrides)", True),
            ("applyStyleOverride / fetchMapConfig / fetchMapConfigBySubdomain / resolveMapConfig", "?style preview + the config fetch/resolution chain", False),
            ("applyMap / withMap", "install the config; slug-qualify links", False),
            ("Passcode helpers", "tokenKey…authWithPasscode — private-map auth", False),
        ],
        "blocks": [
            "MapConfig.redirect?: { toSlug; appendQuery? } — the retired-slug stub shape",
            "slugRedirectUrl() — /m/<toSlug> + current query (minus ?map) + appendQuery merged without overriding",
        ],
    },
    "client-react/src/utils/loadTelemetry.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "the one-per-pageload map-load beacon (navigation start → loader dismissed)"),
        "file": ("loadTelemetry.ts", "~55 LOC — sendBeacon of {map, ms, cachedTopo, nav} to /api/client-timing"),
        "outline": [
            ("Module state", "topoSource / sent — one beacon per page load", False),
            ("reportTopologySource", "cache-vs-network flag from GraphLayer", False),
            ("reportMapLoaded", "build + sendBeacon the payload — now carries src (campaign tag)", True),
        ],
        "blocks": [
            "import getSourceTag; payload gains src: getSourceTag() ?? undefined (absent → server labels \"direct\")",
        ],
    },
    "client-react/src/utils/sourceTag.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "NEW — ?src= campaign attribution: capture once, strip, report, re-attach on redirects"),
        "file": ("sourceTag.ts", "NEW FILE, 49 LOC — capture/strip the tag at boot; expose it to the beacon and redirects"),
        "outline": [
            ("SRC_PATTERN / captured", "[a-zA-Z0-9_-]{1,32} whitelist; module-level once-only state", True),
            ("captureSourceTag", "read + validate ?src, strip via history.replaceState — idempotent (StrictMode-safe)", True),
            ("getSourceTag", "the captured tag, or null for a direct visit", True),
            ("withSourceTag", "re-attach the (already-stripped) tag to a redirect target URL", True),
        ],
        "blocks": [
            "Whole file is new — capture-once + strip keeps re-shared links from inheriting the campaign's attribution.",
        ],
    },
    "docs/README.md": {
        "on": [],
        "module": ("Docs · docs/", "the docs index: per-doc one-liners, naming canon, style conventions"),
        "file": ("README.md", "~46 LOC — documents table + conventions"),
        "outline": [
            ("Documents table", "one row per doc — url-routing.md row now claims source-of-truth for redirects + ?src=", True),
            ("Naming canon", "slug / map / city / mode vocabulary", False),
            ("Style conventions", "how docs are written", False),
        ],
        "blocks": [
            "url-routing.md row — adds slug renames, the redirect inventory, and ?src= visit-source tracking",
        ],
    },
    "docs/url-routing.md": {
        "on": [],
        "module": ("Docs · docs/", "SOURCE OF TRUTH for map addressing: slug/subdomain/apex resolution, link building, redirects"),
        "file": ("url-routing.md", "~216 LOC — address space, client/server resolution, prod hosts, admin runbooks, redirect inventory"),
        "outline": [
            ("Intro", "no router library; every redirect must be in the inventory table", True),
            ("The address space", "slug forms + NEW “Visit-source tracking (?src=)” subsection", True),
            ("Client resolution (the load path)", "resolveMapConfig chain + NEW “Slug redirects (renamed maps)” subsection", True),
            ("Link building (sharing)", "withMap / shareLink", False),
            ("Server resolution", "/api/maps/<slug> + by-subdomain", False),
            ("Production (cityedit.org)", "hosts, nginx, Cloud Run", False),
            ("Admin runbook — add a vanity subdomain", "set_map_subdomain flow", False),
            ("Admin runbook — rename a map slug", "NEW — rename_map.py usage, prod-tunnel notes, cache caveats", True),
            ("Redirect inventory", "NEW — source-of-truth table of EVERY redirect/rewrite + live map_redirects rows", True),
        ],
        "blocks": [
            "Intro — “renames a slug”; pointer: every redirect goes in the inventory",
            "Address space — retired slugs still resolve; NEW ?src= section (capture→strip→beacon→[MAPLOAD]→src label; tag rules)",
            "NEW “Slug redirects (renamed maps)” — 200-stub rationale, param preservation, reserved-forever, append_query retro-tagging",
            "NEW “Admin runbook — rename a map slug” — the one-command flow + prod tunnels + TTL/preview caveats",
            "NEW “Redirect inventory” — donate. 301, feedback. rewrite, canonical-subdomain, retired slugs, staging opt-out; nyc-intersections→nyc-crossings row",
        ],
    },
    "scripts/generate_posters.py": {
        "on": [],
        "module": ("Scripts · scripts/", "the QR poster generator: intersections CSV → filled HTML templates → rendered PNGs + poster book"),
        "file": ("generate_posters.py", "~612 LOC — row loading, template fill/style helpers, QR minting, poster book + map page"),
        "outline": [
            ("Constants", "MASTER/TEMPLATES/OUT paths, BASE_URL → nyc-crossings, QR_VOTE_TYPE, NEW QR_SRC", True),
            ("Template helpers", "load_rows … set_qr — slot fill, styling, footer stamping, QR embedding", False),
            ("Per-template fills/selectors", "_nabe_fill / _after_dark_fill / _dark_select / nabe_targets", False),
            ("main()", "argparse + render loop — NEW --src flag; QR URL now carries &src=", True),
            ("build_map_page", "the overview map HTML", False),
            ("build_poster_book", "checklist + PDF assembly — checklist copy now says nyc-crossings", True),
        ],
        "blocks": [
            "BASE_URL → https://cityedit.org/m/nyc-crossings; QR_SRC = \"qr-poster\" (first print run retro-tagged by the redirect instead)",
            "--src flag (default QR_SRC) — vary per campaign/print run",
            "QR URL — ?w=<lat>,<lon>&vt=…&src=<args.src>",
            "poster-book checklist copy — cityedit.org/m/nyc-crossings",
        ],
    },
    "scripts/seed_poster_votes.py": {
        "on": [],
        "module": ("Scripts · scripts/", "seeds the poster campaign's starter votes against the live API (dry-run by default)"),
        "file": ("seed_poster_votes.py", "~92 LOC — campaign vote-type table + a cast loop over the poster intersections"),
        "outline": [
            ("CAMPAIGN_VOTE_TYPE", "per-campaign vote-type labels", False),
            ("main()", "argparse (--base-url / --map / --cast) + seed loop — default map → nyc-crossings", True),
        ],
        "blocks": [
            "--map default nyc-intersections → nyc-crossings",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WebSocket routes, the per-map vote cache, graph + OSRM registries, startup warmup"),
        "file": ("app.py", "~2430 LOC — every API/WS route plus the vote-response cache and city registries"),
        "outline": [
            ("Flask / Redis / registries setup", "app, CORS, sock, redis client, GraphRegistry, DB imports", True),
            ("Map resolution + passcode gate", "resolve_map, _map_get_cache, private-map tokens", False),
            ("Per-map vote response cache + saturation valve", "bounded LRU · single-flight · shed valve", False),
            ("Maps API", "/api/maps CRUD — map_get now serves the retired-slug redirect stub", True),
            ("WebSocket + Routes API", "/ws delta hub, /api/routes via OSRM", False),
            ("Vote API", "/api/vote — block-scoped clear-then-cast", False),
            ("Graph data APIs", "/api/graph-topology, /api/graph-votes, /api/graph-version", False),
            ("Telemetry + admin APIs", "client_timing ([MAPLOAD] — now with src=), subdomain, refresh-osm, stats", True),
        ],
        "blocks": [
            "import get_map_redirect from database",
            "map_get — on maps-table miss, serve {slug, redirect:{toSlug, appendQuery}} as 200 + max-age=300 (not cached in _map_get_cache)",
            "client_timing — sanitize payload src ([a-zA-Z0-9_-], ≤32) and append src=<tag|direct> to the [MAPLOAD] line",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "the Postgres layer: schema init, edge-vote storage, vote types, maps registry"),
        "file": ("database.py", "~1290 LOC — connection pool, schema, edge_votes read/write, migration helpers, maps & lists"),
        "outline": [
            ("Pool + get_cursor", "ThreadedConnectionPool, autocommit cursor contextmanager", False),
            ("Schema — init_db", "edge_votes / vote_types / maps … + NEW map_redirects table", True),
            ("Edge-vote write/read path", "record/delete/anchors/voter-directions/eviction", False),
            ("Vote types (label ↔ id)", "fetch/normalize/get_or_create", False),
            ("Aggregates / vote migration / admin", "replay, resnap, orphan repair, counts", False),
            ("Maps & vote-type lists", "seed_presets, list/get/create map, set_map_subdomain, slug_available (now checks both tables)", True),
            ("Slug redirects (renamed maps)", "NEW — get_map_redirect / list_map_redirects / rename_map_slug (atomic)", True),
            ("Passcode", "get_map_passcode_hash", False),
        ],
        "blocks": [
            "init_db — CREATE TABLE map_redirects (from_slug PK, to_slug, append_query, note, created_at)",
            "slug_available — maps UNION ALL map_redirects: a retired slug is reserved forever",
            "get_map_redirect — PK read → {toSlug, appendQuery} (mirrors MapConfig.redirect)",
            "list_map_redirects — admin/docs transparency listing",
            "rename_map_slug — explicit BEGIN/COMMIT: maps.slug + edge_votes.map_slug + redirect row + chain flattening (a→b, b→c ⇒ a→c); rolls back whole on any failure",
        ],
    },
    "server/rename_map.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "NEW — admin CLI: rename a map slug, leave a redirect, rebuild Redis"),
        "file": ("rename_map.py", "NEW FILE, 104 LOC — argparse wrapper around database.rename_map_slug + the Redis rebuild"),
        "outline": [
            ("Module docstring", "usage, prod-tunnel notes, the 30–60s map-cache caveat", True),
            ("_redis_client", "connect or warn-and-continue (DB renamed; rerun for Redis)", True),
            ("main", "rename txn → rebuild_redis_for_map(new) → purge old ev:/vote_rev:/bd:/bagg: keys → JSON summary", True),
        ],
        "blocks": [
            "Whole file is new — the only sanctioned way to create a map_redirects row (a bare DB edit would skip edge_votes + Redis).",
        ],
    },
    "terraform/monitoring.tf": {
        "on": [],
        "module": ("Infra · terraform/", "GCP monitoring: uptime checks, log-based metrics, alert policies, the system dashboard"),
        "file": ("monitoring.tf", "~1220 LOC — notification channel, uptime check, api_latency + map_load_ms metrics, 8 alerts, dashboard"),
        "outline": [
            ("Notification channel + uptime check", "email_eric, app_health probe", False),
            ("api_latency log metric", "[API] request-latency distribution", False),
            ("map_load_ms log metric", "[MAPLOAD] client-perceived first-load — now labeled by src", True),
            ("Alert policies", "uptime, 5xx, p99, app/OSRM/Redis memory, SQL disk", False),
            ("system_health dashboard", "the wall of charts", False),
        ],
        "blocks": [
            "map_load_ms header comment — the [MAPLOAD] line format now ends src=<tag|direct>; visits-by-source = count grouped by src",
            "labels — new src STRING label",
            "label_extractors — src = REGEXP_EXTRACT(textPayload, \"src=([a-zA-Z0-9_-]+)\")",
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

  <p class="lede">Three intertwined changes: any URL can now carry <code>?src=&lt;tag&gt;</code> and show up
  as a visits-by-source group-by in Metrics Explorer; renamed maps leave a data-driven redirect behind
  (old slug reserved forever, deep links preserved); and <code>nyc-intersections</code> became
  <code>nyc-crossings</code> with a redirect that retro-tags every scan of the already-printed July QR
  posters as <code>src=qr-poster</code>.</p>

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
