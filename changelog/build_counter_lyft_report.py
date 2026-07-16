#!/usr/bin/env python3
"""Generate the Citibike counter-vote changelog report (2026-07-14).

Run from repo root: python changelog/build_counter_lyft_report.py
Reads changelog/changes-counter-lyft.diff (captured with:
  git diff dc81a6c -- osrm/bicycle-flat.lua scripts/build_bike_osrm.sh \
    server/counter_lyft.py server/tests/unit/test_counter_lyft.py \
    server/app.py server/database.py .gcloudignore \
    > changelog/changes-counter-lyft.diff  — dc81a6c is the pre-workstream base),
writes changelog/2026-07-14-citibike-counter-votes.html

Modeled on build_junction_disjoint_report.py (same styles + hierarchical
context diagrams).
"""
import html
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from diff_syntax import colorize_diff, SYNTAX_CSS  # noqa: E402
DIFF_PATH = os.path.join(HERE, "changes-counter-lyft.diff")
OUT_PATH = os.path.join(HERE, "2026-07-14-citibike-counter-votes.html")

DATE = "2026-07-14"
TITLE = "Citibike import correction — a bike-legality graph votes back"


def split_by_file(diff_text: str):
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


SECTIONS = [
    {
        "id": "why",
        "tag": "Diagnosis · imported votes",
        "title": "1 · Pedestrianized Citibike trips over-credit streets bikes already use",
        "symptom": (
            "The Lyft/Citibike ingest (<code>import_lyft.py</code>) routes every ride through the "
            "FOOT profile and upvotes each edge of that “pedestrianized” path. The heatmap therefore "
            "piles “Add bike lane” votes onto corridors a bike would have ridden anyway — worst "
            "around Citibike stations and along avenues that already carry bike lanes — drowning the "
            "actual signal: the stretches where riders would go if bikes could move like pedestrians."
        ),
        "cause": [
            "A foot route ignores one-way restrictions and cuts through parks, plazas and paths — "
            "that divergence IS the desired signal. But everywhere else it walks ordinary bikeable "
            "streets, and each of those edges got the same +1.",
            "Two graph facts make naive correction impossible: the votable graph stores "
            "<strong>parallel duplicate edges</strong> per node pair (3.30 M edges over 1.64 M node "
            "pairs — an id resolves to either copy), and NYC protected bike lanes are "
            "<strong>separate OSM ways</strong> running parallel to the roadway the foot route "
            "walked — same street, zero shared edge ids. Any id-based overlap test bottoms out at "
            "~3% and misses exactly the already-has-a-lane case (measured before switching to "
            "geometric coverage).",
        ],
        "fixes": [
            "Correct by <em>counter-voting</em>: for each ingested ride, determine which of its "
            "upvoted stretches a bike could have legally ridden, and cast <code>direction=-1</code> "
            "on those with the ride's own voter identity — the standard block-scoped clear-then-cast "
            "flips them to “against”, leaving pedestrian-only and counter-one-way stretches as the "
            "only surviving upvotes.",
            "Imports are recognized by identity, not bookkeeping: <code>import_lyft.py</code> casts "
            "with <code>voter_id = ride_id</code>, stored as <code>device_id = "
            "sha256(ride_id)[:16]</code> — hashing the ride ids in the cached monthly zips "
            "(<code>server/lyft_data/</code>) recovers exactly the imported devices (14,523 of "
            "14,538 nyc-bikes voters) plus each trip's endpoints. Human voters never match.",
        ],
        "files": [],
    },
    {
        "id": "graph",
        "tag": "OSRM · bike-legality dataset",
        "title": "2 · A bike-based routing graph that answers one question",
        "symptom": (
            "“Would a bike have been routed along this stretch?” needs a bicycle router — but the "
            "stock OSRM bicycle profile answers a different question (“what's the nicest ride?”): "
            "it prefers cycleways, penalizes surfaces, and will walk the bike through parks. Its "
            "routes shared almost nothing with the pedestrianized paths (2.6% edge overlap), and "
            "pushing-the-bike would let the router traverse the very pedestrian stretches that must "
            "survive."
        ),
        "cause": [
            "<code>bicycle.lua</code> v5.25.0 routes by duration with per-way speeds, so it detours "
            "to faster parallel streets; its <code>bike_push_handler</code> converts foot-only ways "
            "(footway / pedestrian / steps, <code>bicycle=dismount</code>) into slow "
            "<code>pushing_bike</code> edges — walking, exactly the mode the correction must not "
            "subtract; and ferries/platforms let trips teleport across rivers.",
        ],
        "fixes": [
            "<code>osrm/bicycle-flat.lua</code>: the image's stock profile (extracted verbatim from "
            "the pinned <code>osrm/osrm-backend:v5.25.0</code>) with three deltas — "
            "<code>weight_name = 'distance'</code> (shortest LEGAL path, no preference noise), "
            "<code>bike_push_handler</code> gutted (foot-only ways stay inaccessible; "
            "<code>dismount</code> ways are hard-excluded), and public transport / ferries / "
            "platforms off. One-way handling (incl. <code>oneway:bicycle</code> and contraflow "
            "cycleway tags) is untouched — counter-one-way riding stays illegal.",
            "<code>scripts/build_bike_osrm.sh &lt;city&gt; [--serve [port]]</code>: clips the city's "
            "own <code>server/osm_data/&lt;city&gt;/source.osm.pbf</code> to its "
            "<code>cities.py</code> bbox (osmium), runs extract/partition/customize with the flat "
            "profile in the pinned image, and serves it as "
            "<code>city-edit-osrm-bike-&lt;city&gt;</code> on host port 5006 (not 5000 — AirPlay — "
            "and not 5005, the merged foot instance). Building from the same PBF the votable graph "
            "was built from keeps every OSM node id aligned. Dataset lands in the gitignored "
            "<code>server/osm_data/&lt;city&gt;/osrm-bike/</code>; the NYC build takes ~4 min.",
        ],
        "files": ["osrm/bicycle-flat.lua", "scripts/build_bike_osrm.sh"],
    },
    {
        "id": "counter",
        "tag": "Server · counter-vote pass",
        "title": "3 · counter_lyft.py — via-guided routes, corridor coverage, votes against",
        "symptom": (
            "Even with a legality-only profile, a free endpoint-to-endpoint route can spare legal "
            "stretches by accident (grid ties: many equal-length paths), and edge-id overlap can't "
            "see sidewalk-vs-roadway or lane-vs-roadway parallelism at all."
        ),
        "cause": [
            "The foot route votes land on sidewalk/crosswalk ways; the bike rides the roadway "
            "centimeters-to-meters away on a different OSM way. Identity comparison is the wrong "
            "abstraction — the question is whether the bike route passes along the same corridor.",
        ],
        "fixes": [
            "<strong>Via-guided routing</strong>: each ride's voted edge set is re-ordered into a "
            "path (walking the edges from the degree-1 node nearest the trip start, preferring "
            "continuations over resnap spurs) and every ~8th node becomes an OSRM via-point. The "
            "distance-weighted router then hugs the ride's own corridor wherever riding is legal and "
            "detours only around the stretches it can't. Round trips / fragmented sets fall back to "
            "endpoint-to-endpoint.",
            "<strong>Geometric corridor coverage</strong>: a voted edge is countered when both "
            "endpoints AND its midpoint lie within 20 m of the bike route polyline (local "
            "equirectangular meters, vectorized numpy point-to-segment). 20 m spans a NYC avenue "
            "cross-section but is well under the ~80 m to the next parallel street; a crossing edge "
            "fails on its far endpoint. Pedestrian and counter-one-way stretches force the route a "
            "block away, so their edges are <em>structurally</em> un-subtractable.",
            "<strong>Casting</strong>: per (ride, vote type) one <code>POST /api/vote</code> with "
            "<code>direction=-1</code>, the ride's <code>voter_id</code>, and "
            "<code>ip_from_voter</code> — the same single vote codepath users hit, so block-scoped "
            "clear-then-cast, Redis counters, DB persistence, and WS deltas all behave normally. "
            "Re-running is a no-op (prior direction already −1).",
            "Verified against OSM ground truth on a 400-ride sample: cycleway edges countered "
            "hardest (86.5%), pedestrian/path/steps survive most (~25–29%), sidewalk-along-bikeable-"
            "roadway countered ~80% — the corridor logic sees through the parallel-way mapping.",
        ],
        "files": ["server/counter_lyft.py", "server/tests/unit/test_counter_lyft.py"],
    },
    {
        "id": "prod",
        "tag": "Prod rollout · 2026-07-15",
        "title": "4 · Prod rollout — and the resnap prod turned out to need first",
        "symptom": (
            "Dry-running the counter pass against the prod DB produced nonsense coverage on two "
            "maps (sf-bike-lanes 0.5%, chicago-bikes 4.8%) while nyc-bikes looked plausible — the "
            "kind of asymmetry that means the data, not the code, is wrong."
        ),
        "cause": [
            "Prod's stored votes were <strong>stale against prod's serving graphs</strong>: "
            "comparing each row's lat/lon anchor against its edge id's midpoint in the serving "
            "topology showed sf-bike-lanes 99.9% misaligned (median 4.5 km!), chicago-bikes 97.4%, "
            "nyc-bikes 33%, ny-bike-lanes/nyc-trees/nyc-bus-map ~100%, nyc-walkways 1.7%. A "
            "graph-shifting deploy had gone out without the resnap step "
            "(docs: resnap-on-deploy) — those heatmaps were painting votes on the wrong streets "
            "in prod, independent of this workstream.",
        ],
        "fixes": [
            "Full prod DB snapshot first (<code>~/city-edit-prod-backups/20260715T224016Z/"
            "prod-full.dump</code>, 43 MB, checksummed).",
            "<strong>Resnap driven from the operator machine</strong> with the serving image's own "
            "graph arrays (digest <code>050fd6b7…</code>, etags verified identical to live "
            "<code>/api/graph-votes</code>), through the repo's vote_migration machinery: atomic "
            "per-map DB rewrite → Redis aggregate rebuild via the Memorystore tunnel → "
            "<code>vote_rev</code> bumped +1000 so every cache invalidates. All seven misaligned "
            "maps healed to ≤0.4% residual; nyc-bikes merged only 20 collision rows of 1.77 M.",
            "Overlay code deploy (revision <code>desire-path-mapper-00098-7ft</code>, base pinned "
            "on the serving digest) shipping counter_lyft tooling + current branch state; "
            "<code>.gcloudignore</code> also learned to exclude 6.6 GB of blocks-bake artifacts "
            "that every Cloud Build had been uploading.",
            "Counter passes with <code>--graph-dir</code> (new flag: remote passes must use the "
            "TARGET deployment's graphs — DB edge ids live in its topology space): prod "
            "sf-bike-lanes 348/349 rides countered (72.9% of upvotes), prod chicago-bikes 239/239 "
            "(69.3%), prod nyc-bikes (see Verification), plus local sf (73.9%) and chicago "
            "(79.5%). Zero route/vote failures across every pass.",
            "Two more prod finds along the way: <strong>every cast on prod was paying a "
            "9.4-second full scan</strong> — <code>get_voter_type_rows</code> (the prior-state "
            "read behind clear-then-cast) had no usable index (the identity key leads with "
            "edge_id). New <code>idx_edge_votes_map_vt_device</code>, created CONCURRENTLY on "
            "prod mid-pass: 9,453&nbsp;ms → 0.198&nbsp;ms; human votes on big maps get the same "
            "win. And bulk casts now run through a <strong>scratch Flask wired to prod</strong> "
            "(prod graphs/blocks + tunnelled DB/Memorystore; app.py gained REDIS_PORT) rather "
            "than the public API — same code path, ~50× the throughput, block aggregates "
            "maintained correctly.",
            "The identity join also exposed a second, earlier prod import batch (3,647 "
            "import-marked devices, 314 k rows, cast 2026-05-29) whose ride ids appear in NO "
            "published Citibike file (probed 2025-12 → 2026-06 plus the JC extracts) — its "
            "voter_id preimages are simply gone. New <code>--reconstruct-unmatched</code> mode: "
            "the trip is recovered from the voted edges themselves (endpoints = farthest-apart "
            "midpoints, vias by nearest-neighbor chaining — twice-resnapped sets are "
            "topologically shredded, every edge disjoint, so reconstruction is geometric), "
            "routed in BOTH directions with coverage intersected, so the unknowable true "
            "direction can never counter a one-way stretch. Casting as a stored device without "
            "its hash preimage required a new admin-token-gated <code>admin_device_id</code> "
            "override in <code>/api/vote</code>. Deliberately conservative: ~36% of those "
            "upvotes countered vs ~77% for matched rides.",
        ],
        "files": ["server/app.py", "server/database.py", ".gcloudignore"],
    },
]

