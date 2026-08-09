#!/usr/bin/env python3
"""Content for the longest-wait change-log report.

Kept separate from changelog/build_wait_report.py so the prose can be edited
without touching the rendering machinery that was copied from
changelog/build_report.py.
"""

DATE = "2026-08-09"
TITLE = "The longest waits: ranking every NYC crossing by the time it takes"
OUT_NAME = "2026-08-09-longest-wait-intersections.html"
DIFF_NAME = "changes-wait.diff"

LEDE = (
    "The dangerous-intersections campaign answered <em>where do people get "
    "hit</em>. This one answers <em>where does the city waste people's "
    "time</em> — the wait to cross, multiplied by the crowd that has to stand "
    "in it. Neither number is published, so both are modelled: the wait from "
    "signal engineering and OpenStreetMap carriageway topology, the crowd "
    "from a fit on the 95 corners where DOT actually counts. Across 11,907 "
    "signalised crossings that comes to <strong>406,000 person-hours a "
    "weekday</strong> spent standing at a New York kerb. The output is a "
    "36-poster packet, printed in an ink budget an office laser can afford."
)

SECTIONS = [
    {
        "id": "data",
        "tag": "DATA",
        "title": "Two numbers nobody publishes",
        "symptom": (
            "The brief was &quot;find high-wait intersections, then re-rank "
            "them by how many people actually cross&quot;. Neither input "
            "exists as a dataset."),
        "cause": [
            "<strong>Signal timing is not open data.</strong> NYC DOT runs "
            "over 13,500 signalised intersections and publishes cycle lengths "
            "only as prose — &quot;typically 60, 90 or 120 seconds&quot;. The "
            "open-data catalogue carries signal <em>treatments</em> "
            "(accessible signals, leading pedestrian intervals, Barnes Dance "
            "locations) but no timing plan, per-approach green, or phase "
            "diagram for any intersection.",
            "<strong>Pedestrian counts are 114 points citywide.</strong> The "
            "Bi-Annual Pedestrian Counts programme samples a weekday 7–9 AM "
            "and 4–7 PM screenline plus a Saturday midday one, at 100 retail "
            "corridors, 13 bridges and the Hudson River Greenway. Real "
            "numbers, but three orders of magnitude short of a corner-level "
            "ranking.",
            "What <em>does</em> exist citywide is the raw material for "
            "modelling both: DOT's Pedestrian Mobility Plan scores all "
            "127,277 street segments 1–5 for pedestrian demand, MTA publishes "
            "hourly ridership for every station complex, and the NYC OSM "
            "extract already on disk carries every signal, carriageway and "
            "lane count.",
        ],
        "fixes": [
            "<code>fetch_wait_data.py</code> pulls five public datasets into "
            "<code>data/raw/wait/</code>, caching so a re-run costs nothing. "
            "The MTA month is aggregated server-side with a SoQL "
            "<code>$group</code> rather than downloaded row by row, and "
            "normalised to daily entries per station on the way in — 3.72M "
            "a day, which matches MTA's own published weekday figure.",
            "<code>extract_osm_intersections.py</code> makes two passes over "
            "the 490 MB NYC PBF (nodes are written before ways, so way "
            "membership cannot be known while the nodes stream past): pass one "
            "reads drivable ways and records which node ids matter, pass two "
            "fetches those locations plus signal tags and every POI point. "
            "Three minutes, 25,039 signalised junction nodes.",
            "That node count is itself the first useful signal: DOT counts "
            "~13,500 signalised <em>intersections</em>, so nearly twice as "
            "many nodes means a great many crossings are split across several "
            "carriageways — which turns out to be the whole story.",
        ],
        "files": ["scripts/fetch_wait_data.py",
                  "scripts/extract_osm_intersections.py"],
    },
    {
        "id": "wait",
        "tag": "THE WAIT",
        "title": "Estimating a wait from the shape of a road",
        "symptom": (
            "With no timing data, the wait has to be derived from what a "
            "signal engineer would have done — and the first version put the "
            "West Village at the top of the list."),
        "cause": [
            "The delay itself is standard: for a fixed-time signal with random "
            "arrivals, the average wait is "
            "<code>d = (C − g)² / 2C</code>. The cycle <code>C</code> comes "
            "from the road hierarchy (60 / 90 / 120s, longest on the widest "
            "arterials) and the green <code>g</code> from a split apportioned "
            "between the two streets by lanes × class — a pedestrian crossing "
            "street A walks parallel to street B and goes when B goes, so "
            "crossing a boulevard from a side street gets the side street's "
            "small share.",
            "<strong>What actually decides the ranking is staging.</strong> "
            "Where a street reaches the corner as several separate "
            "carriageways — a median, or service roads — the pedestrian waits "
            "on each island in turn, and absent pedestrian-favourable offsets "
            "each island costs another full expected wait in the same "
            "formula. A four-stage crossing at a 120-second cycle is over two "
            "minutes of standing.",
            "<strong>The bug:</strong> the first stage detector counted the "
            "signalised OSM nodes clustered within 45 m of each other. That "
            "is right for Queens Boulevard and badly wrong for the West "
            "Village, where 7th Avenue South meets Grove, Bleecker and "
            "Christopher inside 45 m. Three ordinary corners were reported as "
            "a four-stage boulevard with a 100-second wait, and it ranked "
            "sixth in the city.",
            "Restricting the count to nodes carrying <em>both</em> streets "
            "fixed the Village and broke the Bronx instead: at East Tremont "
            "and Creston, East Tremont's four carriageways each meet Creston "
            "at their own node — but Creston, a 2-lane residential street, "
            "outweighed a secondary street OSM tags as <code>lanes=1</code> "
            "per carriageway, so the model reported the wait to cross "
            "<em>Creston</em>.",
        ],
        "fixes": [
            "<strong>Carriageways are found topologically, not "
            "geometrically.</strong> Each street's OSM ways at the cluster are "
            "grouped into connected components: the ways of one undivided "
            "street chain together through shared nodes, while a boulevard's "
            "carriageways never touch each other here. One component is one "
            "thing to walk across. This is invariant to how OSM happens to "
            "have split a way for tagging, which neither node-counting nor "
            "way-counting is.",
            "A street's size is then the <strong>sum of lanes across its "
            "carriageways</strong>, which is what makes East Tremont (4 × 1 "
            "lane, secondary) correctly outrank Creston (1 × 2 lanes, "
            "residential) and picks the right street to be crossed.",
            "The stages for a given crossing are the crossed street's "
            "carriageways that touch a node the along-street also reaches — "
            "so neighbouring corners of the same boulevard never inflate it.",
            "Two constraints keep the model physical: a Barnes Dance sizes the "
            "walk interval to the crossing and no more (safer, longer wait), "
            "and a single-stage crossing too wide to clear in the computed "
            "green has its green raised to the MUTCD 3.5 ft/s requirement — "
            "DOT must give the time, so the model must too.",
            "<strong>Result:</strong> the longest waits are Queens Boulevard, "
            "Woodhaven, Ocean Parkway, the Grand Concourse, Bruckner and "
            "Linden — which is the answer a New Yorker would give unprompted. "
            "56 crossings score four stages, 103 score three, 1,283 score two.",
        ],
        "files": ["scripts/build_wait_rankings.py"],
    },
    {
        "id": "crowd",
        "tag": "THE CROWD",
        "title": "Calibrating a citywide crowd on 95 corners",
        "symptom": (
            "A long wait on an empty street is a curiosity. The re-ranking "
            "needs a pedestrian volume for all 11,907 crossings, from 114 "
            "measurements."),
        "cause": [
            "The count sites give a real weekday volume once the AM (2 h) and "
            "PM (3 h) screenlines are expanded to a 12-hour day; the Saturday "
            "midday column is a different day and is deliberately left out.",
            "<strong>18 of the sites are not street corners.</strong> Bridges "
            "and the greenway count a walk with no alternative for a mile in "
            "either direction, and they were the worst outliers in both "
            "directions — the Brooklyn Bridge under-predicted 16×, the "
            "Queensboro over-predicted 9×. Removing them is a modelling "
            "decision, not a cherry-pick: the model is only ever asked about "
            "street corners.",
            "<strong>POI density looked essential and was not.</strong> It "
            "correlates 0.55 with subway access, adds 0.001 to out-of-sample "
            "R², and takes a <em>negative</em> coefficient once subway access "
            "is in the fit — an artefact of collinearity that would have to be "
            "defended on a poster's methodology page as &quot;more shops, "
            "fewer pedestrians&quot;.",
        ],
        "fixes": [
            "A log-linear fit on the 95 street corners from two predictors: "
            "distance-decayed subway entries and DOT's own Pedestrian "
            "Mobility Plan demand class for that specific street. Both "
            "coefficients positive, both meaning what they say.",
            "Scored honestly with <strong>leave-one-out</strong> R², not "
            "in-sample: <strong>0.71</strong> (in-sample 0.74, Spearman 0.82, "
            "log-RMSE 0.47). A corner's volume is right to roughly a third "
            "either way. Dropping the bridges moved leave-one-out R² from 0.66 "
            "to 0.71 on its own.",
            "The two weaknesses are stated on the packet's cover rather than "
            "buried: DOT counts retail corridors, so the fit extrapolates "
            "worst on quiet streets; and subway access carries most of the "
            "signal, so volumes are least trustworthy far from the network — "
            "much of Staten Island and eastern Queens.",
            "Crossers of street A are the people walking along street B "
            "(×0.65 for those who turn off), so an intersection's total is the "
            "sum over both crossings, and the ranking metric is "
            "<strong>person-hours per weekday</strong>.",
        ],
        "files": ["scripts/build_wait_rankings.py"],
    },
    {
        "id": "poster",
        "tag": "DESIGN",
        "title": "A poster that argues in ink you can afford",
        "symptom": (
            "The existing posters are clip-art on white with heavy black "
            "display type; the on-brand reference image is dark-mode and "
            "photo-realistic. Neither is printable at volume, and neither "
            "looks like City Edit, which is a monospace terminal for editing a "
            "city."),
        "cause": [
            "City Edit's own tokens are unambiguous: <code>--font-main</code> "
            "is Red Hat Mono and the comment above it reads &quot;Typography — "
            "monospace only&quot;; every radius token is <code>0</code>; the "
            "start/end markers are cyan <code>#00C4D4</code> and red "
            "<code>#DC343B</code>. A poster that uses those is on-brand "
            "without importing anything.",
            "Ink coverage is a design constraint with a right answer per "
            "element, not a global dimmer: large type wants to be an outline, "
            "large areas want to be a hatch, and the page wants to stay white.",
        ],
        "fixes": [
            "<strong>The hero figure is an outline</strong> "
            "(<code>-webkit-text-stroke</code>, 3px so a 300 dpi laser holds "
            "it) — 196px of presence for a twentieth of the toner. Red Hat "
            "Mono's slashed zero does a lot of work here.",
            "<strong>The diagram is the argument.</strong> A plan view of the "
            "carriageways sits beside one timing bar per island, sharing a "
            "row, so a band of asphalt is directly next to the seconds it "
            "costs. The bar splits the cycle into WALK (the only seconds you "
            "may <em>start</em> on — 7 of 120), the countdown, and the wait.",
            "That split was also the ink fix. The first version filled the "
            "whole green in solid cyan, which was simultaneously the largest "
            "ink block on the page and an argument against itself — the bar "
            "read as generous. Showing only the 7-second WALK as solid cut the "
            "cyan by three quarters and made the point harder.",
            "The one large field of colour is a 45° hatch at roughly an eighth "
            "coverage. The countdown segment paints white underneath its own "
            "hatch, or the two screens moiré into mud where they overlap.",
            "Atmosphere for free: a 1px dot grid on an 18px pitch (~0.3% "
            "coverage), print-shop registration ticks, a rotated survey line "
            "down the left spine, and the app's square-boxed CITY EDIT "
            "wordmark in the footer.",
            "A locator silhouette of the five boroughs with a crosshair "
            "replaces the old posters' &quot;YOU ARE HERE&quot; pin — the "
            "borough seams are dropped at 168px so it reads as one shape.",
        ],
        "files": ["data/posters/templates/longest_wait.html"],
    },
    {
        "id": "packet",
        "tag": "THE PACKET",
        "title": "36 corners, four campaigns, one PDF",
        "symptom": (
            "The top of the wait list is nine posters about Queens Boulevard, "
            "and the top of the time-lost list is fourteen about Manhattan. "
            "Both are true and both are bad campaigns."),
        "cause": [
            "The two metrics have different statistical characters, and the "
            "claims have to respect that. Person-hours is continuous and "
            "unties cleanly. The wait model <em>saturates</em> at its "
            "four-stage ceiling — a dozen boulevard crossings score exactly "
            "129 seconds — so &quot;the 3rd longest wait in New York&quot; "
            "would be a coin toss printed as a fact.",
            "A poster claiming the longest wait in the city also has to be "
            "somewhere people cross, or it is arguing with an empty sidewalk.",
        ],
        "fixes": [
            "<strong>Claims are pinned to what each model can carry.</strong> "
            "The time-lost campaign claims an ordinal rank; the longest-wait "
            "campaign claims membership in a tier and no number at all. The "
            "chip in the header reflects that too — a rank for one, a category "
            "for the other.",
            "Per-corridor and per-borough caps spread each campaign over the "
            "streets that share its claim: 2 posters maximum per street, so "
            "the longest-wait set becomes Queens Boulevard, Woodhaven, "
            "Bruckner, Atlantic, Linden and East Tremont rather than one road "
            "nine times.",
            "Four campaigns, claimed in order, each intersection used once: "
            "longest wait citywide, most time burnt citywide, worst in each "
            "borough (which is what puts Staten Island in the packet at all), "
            "and worst in each of 14 neighbourhoods.",
            "Every QR opens <code>/m/nyc-proposals</code> at that exact "
            "crossing with the <strong>Fix signal timing</strong> point type "
            "pre-selected and <code>src=qr-wait</code> for attribution — a "
            "vote type that already exists on the map, so a scan lands on a "
            "cast-ready pin.",
            "The packet is a 39-page PDF: a cover that states what the two "
            "numbers are and are <em>not</em>, a placement checklist with a "
            "tick box and a code per poster, a citywide placement map with "
            "collision-nudged labels and leader lines, then the 36 posters.",
        ],
        "files": ["scripts/generate_wait_posters.py",
                  "scripts/build_wait_packet.py"],
    },
]

