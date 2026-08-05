#!/usr/bin/env python3
"""Generate the HTML changelog report from the captured unified diff.

Run from repo root: python changelog/build_report.py
Reads changelog/changes-place-labels.diff, writes changelog/2026-08-03-place-labels.html
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-place-labels.diff")
OUT_PATH = os.path.join(HERE, "2026-08-03-place-labels.html")

DATE = "2026-08-03 → 08-05"
TITLE = "Putting names back on the map: streets, places and businesses"


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
        "id": "streets",
        "tag": "BASEMAP",
        "title": "The map knew where you were, but wouldn't say",
        "symptom": "At neighborhood zoom the map was anonymous grey blocks. A proposal card could read &quot;Rockaway Parkway &amp; Linden Boulevard&quot; while the map underneath named neither street — you could not tell Canarsie from Corona.",
        "cause": [
            "The basemap has always been CARTO's <code>dark_nolabels</code> / <code>light_nolabels</code> tiles. That was a deliberate choice — the vote heat is the product and unlabelled tiles never compete with it — but it costs every scrap of orientation",
            "Simply switching to a labelled basemap would not work: the block heat is drawn ON TOP of the base raster, so every street name would end up buried under a translucent hot block",
        ],
        "fixes": [
            "CARTO ships the same cartography split in two, so the text half is re-added as a separate transparent raster layer drawn <strong>last</strong> — above the heat and the selection wash. The split is the whole trick; it is what lets a street name stay readable over a hot block",
            "Labels sit at full opacity on the dark styles and 0.75 on the light ones. Dialling the dark ones back sounded tasteful and was wrong: a raster overlay has no halo knob, so opacity is the only contrast control, and on a saturated map (Williamsburg on nyc-bikes) anything under 1.0 washed the street names out entirely",
            "The no-WebGL Leaflet fallback gets the same overlay, gated on the same <code>!maplibreActive</code> so a WebGL browser never stacks two label sets",
        ],
        "files": ["client-react/src/mapStyles.ts", "client-react/src/components/MapView/MapView.tsx"],
    },
    {
        "id": "poi",
        "tag": "DATA",
        "title": "Business names had to come from somewhere else",
        "symptom": "CARTO's label tiles carry streets, neighborhoods and water — and no businesses or civic landmarks at any zoom. Verified against their own z13–z18 tiles over Times Square: not one theatre, shop or restaurant.",
        "cause": [
            "Positron's label layer is designed as basemap furniture, not as a POI directory. What it does label beyond streets is green space, in green caps",
            "Everything needed to build the missing half was already on disk: each city ships <code>source.osm.pbf</code>, the server already parses it with pyosmium, already encodes MVT, already writes PMTiles, and <code>/api/tiles/&lt;city&gt;/*.pmtiles</code> already serves and caches them",
        ],
        "fixes": [
            "<code>build_place_labels.py</code>: one pyosmium pass produces <code>places.pmtiles</code> per city — <strong>1.6 MB for NYC</strong>, under 0.5 MB elsewhere, ~100s to build",
            "The client adds a MapLibre <code>symbol</code> layer above the CARTO label raster, filtered by each POI's reveal zoom and sorted so important labels win collisions",
            "<code>cities.py</code> advertises a <code>placesVersion</code> exactly like <code>blocksVersion</code>: it doubles as the &quot;does this city have labels?&quot; flag and as the <code>?v=</code> cache-buster, so a city built before the artifact existed adds no layer rather than requesting an archive that 404s on every pan",
        ],
        "files": ["server/build_place_labels.py", "server/cities.py", "client-react/src/config.ts"],
    },
    {
        "id": "curation",
        "tag": "CARTOGRAPHY",
        "title": "Four ways the labels wanted to feel broken",
        "symptom": "Every intermediate version was legible and wrong: a directory dump at city zoom, subway stops that read as street labels, and park names printed twice in two different fonts.",
        "cause": [
            "<strong>The raw data is mostly noise.</strong> The five most common named POI tags in the NYC extract are subway platforms (15,688 — one per track), restaurants, named rail segments (10,675), places of worship, and abandoned track (4,950). Four of those five are not places",
            "<strong>Density does not tune by hand.</strong> Any fixed per-kind zoom that looks right in Midtown is empty in Canarsie, and vice versa",
            "<strong>Subway stops share Grand Central's tag.</strong> Both are <code>railway=station</code>, but NYC has 470-odd stops and most are named after the street above them",
            "<strong>Two cartographies can't collide-avoid each other.</strong> The CARTO labels are baked pixels; ours are live symbols. Anything both layers label gets printed twice",
        ],
        "fixes": [
            "An explicit <strong>allowlist</strong> cuts 210,003 named features to 34,349. Anything not named in it is dropped, so a new junk tag can never leak in",
            "<strong>Grid thinning</strong> decides the zoom each label appears at: at every zoom the map is divided into cells and only the highest-ranked unplaced POI in a cell earns that zoom, with already-placed labels re-claiming their cell first. The density cap is geometric, so it holds everywhere without per-neighborhood tuning. Quarter-tile cells gave ~50 labels on the city view; full-tile cells give ~18",
            "Subway stops get their own entry so 496 street-named labels stay off the city-scale view, and it deliberately skips the wikidata notability bonus — nearly every stop carries one and would otherwise land straight back among the mainline terminals. Getting the <em>weight</em> of that entry right then took a second correction in the opposite direction; see <a href=\"#stops\">The subway stops that disappeared</a>",
            "<strong>Green space belongs to the basemap.</strong> Parks, gardens and squares are dropped from the allowlist entirely; this layer owns buildings and businesses. Verified the division is clean: CARTO labels no stadium, hospital or university",
            "The thinning runs to zoom 21 (<code>CONFIG.maxZoom</code>), not 19. Every POI the grid never places falls back to the last zoom, and a ceiling of 19 dumped 20k labels — 60% of the set — into one ungoverned band",
        ],
        "files": ["server/build_place_labels.py", "server/tests/unit/test_place_labels.py"],
    },
    {
        "id": "type",
        "tag": "ASSETS",
        "title": "Drawing text meant owning the font",
        "symptom": "The MapLibre style's <code>glyphs</code> URL pointed at <code>demotiles.maplibre.org</code> — harmless while nothing on the map drew text, and load-bearing the moment something did.",
        "cause": [
            "MapLibre cannot render a <code>text-field</code> from a webfont; it needs signed-distance-field glyphs served as protobuf ranges",
            "Red Hat Mono — the UI's own face — has no CJK coverage, and NYC has plenty of Chinese shop names in the extract",
        ],
        "fixes": [
            "<code>scripts/build_glyphs.js</code> bakes the Latin ranges from Red Hat Mono into <code>public/fonts/</code>: <strong>144 KB total</strong>, served as static assets by Vite in dev and nginx in prod",
            "Using the UI's mono face is also a legibility decision: our labels read as visibly ours, distinct from the basemap's sans street names, which makes the two-cartography stack legible as a hierarchy rather than an inconsistency",
            "CJK is handled by MapLibre's <code>localIdeographFontFamily</code>, which draws those ranges from a system font at runtime — without it every Chinatown shop name would render as nothing at all",
        ],
        "files": ["client-react/scripts/build_glyphs.js", "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx", "client-react/package.json"],
    },
    {
        "id": "stops",
        "tag": "REGRESSION",
        "title": "The subway stops that disappeared",
        "symptom": "Six subway stations ring the East Village. At z16 the map showed one, and none appeared anywhere until z17–19.",
        "cause": [
            "496 of the 561 stations in the NYC bbox are <code>station=subway</code>, sharing Grand Central's <code>railway=station</code> tag. They were given their own entry to keep 496 street-named labels off the city-scale view — but that entry set rank <strong>and</strong> floor together, and rank 64 is near the BOTTOM of everything eligible at z15",
            "So every grid cell went to a museum, hospital or university first. The stops were in the tileset the whole time; they just never won a cell",
            "Worse, the shared grid is the wrong budget for them in principle. One museum per cell is plenty — museums are individually optional. Stations are read as a <em>set</em>, and showing one of six is not a sixth as useful, it is close to useless",
        ],
        "fixes": [
            "Split the two concerns the single rank was carrying: prominence goes back <strong>up</strong> (rank 86 — a stop should win its cell whenever it is eligible) and the <em>zoom</em> holds them back instead (floor 14, never city scale)",
            "Transit thins on its own grid, one level finer than everything else. The grid zoom is part of the cell key, so stations now compete only against other stations and never against a nearby deli",
            "All six East Village stops now show at z16, and the city-wide z14 band grew from 157 labels to 420 as the stations surface",
            "Two tests pin the regression from both directions: a stop must outrank every kind that once buried it, and it must not be displaced by a co-located museum — while two stops on one corner must still compete",
        ],
        "files": ["server/build_place_labels.py", "server/tests/unit/test_place_labels.py"],
    },
    {
        "id": "icons",
        "tag": "ICONS",
        "title": "27 little marks, and why they let the ranking go back up",
        "symptom": "A column of names in four colors still reads as a list. And the original reason for demoting the subway stops was real: a blue &quot;5th Avenue&quot; sitting beside the basemap's own &quot;5th Avenue&quot; street label reads as a rendering bug.",
        "cause": [
            "Color alone carries about two bits. It can say <em>which family</em> a label belongs to, but not what the place is, and it cannot distinguish a station from a street name that happens to share its text",
            "MapLibre draws icons from a sprite sheet, which would mean a second asset pipeline to keep in sync with the palette in <code>mapStyles.ts</code>",
        ],
        "fixes": [
            "27 glyphs on a 16px grid at 1.5 stroke — the vote-type icons' thin-line language (32-grid, 1.2 stroke) scaled so the strokes hold together at 13 CSS px",
            "Rasterised at mount and registered with <code>map.addImage</code> instead of shipped as a sprite, so each glyph is baked in its category's color for the ACTIVE basemap and there is no sprite to regenerate when a palette changes",
            "<code>text-optional</code> makes the layer degrade well: where a block is too busy for both, MapLibre keeps the mark and drops the name, so a dense strip thins into icons rather than into nothing",
            "The set is deliberately coarser than the OSM tags feeding it — a pub and a bar share a glass, a college and a university a mortarboard. At 13px a set of 40 near-identical marks is harder to read than one of 20 distinct ones",
            "Three glyphs were redrawn after seeing them at size: two concentric ellipses read as an <em>eye</em> rather than a stadium, a compact capsule as <em>no entry</em> rather than a pharmacy, and stage curtains as a <em>Greek letter</em> rather than a theatre",
        ],
        "files": ["client-react/src/components/MapLibreBackground/placeIcons.ts", "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx"],
    },
    {
        "id": "color",
        "tag": "LEGEND",
        "title": "Four colors, one palette per basemap",
        "symptom": "An undifferentiated wall of names is only marginally better than no names — you cannot scan it for the thing you actually want (the nearest station, the school on the corner). But colour it at full strength and the labels start competing with the very thing the map exists to show.",
        "cause": [
            "Colour coding only pays off if it is learnable, and re-hueing categories per map style would mean re-learning them on every one of the eight styles",
            "The heat ramp already owns each style's accent hue, so a loud label palette competes with the product",
        ],
        "fixes": [
            "Four categories — <strong>transit</strong>, <strong>civic</strong>, <strong>culture</strong>, <strong>retail</strong> — in one palette per basemap (dark / light), not per style",
            "<strong>Nothing is painted at full strength.</strong> The hues live in <code>POI_HUES</code> and <code>POI_COLORS</code> derives from them by mixing 58% toward a per-basemap neutral grey. At full saturation a screenful of blue, amber and violet names competed with the heat and the proposal pins instead of sitting underneath them; the coding survives the mute (a station is still visibly not a school) while the layer's weight drops well below the pins'. Keeping the vivid values as the source of truth leaves one number to turn, <code>POI_MUTE</code>, rather than eight hexes to re-pick",
            "The rest recedes with it: text 9.5–12px → 8.6–10.5px, icons 13px → 11px base (~8–10px on screen), icon opacity 0.9 → 0.72, halo 1.5 → 1.2 — at ~9px type a 1.5 halo had started reading as a bold weight rather than as separation from the basemap",
            "A side effect worth having: muted labels sit at about the same visual weight as CARTO's own street names underneath, so the two cartographies read as one quiet base layer instead of two competing ones",
            "Retail is deliberately the most neutral of the four: it is by far the largest category, and tinting every deli and bank would turn the deep zooms into confetti",
            "Green is absent by design — parks are labelled by the CARTO raster underneath, in its own green, which makes five readable classes on screen from four of our own",
            "Halos are painted in the basemap's own paper/ink token, which is the contrast control the raster overlay above it does not get to have",
        ],
        "files": ["client-react/src/mapStyles.ts", "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx"],
    },
    {
        "id": "build",
        "tag": "BUILD",
        "title": "Built where the PBFs still exist",
        "symptom": "The label build reads <code>source.osm.pbf</code>. The prod image deletes every one of them.",
        "cause": [
            "The Dockerfile's graph stage ends with <code>rm -f osm_data/*/source.osm.pbf</code> to keep the image small, and <code>build_pmtiles.py</code> runs in a LATER layer — it only needs the walk graph, so it never noticed",
        ],
        "fixes": [
            "<code>build_place_labels.py --all</code> runs inside that same <code>RUN</code>, immediately before the <code>rm -f</code>",
            "The Makefile <code>tiles</code> target and the docker-compose <code>osm-refresh</code> sidecar (first-run and the weekly refresh loop) build the labels alongside the graph tiles, so dev and prod stay on the same artifacts",
            "Freshness is mtime-based against the PBF, like the graph tiles: repeated builds skip, <code>--force</code> rebuilds",
        ],
        "files": ["Dockerfile", "Makefile", "docker-compose.yml"],
    },
]


VERIFY = [
    "Graph built and loaded: <strong>398,472 nodes / 901,030 edges</strong>, and /api/cities lists <code>sacramento</code> after the Flask restart.",
    "Blocks baked clean: <strong>102,387 blocks, 0 overlapping pairs</strong> in the final ship-frame audit (CC/CJ/JJ all zero).",
    "Local OSRM rebuilt and re-verified BOTH ways: a Broadway MLK→Stockton foot route returns <strong>1,097 m</strong> (matching the ~1 km crow distance), and an existing NYC route still returns 702 m — the merged dataset gained a city without losing one.",
    "Every classification and corridor parse was reviewed offline against all 33 pages before a single vote was cast, and the three failure modes above were each found that way rather than after the fact.",
    "The wrong-street geocode was confirmed by hand, not inferred: <code>Broadway &amp; Franklin Boulevard</code> resolves to &quot;Franklin, Cosumnes River Boulevard, Valley Hi / North Laguna&quot;. The verification rejects it and the two corridors it legitimately cannot confirm (Marysville, La Mancha Way) now pin on their own street.",
    "Cast result on local: <strong>29 ok, 0 route-failed, 0 detour demotions</strong>, giving 1,057 voted edges over 8 vote types and 601 blocks with heat.",
    "Rendered and driven in the browser: topology 891,764 edges / 102,388 blocks loaded, <strong>11 route proposals</strong> computed client-side, and clicking a pin opens a TOP PROPOSAL card (&quot;Street redesign — 1 proposal&quot;) with working ±1 controls.",
    "Deploy discipline: prod DB backed up BEFORE any build (<code>~/city-edit-prod-backups/20260803T180348Z/votes.dump</code>, 28 MB), client type-checked with <code>npx tsc -b</code> (clean) since Vite dev never type-checks, and the overlay submitted from a tree whose only diff from HEAD is this work.",
    "Sacramento is added to the app image via the <strong>arrays overlay</strong>, not a full rebuild — the other cities keep the image's own baked graphs, so their edge ids do not shift and no vote resnap or block re-bake is needed. Only <code>sacramento</code> was staged; philly / test-cp / test-mid were moved OUT of <code>.arrays-staging/</code> first, because their stale local pickles predate the 07-29 full rebuild and copying them in would have shifted philly's edge ids.",
]

CHECKLIST = [
    "Open <code>https://cityedit.org/m/sac-proposals</code> — expect the Sacramento basemap with terracotta corridors along Broadway, Franklin, Stockton and Marysville, plus proposal pins.",
    "Click one of the corridor lines and confirm the proposal card names a real vote type (e.g. <strong>Add traffic calming</strong> on Broadway) rather than &quot;No votes yet&quot;.",
    "Open Propose-a-Map on any map and confirm <strong>Sacramento</strong> now appears in the city dropdown.",
    "Spot-check that an existing city still routes: draw a route on <code>nyc-walkways</code> — the merged OSRM dataset was rebuilt, so this is the regression that matters most.",
    "<code>curl -s https://cityedit.org/api/maps | grep sac-proposals</code> and <code>curl -s 'https://cityedit.org/api/graph-votes?map=sac-proposals&amp;mode=walk'</code> — remember the SWR trap: the first hit after a bulk cast serves the stale snapshot, so poll until the count settles before concluding votes are missing.",
    "Compare a couple of pins against the source pages at <code>cityofsacramento.gov/public-works/engineering/projects</code> — the plan JSONL records the exact geocode query used for each one.",
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
    "client-react/src/mapStyles.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · map identity", "The visual identity of a map: two-color representation, basemap tiles, heat ramp, and now the label overlay + POI palette"),
        "file": ("mapStyles.ts", "~410 LOC — MapStyle interface, tile constants, the eight styles, ramp sampling helpers"),
        "outline": [
            ("Basemap / HeatRamp types", "unchanged", False),
            ("MapStyle interface", "+2 fields — labelTileUrl, labelOpacity", True),
            ("TILE_DARK / TILE_LIGHT", "unchanged — the nolabels basemaps", False),
            ("TILE_*_LABELS + LABEL_OPACITY_*", "new — the labels-only companions and their strength", True),
            ("DARK_WARM / darkStyle / lightStyle", "+2 lines each — wire the label tiles into every style", True),
            ("MAP_STYLES (8 styles)", "unchanged", False),
            ("POI_COLORS", "new — the four label categories, one palette per basemap", True),
            ("maplibreRasterTiles / maplibreLabelTiles", "refactored onto a shared expandTileTemplate", True),
            ("heatTip / heatGradientCss / ramp sampling", "unchanged", False),
        ],
        "blocks": [
            "labelOpacity is per-basemap because the two label rasters are not equally loud: CARTO's dark labels are light grey on near-black, its light ones near-black slate on paper",
            "Dark stays at 1.0 — a raster overlay has no halo, so opacity is the ONLY contrast control, and under 1.0 the street names vanish over saturated heat blocks",
            "Light can afford 0.75 because those styles blend heat by multiply, darkening the paper around the text rather than lighting up behind it",
            "POI_COLORS is keyed by basemap, NOT by style id — colour coding is only worth having if it survives switching maps",
            "No green category: parks come from the CARTO raster underneath, in its own green, so adding one here printed every park name twice",
            "Retail is the most neutral hue on purpose — it is the biggest category and a tinted deli on every corner is confetti",
        ],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GL background", "The MapLibre layer stack behind Leaflet: basemap raster, block-heat fills driven by feature-state, and now the two label tiers"),
        "file": ("MapLibreBackground.tsx", "~700 LOC — block paint expressions, style builder, camera sync, feature-state heat apply"),
        "outline": [
            ("imports / zoom-offset constant", "+POI_COLORS, +maplibreLabelTiles", True),
            ("BlockVotes / BlockSelect events + hit-test bridge", "unchanged", False),
            ("blockFillPaint / blockLinePaint", "unchanged — the heat ramp expressions", False),
            ("rebindBlockTiles", "unchanged — z/x/y swap on load", False),
            ("PLACE_LABEL_FONT / addPlaceLabels", "new — the POI symbol layer, added on `load`", True),
            ("buildStyle", "+labels raster source & layer, +self-hosted glyphs URL", True),
            ("Map constructor", "+localIdeographFontFamily", True),
            ("`load` handler", "+addPlaceLabels call", True),
            ("camera sync / zoom-animation ride", "unchanged", False),
            ("feature-state heat + selection apply", "unchanged", False),
        ],
        "blocks": [
            "carto-labels is declared LAST in layers[] — above block-heat and block-select — because a street name under a translucent hot block is unreadable",
            "It shares carto-base's tile scheme and maxzoom, so the two are pixel-locked and ride Leaflet's zoom animation together; labels can never drift off the streets they name",
            "addPlaceLabels runs on the map's `load` event, not inside buildStyle: the GL map is constructed at mount but CONFIG.placeTilesUrl only resolves once the map config lands from the network",
            "filter compares minz against ['+', ['zoom'], 1] — minz is a LEAFLET zoom (the units of CONFIG.maxZoom and every map URL), and MapLibre drives its camera one level lower",
            "symbol-sort-key is minz itself: the builder reveals a POI at the earliest zoom its rank can claim, so minz IS the importance ranking and no separate rank property has to ship",
            "text-max-width 11, not 9 — mono characters are uniformly wide and 9 turned 'New York Institute of Technology' into a three-line clump",
            "text-halo-color is the style's own base token, which is what keeps a label readable over a saturated heat block",
            "glyphs moved off demotiles.maplibre.org — fine while nothing drew text, a real dependency the moment this layer exists",
        ],
    },
    "client-react/src/components/MapView/MapView.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · map view", "The Leaflet map: interactive panes (routes, pins, drag-to-insert) plus the raster basemap fallback for browsers without WebGL"),
        "file": ("MapView.tsx", "~1500 LOC — selection state, handlers, and the layer tree"),
        "outline": [
            ("state / handlers / selection wiring", "unchanged", False),
            ("CursorTracker / SnapMarker", "unchanged", False),
            ("TileLayer (base raster fallback)", "+zIndex so the labels can stack above it", True),
            ("TileLayer (labels fallback)", "new — same !maplibreActive gate", True),
            ("ZoomControl / DesirePathLayer / split paths", "unchanged", False),
        ],
        "blocks": [
            "Gated on the SAME !maplibreActive as the base tiles: MapLibre draws its own labels, so a WebGL browser would otherwise stack two label sets on top of each other",
            "zIndex 1/2 orders the pair inside the tile pane; both stay below every interactive overlay",
            "Leaflet fills the {r} retina placeholder from Browser.retina automatically — the existing base layer already relies on this",
        ],
    },
    "client-react/src/config.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · runtime config", "Per-city camera, bounds, zooms and tile URLs, rebound at bootstrap from the /api/maps payload"),
        "file": ("config.ts", "~180 LOC — the CONFIG object, the CityConfig shape, applyCityConfig"),
        "outline": [
            ("env detection / dev API base", "unchanged", False),
            ("CONFIG: camera, bounds, zooms", "unchanged", False),
            ("CONFIG: graphTilesUrl / blockTilesUrl / blockTiles", "unchanged", False),
            ("CONFIG.placeTilesUrl", "new — null until the city advertises labels", True),
            ("BlockTilesConfig / CityConfig", "+placesVersion", True),
            ("applyCityConfig", "+places path with the ?v= cache-buster", True),
        ],
        "blocks": [
            "placeTilesUrl stays null when placesVersion is absent, so a city without the artifact adds no layer at all rather than a source that 404s on every pan",
            "?v=<mtime> follows the same discipline as blocks.pmtiles: rebuilt labels must never be read through week-old cached byte ranges of the previous build",
            "Dev resolves against devApiBase (Flask :5001) and prod against a relative path, matching every other tile URL here",
        ],
    },
    "server/build_place_labels.py": {
        "on": ["Flask API"],
        "module": ("Server · label tile builder", "New sibling of build_pmtiles.py: turns a city's OSM extract into the place/business label tileset the client draws"),
        "file": ("build_place_labels.py", "~400 LOC — allowlist, classification, dedup, grid thinning, MVT/PMTiles encode"),
        "outline": [
            ("module docstring", "new — why an allowlist, why grid thinning, the zoom-unit convention", True),
            ("zoom + thinning constants", "new — tile band, label band, GRID_OFFSET, DEDUP_METERS", True),
            ("CAT_* categories", "new — transit / civic / culture / retail (no green, by design)", True),
            ("KINDS allowlist", "new — (osm key, value) → category, rank, floor zoom", True),
            ("SUBWAY_STATION override", "new — the demotion for stops sharing Grand Central's tag", True),
            ("classify / normalize_name", "new — tag priority, notability bonus, name folding", True),
            ("PoiCollector (osmium handler)", "new — nodes + closed-way centroids inside the city bbox", True),
            ("dedup", "new — same-name cluster collapse within 400m", True),
            ("assign_zooms", "new — the grid-thinning pass that sets each label's reveal zoom", True),
            ("build_city_place_labels / _collect_tiles", "new — bucket to tiles, encode, atomic replace", True),
            ("main (CLI)", "new — --city / --all / --force, mtime freshness", True),
        ],
        "blocks": [
            "The allowlist is the whole design: 210,003 named features in the NYC extract become 34,349. The four noisiest tags — platforms, named rail segments, abandoned track, pitches — are not places at all",
            "rank breaks collisions; floor is the earliest zoom a kind may EVER appear; assign_zooms decides the zoom it actually does",
            "assign_zooms lets already-placed labels re-claim their cell before newcomers, so zooming in adds detail BETWEEN existing labels instead of stacking on top of them",
            "GRID_OFFSET=0 (one cell per tile, ~256 CSS px). At 1 the city view came out at ~50 labels and read as a directory",
            "MAX_LABEL_ZOOM=21 matches CONFIG.maxZoom: every unplaced POI falls back to the last zoom, and a ceiling of 19 dumped 60% of the set into one ungoverned band",
            "SUBWAY_STATION returns before the wikidata bonus — nearly every NYC stop has one and would otherwise claim back the rank the demotion just removed",
            "Point labels are never clipped by a tile edge (MapLibre draws glyphs that overflow the tile holding their anchor), so each POI goes into exactly ONE tile per zoom and no buffer is needed",
            "Stable feature ids let MapLibre's cross-tile index recognise the same label across adjacent tiles instead of drawing it twice",
            "Multipolygon relations are skipped: assembling areas costs a second full pass over a 500MB extract, and the features that matter are mapped as ways",
        ],
    },
    "server/cities.py": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("Server · city registry", "The curated list of supported cities and the artifacts each one advertises to the client"),
        "file": ("cities.py", "~280 LOC — blocks-header cache, the City dataclass, the CITIES registry"),
        "outline": [
            ("_blocks_header_info / _env_osrm_host", "unchanged", False),
            ("City.to_public", "+placesVersion in the payload", True),
            ("City._block_tiles / _blocks_version", "unchanged", False),
            ("City._places_version", "new — mtime of places.pmtiles, or None", True),
            ("CITIES registry", "unchanged", False),
        ],
        "blocks": [
            "_places_version mirrors _blocks_version exactly — a cheap stat, no graph load",
            "It does double duty: the ?v= cache-buster AND the 'does this city have labels?' flag",
            "None is the important case: cities built before the artifact existed report nothing and the client adds no layer, instead of requesting an archive that 404s on every pan",
        ],
    },
    "server/tests/unit/test_place_labels.py": {
        "on": ["Flask API"],
        "module": ("Server · tests", "Pure unit coverage (no Redis/Postgres) for the curation rules that decide what the label layer shows"),
        "file": ("test_place_labels.py", "~130 LOC — 25 tests across classify, normalize_name, dedup, assign_zooms"),
        "outline": [
            ("TestClassify", "new — allowlist, noise rejection, notability, the subway demotion, tag priority", True),
            ("TestNormalizeName", "new — accent/case/punctuation folding", True),
            ("TestDedup", "new — station complexes collapse, distant namesakes survive", True),
            ("TestAssignZooms", "new — floors respected, rank wins cells, nothing vanishes", True),
        ],
        "blocks": [
            "The noise-rejection test is parameterized over the actual top offenders in the NYC extract, so a bad allowlist entry fails loudly instead of quietly restoring 15,000 platforms",
            "test_subway_demotion_survives_a_wikidata_tag pins the exact ordering bug that made the demotion a no-op",
            "test_every_poi_gets_a_reveal_zoom guards the fallback: a POI whose cell is taken at every zoom must still end up somewhere rather than silently leaving the tileset",
            "Green-space tags are asserted as DROPPED — the duplicate-label regression has a test now",
        ],
    },
    "client-react/src/components/MapLibreBackground/placeIcons.ts": {
        "on": ["React / Leaflet client"],
        "module": ("Client · place-label icons", "The 27-glyph mark set drawn beside each place name, rasterised at mount and coloured per category"),
        "file": ("placeIcons.ts", "~290 LOC — the ICONS table, SVG→ImageData rasterisation, map registration"),
        "outline": [
            ("header / ICON_PX / IconDef", "new — the 13px constraint every decision follows from", True),
            ("ICONS: transit (5)", "new — subway, rail, ferry, bus, airport", True),
            ("ICONS: civic (7)", "new — school, library, hospital, government, worship, emergency, post", True),
            ("ICONS: culture (6)", "new — museum, theatre, cinema, stadium, attraction, monument", True),
            ("ICONS: retail (9)", "new — restaurant, cafe, bar, shop, market, hotel, pharmacy, bank, sports", True),
            ("toSvg / rasterize", "new — colour injection, data-URI decode to ImageData", True),
            ("registerPlaceIcons", "new — awaits every glyph before the layer is added", True),
            ("placeIconExpression / PLACE_ICON_NAMES", "new — icon-image, plus the drift-test export", True),
        ],
        "blocks": [
            "13 CSS px is the whole constraint: anything past three or four primitives turns to mush, which is why the set is coarser than the OSM tags feeding it",
            "addImage rather than a sprite sheet — glyphs are baked in the ACTIVE basemap's palette, so a colour change needs no asset regeneration and there is no second pipeline to drift from mapStyles.ts",
            "registerPlaceIcons is awaited BEFORE addLayer, so MapLibre never renders a frame asking for an image that isn't there yet",
            "The subway mark is the load-bearing one: it is what lets a stop named '5th Avenue' sit beside the basemap's '5th Avenue' street label without reading as a bug — and therefore what let the stops' rank go back up",
            "stadium was two concentric ellipses (an eye), pharmacy a compact capsule (a no-entry sign), theatre stage curtains (a Greek letter) — all three only showed up on a contact sheet at actual size",
            "A `poi-dot` fallback plus a coalesce keeps an older client against a newer tileset quiet instead of storming the console with missing-image warnings",
        ],
    },
    "client-react/scripts/build_glyphs.js": {
        "on": ["React / Leaflet client"],
        "module": ("Client · build tooling", "One-shot asset bake: the SDF glyph ranges MapLibre needs to draw any text at all"),
        "file": ("build_glyphs.js", "~60 lines — font fetch, fontnik range loop, write to public/fonts"),
        "outline": [
            ("header / FONT_URL / FONTSTACK", "new — Red Hat Mono, the UI's own face", True),
            ("RANGES", "new — Latin + Latin-1 + Extended + General Punctuation", True),
            ("main", "new — read or download the TTF, emit one pbf per range", True),
        ],
        "blocks": [
            "144 KB total for four ranges — small enough to commit, so the client build needs no native toolchain",
            "General Punctuation is not optional: business names use U+2019 for the apostrophe in \"Joe's\" far more often than U+0027",
            "No CJK ranges — Red Hat Mono has no coverage, and localIdeographFontFamily draws those from a system font at runtime instead",
            "ESM, not CJS: client-react's package.json declares \"type\": \"module\"",
        ],
    },
    "Dockerfile": {
        "on": ["nginx", "Flask API", "OSRM"],
        "module": ("Deploy · prod image", "Multi-stage build: client bundle, per-city graphs, tiles, then nginx + supervisor"),
        "file": ("Dockerfile", "~80 lines — deps, client-builder, graph build, tile build, serving config"),
        "outline": [
            ("base deps / python env", "unchanged", False),
            ("client-builder stage", "unchanged — picks up public/fonts automatically", False),
            ("graph build RUN (refresh_osm ×6)", "+build_place_labels before the rm -f", True),
            ("build_pmtiles RUN", "unchanged", False),
            ("nginx / supervisor / healthcheck", "unchanged", False),
        ],
        "blocks": [
            "The label build MUST sit inside this RUN: the same command ends with `rm -f osm_data/*/source.osm.pbf`, and the labels are built from those PBFs",
            "build_pmtiles.py can stay in its later layer because it reads the walk graph, not the extract",
            "The glyph pbfs need no build step here — they live in public/ and the client-builder copies them with the rest of the bundle",
        ],
    },
    "Makefile": {
        "on": ["Flask API", "React / Leaflet client"],
        "module": ("Dev · local loop", "make deps / graphs / tiles / dev — the hybrid loop of Docker backing services plus host Flask and Vite"),
        "file": ("Makefile", "~120 lines — dev targets, docker targets, helpers"),
        "outline": [
            ("deps / graphs", "unchanged", False),
            ("tiles", "+build_place_labels --all", True),
            ("dev / docker targets", "unchanged", False),
        ],
        "blocks": [
            "Hangs off `tiles`, which already depends on `graphs`, so `make dev` produces labels without a new entry point",
            "Freshness is mtime-vs-PBF, so repeated runs skip the ~100s NYC pass",
        ],
    },
    "docker-compose.yml": {
        "on": ["Flask API", "OSRM"],
        "module": ("Dev · docker stack", "Backing services plus the osm-refresh sidecar that builds graphs and tiles on first start and weekly after"),
        "file": ("docker-compose.yml", "~140 lines — redis, postgres, osrm, flask, nginx, osm-refresh"),
        "outline": [
            ("redis / postgres / osrm / flask / nginx", "unchanged", False),
            ("osm-refresh: first-run build", "+build_place_labels --all", True),
            ("osm-refresh: weekly loop", "+build_place_labels --all --force", True),
        ],
        "blocks": [
            "Both paths are patched: a fresh checkout gets labels on first start, and the Sunday refresh rebuilds them against the new extract",
            "--force on the weekly path matches the graph/tile rebuild — a refreshed PBF must not be read through a stale artifact",
        ],
    },
    "client-react/package.json": {
        "on": ["React / Leaflet client"],
        "module": ("Client · package manifest", "Scripts and dependencies for the Vite client"),
        "file": ("package.json", "~50 lines"),
        "outline": [
            ("scripts: dev / build / lint / test", "unchanged", False),
            ("scripts: build:glyphs", "new", True),
            ("devDependencies", "+fontnik", True),
        ],
        "blocks": [
            "fontnik is a devDependency only — the generated pbfs are committed, so neither CI nor the Docker client-builder needs it",
            "fontnik ships prebuilt binaries; no freetype/node-gyp toolchain is pulled into the image",
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