# Filled from the full nyc-bikes run (see scratch log counter-run-nyc.log).
VERIFY = [
    "<strong>Prod end state (2026-07-15)</strong>: nyc-bikes 1,243,406 rows against / 603,344 for "
    "(main pass: 11,060 matched rides, 77.0% of upvotes flipped; reconstructed pass: 3,634 of "
    "3,647 unpublished-ride imports, 35.8% — conservative by design; 12 residual failures "
    "≈ 0.06% of edges). sf-bike-lanes 34,425 / 12,590 (72.9%); chicago-bikes 20,818 / 8,825 "
    "(69.3%). Zero route failures everywhere. Live serving: 80% / 76% / 72% of voted edges now "
    "net-negative, block layers intact (n_blocks 289,647 / 45,712 / 140,248).",
    "Prod resnap verification: all seven misaligned maps healed to ≤ 0.4% anchor-vs-edge "
    "residual (was 33–100%); bd:/bagg:/bver purged per map and lazily rebuilt from Postgres "
    "(nyc-bikes rebuild: 174 s on-instance); revisions bumped so every cache invalidated.",
    "Prod deploy: revision <code>desire-path-mapper-00098-7ft</code> (overlay image "
    "<code>1e16f40f…</code> pinned on serving digest <code>050fd6b7…</code>), /health green, "
    "graph-votes serving with unchanged topology etags.",
    "Full nyc-bikes pass (two runs — the first was killed externally at ~8,800/14,523 rides; the "
    "pass is idempotent so the relaunch converged): <strong>0 route failures, 0 vote failures</strong> "
    "across all rides; 4,975 rides fully divergent (left alone, mostly round trips at one station). "
    "DB end state: of 1,790,211 nyc-bikes upvote rows, <strong>1,354,898 (75.7%) flipped to "
    "direction=−1</strong> under 14,240 imported devices, 399,335 remain +1, ~36 k unvoted by "
    "partial-block clears (one-direction-per-block invariant).",
    "Serving path: <code>/api/graph-votes?map=nyc-bikes&amp;mode=bikepaths</code> now returns "
    "144,957 net-negative voted edges (83.4%) vs 28,864 net-positive survivors; revision bumped "
    "(18080) via the normal publish_delta path — no cache surgery needed.",
    "Human votes untouched: the 15 unmatched devices and the human-suggested vote types "
    "(49, 74420, 74421, …) kept their rows bit-for-bit.",
    "Unit tests: <code>tests/unit/test_counter_lyft.py</code> — 9 passed (path ordering from either "
    "trip end, spur preference, loop/fragment fallbacks, corridor cover/crossing/parallel-street "
    "cases against COVER_DIST_M).",
    "Highway-type ground truth (400-ride sample, decoded from <code>walk_graph_arrays.npz</code> "
    "<code>edge_highway</code>): cycleway 86.5% countered / footway 79.9% / primary 81.3%, vs "
    "pedestrian 71.5%, path 75.9%, steps 74.5% — the survivors skew exactly toward walk-only ways.",
    "Local DB snapshot taken before the run: <code>~/city-edit-local-backups/"
    "20260714T171845Z/votes-pre-counter.dump</code> (edge_votes + vote_types, pg_dump -F c).",
]