VERIFY = [
    "Full pipeline runs end to end from a clean cache: "
    "<code>fetch_wait_data.py</code> (5 datasets, 88 MB) → "
    "<code>extract_osm_intersections.py</code> (3 min over the 490 MB PBF) → "
    "<code>build_wait_rankings.py</code> (4.5 s) → "
    "<code>generate_wait_posters.py</code> (36 posters + packet).",
    "<strong>Volume model scored out-of-sample, not in-sample:</strong> "
    "leave-one-out R² 0.71 over 95 sites, refitting without each site and "
    "predicting it. In-sample R² is 0.74, Spearman 0.82, log-RMSE 0.47.",
    "<strong>Predictor selection was tested, not assumed:</strong> all seven "
    "subsets of {subway, POI, demand} were scored by leave-one-out R². Subway "
    "alone scores 0.700, subway+demand 0.709, all three 0.710 with POI "
    "negative — so POI was dropped.",
    "<strong>Face validity on named places.</strong> The longest waits are "
    "Queens Boulevard, Woodhaven, Ocean Parkway, Grand Concourse and Bruckner; "
    "Broadway at 79th and 96th correctly score 2 stages (the malls); "
    "Times Square, Herald Square and Fulton Street top the time-lost list. "
    "The two stage-detector bugs were each found by checking a specific corner "
    "against what is actually there, not by looking at aggregate statistics.",
    "Signalised node count (25,039) against DOT's ~13,500 signalised "
    "intersections is the ~1.85 nodes-per-intersection ratio the carriageway "
    "model predicts; clustering yields 21,070 intersections, 11,907 of which "
    "sit inside the five boroughs with two named streets.",
    "Citywide total (406,261 person-hours a weekday) is reported on the cover "
    "alongside the model's weaknesses rather than as a bare headline.",
    "A generated QR target was fetched and returns <strong>HTTP 200</strong>: "
    "<code>https://cityedit.org/m/nyc-proposals?w=40.720984,-73.842910"
    "&amp;vt=Fix+signal+timing&amp;src=qr-wait</code>. "
    "<code>Fix signal timing</code> was confirmed against "
    "<code>/api/maps</code> as a real point vote type on that map.",
    "Every poster variant was rendered and inspected: 1-stage (42nd &amp; "
    "7th), 2-stage, 3-stage and 4-stage (Queens Boulevard), plus both hero "
    "flavours (m:ss wait, and hours lost). Page overflow was caught and fixed "
    "at the 4-stage extreme, checklist overflow at 36 rows.",
    "Generated artefacts are gitignored to match the existing campaign's "
    "convention (<code>data/.gitignore</code> already excludes "
    "<code>posters/out/</code>); the 88 MB of downloads sit under the "
    "already-ignored <code>data/raw/</code>.",
]

