#!/usr/bin/env python3
"""Generate the vote-path-mitigations changelog report.

Run from repo root: python changelog/build_vote_mitigations_report.py
Reads changelog/changes-vote-mitigations.diff,
writes changelog/2026-07-09-vote-path-mitigations.html

Modeled on build_latency_opts_report.py (same styles + hierarchical context
diagrams). Companion to 2026-07-08-agent-load-test.html — this is the fix set
for that report's findings, measured with the same harness.
"""
import html
import os
import re

HERE = os.path.dirname(__file__)
DIFF_PATH = os.path.join(HERE, "changes-vote-mitigations.diff")
OUT_PATH = os.path.join(HERE, "2026-07-09-vote-path-mitigations.html")

DATE = "2026-07-09"
TITLE = "Vote-path mitigations — 160 agents from lockup to 45 votes/s"


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


def colorize(diff_chunk: str) -> str:
    out = []
    for raw in diff_chunk.splitlines():
        esc = html.escape(raw)
        if raw.startswith("+++") or raw.startswith("---"):
            cls = "d-meta"
        elif raw.startswith("@@"):
            cls = "d-hunk"
        elif raw.startswith("diff ") or raw.startswith("index "):
            cls = "d-meta"
        elif raw.startswith("+"):
            cls = "d-add"
        elif raw.startswith("-"):
            cls = "d-del"
        else:
            cls = "d-ctx"
        out.append(f'<span class="{cls}">{esc or "&nbsp;"}</span>')
    return "\n".join(out)


# Baseline = 2026-07-08 report; Final = same harness, same map, this code.
TIER_ROWS = [
    # tier, agents, metric rows: (label, baseline, final)
    ("20", [("vote p50", "1.76 s", "0.06–0.50 s"), ("vote p95", "2.10 s", "0.49 s"),
            ("throughput", "7.9 votes/s", "~20 votes/s (offered-limited)"), ("failures", "0", "0")]),
    ("40", [("vote p50", "3.79 s", "0.16 s"), ("vote p95", "4.26 s", "1.7 s"),
            ("throughput", "8.6 votes/s", "29.6 votes/s"), ("failures", "0", "0")]),
    ("80", [("vote p50", "8.37 s", "0.22 s"), ("vote p95", "9.54 s", "4.3 s"),
            ("throughput", "8.2 votes/s", "34.1 votes/s"), ("failures", "0", "6 clean 503s (1.3%)")]),
    ("160", [("vote p50", "16.57 s", "1.25 s"), ("vote p95", "45.63 s (= timeout)", "3.9 s"),
             ("throughput", "5.7 votes/s", "45.7 votes/s"), ("wall clock", "146.9 s", "20.4 s"),
             ("failures", "129 timeouts (13%) + 11 truncated bodies", "28 clean 503s (2.9%), 0 truncations"),
             ("RSS peak", "7.1 GB", "4.0 GB"), ("WS sends", "132,808", "19,385")]),
]


def tiers_html():
    out = []
    for tier, rows in TIER_ROWS:
        trs = "".join(
            f"<tr><td>{html.escape(l)}</td><td>{html.escape(b)}</td>"
            f"<td class='good'>{html.escape(f)}</td></tr>"
            for l, b, f in rows)
        out.append(
            f"<h3>{tier} agents</h3>"
            f"<table class='cmp'><thead><tr><th></th><th>2026-07-08 baseline</th>"
            f"<th>after mitigations</th></tr></thead><tbody>{trs}</tbody></table>")
    return "\n".join(out)