CHECKLIST = [
    "Open <a href=\"http://localhost:3000/m/nyc-bikes\">http://localhost:3000/m/nyc-bikes</a>: avenues "
    "with existing bike lanes (8th/9th Ave, 1st/2nd Ave protected lanes) should now show "
    "<em>against</em>-dominated heat, while park crossings (Central Park transverses, plaza paths) "
    "and one-way streets ridden against the flow keep their <em>for</em> votes.",
    "Click a countered stretch: the block card should show the imported vote types with down-counts "
    "(the same devices that voted for now vote against).",
    "Spot-check the Hudson River Greenway: it's a real cycleway, so imported trips along it should "
    "be countered (it already routes bikes).",
    "Re-run <code>python counter_lyft.py --city nyc --map nyc-bikes --api-base "
    "http://localhost:5001 --dry-run --limit 200</code>: idempotency means the live pass changes "
    "nothing further (prior direction already −1).",
    "Run <code>cd server && ./env/bin/python -m pytest tests/unit/test_counter_lyft.py</code> — 9 tests.",
    "To restore the pre-run votes if the correction is unwanted: <code>pg_restore -h localhost -U app "
    "-d votes --clean --if-exists -t edge_votes ~/city-edit-local-backups/20260714T171845Z/"
    "votes-pre-counter.dump</code>, then rebuild Redis for the map.",
]