CHECKLIST = [
    "Open <code>data/posters/wait/wait-poster-book.pdf</code> and read the "
    "cover — it is the page that states what is modelled and where the model "
    "is weakest, and it is the one to disagree with first if any claim feels "
    "overstated.",
    "<strong>Print one poster on the actual office printer</strong> before "
    "committing to a run. The outlined hero figure and the 1px dot grid are "
    "the two elements most likely to behave differently on paper than on "
    "screen.",
    "Scan a QR with a phone and confirm it lands on the right corner of "
    "<code>/m/nyc-proposals</code> with <strong>Fix signal timing</strong> "
    "selected and ready to cast.",
    "Sanity-check three corners you know personally against "
    "<code>python scripts/build_wait_rankings.py --inspect &quot;&lt;street "
    "name&gt;&quot;</code>, which prints the cycle, green, stage count and "
    "crossing width behind each number. Local knowledge is the best available "
    "test of a model built without timing data.",
    "Decide whether the <code>longest_wait</code> campaign should keep its "
    "4,000-pedestrians-a-day floor — it currently excludes some genuinely "
    "brutal crossings in low-volume industrial areas.",
    "If the packet ships, consider whether <code>src=qr-wait</code> should be "
    "split per campaign (<code>qr-wait-boro</code>, <code>qr-wait-nabe</code>) "
    "so the MAPLOAD attribution can tell which framing actually gets scanned.",
]