SECTIONS = [
    {
        "id": "m1",
        "tag": "Flask API · Redis write path",
        "title": "M1 · One pipeline per vote, not one round trip per edge",
        "symptom": (
            "~100&nbsp;ms of serialized work per vote capped the instance near 9 votes/s. Profiled: a "
            "145-edge route vote made ~300–550 sequential Redis round trips inside the vote lock."
        ),
        "cause": [
            "<code>apply_directional</code> built and <em>executed</em> a 2-command pipeline once per "
            "edge (~145 executes per vote).",
            "<code>apply_block_delta</code> issued 1–3 <em>unpipelined</em> HINCRBY/HDELs per edge "
            "(~150–400 more) because each edge's bagg move depends on that edge's returned multiplicity.",
        ],
        "fixes": [
            "<code>apply_directional_batch</code>: every edge-counter HINCRBY of the whole plan "
            "(clears then casts, plan order) in ONE pipeline execute.",
            "<code>apply_block_deltas_batch</code>: two phases — pipeline all bd: multiplicity "
            "HINCRBYs, then derive the bagg moves from the returned values. bagg moves on every "
            "presence-boundary crossing (opposite crossings cancel by summing); a device field is "
            "deleted only when the LAST op touching it left it ≤ 0, because a per-op HDEL could wipe "
            "a field a later cast in the same plan re-created (clear edge A + cast edge B, one block).",
            "Equivalence pinned by fakeredis tests comparing batch vs per-edge loop state "
            "byte-for-byte, including the clear-then-cast-same-block case.",
        ],
        "files": ["server/vote_store.py", "server/block_votes.py", "server/app.py"],
    },
    {
        "id": "m2",
        "tag": "Flask API · locking",
        "title": "M2 · The global vote lock becomes 256 per-voter stripes",
        "symptom": (
            "Vote latency grew exactly linearly with concurrent voters (queue depth × ~100 ms): all "
            "voters on all maps serialized through one process-wide <code>threading.Lock</code>."
        ),
        "cause": [
            "The lock only ever needed the scope the cross-instance Redis <code>voter_lock</code> "
            "already has: one voter's clear-then-cast read-modify-write on one map. Cross-voter "
            "exclusion protected nothing — counter updates are atomic HINCRBYs (batched by M1), DB "
            "rows are per-device, and the read-counts-then-publish interleaving it ordered was already "
            "unordered across prod instances.",
        ],
        "fixes": [
            "256 striped locks keyed by <code>hash((slug, device_id))</code> — same-voter casts still "
            "serialize (a rapid ± toggle can't read a stale prior direction); different voters "
            "proceed concurrently. Verified live: 20 concurrent alternating-direction casts from one "
            "voter converge with exact per-edge deltas (+1 cast / −1 remove, state restored).",
            "<code>DB_POOL_MAX</code> env override (default 5 unchanged) — the pool becomes the write "
            "path's throughput governor once voters run concurrently.",
        ],
        "files": ["server/app.py", "server/database.py"],
    },
    {
        "id": "m3",
        "tag": "Flask API + client · graph-votes",
        "title": "M3 · Debounced, pre-gzipped snapshots (37 MB → 0.75 MB on the wire)",
        "symptom": (
            "Every vote bumped the revision AND hard-purged the snapshot cache, so under sustained "
            "voting every /api/graph-votes fetch rebuilt the full 37 MB dense-JSON body — and 160 "
            "concurrent joiners buffered gigabytes (RSS 3.5→7.1 GB) and truncated 11 bodies mid-stream."
        ),
        "cause": [
            "Cache keyed by exact revision + per-vote invalidation = a rebuild per cast under load.",
            "Un-compressed 37 MB responses, buffered per request.",
            "Each rebuild held the GIL ~2 s (stdlib json.dumps + gzip level 6 per REQUEST would have "
            "been worse still) — the p95 spikes on unrelated endpoints.",
        ],
        "fixes": [
            "Snapshot entries {rev, bstamp, body, gz, built}: orjson serialization (~10× faster) + "
            "ONE gzip (level 3; zlib releases the GIL) per rebuild, shared by every client.",
            "Debounce: a snapshot serves while rev-current OR for GRAPH_VOTES_DEBOUNCE (2 s) after "
            "staling — one rebuild per window, not per cast. Stale-while-revalidate: one caller "
            "rebuilds, the rest keep serving the old snapshot.",
            "The ETag derives from the SERVED entry's rev + blocks-stamp (+ encoding) so the tag "
            "always describes the body it rides with; an entry with a stale blocks stamp never "
            "serves. Per-vote invalidation is gone — hard invalidation remains for structural "
            "changes (resnap, block re-bake).",
            "Client: a bounded ring of recently received deltas replays over every installed "
            "snapshot (idempotent SET counts), so a debounced (slightly old) body can't regress "
            "counts or trigger a gap-refetch loop; a body+ring hole schedules ONE delayed refetch.",
        ],
        "files": ["server/app.py", "client-react/src/components/GraphLayer/GraphLayer.tsx",
                  "server/requirements.in", "server/requirements.txt"],
    },
    {
        "id": "m4",
        "tag": "Flask API + client · WebSocket",
        "title": "M4 · One pubsub listener, coalesced deltas — O(votes×clients) sends gone",
        "symptom": (
            "Every WS client held its own Redis pubsub connection and 10 Hz poll thread; every vote "
            "was sent to every client individually — 132,808 sends at the 160-agent tier, and the "
            "GIL contention pushed unrelated routes p95 from 30 ms to 2.1 s."
        ),
        "cause": [
            "Per-client fan-out did O(votes × clients) work; per-client pubsub connections and poll "
            "loops multiplied threads and Redis connections by the audience size.",
            "New with M2: concurrent casts claim revs by INCR then publish, so deltas can publish "
            "out of rev order — each inversion tripped the client's gap detector into a full 33 MB "
            "refetch.",
        ],
        "fixes": [
            "<code>delta_hub.py</code>: ONE listener thread psubscribes vote_deltas:*, buffers per "
            "map, and flushes merged {type:\"deltas\", rev, items:[…]} to registered client queues "
            "every 100 ms. Coalescing is lossless (counts are authoritative SETs).",
            "Inversions sort within a window; a batch whose lowest rev leaves a hole HOLDS 300 ms "
            "for the straggler, then flushes and lets the client's gap detection decide (a manual "
            "<code>INCR vote_rev:&lt;slug&gt;</code> still surfaces as one refetch).",
            "A slow client that overflows its bounded queue gets its socket closed; reconnect + gap "
            "refetch resyncs it. WS handlers now block on their queue — no per-client Redis "
            "connection, no 10 Hz poll.",
            "Client socket layer unwraps merged batches into individual rev-ordered deltas — "
            "GraphLayer's gap/apply logic untouched.",
        ],
        "files": ["server/delta_hub.py", "server/app.py",
                  "client-react/src/context/WebSocketContext.tsx"],
    },
    {
        "id": "m5",
        "tag": "Flask API · backpressure",
        "title": "M5 · Bounded queue + load shedding + the metrics /health can't show",
        "symptom": (
            "At the July-8 lockup, /health stayed at 1–2 ms while votes queued past 45 s — "
            "monitoring keyed on health was blind, and requests abandoned by their clients kept "
            "burning the server."
        ),
        "cause": [
            "Nothing bounded the vote queue; nothing measured it.",
        ],
        "fixes": [
            "At most VOTE_MAX_INFLIGHT (64) votes run concurrently; a vote may wait "
            "VOTE_QUEUE_WAIT_SECONDS (1.5 s) for a slot, then sheds with 503 + Retry-After: 1 "
            "(clients roll back optimistic casts). A same-voter stripe not acquired in "
            "VOTE_LOCK_SHED_SECONDS (2 s) sheds with Retry-After: 2.",
            "Instant-shed was too eager (58% of a 160-agent wave); the bounded wait turns the cap "
            "into a short queue — the same wave now completes 97% at p50 1.25 s.",
            "/api/admin/stats gains the saturation signals: rolling-60s votes/s, lock-wait and "
            "handler p50/p95, shed counts by reason, WS subscriber count, snapshot-cache "
            "{entries, mb, budget_mb}, and peak RSS.",
        ],
        "files": ["server/app.py"],
    },
    {
        "id": "m6",
        "tag": "Flask API · memory / prod fit",
        "title": "M6 · Byte-budgeted snapshot cache + measured prod headroom",
        "symptom": (
            "With snapshots persisting across votes (M3), the old 64-ENTRY cache cap could quietly "
            "hold ~2.4 GB of NYC-sized bodies on prod's 8 GiB instances."
        ),
        "cause": [
            "An entry count says nothing about footprint: an NYC snapshot is ~37 MB, a small city's "
            "a few hundred KB.",
        ],
        "fixes": [
            "The LRU now evicts against VOTE_CACHE_MAX_MB (512 default), counting body+gz per entry; "
            "invalidation keeps the byte counter exact; admin stats report it.",
            "Measured for prod sizing (8 GiB / max_loaded=3 / 1–4 instances): base 211 MB, +NYC "
            "graph 3.35 GB, +Chicago graph 1.22 GB, both snapshots cached = 4.84 GB total — ~2 GB "
            "headroom with a third mid-size city resident. Under the 160-agent tier RSS peaked at "
            "4.0 GB vs 7.1 GB before.",
            "Overlay deploys re-sync Python deps from the lockfile (base image predates orjson — "
            "code-only overlays would have crashed at boot).",
        ],
        "files": ["server/app.py", "Dockerfile.overlay"],
    },
]

