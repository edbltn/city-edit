#!/usr/bin/env python3
"""Changelog report: deep-link centering fix + nyc-intersections prod map + QR retarget.

Run from repo root: python changelog/build_report_deeplink.py
"""
import html
import importlib.util
import os

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("base", os.path.join(HERE, "build_report.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

base.DIFF_PATH = os.path.join(HERE, "changes-deeplink.diff")
base.OUT_PATH = os.path.join(HERE, "2026-07-27-deeplink-centering-nyc-intersections.html")
base.DATE = "2026-07-27"
base.TITLE = "Deep links center on their selection + the nyc-intersections map goes live"

base.SECTIONS = [
    {
        "id": "map",
        "tag": "Production data",
        "title": "1 · nyc-intersections — the poster campaign's landing map",
        "symptom": (
            "The poster QR codes needed a production map dedicated to <em>fixing</em> dangerous "
            "intersections: a curated set of point- and route-based improvement vote types, in a look "
            "that reads as a safety campaign."
        ),
        "cause": [
            "Created via the app's own <code>POST /api/maps</code> (the Propose-a-Map path — no DB "
            "surgery, all cache invalidation for free): slug <code>nyc-intersections</code>, name "
            "“NYC Dangerous Intersections”, NYC streets network, <strong>terracotta</strong> style "
            "(the light basemap with the brick-red heat ramp), symbol <code>safety</code>.",
            "14 custom vote types — 9 point-kind (Fix dangerous intersection, Add pedestrian signal, "
            "Fix signal timing, Daylight this corner, Add curb extension, Add raised crosswalk, "
            "Add pedestrian refuge island, Ban turn on red, Add intersection lighting) and 5 route-kind "
            "(Add protected bike lane, Add traffic calming, Add crosswalk, Widen sidewalk, Lower speed "
            "limit). Labels shared with presets reuse their registry ids/kinds; new labels were stamped "
            "at creation so a later cast can't mis-kind them.",
            "Dry-run: the identical payload was created and exercised on local dev first (picker "
            "filtering, icons, a smoke vote, legend) before the prod POST.",
        ],
        "fixes": [
            "Live at <code>https://cityedit.org/m/nyc-intersections</code> (201 on create; config "
            "round-trip verified: style/subtitle/network/14 vote types).",
            "<strong>generate_posters.py</strong> now QR-links every poster to this map with "
            "<code>vt=Fix+dangerous+intersection</code> preselected — a scan lands with the pin set "
            "and one tap left to cast. All 94 posters + manifest regenerated.",
        ],
        "files": [
            "scripts/generate_posters.py — BASE_URL → nyc-intersections, vt param on QR deep links",
            "data/posters/out/ — 94 posters regenerated (gitignored; manifest.csv has final URLs)",
        ],
    },
    {
        "id": "centering",
        "tag": "Frontend · deployed",
        "title": "2 · Deep links now center the camera on what they select",
        "symptom": (
            "Visiting a shared link like <code>walkways.cityedit.org/?w=40.869434,-73.826116&amp;"
            "vt=Improve+sidewalk</code> restored the selection but opened at the citywide default "
            "view — the pin was set, invisible, five zoom levels away. Every poster QR would have "
            "landed users on a borough-scale map."
        ),
        "cause": [
            "<code>getInitialMapView()</code> only ever read the explicit camera params "
            "(<code>z/lat/lng</code>). Selection restore (<code>?w=</code>) seeds the waypoints "
            "synchronously in RouteContext, but nothing ever moved the camera to them — links shared "
            "from the app happen to carry camera params, so only <em>hand-built</em> deep links "
            "(exactly what the QR generator makes) hit the gap.",
        ],
        "fixes": [
            "When no explicit camera params are present, the initial view now derives from the URL "
            "selection (<code>w=</code> or legacy <code>slat/slng</code>): a single waypoint centers "
            "at street zoom 17; a multi-waypoint route fits its bounding box (cos-corrected span → "
            "zoom 11–16). Explicit camera params still always win; <code>vt</code>-only links keep "
            "the default view. Pure core extracted as <code>computeInitialMapView()</code> with 8 unit "
            "tests (305 client tests green).",
            "<strong>Deployed staging-first per runbook</strong>: prod DB snapshot "
            "(<code>~/city-edit-prod-backups/20260727-204128/</code>, full dump + CSVs + checksums) → "
            "overlay image on the serving digest base (no graph rebake, no resnap) → staging service "
            "verified live (camera centered on Co-op City Blvd &amp; Bartow Ave at street zoom) → same "
            "digest <code>d443e966…</code> promoted to prod (revision 00110, 100% traffic, asset hash "
            "matches staging). Prod verified on the original repro URL and on a poster QR link.",
            "Build hygiene: repo-root <code>/data/</code> (114 MB of analysis + posters) excluded from "
            "Cloud Build uploads — anchored so <code>server/data/</code> (baked into images) keeps "
            "uploading.",
        ],
        "files": [
            "client-react/src/utils/mapViewState.ts — viewFromSelectionParams + computeInitialMapView",
            "client-react/src/utils/mapViewState.test.ts — NEW: 8 tests on the pure core",
            ".gcloudignore — /data/ exclusion (root-anchored)",
        ],
    },
]

base.VERIFY = [
    "Client: <code>vitest run</code> — 305 passed (8 new); <code>tsc -b</code> clean.",
    "Local dev: map created with the prod payload; picker shows the 9 point types on a point "
    "selection; smoke vote landed (legend + nonzero node/edge votes in /api/graph-votes).",
    "Staging: served the built digest, repro URL centered at street zoom on the named intersection, "
    "STAGING ribbon present.",
    "Prod: asset hash identical to staging post-promotion; repro URL and a poster QR URL both center "
    "correctly; new map serves with terracotta style and vt preselect.",
]

base.CHECKLIST = [
    "Scan any poster QR from <code>data/posters/out/</code> — phone should open nyc-intersections "
    "centered on the intersection with “Fix dangerous intersection” selected.",
    "Open <code>https://walkways.cityedit.org/?w=40.869434,-73.826116&amp;vt=Improve+sidewalk</code> "
    "— should land at street zoom on Co-op City Blvd &amp; Bartow Ave.",
    "Open a link WITH camera params (share a view from the app) — explicit z/lat/lng must still win.",
    "Cast a real vote on <code>cityedit.org/m/nyc-intersections</code> and confirm the terracotta "
    "heat appears on the block.",
    "If the vote-type set needs tweaks, edit the map's custom_vote_types in the DB and rebuild Redis "
    "for the map (DB-only edits are invisible until then).",
]

base.FILE_CONTEXT = {
    "client-react/src/utils/mapViewState.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "camera state singleton: initial view from URL, pan helpers, current-view mirror"),
        "file": ("mapViewState.ts", "~140 LOC — initial map view resolution + map-instance pan/state helpers"),
        "outline": [
            ("setMapInstance / panTo", "imperative pan access to the Leaflet map", False),
            ("viewFromSelectionParams", "NEW — derive center+zoom from ?w= / legacy slat-slng waypoints", True),
            ("computeInitialMapView", "NEW pure core — explicit camera params win, else selection view, else default", True),
            ("getInitialMapView", "thin window.location wrapper over the pure core", True),
            ("getInitialPoints / cleanNavParams", "legacy point params + URL cleanup", False),
        ],
        "blocks": [
            "POINT_DEEP_LINK_ZOOM=17 + zoomForSpan() span→zoom table",
            "viewFromSelectionParams() — single waypoint → street zoom; multi → cos-corrected bbox fit",
            "computeInitialMapView() — extracted pure core (tested)",
        ],
    },
    "client-react/src/utils/mapViewState.test.ts": {
        "on": ["React / Leaflet client"],
        "module": ("React client · utils/", "unit tests"),
        "file": ("mapViewState.test.ts", "NEW — 8 cases on computeInitialMapView"),
        "outline": [("cases", "default / explicit params / single w / multi w bbox / legacy / precedence / vt-only / forced-corridor token", True)],
        "blocks": ["Whole file is new."],
    },
    "scripts/generate_posters.py": {
        "on": ["React / Leaflet client"],
        "module": ("Poster tooling · scripts/", "campaign generator that turns rankings into printable QR posters"),
        "file": ("generate_posters.py", "~240 LOC — 7 campaigns × targets → filled templates → PNGs"),
        "outline": [
            ("BASE_URL / QR_VOTE_TYPE", "now nyc-intersections + vt preselect", True),
            ("slot fill / campaigns", "unchanged", False),
            ("main — QR generation", "URL now carries vt=Fix+dangerous+intersection", True),
        ],
        "blocks": [
            "BASE_URL → https://cityedit.org/m/nyc-intersections",
            "QR URL gains &vt=<quoted QR_VOTE_TYPE>",
        ],
    },
    ".gcloudignore": {
        "on": [],
        "module": ("Build config · repo root", "what uploads to Cloud Build"),
        "file": (".gcloudignore", "upload exclusions"),
        "outline": [("exclusions", "env/node_modules/osm_data/… + NEW root-anchored /data/", True)],
        "blocks": ["/data/ excluded (root-anchored so server/data/ still uploads)"],
    },
}


def patched_section_html(s):
    return f"""
    <section class="card" id="{s['id']}">
      <div class="tag">{s['tag']}</div>
      <h2>{s['title']}</h2>
      <h3>Context</h3>
      <p>{s['symptom']}</p>
      <h3>Root cause / how it was done</h3>
      <ul>{base.li(s['cause'])}</ul>
      <h3>What changed</h3>
      <ul>{base.li(s['fixes'])}</ul>
      <h3>Files touched</h3>
      <ul class="files">{base.li(html.escape(f) for f in s['files'])}</ul>
    </section>
    """


base.section_html = patched_section_html

if __name__ == "__main__":
    base.main()
    with open(base.OUT_PATH) as f:
        doc = f.read()
    doc = doc.replace(
        "branch <code>fix/unify-voting</code>", "branch <code>main</code>"
    ).replace(
        "Three fixes in one pass: the stale-cache crash that loops mobile Safari on heatmap load,\n"
        "  the concurrency limits that broke the app under multiple tenants, and the heatmap that vanished mid-zoom.",
        "The poster campaign gets its landing map and its links start working: nyc-intersections is live in "
        "prod (terracotta theme, 14 improvement vote types), all 94 poster QRs point at it, and deep links "
        "carrying only a selection now open centered on that intersection — deployed staging-first as digest "
        "d443e966, with a prod DB snapshot taken beforehand.",
    )
    with open(base.OUT_PATH, "w") as f:
        f.write(doc)
    print("patched lede + branch")