# ── Hierarchical "where does this block sit" context, one entry per file ──────
FILE_CONTEXT = {
    "scripts/fetch_wait_data.py": {
        "on": ["Analysis + posters (offline)"],
        "module": ("Analysis · wait pipeline (1/4)",
                   "Downloads the five public datasets the wait and volume models are built from, caching so a re-run is free"),
        "file": ("fetch_wait_data.py",
                 "~110 LOC — dataset table, a Socrata aggregate query, cache-aware fetch"),
        "outline": [
            ("module docstring — what each file is and why", "new — the provenance record for the whole pipeline", True),
            ("RIDERSHIP_START / END / DAYS", "new — the month summed for the subway proxy", True),
            ("SIMPLE (4 direct dataset URLs)", "new — counts, Barnes Dance, LPI, ped demand", True),
            ("subway_url()", "new — SoQL $select/$group so MTA aggregates server-side", True),
            ("main()", "new — cache check, fetch, normalise to daily entries", True),
        ],
        "blocks": [
            "ped_demand.geojson is 73 MB and 127,277 segments — DOT's own 1-5 pedestrian demand score for every street in the city",
            "The MTA table is one row per station per hour per fare class; downloading a month of it row-wise would be millions of rows, so $group does the sum on Socrata's side",
            "Normalising the month to daily entries on the way in means no downstream code has to remember the window",
            "Sanity check baked into the output: 3,724,102 entries/day citywide, against MTA's own ~3.7M published weekday figure",
            "Everything lands under data/raw/, which is already gitignored — 88 MB stays out of the repo",
        ],
    },
    "scripts/extract_osm_intersections.py": {
        "on": ["Analysis + posters (offline)"],
        "module": ("Analysis · wait pipeline (2/4)",
                   "Reduces the 490 MB NYC PBF to the signalised junctions, the streets meeting at them, and the POI field around them"),
        "file": ("extract_osm_intersections.py",
                 "~200 LOC — two osmium passes, junction assembly, gzipped JSON out"),
        "outline": [
            ("ROAD_CLASSES / POI keys", "new — what a pedestrian has to wait to cross, and what counts as a destination", True),
            ("road_class / parse_lanes", "new — class normalisation and class-typical lane defaults", True),
            ("class WayPass (pass 1)", "new — drivable ways + which node ids matter", True),
            ("class NodePass (pass 2)", "new — locations, signal tags, crossings, POIs", True),
            ("main — junction assembly", "new — per-node street list WITH way ids", True),
        ],
        "blocks": [
            "Two passes, not one: a PBF writes all nodes before any way, so way membership cannot be known while the nodes stream past",
            "Pass 1 deliberately defines no node() handler, so pyosmium skips node decoding entirely on that read",
            "A junction needs 2+ distinct street NAMES, which is what keeps approach-crossing nodes (mid-block, one street) out of the signal set",
            "s['w'] carries the OSM way indices per street per node — the single field the whole carriageway model depends on, added in the second iteration after node-counting failed",
            "Output: 25,039 signalised junction nodes, 246,773 crossings, 116,396 POIs, 16.5 MB gzipped",
            "NYC DOT reports ~13,500 signalised intersections, so ~1.85 nodes each — the divided-boulevard signal, visible before any modelling",
        ],
    },
    "scripts/build_wait_rankings.py": {
        "on": ["Analysis + posters (offline)"],
        "module": ("Analysis · wait pipeline (3/4)",
                   "The two models and the ranking: an estimated wait per crossing, a fitted crowd, and the person-hours they multiply out to"),
        "file": ("build_wait_rankings.py",
                 "~560 LOC — wait model, spatial index, volume fit, clustering, CSV out"),
        "outline": [
            ("module docstring", "new — states plainly that BOTH numbers are estimates", True),
            ("CLASS_WEIGHT / geometry constants", "new — lane width, median, MUTCD walk speed, split clamps", True),
            ("norm_street", "new — OSM/DOT name matching; suffixes expand only in final position", True),
            ("cycle_length / street_weight / crossing_width", "new — 60/90/120s from hierarchy, kerb-to-kerb metres", True),
            ("crossing_wait", "new — d = (C-g)^2/2C per stage, Barnes and MUTCD constraints", True),
            ("class Grid", "new — uniform lat/lon bucket index for radius queries", True),
            ("load_* (OSM, NTA, demand, subway, POI, flags)", "new — the five inputs into queryable form", True),
            ("subway_access / poi_count / demand_for", "new — the volume predictors", True),
            ("observed_volume / NOT_A_STREET", "new — count expansion, bridges held out", True),
            ("design_matrix / fit_volume_model / leave_one_out_r2", "new — the fit and its honest score", True),
            ("cluster_signals", "new — union-find over signalised nodes within 45 m", True),
            ("merge_streets / connected_carriageways", "new — THE fix: carriageways as connected components", True),
            ("crossing_stages", "new — only the carriageways the cross street reaches", True),
            ("build()", "new — assembles, ranks, writes the CSV and the fit record", True),
        ],
        "blocks": [
            "d = (C - g)^2 / (2C) is the textbook uniform-arrival delay; every extra island costs another d in expectation, because absent a known offset the arrival phase at stage 2 is uniform too",
            "Green split = w_along / (w_cross + w_along), clamped [0.30, 0.62] — a side street never gets nothing, an arterial never gets everything",
            "connected_carriageways is the correctness core: ways of one street chain through shared nodes, a boulevard's carriageways do not. Invariant to how OSM split a way for tagging, which node-counting and way-counting both are not",
            "A street's size is the SUM of lanes across carriageways — without that, East Tremont (4 x 1 lane, secondary) loses to Creston (1 x 2 lanes, residential) and the model reports the wrong crossing",
            "crossing_stages counts only carriageways touching a node the along-street reaches — the West Village's three ordinary corners stop reading as a four-stage boulevard",
            "Barnes Dance: g = exactly what the crossing needs, so an exclusive phase is correctly safer AND a longer wait",
            "Single stage too wide for the computed green: g rises to the MUTCD 3.5 ft/s requirement, because DOT has to provide the time",
            "NOT_A_STREET holds out 18 bridge/greenway count sites — leave-one-out R2 0.66 -> 0.71 from that alone",
            "design_matrix documents the DROPPED predictor and why, so the negative POI coefficient cannot quietly return",
            "leave_one_out_r2 refits without each site: a 3-parameter fit on 95 points flatters itself in-sample",
        ],
    },
    "data/posters/templates/longest_wait.html": {
        "on": ["Analysis + posters (offline)", "React / Leaflet client"],
        "module": ("Posters · template",
                   "The printable poster itself: City Edit's monospace tokens, an outlined hero figure, and a signal-cycle diagram that carries the argument"),
        "file": ("longest_wait.html",
                 "~470 lines — tokens, page furniture, the chart, the slots the generator fills"),
        "outline": [
            (":root tokens", "new — ink ramp, --red/--cyan lifted from the app's marker colours", True),
            (".grid / .reg (dot plane, registration ticks)", "new — atmosphere at ~0.3% coverage", True),
            (".spine", "new — rotated survey line down the left margin", True),
            ("#claim / .hero / #figure", "new — outlined 196px figure, deliberately small claim", True),
            (".chart / .bar / .seg", "new — plan view beside timing bars, WALK vs countdown vs wait", True),
            ("#notes / .stats / #yearline", "new — signal facts, three stats, the compounding line", True),
            (".cta / .qr / .here / .foot", "new — locator, ask, QR, boxed wordmark", True),
            ("body markup with id= slots", "new — what generate_wait_posters.py fills", True),
        ],
        "blocks": [
            "-webkit-text-stroke: 3px — an outline gives 196px of presence for a twentieth of the toner; 3px so a 300 dpi laser still holds the stroke",
            ".seg.flash paints #ffffff UNDER its own hatch, or it moires against the red hatch behind it",
            "The bar's solid cyan is only the 7-second WALK, not the whole green — that cut the largest ink block on the page by three quarters and made the argument stronger, since the countdown is not time you may start crossing in",
            "grid-template-columns: 100px 1fr — plan view and timing chart share a row per carriageway, so asphalt sits beside the seconds it costs",
            "Zero radius everywhere and Red Hat Mono throughout, matching globals.css where the comment reads 'Typography — monospace only'",
            "#claim is set small on purpose: from across the street you read the figure and the street name, and the claim is for whoever has already stopped",
            "margin-top:auto on .cta anchors the bottom block, so a one-stage diagram and a four-stage one both produce a full page",
        ],
    },
    "scripts/generate_wait_posters.py": {
        "on": ["Analysis + posters (offline)", "React / Leaflet client", "Flask API"],
        "module": ("Posters · generator (4/4)",
                   "Turns ranked rows into posters: the per-crossing diagram, the QR deep link, and the campaign rules that decide what each poster may claim"),
        "file": ("generate_wait_posters.py",
                 "~460 LOC — slot filling, chart/locator SVG, campaigns, claim and diversity rules"),
        "outline": [
            ("BASE_URL / QR_VOTE_TYPE / QR_SRC", "new — nyc-proposals + 'Fix signal timing' + src=qr-wait", True),
            ("fill()", "new — balanced-tag slot replacement", True),
            ("crossing_chart", "new — the per-crossing plan view + timing bars", True),
            ("site_notes", "new — cycle, stages, width, LPI/Barnes chips", True),
            ("city_path / locator", "new — borough silhouette, projected once", True),
            ("CAMPAIGNS + claim rules", "new — what each campaign is allowed to say", True),
            ("borough_targets / nabe_targets", "new — spread beyond Manhattan", True),
            ("build_poster / main", "new — QR, render, manifest, caps", True),
        ],
        "blocks": [
            "fill() scans for the MATCHING close tag: the chart and stats slots hold nested divs, and the prior campaign's non-greedy regex would close them at the first child and silently shred the layout",
            "BAR_H/BAR_GAP are keyed on stage count so a 1-stage and a 4-stage diagram occupy a similar block height",
            "The bar shows walk = min(7s, green) as solid and the remainder of the green as countdown — the same distinction the design depends on",
            "longest_wait claims NO ordinal: the wait model saturates at its four-stage ceiling, so a dozen crossings tie at 129s and '3rd longest' would be a coin toss printed as fact",
            "time_lost DOES claim a rank — person-hours is continuous and unties cleanly",
            "caps={'major': 2} — without it the longest-wait campaign is nine posters about Queens Boulevard; with it, six corridors share the claim",
            "MIN_PEDS_FOR_WAIT_CLAIM = 4000 — a 'longest wait in New York' poster has to be somewhere people actually cross",
            "city_path memoises the projected silhouette; only the crosshair moves per poster",
        ],
    },
    "scripts/build_wait_packet.py": {
        "on": ["Analysis + posters (offline)"],
        "module": ("Posters · packet",
                   "Front matter and assembly: the cover that states the models' limits, the placement checklist, the citywide map, and the PDF"),
        "file": ("build_wait_packet.py",
                 "~330 LOC — shared sheet CSS, cover, checklist, placement map, PDF"),
        "outline": [
            ("SHEET_CSS / wordmark", "new — poster typography carried onto the front matter", True),
            ("cover_sheet", "new — totals plus 'what the two numbers are'", True),
            ("checklist_pages", "new — tick box, code, crossing, wait; field notes", True),
            ("map_page", "new — borough outlines, campaign-coloured dots, label de-collision", True),
            ("build_packet", "new — front matter + posters into one PDF", True),
        ],
        "blocks": [
            "The cover reads the fit record straight out of wait_model_fit.json, so the R2 printed on paper cannot drift from the model that produced the ranking",
            "'Where it is weakest' is a required paragraph, not a footnote — retail-corridor calibration and subway-dominated volume are both stated on the cover",
            "Dataset ids (cqsj-cfgu, fwpa-qxaf, 8kuj-2n3u, xc4v-ntf4, 5wq4-mkjj) are printed so anyone can re-derive the ranking",
            "ROWS_PER_COL = 18: at 22 the second column ran off the bottom of the sheet",
            "Map labels are nudged down and tied back with leader lines — Downtown and Midtown stack six corners inside a few hundred metres",
            "Field notes ('before you go') use the space the tightened checklist freed: eye level, facing the crossing, don't cover signage",
        ],
    },
    "data/.gitignore": {
        "on": ["Analysis + posters (offline)"],
        "module": ("Repo hygiene", "Keeps regenerable poster output out of git, matching the existing campaign's convention"),
        "file": (".gitignore", "5 lines — raw downloads, poster work/out dirs"),
        "outline": [
            ("raw/ , posters/work/ , posters/out/", "unchanged — already excluded", False),
            ("posters/wait/", "+1 line — the new campaign's rendered output", True),
        ],
        "blocks": [
            "42 MB of PNGs and a 20 MB PDF, fully regenerable from the two committed scripts plus the CSV",
            "data/raw/ was already ignored, so the 88 MB of downloads needed no new rule",
            "The scripts, the template and the ranking CSV ARE committed — everything needed to rebuild the packet",
        ],
    },
}