VERIFY = [
    "Server suite green after every step (62 tests at the end: vote-count batch equivalence, "
    "block-delta batch equivalence incl. clear-then-cast-same-block, 7 delta-hub tests, "
    "multi-agent convergence).",
    "Same-voter race probe: 20 concurrent alternating casts from one voter — all 200s, exact "
    "per-edge count deltas on cast and remove, state restored.",
    "Live WS probe: 5 concurrent casts arrive as ONE merged message with contiguous ordered revs.",
    "graph-votes: gzip wire size 37.4 MB → 0.56–0.75 MB; 304s intact; debounce verified "
    "(back-to-back votes serve the fresh snapshot's rev inside the window, the new rev after).",
    "Shed valve: with VOTE_MAX_INFLIGHT=2, 12 concurrent casts → exactly 2×200 + 10×503 "
    "(Retry-After: 1); at defaults a 40-agent tier sheds nothing.",
    "Full tier ladder rerun with the July-8 harness on the same map — table above.",
    "Prod DB backed up before deploy (pg_dump full + per-table CSVs + checksums, "
    "~/city-edit-prod-backups/20260709-054934/, 668,651 edge_votes, dump verified with "
    "pg_restore -l).",
]

CHECKLIST = [
    "Open <a href='http://localhost:3000/m/agent-test-1'>http://localhost:3000/m/agent-test-1</a> — "
    "cast a route vote, reverse it, remove it: the heat and block counts should track each press "
    "with no drift.",
    "Open the same map in two windows and vote in one — the other should update within ~100 ms "
    "(one merged WS delta), and its console (dbg tab) should show 'deltas' messages, not per-vote "
    "'delta' spam.",
    "curl -H 'Accept-Encoding: gzip' -I 'localhost:5001/api/graph-votes?map=agent-test-1&mode=walk' "
    "— expect Content-Encoding: gzip, an ETag ending in -gz, and a sub-MB Content-Length.",
    "curl localhost:5001/api/admin/stats — the votes/ws/graph_votes_cache sections carry the new "
    "saturation metrics.",
    "On prod after the deploy: cast a vote on a live map, watch it appear in a second browser; "
    "check /api/admin/stats shows the new sections (the deploy marker).",
    "rerun server/tests/loadtest_votes.py --agents 160 against local dev whenever the vote path "
    "changes again — the harness is the regression instrument for all of this.",
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


SYSTEM_NAME = "City Edit"
SYSTEM_COMPONENTS = ["nginx", "Flask API", "OSRM", "Redis", "React / Leaflet client"]

FILE_CONTEXT = {
    "server/vote_store.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · vote_store.py", "the Redis vote codec + write/read path (ev:<slug> hashes, deltas, revisions)"),
        "file": ("vote_store.py", "~450 LOC — packing, apply/read, publish_delta, build_arrays"),
        "outline": [
            ("codec (pack/unpack, dir bit)", "53-bit field layout", False),
            ("write path", "apply_directional + NEW apply_directional_batch (one pipeline per plan)", True),
            ("publish_delta", "rev INCR + per-vote pubsub (unchanged; hub coalesces at delivery)", False),
            ("read path", "read_edge_vt_counts (already pipelined) · build_arrays", False),
        ],
        "blocks": [
            "apply_directional_batch — all HINCRBYs of a plan, plan order, one execute",
        ],
    },
    "server/block_votes.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · block_votes.py", "deduped per-block projection of edge votes (bd:/bagg: hashes)"),
        "file": ("block_votes.py", "~350 LOC — incremental write path, read path, rebuild_from_db"),
        "outline": [
            ("key helpers + packing", "bd:/bagg: layout", False),
            ("write path", "apply_block_delta (kept) + NEW apply_block_deltas_batch (two-phase pipelined)", True),
            ("read path", "build_block_arrays · read_block_vt_counts", False),
            ("rebuild", "clear + rebuild_from_db", False),
        ],
        "blocks": [
            "apply_block_deltas_batch — phase A: all bd: HINCRBYs pipelined; phase B: bagg moves from boundary crossings, HDEL only on final multiplicity ≤ 0",
        ],
    },
    "server/app.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · app.py", "all HTTP endpoints + the vote codepath, WS handler, snapshot cache"),
        "file": ("app.py", "~2100 LOC — this diff touches the vote transaction, the graph-votes cache/endpoint, the WS handler, and admin stats"),
        "outline": [
            ("imports · config", "+gzip, orjson, queue, resource, deque; delta_hub", True),
            ("snapshot cache", "byte-budgeted LRU {rev,bstamp,body,gz,built} + debounce + peek", True),
            ("vote locks + metrics", "global lock → 256 (slug,device) stripes; inflight valve; rolling metrics", True),
            ("delta listener", "per-vote cache invalidation DELETED (rev-check + debounce subsume it)", True),
            ("WS handler", "per-client pubsub+poll → blocks on its delta_hub queue", True),
            ("cast_vote", "batched Redis writes · bounded-wait shed · no cache purge", True),
            ("graph-votes endpoint", "serve current-or-debounced snapshot, entry-derived ETag, pre-gzipped body", True),
            ("admin stats", "votes/ws/cache/RSS saturation sections", True),
            ("maps/routes/admin endpoints", "unchanged", False),
        ],
        "blocks": [
            "_VOTE_CACHE_MAX_MB byte-budget LRU + _vote_cache_peek + _entry_bytes",
            "_VOTE_LOCK_STRIPES(256) + _voter_lock_stripe",
            "_vote_inflight semaphore + _shed_response + _vote_metrics_record/_snapshot",
            "delta_hub instantiation (one listener per process)",
            "ws() — subscribe/unsubscribe, queue-blocking send loop, overflow close",
            "cast_vote — apply_directional_batch + apply_block_deltas_batch under the stripe",
            "graph_votes() — debounce + stale-while-revalidate + gzip/identity + entry ETag",
            "admin_stats — votes/ws/graph_votes_cache/peak_rss_mb",
        ],
    },
    "server/delta_hub.py": {
        "on": ["Flask API", "Redis"],
        "module": ("Flask API · delta_hub.py", "NEW — per-process fan-out hub for vote deltas"),
        "file": ("delta_hub.py", "~180 LOC — listener thread, per-map buffers, merged flushes, subscriber queues"),
        "outline": [
            ("Subscriber", "bounded queue + overflow Event per WS client", True),
            ("DeltaHub", "subscribe/unsubscribe · listener loop · _ingest/_flush (sync, unit-tested)", True),
        ],
        "blocks": [
            "_ingest — parse, drop if no local subscriber, buffer per map",
            "_flush — sort by rev, hole-grace hold (300ms), merged payload to every queue, overflow → flag",
        ],
    },
    "server/database.py": {
        "on": ["Flask API"],
        "module": ("Flask API · database.py", "Postgres persistence (pool, votes, maps)"),
        "file": ("database.py", "~1000 LOC — one-line change"),
        "outline": [("connection pool", "DB_POOL_MAX env override (default 5)", True)],
        "blocks": ["_POOL_MAX = int(os.environ.get('DB_POOL_MAX', '5'))"],
    },
    "server/requirements.in": {
        "on": ["Flask API"],
        "module": ("Flask API · deps", "hand-written top-level dependency list (uv two-file flow)"),
        "file": ("requirements.in", "20 lines — one addition"),
        "outline": [("deps", "+ orjson", True)],
        "blocks": ["orjson — C-speed JSON for the 37MB snapshot rebuild"],
    },
    "server/requirements.txt": {
        "on": ["Flask API"],
        "module": ("Flask API · deps", "uv-compiled lockfile"),
        "file": ("requirements.txt", "compiled from requirements.in"),
        "outline": [("pins", "+ orjson==3.11.9", True)],
        "blocks": ["orjson==3.11.9"],
    },
    "server/tests/unit/test_vote_counts.py": {
        "on": ["Flask API"],
        "module": ("Flask API · tests", "Redis vote-count logic on fakeredis"),
        "file": ("test_vote_counts.py", "+2 tests"),
        "outline": [("batch path", "batch ≡ per-edge loop, byte-identical hashes; empty/no-op plans", True)],
        "blocks": ["test_batch_matches_loop_for_mixed_plan", "test_batch_empty_plan_is_noop"],
    },
    "server/tests/unit/test_block_votes.py": {
        "on": ["Flask API"],
        "module": ("Flask API · tests", "block projection invariants on fakeredis"),
        "file": ("test_block_votes.py", "+4 tests"),
        "outline": [("batch path", "route vote ≡ loop; clear-then-cast keeps field; full clear cleans; unmapped skipped", True)],
        "blocks": [
            "test_batch_matches_loop_route_vote",
            "test_batch_clear_then_cast_same_block_keeps_field",
            "test_batch_full_clear_removes_field_and_aggregate",
            "test_batch_skips_unmapped_and_noop",
        ],
    },
    "server/tests/unit/test_delta_hub.py": {
        "on": ["Flask API"],
        "module": ("Flask API · tests", "NEW — delta hub merge/ordering semantics, no threads or timing"),
        "file": ("test_delta_hub.py", "7 tests driving _ingest/_flush synchronously"),
        "outline": [("hub", "merge order · inversion absorb · hole-grace hold/expiry · isolation · overflow", True)],
        "blocks": ["seven tests pinning the merged-delivery contract"],
    },
    "client-react/src/components/GraphLayer/GraphLayer.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · GraphLayer", "the heatmap layer: votes, deltas, hover/click, proposals"),
        "file": ("GraphLayer.tsx", "~4000 LOC — this diff touches the delta/refetch reconciliation only"),
        "outline": [
            ("refs & state", "NEW recentDeltasRef ring (500) beside pendingDeltasRef", True),
            ("fetchVotes", "replays ring+pending deltas newer than the installed body; hole → ONE delayed refetch", True),
            ("WS delta handler", "pushes every received delta into the ring before gap check", True),
            ("hover/click/proposals", "unchanged", False),
        ],
        "blocks": [
            "recentDeltasRef — bounded ring of received deltas",
            "fetchVotes — install body → replay newer deltas (idempotent SETs) → hole ⇒ delayed refetch",
            "delta handler — ring push (incl. gap-triggering deltas, so the refetch can replay them)",
        ],
    },
    "client-react/src/context/WebSocketContext.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · WebSocketContext", "the /ws connection + delta subscription API"),
        "file": ("WebSocketContext.tsx", "~130 LOC"),
        "outline": [
            ("onmessage", "NEW: unwrap {type:'deltas', items} into rev-ordered individual deltas", True),
            ("reconnect/backoff", "unchanged (overflow-closed sockets land here)", False),
        ],
        "blocks": ["'deltas' branch — sort items by rev, dispatch each to subscribers"],
    },
    "client-react/src/components/MapLibreBackground/MapLibreBackground.tsx": {
        "on": ["React / Leaflet client"],
        "module": ("Client · MapLibreBackground", "the MapLibre GL basemap + block-heat styling"),
        "file": ("MapLibreBackground.tsx", "build fix only — no behavior change"),
        "outline": [
            ("blockFillPaint/blockLinePaint", "heat expression typed as ExpressionSpecification (was readonly as-const)", True),
            ("buildStyle", "dead graphTilesUrl param underscored", True),
        ],
        "blocks": ["two pre-existing tsc-build errors fixed to unblock the prod client build"],
    },
    "Dockerfile.overlay": {
        "on": ["nginx", "Flask API"],
        "module": ("Deploy · Dockerfile.overlay", "code-only overlay build reusing the base image's baked graphs"),
        "file": ("Dockerfile.overlay", "+5 lines"),
        "outline": [
            ("client build stage", "unchanged", False),
            ("overlay stage", "NEW: uv pip install -r requirements.txt before copying code", True),
        ],
        "blocks": ["dep re-sync — an overlay shipping code that imports a package the base predates (orjson) would crash at boot"],
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
  table.cmp {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 13.5px; }}
  table.cmp th {{ text-align: left; border-bottom: 2px solid var(--ink); padding: 6px 10px; font-weight: 600; }}
  table.cmp td {{ border-bottom: 1px solid var(--hairline); padding: 6px 10px;
    font-family: var(--font-mono); font-size: 12.5px; font-variant-numeric: tabular-nums; }}
  table.cmp td:first-child {{ font-family: var(--font-ui); color: var(--muted); }}
  table.cmp td.good {{ color: #06402b; font-weight: 600; }}
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
    <div class="dateline">{DATE} · branch <code>fix/unify-voting</code> · fix set for
      <a href="./2026-07-08-agent-load-test.html">the 2026-07-08 agent load test</a></div>
  </header>

  <nav class="toc">{nav}
    <a href="#results">Before / after</a><a href="#verify">Verification</a><a href="#checklist">Checklist</a><a href="#diff">Full diff</a>
  </nav>

  <p class="lede">The July-8 load test found the vote pipeline capped at ~9 votes/s — every cast
  serialized through one process-wide lock holding ~300–550 sequential Redis round trips and a
  synchronous Postgres write, every cast invalidated the 37&nbsp;MB graph-votes body, and every cast
  was fanned out to every WebSocket client individually. At 160 agents the app functionally locked
  up while /health stayed green. This lands the whole mitigation plan (M1–M6): batched Redis writes,
  per-voter lock striping, debounced pre-gzipped snapshots with client-side delta replay, one
  coalescing pubsub hub for all WS clients, a bounded vote queue that sheds with 503 + Retry-After,
  the saturation metrics /health can't show, and a byte-budgeted snapshot cache sized against
  prod's 8&nbsp;GiB instances. Same harness, same map: 160 agents now complete 97% of casts at
  p50 1.25&nbsp;s / p95 3.9&nbsp;s and 45.7 votes/s — an 8× throughput lift with less than half the
  peak memory.</p>

  <section class="card" id="results">
    <div class="tag">Measured — server/tests/loadtest_votes.py, map agent-test-1</div>
    <h2>Before / after by tier</h2>
    {tiers_html()}
    <p style="color:var(--muted);font-size:13px;">Baseline numbers are the 2026-07-08 report's
    (Werkzeug dev server, same machine, same harness, same map). 20-agent throughput is
    offered-load-limited (agents pace 0.1–0.5&nbsp;s between casts) — the ceiling above 8–9/s only
    becomes visible from 40 agents up. 503s are the new bounded-queue shed: instant clean failures
    with Retry-After that a real client retries, versus the baseline's 45&nbsp;s hangs.</p>
  </section>

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
    <p style="color:var(--muted);font-size:14px;margin-top:0;">Green is added, red removed. Click a file to expand.</p>
    {''.join(diff_blocks)}
  </section>

  <footer>
    Generated from <code>changelog/changes-vote-mitigations.diff</code> by
    <code>changelog/build_vote_mitigations_report.py</code>. Regenerate with
    <code>git diff ca50ca5..HEAD &gt; changelog/changes-vote-mitigations.diff &amp;&amp;
    python changelog/build_vote_mitigations_report.py</code>.
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