def li(items):
    return "\n".join(f"<li>{x}</li>" for x in items)


def file_detail_html(name, chunk, open_=False):
    added = sum(1 for ln in chunk.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in chunk.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return f"""
        <details{" open" if open_ else ""}>
          <summary><span class="fname">{html.escape(name)}</span>
            <span class="stat"><span class="d-add">+{added}</span> / <span class="d-del">-{removed}</span></span>
          </summary>
          {context_html(name)}
          <pre class="diff">{colorize_diff(chunk, name)}</pre>
        </details>
        """


def section_html(s, diff_by_file):
    file_rows = []
    for f in s["files"]:
        chunk = diff_by_file.get(f)
        if chunk is not None:
            file_rows.append(file_detail_html(f, chunk))
        else:
            file_rows.append(f'<ul class="files"><li>{html.escape(f)}</li></ul>')
    diffs_h = f"<h3>Diffs — files touched (click to expand)</h3>{''.join(file_rows)}" if s["files"] else ""
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
      {diffs_h}
    </section>
    """


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "osrm/bicycle-flat.lua": {
        "on": ["OSRM"],
        "module": ("OSRM profiles · osrm/", "the pinned way-acceptance rules each routing dataset is extracted with"),
        "file": ("bicycle-flat.lua", "~640 LOC — stock v5.25.0 bicycle.lua, three surgical deltas (header documents them)"),
        "outline": [
            ("Header", "provenance + the three deltas and why each exists", True),
            ("setup() properties", "weight_name 'duration' → 'distance'; use_public_transport off; ferry/platform speeds emptied", True),
            ("Speed / access tables", "bicycle_speeds, one-way + cycleway tags — UNTOUCHED (legality is stock)", False),
            ("way handlers", "classification pipeline — untouched", False),
            ("bike_push_handler", "GUTTED: no pushing_bike fallback; bicycle=dismount hard-excluded", True),
            ("safety/turn handlers", "cyclability-only branches (dead under 'distance') — untouched", False),
        ],
        "blocks": [
            "header comment — provenance (docker image v5.25.0) + delta list",
            "weight_name = 'distance' — route choice is shortest legal path",
            "use_public_transport=false, platform_speeds={}, route_speeds={} — no ferries/platforms",
            "bike_push_handler — foot-only ways stay inaccessible; dismount ⇒ inaccessible",
        ],
    },
    "scripts/build_bike_osrm.sh": {
        "on": ["OSRM"],
        "module": ("scripts/", "operator tooling that wraps the docker/osmium build machinery"),
        "file": ("build_bike_osrm.sh", "~90 lines — clip → extract → partition → customize → optionally serve"),
        "outline": [
            ("Header", "purpose (import corrections), node-id alignment contract, usage", True),
            ("City → bbox map", "kept in sync with cities.py, same duplication as osrm/build-merged.sh", True),
            ("Clip + build", "osmium extract to bbox → osrm-extract -p bicycle-flat.lua → partition → customize", True),
            ("Serve", "detached container city-edit-osrm-bike-<city> on :5006 (not 5000/5005)", True),
        ],
        "blocks": [
            "the whole file is new — per-city bike-legality dataset builder/server",
        ],
    },
    "server/counter_lyft.py": {
        "on": ["Flask API", "OSRM"],
        "module": ("Flask backend · server/", "bulk-import tooling: import_lyft.py casts the votes, counter_lyft.py subtracts the over-credit"),
        "file": ("counter_lyft.py", "~430 LOC — DB join → ride matching → via-guided bike routing → corridor coverage → /api/vote −1"),
        "outline": [
            ("Docstring", "the full correction model: identity, via-guidance, corridor coverage, idempotency", True),
            ("device_of / load_upvotes", "sha256(ride_id)[:16] identity; {device: {vt: edges}} from edge_votes", True),
            ("match_rides", "stream cached monthly zips, hash ride ids, recover trip endpoints", True),
            ("voted_path_vias", "re-order the voted edge set into a path; every ~8th node → via-point", True),
            ("bike_route_geometry / covered_edges", "flat-profile OSRM polyline; 20 m corridor test (endpoints + midpoint)", True),
            ("counter_one / run_async", "per (ride, vote type) POST /api/vote direction=-1, same voter_id; asyncio + stats", True),
            ("main()", "args, OSRM/backend health gates, mode resolution via /api/maps, summary", True),
        ],
        "blocks": [
            "identity join — DB device_ids ∩ sha256(ride_id) over lyft_data zips (imports only, humans never match)",
            "voted_path_vias — degree-1 anchor nearest trip start, continuation-over-spur walk, fragment fallback",
            "covered_edges — local-meters projection, vectorized min distance to route polyline, 3-point rule",
            "counter_one — via-guided route (endpoint fallback) → per-vt overlap → direction=-1 casts",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask backend · server/", "HTTP + WS routes; the single /api/vote codepath every cast goes through"),
        "file": ("app.py", "~2080 LOC — three surgical touches for operator runs + cast cost"),
        "outline": [
            ("Redis setup", "REDIS_PORT env honored (tunnelled Memorystore on :6380; 6379 is local dev)", True),
            ("_resolve_user", "admin-token-gated admin_device_id: act as a stored device whose voter_id preimage is gone", True),
            ("cast_vote — per-IP cap", "skipped for ip_from_voter casts (their ip is unique per voter by construction)", True),
            ("everything else", "routes, caches, warmup, WS — untouched", False),
        ],
        "blocks": [
            "redis_port = REDIS_PORT env (3 constructors)",
            "_resolve_user — admin_device_id verbatim when _admin_authorized()",
            "ip_device_counts skipped when ip_from_voter",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask backend · server/", "Postgres layer: schema migrations, vote reads/writes"),
        "file": ("database.py", "~800 LOC — one new index in the boot migration"),
        "outline": [
            ("init migrations", "idx_edge_votes_map_vt_device — serves get_voter_type_rows (9.4s → 0.2ms on prod nyc-bikes)", True),
            ("everything else", "untouched", False),
        ],
        "blocks": [
            "CREATE INDEX IF NOT EXISTS idx_edge_votes_map_vt_device (map_slug, vote_type_id, device_id)",
        ],
    },
    ".gcloudignore": {
        "on": ["nginx", "Flask API"],
        "module": ("deploy · build context", "what gcloud builds submit uploads"),
        "file": (".gcloudignore", "excludes 6.6 GB of blocks-bake artifacts + QGIS exports every build had been uploading"),
        "outline": [
            ("blocks artifacts + exports", "streetscape_blocks output/cache/env/eval + exports/", True),
        ],
        "blocks": [
            "server/streetscape_blocks/{output,cache,env,eval}/ + exports/",
        ],
    },
    "server/tests/unit/test_counter_lyft.py": {
        "on": ["Flask API"],
        "module": ("server tests · unit", "pure-function coverage, no OSRM/DB/Flask"),
        "file": ("test_counter_lyft.py", "~110 LOC — FakeGraph over synthetic coords; 9 tests"),
        "outline": [
            ("FakeGraph + line_graph", "the two CityGraph fields the helpers read", True),
            ("voted_path_vias tests", "ordering from trip start, spur preference, loop + fragment fallbacks", True),
            ("covered_edges tests", "on-route, 10 m parallel (covered), 80 m parallel street + crossings (survive)", True),
        ],
        "blocks": [
            "the whole file is new",
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
    diff_by_file = dict(files)
    claimed = {f for s in SECTIONS for f in s["files"]}
    leftover_blocks = "".join(
        file_detail_html(name, chunk) for name, chunk in files if name not in claimed
    )
    leftover_html = f"""
  <section id="diff">
    <h2 style="font-family:var(--font-ed);font-size:24px;margin:32px 0 10px;">Other files in this diff</h2>
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Changes not tied to a section above.</p>
    {leftover_blocks}
  </section>""" if leftover_blocks else ""
    diff_link = '<a href="#diff">Other diffs</a>' if leftover_blocks else ""

    sections_html = "\n".join(section_html(s, diff_by_file) for s in SECTIONS)
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
    border-top: 1px solid var(--hairline); font-family: var(--font-mono); font-size: 12px; line-height: 1.25; }}
  pre.diff span {{ display: block; white-space: pre; }}
  pre.diff span span {{ display: inline; }}
{SYNTAX_CSS}

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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code></div>
  </header>

  <nav class="toc">{nav}
    <a href="#verify">Verification</a><a href="#checklist">Checklist</a>{diff_link}
  </nav>

  <p class="lede">Imported Citibike trips are routed through the foot profile on purpose — the divergence
  between how a pedestrian moves and where a bike is allowed to go IS the vote. But the ingest upvoted the
  whole pedestrianized path, so corridors that already carry bikes (and especially streets that already
  have bike lanes, densest near Citibike stations) got the same +1 as the genuinely un-bikeable stretches.
  The correction: a second, bike-legality OSRM dataset (stock v5.25.0 bicycle profile flattened to
  shortest-legal-path, pushing-the-bike disabled) re-routes every ingested ride pinned to its own voted
  corridor via via-points; every upvoted edge lying bodily inside the resulting route's 20 m corridor gets
  a <code>direction=-1</code> cast from the ride's own voter identity. What survives upvoted is exactly
  what can't be ridden: park and plaza paths, stairs, and one-way streets taken against the flow.</p>

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

{leftover_html}

  <footer>
    Generated from <code>changelog/changes-counter-lyft.diff</code> by <code>changelog/build_counter_lyft_report.py</code>.
    Regenerate after further edits with
    <code>git diff dc81a6c -- osrm/bicycle-flat.lua scripts/build_bike_osrm.sh server/counter_lyft.py server/tests/unit/test_counter_lyft.py server/app.py server/database.py .gcloudignore &gt; changelog/changes-counter-lyft.diff &amp;&amp; python changelog/build_counter_lyft_report.py</code>.
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
