# Rekey + scale plan: stable edge IDs, <1.5s first load, ~300 concurrent on one server

> **STATUS 2026-07-22: steps 1–5 + 7 EXECUTED** (commits after `f6e6d5c`).
> Measured on local prod-shaped setup (gunicorn+gevent, prod client build):
>
> - **300 WebSocket clients**: connect+init 813ms, vote delta fan-out p95 **80ms**
>   (300/300), 20 sustained votes → 300/300 clients, **zero rev gaps**.
> - **100 simultaneous cold visitors** (worst-case stampede, no nginx cache):
>   heat p95 4.0s, topology p95 3.3s, tiles p95 2.7s — graceful, no errors.
> - **Cold first load** (prod build): heatmap **3.3s**, streets ~4.5s — the
>   heat gate is inflated by main-thread contention from the *background*
>   topology parse; **warm repeat visit: heatmap 1.4s**. The 1.5s target is
>   met warm; cold is ~2-3.5s visual on local infra (was ~7s at session start).
> - **eid registry seeded for nyc** — topology etag unchanged
>   (3801edb62737e4e3), proving existing votes unaffected. OSM refreshes are
>   unfrozen: refresh_osm.py updates the registry; rebuild tiles via
>   `python build_pmtiles.py --city <id>` afterwards.
> - Found during rollout: heat/route GL source pushes were deferred to the map
>   "load" event, which waits for every initial tile — early data now applies
>   as soon as the style parses (styledata), worth ~5s on cold loads.
> - Load test: `perf/loadtest.mjs` (results in perf/results/loadtest-*.json).
> - Step 6 (binary topology) intentionally deferred — biggest remaining lever
>   for the cold-load tail alongside viewport-lazy topology via eid'd tiles.

**Goals**
1. First load ≤1.5s to *streets visible + heatmap painted* on ordinary broadband (≥20Mbps).
2. Hundreds (~300) of concurrent connections — WebSockets + page loads + votes — on a
   single server instance without degradation.
3. Stable edge identity so OSM refreshes stop being frozen and the block-face survey
   redesign lands on a foundation, not a second migration.

Context: post-MapLibre-migration (see maplibre-migration.md). Measured baseline after the
2026-07-22 gzip/parallel-fetch work: prod-build cold load ≈ 2.2s streets / 4.7s heatmap;
topology 5.3MB gz on the wire; votes 57KB gz; client parse+index ≈ 1s.

---

## Part 1 — Edge identity: pin the keys, don't rewrite them

### The insight that makes this cheap

Today an edge's ID is its **array position** in the current topology build. Everything —
Redis field packing (24-bit edge id, `vote_store.py`), the Postgres `edge_votes` table,
WebSocket deltas, client vote arrays, localStorage my-votes — keys on that int. The int
is fine; the problem is only that it's **positional**, so any graph rebuild reassigns it.

So don't change the key type. **Make the assignment durable:**

- New Postgres table `edge_registry(map_city, stable_key, eid)` where
  `stable_key = (osm_way_id, segment_ordinal_within_way)` (fallback: rounded endpoint
  geometry hash for way-less edges) and `eid` is the dense small int used everywhere today.
- **Seed the registry from the current build with `eid := current index`.** Existing votes
  are correct by construction — no data rewrite, no checksum-risk migration.
- Graph builds (refresh_osm / python_router) become registry-aware: an edge that matches an
  existing `stable_key` keeps its `eid`; new edges allocate the next free `eid`; vanished
  edges retire theirs (votes preserved, unit unrenderable → surfaced in an admin report).
- Topology payload becomes eid-addressed. eids stay dense enough for arrays; tolerate holes
  (retired eids) with sparse guards.
- **PMTiles build emits `eid` on every edge feature.** This is the enabler for
  queryRenderedFeatures hit-testing, GL feature-state heat, and viewport-lazy topology —
  and for block faces later (a block face = a set of eids + side-of-street qualifier).

### What changes / what doesn't

| Layer | Change |
|---|---|
| Redis 24-bit packing | none (eids stay small ints) |
| Postgres edge_votes | none for existing rows; new `edge_registry` table |
| WS deltas, /api/vote, my-votes | none |
| Client vote arrays / voteApply / winners | none initially (eid == index at seed time) |
| Graph build pipeline | registry-aware eid assignment (the real work) |
| PMTiles build | emit eid per feature |
| graph-version semantics | topology etag still gates client caches; eids survive across versions |

### Verification protocol

- Invariant check before/after any rebuild: total votes per (street name, vote type)
  unchanged; sample N voted eids and diff their geometry.
- Keep `edge_votes` untouched; registry is additive. Rollback = ignore registry.

Effort: ~2–4 days, almost all in the Python build pipeline + tiler.

---

## Part 2 — The 1.5s first load

Budget on a 20Mbps / mid-range-device profile (numbers from measurement where available):

| Item | Today | Plan | Target |
|---|---|---|---|
| HTML + CSS + JS bundle | 325KB gz | precompress (brotli ~260KB), HTTP/2, immutable cache | ~450ms incl. parse |
| Style + first PMTiles batch | ~1.5–2MB, ~2.2s local | overzoom sooner (drop max tile zoom fetched at load), tile cache endpoint (below), HTTP/2 multiplex | ~700–900ms, progressive |
| Heatmap data | waits on 5.3MB topology → 4.7s | **new `/api/heat` endpoint**: server-built voted-edges GeoJSON from the (already cached) vote arrays + graph coords, gzipped ~50–150KB | paints with the tiles, ~1–1.2s |
| Votes arrays (57KB gz) | on critical path | background — only needed for cards/interactions | off path |
| Topology (5.3MB gz + 1s parse/index) | on critical path | **background fetch after first paint**; binary (Float64Array nodes / Uint32Array edges) later kills the 330ms parse and makes IndexedDB warm reads ~instant | off path |
| Reverse-geocode / graph-version probes | trivial | unchanged | — |

Result: streets + heat ≈ 1.2–1.5s; full interactivity (hover/snap) arrives ~2–3s in the
background without blocking anything visible. Longer term (with eids in tiles) hit-testing
can come from `queryRenderedFeatures` + a Flatbush built over loaded tile features, and the
full-topology download disappears entirely — fold that into the block-face redesign.

### Tile caching fix (helps repeat visits AND server load)

Browsers do not HTTP-cache range requests, so every visit re-downloads the PMTiles ranges.
Add `/tiles/<city>/<z>/<x>/<y>.mvt` (Flask, pmtiles reader) with
`Cache-Control: public, max-age=31536000, immutable` + a build-hash in the path, fronted by
nginx `proxy_cache`. Browser cache + nginx cache + (optionally) any CDN all start working.
MapLibre points at the z/x/y template instead of the pmtiles:// protocol. (~½ day.)

---

## Part 3 — Hundreds of concurrent connections on one box

Four concrete findings in the current server (file:line as of 7e176dd):

1. **Postgres: one global psycopg2 connection shared by all greenlets**
   (`server/database.py:20-36`). Not safe under concurrency — interleaved cursors on one
   connection will start failing/corrupting responses under real load, and every query
   blocks the whole gevent event loop (psycopg2 waits in C).
   **Fix**: `psycogreen`/wait_callback to make psycopg2 gevent-cooperative + a small
   `ThreadedConnectionPool` (2–10). ~½ day, correctness fix, do first.

2. **WebSocket loop busy-polls per client and opens a Redis connection per client**
   (`server/app.py:419-455`): `pubsub.get_message(timeout=0.1)` per client = 10 wakeups/s
   × N clients, plus N Redis connections. At 300 clients: 3,000 wakeups/s + 300 Redis conns
   on one event loop.
   **Fix**: one pubsub listener greenlet per (process, map-channel) fanning out to
   per-client `gevent.queue.Queue`s; client loops block on their queue (zero idle CPU,
   one Redis connection per channel). ~1 day.

3. **gunicorn `--workers 1`** (`deploy/supervisord.conf:15`). One gevent worker is fine for
   I/O concurrency but any CPU burst — building a vote cache after a cast (json.dumps +
   gzip of ~7MB), a python-fallback route calculation (rustworkx, can be seconds!) —
   stalls *every* connection including all WebSockets.
   **Fix**: (a) make OSRM the only prod routing path (python fallback dev-only or
   hard-capped); (b) `--workers 2` with `preload_app` + pre-warmed graphs so the big
   read-only graph memory is COW-shared across forks; (c) offload vote-cache gzip to a
   `gevent.spawn`-friendly chunked compressor or accept the ~100ms hit (cached per rev).

4. **nginx**: `worker_connections 1024` shared across HTTP+WS. Bump to 4096, add upstream
   `keepalive`, confirm `proxy_read_timeout` > WS keepalive (30s), enable http2 where TLS
   terminates locally. Trivial config change.

Redis itself (hincrby + pubsub) and the vote write path are nowhere near limits at this scale.

### Load-test verification (defines "done")

Script (k6 or a gevent script in `perf/`): against a prod-shaped container —
- 300 concurrent WS clients receiving a vote delta broadcast ≤250ms p95;
- 100 simultaneous cold page loads: `/api/heat` p95 < 300ms, tiles p95 < 500ms;
- 50 votes/s sustained: no delta gaps (client rev-gap refetch counter stays 0);
- CPU/memory headroom recorded.

---

## Sequencing (each step ships alone)

1. **`/api/heat` endpoint + defer topology** → hits the 1.5s visual budget. (~1 day)
2. **Postgres pool + psycogreen** → correctness under load. (~½ day)
3. **WS fanout rework** → idle-cheap sockets. (~1 day)
4. **Tile z/x/y cache endpoint + nginx cache/headers + worker bumps.** (~1 day)
5. **eid registry** (Part 1) → unfreezes OSM refresh; PMTiles emit eids. (~2–4 days)
6. **Binary topology** (optional, warm-load polish). (~1 day)
7. **Load test + perf-harness rerun**, record numbers in perf/results. (~½ day)

Steps 1–4 are identity-preserving and independent of the rekey. Step 5 is the transmission
rebuild; block-face survey work builds on its eids.
