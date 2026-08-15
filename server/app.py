import gzip
import json
import logging
import os
import queue
import re
import resource
import sys
import time
import hashlib
import threading
from collections import OrderedDict, deque
from functools import lru_cache

import orjson
import redis
import requests
from dotenv import load_dotenv
from pmtiles.reader import Reader as PMTilesReader, MmapSource
from pmtiles.tile import Compression

load_dotenv()

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
# flask-sock's transport. Raised by ws.receive() the moment the peer closes —
# the WS loop needs the type, not a substring match on the message.
from simple_websocket import ConnectionClosed
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

import vote_store
import vote_semantics
import vote_migration
import block_votes
import database
import delta_hub as delta_hub_mod
import presence
import view_counts
from cities import CITIES, DEFAULT_CITY_ID, get_city, all_cities
from graph_registry import GraphRegistry, OsrmRegistry, STATION_NETWORKS
from osrm_router import extract_all_segments
from database import (
    init_db, get_cursor, record_edge_votes, delete_edge_votes,
    get_voter_edge_directions, get_voter_type_rows,
    count_devices_per_ip_for_edges, count_unique_voters_for_edges,
    count_unique_voters_for_edge_sets,
    evict_lru_devices_for_edges,
    seed_presets, backfill_vote_type_kinds, normalize_point_type,
    list_maps, get_map, get_map_by_subdomain, slug_available, get_map_redirect,
    create_map, get_map_passcode_hash, list_vote_type_lists, set_map_subdomain,
    set_vote_sources, fetch_vote_sources_for_edges,
    promote_vote_types, fetch_voted_vote_type_labels,
    get_sticker, resolve_sticker,
    DATABASE_URL,
)
from vote_identity import counting_identity

# ── Logging ────────────────────────────────────────────────────────────────

# Every server log line carries a [TAG] prefix ([VOTE], [GRAPH], [DB], …) —
# the tag table + debugging workflow live in docs/debugging.md. LOG_LEVEL=DEBUG
# turns on the chattier lines without a code change.
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format='%(message)s', stream=sys.stdout,
)
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(line_buffering=True)

logger.info("[STARTUP] Multi-city: OSRM-per-city router + per-city Python graph provider")

# ── Flask ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)
sock = Sock(app)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
_passcode_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="map-passcode")
PASSCODE_TOKEN_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# Gate for state-changing admin endpoints (e.g. assigning vanity subdomains).
# When unset, those endpoints are disabled (403) rather than silently open.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

# Soft per-IP abuse cap, guarding the EDGE layer. device_id stays the hard dedup
# key for storage, but a single IP may hold at most this many DISTINCT devices
# voting a given (map, edge, vote_type). This is a speed bump against one person
# clearing localStorage to mint fresh device_ids and re-vote the same edge —
# while a threshold > 1 still lets a few genuine people behind one NAT/router
# each vote. Imports key ip_hash per-ride (ip_from_voter), so they're unique on
# both axes and never trip this.
#
# The BLOCK layer no longer needs this cap to stay honest: since 2026-08-13 it
# dedupes on the counting identity (vote_identity.py), so the extra devices this
# cap tolerates collapse to one voter there anyway. The cap still matters for
# the raw edge counters in `ev:`, which carry no identity at all and would
# otherwise show one person's fragmented ids as N votes on one edge.
#
# Past the cap a fresh device doesn't add a vote (which would let one IP inflate
# a total without bound) — it takes over the IP's least-recently-active device
# vote on that edge (evict_lru_devices_for_edges), so the voter's own state
# registers the vote while the total stays put.
MAX_DEVICES_PER_IP_PER_EDGE = int(os.environ.get("MAX_DEVICES_PER_IP_PER_EDGE", "10"))

# Share of a route's consecutive OSM node pairs that must resolve to votable edges
# before /api/routes stops complaining. Below this the topology is not the superset
# of OSRM's foot network it's supposed to be (docs/algorithms/04-route-finding.md),
# and every unmapped stretch silently drops its votes. Not 1.0: OSRM legitimately
# repeats a node at a via/turn, so a route or two short of perfect is normal.
ROUTE_EDGE_COVERAGE_MIN = 0.8


def _admin_authorized() -> bool:
    """True when ADMIN_TOKEN is configured and matches the request header."""
    return bool(ADMIN_TOKEN) and request.headers.get("X-Admin-Token") == ADMIN_TOKEN

DEFAULT_MAP_SLUG = "nyc-walkways"

# ── Redis ──────────────────────────────────────────────────────────────────

redis_host = os.environ.get('REDIS_HOST', 'localhost')
# REDIS_PORT matters for operator runs against a tunnelled prod Memorystore
# (e.g. localhost:6380) where 6379 is the local dev instance.
redis_port = int(os.environ.get('REDIS_PORT', '6379'))
redis_client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)

logger.info(f"[REDIS] Connecting to Redis at: {redis_host}:{redis_port}")
try:
    redis_info = redis_client.info("server")
    logger.info(f"[REDIS] Connected — v{redis_info.get('redis_version', '?')}")
except redis.ConnectionError as e:
    logger.error(f"[REDIS] Could not connect: {e}")

# ── Registries (per-city graph + OSRM) ───────────────────────────────────────

# All four cities (nyc, sf, chicago, dc) plus the station networks stay resident
# (no cross-city reload latency). This fits comfortably under 16Gi now that
# python_router drops the heavy networkx graph after load (compact arrays instead)
# and carries no routing graph at all — OSRM serves every route. DC's graph is
# small (~SF size); station networks are tiny (tens of nodes).
graph_registry = GraphRegistry(redis_client=redis_client, max_loaded=len(CITIES) + len(STATION_NETWORKS))
osrm_registry = OsrmRegistry()

# ── Database + vote types ──────────────────────────────────────────────────

init_db()
seed_presets()
backfill_vote_type_kinds()  # repair route/point kinds missing on legacy rows

if DATABASE_URL:
    try:
        vote_store.load_vote_types()
    except Exception as e:
        logger.error(f"[STARTUP] Failed to load vote types: {e}")


# ── Map resolution ───────────────────────────────────────────────────────────

class ResolvedMap:
    """A map plus its resolved city, loaded graph, OSRM router, and policy."""

    __slots__ = ("slug", "city", "network", "mode", "graph", "osrm",
                 "allow_suggestions", "requires_passcode", "vote_type_labels")

    def __init__(self, slug, city, network, mode, graph, osrm, allow_suggestions,
                 requires_passcode, vote_type_labels):
        self.slug = slug
        self.city = city
        self.network = network
        self.mode = mode
        self.graph = graph
        self.osrm = osrm
        self.allow_suggestions = allow_suggestions
        self.requires_passcode = requires_passcode
        self.vote_type_labels = vote_type_labels


# resolve_map runs on every API request, and its map lookup was a Postgres
# round-trip each time — the shared ~100-300ms latency floor under otherwise
# sub-ms endpoints (reverse-geocode, nearest-node, vote). Map config changes
# rarely, so cache rows (including misses) briefly. Staleness is bounded by
# the TTL on other instances; mutating endpoints invalidate their own entry.
_MAP_CACHE_TTL = 30.0
_MAP_CACHE_MAX = 512  # bots probe random slugs; don't let misses grow the dict
_map_cache: dict[str, tuple[float, dict | None]] = {}
_map_cache_lock = threading.Lock()


def _cached_get_map(slug: str) -> dict | None:
    now = time.monotonic()
    hit = _map_cache.get(slug)
    if hit and hit[0] > now:
        return hit[1]
    m = get_map(slug, with_vote_count=False)
    with _map_cache_lock:
        if len(_map_cache) >= _MAP_CACHE_MAX:
            _map_cache.clear()
        _map_cache[slug] = (now + _MAP_CACHE_TTL, m)
    return m


def invalidate_map_cache(slug: str | None = None):
    """Drop cached map config after a mutation (None clears everything)."""
    global _maps_list_cache
    _maps_list_cache = None  # the landing-grid list embeds map config too
    with _map_cache_lock:
        if slug is None:
            _map_cache.clear()
        else:
            _map_cache.pop(slug, None)
    # The /api/maps/<slug> SWR cache is separate — without this an admin
    # mutation kept serving the pre-mutation config for up to 30s.
    if slug is None:
        _map_get_cache.clear()
    else:
        _map_get_cache.pop(slug, None)


def resolve_map(slug: str | None) -> ResolvedMap:
    """Resolve a map slug → city, graph, OSRM router, and policy.

    Falls back gracefully so the app still serves a city graph when the map row
    is missing (e.g. DB unavailable, or slug is a bare city id).
    """
    slug = (slug or DEFAULT_MAP_SLUG).strip()

    m = _cached_get_map(slug)
    if m:
        city = get_city(m["cityId"]) or get_city(DEFAULT_CITY_ID)
        network = m.get("network") or "streets"
        mode = m.get("mode") or "walk"
        vote_types = m.get("voteTypes") or []
        labels = {vt.get("label") for vt in vote_types if vt.get("label")}
        allow_suggestions = m.get("allowSuggestions", True)
        requires_passcode = m.get("requiresPasscode", False)
    else:
        city = get_city(slug) or get_city(DEFAULT_CITY_ID)
        network = "streets"
        mode = "walk"
        labels = set()
        allow_suggestions = True
        requires_passcode = False

    graph = graph_registry.get(city, network)
    # Station networks aren't routable (no OSRM dataset); only streets need a router.
    osrm = osrm_registry.get(city) if network not in STATION_NETWORKS else None
    return ResolvedMap(slug, city, network, mode, graph, osrm, allow_suggestions,
                       requires_passcode, labels)


# ── Passcode gate ────────────────────────────────────────────────────────────

# Brute-force protection for /api/maps/<slug>/auth: cap wrong guesses per
# (ip, slug) in a rolling window, then lock that pair out for a cooldown.
# Counters live in Redis so the limit holds across all Flask replicas.
AUTH_MAX_ATTEMPTS = 8
AUTH_WINDOW_SECONDS = 300       # window the attempts are counted in
AUTH_LOCKOUT_SECONDS = 900      # cooldown once the cap is hit

# Mirrors the app's other inline notices (e.g. the out-of-bounds toast): one
# plain, friendly sentence. Shown on the load-time gate and on any gated API.
PASSCODE_REQUIRED_MESSAGE = "This map is private — enter its passcode to open it."


def _passcode_pv(passcode_hash: str | None) -> str:
    """Short fingerprint of the current passcode hash.

    Embedded in every session token so rotating a map's passcode (which changes
    the stored hash) invalidates every token minted against the old one.
    """
    if not passcode_hash:
        return ""
    return hashlib.sha256(passcode_hash.encode()).hexdigest()[:12]


def _issue_passcode_token(slug: str, passcode_hash: str | None) -> str:
    return _passcode_serializer.dumps({"s": slug, "pv": _passcode_pv(passcode_hash)})


def _request_passcode_token() -> str | None:
    """Pull the passcode token from wherever this request can carry it.

    Header for REST GETs, `?token=` for the WebSocket handshake (browsers can't
    set WS headers), JSON body for vote POSTs.
    """
    return (
        request.headers.get("X-Map-Passcode")
        or request.args.get("token")
        or (request.get_json(silent=True) or {}).get("passcode_token")
    )


def _passcode_ok(slug: str) -> bool:
    """True if the request carries a valid, current passcode token for `slug`."""
    token = _request_passcode_token()
    if not token:
        return False
    try:
        data = _passcode_serializer.loads(token, max_age=PASSCODE_TOKEN_MAX_AGE)
    except (BadSignature, Exception):
        return False
    # Legacy tokens were a bare slug string (no passcode-version binding); accept
    # them so sessions issued before this change keep working until they expire.
    if isinstance(data, str):
        return data == slug
    if not isinstance(data, dict) or data.get("s") != slug:
        return False
    return data.get("pv", "") == _passcode_pv(get_map_passcode_hash(slug))


def _locked(rmap: "ResolvedMap") -> bool:
    """True if this map gates viewing and the request hasn't proven the passcode."""
    return rmap.requires_passcode and not _passcode_ok(rmap.slug)


def _locked_response():
    """401 used by every gated content endpoint — single shape the client keys on."""
    resp = jsonify({
        "error": PASSCODE_REQUIRED_MESSAGE,
        "requires_passcode": True,
        "locked": True,
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp, 401


# ── Per-map vote response cache ───────────────────────────────────────────

# Bounded LRU of built /api/graph-votes snapshots, keyed by "<slug>:<mode>".
# This used to be an unbounded dict: every (map, mode) ever requested kept its
# full JSON body resident forever, so a multi-tenant server with many maps
# leaked memory until the worker OOM-crashed (exactly the "more than one tenant
# → crash" symptom). The bound is in BYTES, not entries: an NYC-sized snapshot
# is ~37MB while a small city's is a few hundred KB, so an entry count says
# nothing about footprint — and since entries now persist across votes (the
# debounce serves rev-stale snapshots instead of purging them), a count-capped
# cache could quietly hold gigabytes on an 8GiB prod instance. Eviction drops
# the least-recently-served snapshot until under budget. Access is guarded by
# _vote_cache_lock since several greenlets/requests touch it concurrently.
_VOTE_CACHE_MAX_MB = float(os.environ.get("VOTE_CACHE_MAX_MB", "512"))
_vote_cache: "OrderedDict[str, dict]" = OrderedDict()
_vote_cache_bytes = 0
_vote_cache_lock = threading.Lock()

# Per-cache-key single-flight locks. Building the vote arrays for a big map (NYC
# streets: ~2M edges, pure-Python) takes seconds and pins the GIL, freezing the
# single gevent worker. Without single-flight, N concurrent first-requests for
# the same map each rebuild the same body in series — head-of-line blocking that
# stacks into the multi-tenant stall/crash. One builder per key; the rest wait on
# its result.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()

# Serializes the read-modify-write of a voter's per-proposal direction so a
# rapid +/− toggle can't read a stale prior direction (see /api/vote). This is
# the PER-PROCESS half of the guarantee; across Flask instances/workers the
# Redis lock in cast_vote (vote_store.voter_lock) provides the same scope
# fleet-wide. STRIPED by (slug, counting identity): a voter's own casts serialize, but
# different voters vote concurrently — a single global lock here capped the
# whole instance at ~9 votes/s (changelog/2026-07-08-agent-load-test.html,
# finding #1 / mitigation M2). Redis counter updates are atomic HINCRBYs and
# commute across voters, so cross-voter mutual exclusion buys nothing.
_VOTE_LOCK_STRIPES = tuple(threading.Lock() for _ in range(256))


def _voter_lock_stripe(slug: str, identity: str) -> threading.Lock:
    return _VOTE_LOCK_STRIPES[hash((slug, identity)) % len(_VOTE_LOCK_STRIPES)]


# ── Vote-path saturation: backpressure valve + rolling metrics (M5) ─────────
# The July load test's sharpest lesson was that /health stayed at 1-2ms while
# votes queued 45s — monitoring keyed on health was blind to the melt. Two
# valves + a metrics window make saturation both bounded and visible:
#   · at most VOTE_MAX_INFLIGHT votes run concurrently; past that the endpoint
#     sheds with 503 + Retry-After instead of queueing into timeouts (clients
#     already roll back optimistic casts on failure);
#   · a same-voter stripe that can't be acquired in VOTE_LOCK_SHED_SECONDS
#     sheds the same way (a wedged voter shouldn't hold a request thread);
#   · /api/admin/stats reports votes/s, lock-wait and handler percentiles,
#     shed counts, and process RSS from a rolling window.
VOTE_MAX_INFLIGHT = int(os.environ.get("VOTE_MAX_INFLIGHT", "64"))
VOTE_LOCK_SHED_SECONDS = float(os.environ.get("VOTE_LOCK_SHED_SECONDS", "2.0"))
# How long a vote may WAIT for an inflight slot before shedding. Zero would
# shed the moment a burst exceeds the cap (58% of a 160-agent wave); a bounded
# wait turns the cap into a short queue that absorbs bursts while still
# capping worst-case latency at wait + handler time.
VOTE_QUEUE_WAIT_SECONDS = float(os.environ.get("VOTE_QUEUE_WAIT_SECONDS", "1.5"))

_vote_inflight = threading.Semaphore(VOTE_MAX_INFLIGHT)
_vote_metrics_lock = threading.Lock()
_vote_metrics: "deque[tuple[float, float, float]]" = deque(maxlen=2048)
_vote_shed = {"inflight": 0, "stripe": 0}


def _vote_metrics_record(lock_wait_s: float, total_s: float) -> None:
    with _vote_metrics_lock:
        _vote_metrics.append((time.time(), lock_wait_s, total_s))


def _shed_response(reason: str, retry_after: int):
    with _vote_metrics_lock:
        _vote_shed[reason] += 1
    resp = jsonify({"error": "Server is at vote capacity — retry shortly",
                    "shed": reason, "retry": True})
    resp.headers["Retry-After"] = str(retry_after)
    return resp, 503


def _vote_metrics_snapshot(window_s: float = 60.0) -> dict:
    now = time.time()
    with _vote_metrics_lock:
        rows = [r for r in _vote_metrics if now - r[0] <= window_s]
        shed = dict(_vote_shed)

    def pct(vals: list[float], p: float) -> float:
        if not vals:
            return 0.0
        vals = sorted(vals)
        return round(vals[min(len(vals) - 1, int(p * len(vals)))] * 1000, 1)

    lock_waits = [r[1] for r in rows]
    totals = [r[2] for r in rows]
    return {
        "window_s": window_s,
        "count": len(rows),
        "votes_per_sec": round(len(rows) / window_s, 2),
        "lock_wait_ms": {"p50": pct(lock_waits, 0.5), "p95": pct(lock_waits, 0.95)},
        "handler_ms": {"p50": pct(totals, 0.5), "p95": pct(totals, 0.95)},
        "shed": shed,
        "max_inflight": VOTE_MAX_INFLIGHT,
    }


# How long a built snapshot keeps serving after its revision goes stale. Under
# sustained voting the revision bumps on every cast, so rebuilding per rev is a
# full-array rebuild per vote (finding #3 of the load-test report); serving the
# last complete snapshot for a couple of seconds costs nothing — clients
# reconcile forward from WS deltas, which carry authoritative counts — and
# collapses a join storm to one rebuild per window.
_VOTE_CACHE_DEBOUNCE = float(os.environ.get("GRAPH_VOTES_DEBOUNCE", "2.0"))


def _vote_cache_get(cache_key: str, rev: int) -> dict | None:
    """Return the cached entry for this key when built at exactly this revision,
    marking it most-recently-used."""
    with _vote_cache_lock:
        cached = _vote_cache.get(cache_key)
        if cached and cached["rev"] == rev:
            _vote_cache.move_to_end(cache_key)
            return cached
    return None


def _vote_cache_peek(cache_key: str) -> dict | None:
    """Return the cached entry regardless of revision (debounce decides
    servability), marking it most-recently-used."""
    with _vote_cache_lock:
        cached = _vote_cache.get(cache_key)
        if cached:
            _vote_cache.move_to_end(cache_key)
        return cached


def _entry_bytes(entry: dict) -> int:
    return (len(entry["body"]) + len(entry["gz"])
            + len(entry.get("sp_body") or b"") + len(entry.get("sp_gz") or b""))


def _vote_cache_put(cache_key: str, entry: dict) -> None:
    """Store a built entry, evicting least-recently-used entries past the
    byte budget (never the entry just stored — a snapshot bigger than the
    whole budget still serves; the cache just holds nothing else)."""
    global _vote_cache_bytes
    budget = int(_VOTE_CACHE_MAX_MB * 1024 * 1024)
    with _vote_cache_lock:
        old = _vote_cache.pop(cache_key, None)
        if old is not None:
            _vote_cache_bytes -= _entry_bytes(old)
        _vote_cache[cache_key] = entry
        _vote_cache_bytes += _entry_bytes(entry)
        while _vote_cache_bytes > budget and len(_vote_cache) > 1:
            _, evicted = _vote_cache.popitem(last=False)
            _vote_cache_bytes -= _entry_bytes(evicted)


def _invalidate_vote_cache(slug: str) -> None:
    """Drop every cached body for a map — the bare slug AND each "<slug>:<mode>".

    The cache is keyed per mode ("<slug>:<mode>"), but writes only know the slug,
    so a plain `pop(slug)` left the mode-scoped bodies stale. (The revision check
    in _build_graph_votes_body still kept responses CORRECT, but the stale entries
    lingered in memory and forced an extra rebuild.) Clearing all of a slug's
    variants makes invalidation actually free the right entries."""
    global _vote_cache_bytes
    prefix = f"{slug}:"
    with _vote_cache_lock:
        for key in [k for k in _vote_cache if k == slug or k.startswith(prefix)]:
            dropped = _vote_cache.pop(key, None)
            if dropped is not None:
                _vote_cache_bytes -= _entry_bytes(dropped)


def _build_lock_for(cache_key: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[cache_key] = lock
        return lock


def _build_graph_votes_body(rmap: ResolvedMap, mode: str | None = None) -> dict:
    """Build (or reuse) the /api/graph-votes snapshot for one map+mode.

    Returns a cache entry {rev, bstamp, body, gz, built}: the JSON bytes, their
    pre-compressed gzip twin (one compression per rebuild, shared by every
    client — not one per response), the revision + blocks stamp the body
    describes, and a monotonic build time for the serve-stale debounce.

    `mode` scopes the legend and heatmap to a single vote namespace (e.g.
    "walkways") so proposals cast under other modes on the same map don't leak
    in. When omitted, all modes are aggregated (legacy behavior).
    """
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    cache_key = f"{rmap.slug}:{mode}" if mode else rmap.slug
    hit = _vote_cache_get(cache_key, rev)
    if hit is not None:
        return hit

    # Single-flight: one greenlet builds; concurrent callers wait, then take the
    # freshly-cached result instead of rebuilding the same arrays in series.
    with _build_lock_for(cache_key):
        hit = _vote_cache_get(cache_key, rev)
        if hit is not None:
            return hit
        return _build_graph_votes_body_locked(rmap, mode, rev, cache_key)


def _build_graph_votes_body_locked(
    rmap: ResolvedMap, mode: str | None, rev: int, cache_key: str,
) -> dict:
    rmap.graph.ensure_loaded()

    # The heatmap serves ONLY from Redis; Postgres is the durable copy that the
    # startup replay (_populate_redis) loads into Redis. If that replay never ran
    # or this map's hash is empty for any reason, the heatmap would be blank even
    # though the votes are safely in Postgres (and the map card's vote count, read
    # straight from Postgres, would still show them — the exact divergence this
    # guards against). Reconcile on demand the first time we'd serve an empty hash.
    if redis_client.hlen(vote_store.hash_key(rmap.slug)) == 0:
        if _hydrate_map_redis(rmap.slug, rmap.mode):
            rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)

    votes = vote_store.read_all(redis_client, rmap.slug)
    mode_filter = vote_store.mode_to_int(mode) if mode else None
    arrays = vote_store.build_arrays(
        votes, rmap.graph.n_edges, rmap.graph.n_nodes, rmap.graph.edge_ends,
        mode_filter=mode_filter,
    )
    arrays["rev"] = rev
    arrays["vote_types"] = {str(k): v for k, v in vote_store.all_vote_types().items()}
    # Stamp the topology this body is indexed against. The vote arrays are sized
    # to the CURRENT graph; the client may still hold a STALE cached topology (the
    # /api/graph-topology response is cached for a day). Carrying the dimensions
    # and the topology etag lets the client detect a mismatch and refuse to paint
    # votes against the wrong topology — the root of the mobile-Safari crash —
    # rather than indexing past the end of its node/edge arrays.
    arrays["n_edges"] = rmap.graph.n_edges
    arrays["n_nodes"] = rmap.graph.n_nodes
    arrays["topology_version"] = rmap.graph.topology_etag

    # Block layer: when this map has an edge→block mapping, also project the
    # deduped per-block votes (the client renders blocks as the primary heat;
    # maps without a mapping just omit these and fall back to the edge heatmap).
    if rmap.graph.edge_block_id is not None:
        block_mode = vote_store.mode_to_int(mode or rmap.mode)
        # bd:/bagg: are derived state; rebuild from Postgres if cold while edge
        # votes exist (first request after a deploy, evicted keys, a resnap) OR
        # when the aggregate was built against a DIFFERENT block set — block ids
        # renumber on every re-bake, so stale bagg entries would color and count
        # the wrong polygons. The blocks version marker rides next to the bagg.
        bver_key = f"bver:{rmap.slug}:{block_mode}"
        bver_now = block_votes.version_marker(rmap.graph.blocks_version)
        bagg_cold = redis_client.exists(block_votes.bagg_key(rmap.slug, block_mode)) == 0
        bagg_stale = (redis_client.get(bver_key) or "") != bver_now
        if (bagg_cold or bagg_stale) and redis_client.hlen(vote_store.hash_key(rmap.slug)) > 0:
            block_votes.rebuild_from_db(
                redis_client, rmap.slug, block_mode, rmap.graph.edge_block_id,
                database.fetch_edge_vote_identities(rmap.slug))
        if bagg_cold or bagg_stale:
            redis_client.set(bver_key, bver_now)
        arrays.update(block_votes.build_block_arrays(
            redis_client, rmap.slug, block_mode, rmap.graph.n_blocks))
        arrays["blocks_version"] = rmap.graph.blocks_version

    # format=sparse twin. The dense arrays are >99.5% zeros/empties on a city
    # graph (nyc-bikes: ~37K voted edges across 3.3M slots), and JSON.parse-ing
    # the ~26MB dense body materializes ~9M boxed JS values on the client —
    # hundreds of MB on mobile Safari, the "bikepaths" jetsam OOM. The sparse
    # body carries only nonzero indices + values (the client rebuilds typed
    # arrays), so it's ~50x smaller to download AND to parse. The private
    # _*_sparse keys ride in from build_arrays/build_block_arrays (O(voted),
    # never an O(graph) scan here) and must not leak into the dense body.
    sparse = _sparse_votes_body(
        arrays,
        arrays.pop("_evt_sparse", {}),
        arrays.pop("_nvt_sparse", {}),
        arrays.pop("_bvt_sparse", None),
    )

    # orjson + gzip level 3: the rebuild runs on the request path (debounced,
    # but each one used to stall the GIL ~2s serializing 37MB and compressing
    # at level 6 — the p95 spikes on unrelated endpoints). orjson is ~10x
    # faster than json here and zlib releases the GIL while compressing;
    # level 3 halves compression time for ~1.5% larger output.
    body = orjson.dumps(arrays, option=orjson.OPT_SERIALIZE_NUMPY)
    sp_body = orjson.dumps(sparse, option=orjson.OPT_SERIALIZE_NUMPY)
    entry = {
        "rev": rev,
        "bstamp": rmap.graph.blocks_stamp(),
        "body": body,
        "gz": gzip.compress(body, 3),
        "sp_body": sp_body,
        "sp_gz": gzip.compress(sp_body, 3),
        "built": time.monotonic(),
    }
    _vote_cache_put(cache_key, entry)
    return entry


def _rebuild_votes_snapshot(rmap: ResolvedMap, mode: str | None, cache_key: str,
                            lock: threading.Lock) -> None:
    """Background revalidation for /api/graph-votes (runs on a real OS thread).

    Acquire AND release the single-flight lock here in the worker so the lock
    never crosses the request-greenlet/thread boundary mid-hold; losing the
    non-blocking acquire just means a rebuild is already in flight."""
    if not lock.acquire(blocking=False):
        return
    try:
        rmap.graph.ensure_loaded()
        fresh_rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
        if _vote_cache_get(cache_key, fresh_rev) is None:
            _build_graph_votes_body_locked(rmap, mode, fresh_rev, cache_key)
    except Exception as e:
        logger.warning(f"[VOTES] background rebuild '{cache_key}' failed: {e}")
    finally:
        lock.release()


def _sparse_votes_body(arrays: dict, evt: dict, nvt: dict, bvt: dict | None) -> dict:
    """Assemble the format=sparse /api/graph-votes body from the dense arrays.

    Vote totals become (idx, val) pairs of the nonzero entries (numpy
    flatnonzero — C-speed, no Python loop over the graph); the per-index
    vote-type breakdowns arrive pre-sparsified from build_arrays /
    build_block_arrays because a breakdown can be non-empty where the net is 0
    (counter-votes cancel), so nonzero totals alone can't recover its keys."""
    import numpy as np

    ev = np.asarray(arrays["edge_votes"])
    nv = np.asarray(arrays["node_votes"])
    ei = np.flatnonzero(ev)
    ni = np.flatnonzero(nv)
    sp = {
        "sparse": 1,
        "rev": arrays["rev"],
        "vote_types": arrays["vote_types"],
        "vote_type_legend": arrays["vote_type_legend"],
        "n_edges": arrays["n_edges"],
        "n_nodes": arrays["n_nodes"],
        "topology_version": arrays["topology_version"],
        "ev_idx": ei, "ev_val": ev[ei],
        "nv_idx": ni, "nv_val": nv[ni],
        "evt": evt, "nvt": nvt,
    }
    if "block_votes" in arrays:
        bv = np.asarray(arrays["block_votes"])
        bi = np.flatnonzero(bv)
        sp.update({
            "n_blocks": arrays["n_blocks"],
            "blocks_version": arrays.get("blocks_version"),
            "block_vote_type_legend": arrays["block_vote_type_legend"],
            "bv_idx": bi, "bv_val": bv[bi],
            "bvt": bvt or {},
        })
    return sp


def _write_vote_fields(slug: str, fields: list[tuple[int, int]]) -> None:
    """HSET a batch of (redis_field, count) into a map's Redis hash, pipelined.

    HSET writes ABSOLUTE counts, so this re-asserts the Postgres-authoritative
    state (the vote path commits to Postgres synchronously before touching Redis)
    rather than incrementing — safe to re-run."""
    h = vote_store.hash_key(slug)
    pipe = redis_client.pipeline()
    for i, (field, cnt) in enumerate(fields):
        pipe.hset(h, str(field), cnt)
        if (i + 1) % 5000 == 0:
            pipe.execute()
            pipe = redis_client.pipeline()
    pipe.execute()


def _hydrate_map_redis(slug: str, mode: str) -> int:
    """Lazily replay ONE map's Postgres edge_votes into its (empty) Redis hash.

    The on-demand counterpart to _populate_redis, called when the heatmap would
    otherwise serve a blank map because its Redis hash is empty. Keyed by the
    map's `mode` so the fields land in the same namespace the client reads under
    (themeMode == map.mode). Single-flighted via a short Redis lock so concurrent
    first-requests don't stampede the same replay. Bumps the revision so other
    workers' in-memory vote caches invalidate and pick up the now-full hash.
    Returns the number of fields written (0 if the DB has none or another request
    is already hydrating)."""
    if not DATABASE_URL:
        return 0
    # Single-flight: the loser serves this one request from the (briefly still
    # empty) hash and self-heals on its next cache miss.
    if not redis_client.set(f"hydrate_lock:{slug}", "1", nx=True, ex=120):
        return 0
    try:
        rows = database.aggregate_votes_for_replay(slug)
        if not rows:
            return 0
        mode_int = vote_store.mode_to_int(mode)
        fields = [(vote_store.redis_field(edge_id, mode_int, vt_id, direction), cnt)
                  for (_slug, edge_id, vt_id, direction, cnt) in rows]
        _write_vote_fields(slug, fields)
        redis_client.incr(vote_store.revision_key(slug))
        logger.info(f"[HYDRATE] Replayed {len(fields)} fields into "
                    f"{vote_store.hash_key(slug)} from Postgres (Redis was empty)")
        return len(fields)
    except Exception as e:
        logger.warning(f"[HYDRATE] Failed for '{slug}': {e}")
        return 0
    finally:
        redis_client.delete(f"hydrate_lock:{slug}")


def _populate_redis():
    """Replay edge_votes from Postgres into each map's Redis hash if underpopulated."""
    if not DATABASE_URL:
        return
    rows = database.aggregate_votes_for_replay()
    if not rows:
        logger.info("[POPULATE] No edge_votes in Postgres")
        return

    # The Redis field still carries a 4-bit mode (the map's mode), derived here
    # from each map rather than stored per-row.
    slug_mode = {m["slug"]: vote_store.mode_to_int(m.get("mode", "walk"))
                 for m in list_maps()}

    by_slug: dict[str, list] = {}
    for map_slug, edge_id, vt_id, direction, cnt in rows:
        if not map_slug:
            continue
        mode_int = slug_mode.get(map_slug, vote_store.mode_to_int("walk"))
        field = vote_store.redis_field(edge_id, mode_int, vt_id, direction)
        by_slug.setdefault(map_slug, []).append((field, cnt))

    for slug, fields in by_slug.items():
        h = vote_store.hash_key(slug)
        if redis_client.hlen(h) >= len(fields):
            continue
        _write_vote_fields(slug, fields)
        logger.info(f"[POPULATE] Loaded {len(fields)} fields into {h}")


def _prewarm():
    """Preload every map's graph + vote cache so no map cold-loads on first hit.

    Loads each city's walk graph (the slow part — NYC ~60-120s) and builds each
    map's vote body in a background thread. Every city fits resident now that
    python_router frees networkx after load (max_loaded=len(CITIES)), so warming
    everything no longer risks the 16Gi OOM that the all-resident set used to cause."""
    try:
        warmed = 0
        for m in list_maps():
            try:
                rmap = resolve_map(m["slug"])
                # Build the MODE-SCOPED snapshot — the client always requests
                # mode=<map.mode>, so the cache key is "<slug>:<mode>". The old
                # modeless build warmed a key no client ever asks for, and the
                # first visitor per instance still paid the full ~6s build
                # inline (the dominant cold-load term in the [MAPLOAD] P99).
                _build_graph_votes_body(rmap, rmap.mode)
                # Pre-compress the topology blob too, so no tenant's first
                # visitor pays the one-time ~1-2s compression of the big
                # cities (brotli is what browsers actually receive).
                rmap.graph.topology_binary_gz()
                rmap.graph.topology_binary_br()
                warmed += 1
            except Exception as e:
                logger.warning(f"[STARTUP] Pre-warm '{m['slug']}' failed: {e}")
        logger.info(f"[STARTUP] Pre-warmed {warmed} map(s) across all cities")
    except Exception as e:
        logger.warning(f"[STARTUP] Pre-warm failed: {e}")


# Under gunicorn's gevent worker, gevent.monkey patches threading.Thread into a
# GREENLET — and a greenlet running CPU-bound work (the graph preload) never yields
# the event loop, so /health can't be answered and the startup probe fails. Use a
# REAL OS thread instead: the GIL is handed off every few ms, so the gevent hub
# keeps serving /health while the graphs load in the background. Falls back to the
# stdlib thread when gevent isn't present (e.g. the Flask dev server).
try:
    from gevent import monkey as _gmonkey
    _PrewarmThread = _gmonkey.get_original("threading", "Thread")
except Exception:
    _PrewarmThread = threading.Thread

def _startup_warm():
    """Background warmup: refresh vote data, then preload every city's graph.

    Runs entirely on a REAL OS thread so the worker boots clean and serves /health
    immediately (the startup probe passes), while the GIL hands off every few ms so
    the gevent hub keeps serving requests during the CPU-heavy graph loads. Both
    steps used to run inline/at-import and blocked the worker past the startup probe
    (populate) or the gunicorn request timeout (graph load) — hence the move here.
    Vote data already lives in Redis (Memorystore persists across deploys), so a
    brief gap before _populate_redis refreshes it just serves the existing data.
    """
    try:
        _populate_redis()
    except Exception as e:
        logger.warning(f"[STARTUP] populate failed: {e}")
    if os.environ.get("SKIP_PREWARM") != "1":
        _prewarm()


# SKIP_WARMUP=1 skips the whole background warmup (tests/fast boots). SKIP_PREWARM=1
# refreshes votes but skips the graph preload.
if os.environ.get("SKIP_WARMUP") != "1":
    _PrewarmThread(target=_startup_warm, name="startup-warm", daemon=True).start()


# NOTE: the old vote_deltas:* listener that hard-invalidated the vote cache on
# every peer write is gone — publish_delta's revision bump already rev-stales
# cached snapshots (on every instance, since the revision lives in Redis), and
# per-vote invalidation is exactly what forced a full 33MB rebuild per cast
# under load. The graph-votes debounce now decides how long a stale snapshot
# keeps serving.

# One pubsub listener + coalesced fan-out for every WebSocket client (see
# delta_hub.py). Its thread starts lazily on the first subscription.
delta_hub = delta_hub_mod.DeltaHub(
    lambda: redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True))

# Live co-presence, shared across replicas via Redis (presence.py). It rides
# the SAME socket the delta hub does — a second channel for a viewer count
# would double the connection count for a number, and would let presence and
# votes disagree about whether a client is still there.
presence_registry = presence.Presence(redis_client)

# Longest vote-type label a `view` message may carry, matched to the vote path.
VIEW_LABEL_MAX = 128
# Views are rate-limited by a token bucket: VIEW_BURST at once, refilled at
# VIEW_REFILL_PER_SEC. This is a WORK bound, not an integrity control — PFADD
# is idempotent, so no amount of replay can move a count. A flat minimum
# interval was the first version and it was wrong: closing a point card and
# opening a route card lands two DIFFERENT, entirely legitimate views inside a
# second, and the flat rule silently dropped the second one. Dropping a
# proposal somebody genuinely read is a quiet under-count with no upper bound
# on how often it happens, which is not a failure worth accepting to save a
# Redis pipeline.
VIEW_BURST = 4.0
VIEW_REFILL_PER_SEC = 0.5


# ── Helpers ────────────────────────────────────────────────────────────────

def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _resolve_user(data_or_args, ip_from_voter: bool = False) -> tuple[str, str]:
    """Return (device_id, ip_hash). device_id is the hashed device id when the
    client sends one (the dedup key), falling back to the IP for anonymous
    voters; ip_hash is the hashed IP (recorded for abuse/analytics + the soft
    per-IP cap).

    `ip_from_voter` ties ip_hash to the per-voter id instead of the request IP.
    Bulk imports (Lyft/Citibike) all originate from one source IP but represent
    thousands of distinct rides; with this set each ride is unique on BOTH the
    device and IP axes, so the per-IP cap never collapses an import.

    `admin_device_id` (admin-token-gated) acts as an EXISTING device verbatim,
    skipping the voter_id hash. Operator corrections need it for stored rows
    whose voter_id preimage is gone (e.g. countering an import batch whose ride
    ids are no longer published — counter_lyft --reconstruct-unmatched)."""
    admin_device = data_or_args.get("admin_device_id")
    if admin_device and _admin_authorized():
        admin_device = str(admin_device)[:16]
        return admin_device, admin_device if ip_from_voter else get_client_ip()
    ip_hash = get_client_ip()
    voter_id = data_or_args.get("voter_id")
    device_id = hashlib.sha256(str(voter_id).encode()).hexdigest()[:16] if voter_id else ip_hash
    if ip_from_voter and voter_id:
        ip_hash = device_id
    return device_id, ip_hash


# ═══════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    try:
        redis_client.ping()
        return jsonify({"status": "healthy", "redis": "connected"}), 200
    except redis.ConnectionError:
        return jsonify({"status": "unhealthy", "redis": "disconnected"}), 503


# ── Cities / vote-type lists / maps API ──────────────────────────────────────

@app.route("/api/cities", methods=["GET"])
def cities_list():
    resp = jsonify({"cities": [c.to_public() for c in all_cities()]})
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


@app.route("/api/vote-type-lists", methods=["GET"])
def vote_type_lists():
    resp = jsonify({"lists": list_vote_type_lists()})
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


# Server-side twin of the endpoint's max-age=60: browsers cache per visitor,
# but every DISTINCT visitor's cold load still ran the ranking aggregate (a
# full-table COUNT over edge_votes — p95 3.5s on the f1-micro). One shared
# copy per instance amortizes it across visitors.
_maps_list_cache: tuple[float, list] | None = None


@app.route("/api/maps", methods=["GET"])
def maps_list():
    global _maps_list_cache
    now = time.monotonic()
    if _maps_list_cache and _maps_list_cache[0] > now:
        maps = _maps_list_cache[1]
    else:
        maps = list_maps()
        # Enrich with the city's public view config for the landing grid.
        for m in maps:
            city = get_city(m["cityId"])
            if city:
                m["city"] = city.to_public()
        _maps_list_cache = (now + 60.0, maps)
    # Short public cache: this gatekeeps every cold page load (it hit Postgres
    # uncached on each one); the map list changes rarely, so 60s is safe.
    resp = jsonify({"maps": maps})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


# Staging deployments set APP_ENV=staging (terraform staging.tf). The flag rides
# the map config to the client — which then skips the canonical-subdomain
# redirect (it would bounce testers to prod) and shows a STAGING ribbon — and
# marks every Flask response noindex so an accidentally-linked staging URL never
# gets crawled. nginx applies the same header to its static responses via a
# $host map (deploy/nginx-cloudrun.conf). Prod never sets APP_ENV.
IS_STAGING = os.environ.get("APP_ENV", "").strip().lower() == "staging"

if IS_STAGING:
    @app.after_request
    def _staging_noindex(resp):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        return resp


def _enrich_map(m: dict) -> dict:
    """Attach the city public config + searchable vote types to a map dict.

    EVERY path that puts a map into _map_get_cache must cache the ENRICHED
    dict — the SWR background refresh used to cache get_map() raw, so after
    the first TTL expiry clients received an empty `city` (no bounds/center/
    blockTiles; NYC only looked fine because the client bootstrap defaults
    to it)."""
    city = get_city(m["cityId"])
    if city:
        m["city"] = city.to_public()
    # Custom vote types people have already voted here — surfaced by the selector
    # only when searched, never in the default list. Drop the map's own defaults.
    # Each entry is {label, pointType: 'route'|'point'|null} so the client can
    # kind-filter proposals and the selector (null = legacy row, kind unknown).
    default_labels = {vt.get("label") for vt in (m.get("voteTypes") or [])}
    m["searchVoteTypes"] = [
        vt for vt in fetch_voted_vote_type_labels(m["slug"])
        if vt["label"] not in default_labels
    ]
    if IS_STAGING:
        m["staging"] = True
    return m


def _map_response(m: dict):
    """Enrich a map dict with its city's public config and JSON-ify it."""
    resp = jsonify(_enrich_map(m))
    # This is the prod page-load entry point (resolved per load by slug/subdomain)
    # and was uncached. A passcode map only reaches here once unlocked, so keep its
    # config out of shared caches — per-browser only.
    if m.get("requiresPasscode"):
        resp.headers["Cache-Control"] = "private, max-age=30"
    else:
        resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


def _locked_stub(slug: str):
    """Minimal config for a gated map: enough for the client to show the prompt,
    nothing about the map's content (name, votes, area)."""
    resp = jsonify({"slug": slug, "requiresPasscode": True, "locked": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# slug → (expiry_monotonic, enriched map dict). Public maps only; 30s TTL
# (the response is already Cache-Control: public, max-age=60). Every tenant's
# page load enters here, and each uncached hit is two Postgres round-trips —
# a join wave of N clients would otherwise stampede N aggregates onto the
# shared pool (measured: 40 concurrent joins → 10s+ map-config latency).
# Single-flight: one lock per slug so a miss wave costs ONE query burst.
_map_get_cache: dict[str, tuple[float, dict]] = {}
_map_get_locks: dict[str, threading.Lock] = {}


def _refresh_map_get(slug: str, lock: threading.Lock) -> None:
    """Background revalidation for /api/maps/<slug> (runs on a real OS thread).

    get_map's Postgres round-trip (it includes a vote count) takes 1-2s on the
    small prod instance; running it inline on TTL expiry put that stall on the
    FIRST request of a page load, serializing everything behind it. Only public
    maps are ever cached, so refreshing the cache here can't leak a passcode
    map's config."""
    if not lock.acquire(blocking=False):
        return
    try:
        m = get_map(slug)
        if m and not m.get("requiresPasscode"):
            if len(_map_get_cache) > 256:  # bound: one entry per public map
                _map_get_cache.clear()
            _map_get_cache[slug] = (time.monotonic() + 30.0, _enrich_map(m))
        else:
            # Map deleted or newly passcode-protected: stop serving the stale
            # public copy immediately.
            _map_get_cache.pop(slug, None)
    except Exception as e:
        logger.warning(f"[MAPS] background refresh '{slug}' failed: {e}")
    finally:
        lock.release()


@app.route("/api/maps/<slug>", methods=["GET"])
def map_get(slug):
    cached = _map_get_cache.get(slug)
    if cached and cached[0] > time.monotonic():
        resp = jsonify(cached[1])
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    lock = _map_get_locks.setdefault(slug, threading.Lock())
    if cached is not None:
        # Expired but present: serve the stale config NOW and refresh it in the
        # background. Map config changes rarely; staleness is bounded by the
        # refresh duration on top of the 30s TTL.
        _PrewarmThread(target=_refresh_map_get, args=(slug, lock),
                       name=f"map-swr-{slug}", daemon=True).start()
        resp = jsonify(cached[1])
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp
    with lock:  # gevent-patched: waiters yield the hub
        cached = _map_get_cache.get(slug)
        if cached and cached[0] > time.monotonic():
            resp = jsonify(cached[1])
            resp.headers["Cache-Control"] = "public, max-age=60"
            return resp
        m = get_map(slug)
        if not m:
            # Retired slug? Serve its redirect (map_redirects row, created by
            # rename_map.py) so old links — printed QR posters — keep working.
            # A 200 + `redirect` field rather than a 30x: the SPA shell is
            # served by nginx before any slug lookup, so only the client can
            # act on it (mirrors the canonical-subdomain redirect in App.tsx).
            # Not stored in _map_get_cache (that holds enriched map dicts
            # only); the lookup is a single PK read and browsers hold it for
            # max-age anyway.
            r = get_map_redirect(slug)
            if r:
                resp = jsonify({"slug": slug, "redirect": r})
                resp.headers["Cache-Control"] = "public, max-age=300"
                return resp
            return jsonify({"error": "Map not found"}), 404
        if m.get("requiresPasscode") and not _passcode_ok(slug):
            return _locked_stub(slug)
        resp = _map_response(m)
        if not m.get("requiresPasscode"):
            if len(_map_get_cache) > 256:  # bound: one entry per public map
                _map_get_cache.clear()
            _map_get_cache[slug] = (time.monotonic() + 30.0, m)
        return resp


@app.route("/api/maps/by-subdomain/<subdomain>", methods=["GET"])
def map_get_by_subdomain(subdomain):
    """Resolve a vanity subdomain (e.g. "bikepaths") to its map config.

    The client calls this when loaded on a subdomain host so admins can point
    any subdomain at any map purely by setting the DB `subdomain` column — no
    code change. Two path segments, so it never collides with /api/maps/<slug>.
    """
    m = get_map_by_subdomain((subdomain or "").strip().lower())
    if not m:
        return jsonify({"error": "Map not found"}), 404
    if m.get("requiresPasscode") and not _passcode_ok(m["slug"]):
        return _locked_stub(m["slug"])
    return _map_response(m)


@app.route("/api/maps/check-slug", methods=["GET"])
def map_check_slug():
    slug = (request.args.get("slug") or "").strip().lower()
    return jsonify({"slug": slug, "available": bool(slug) and slug_available(slug)})


def _normalize_slug(raw: str) -> str:
    import re
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return s


# Visual styles a proposer may pick (must mirror MAP_STYLES in mapStyles.ts).
_VALID_MAP_STYLES = {
    "default", "bikepaths", "walkways", "trees",
    "transit", "terracotta", "waterfront", "plum",
}


@app.route("/api/maps", methods=["POST"])
def map_create():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    city_id = (data.get("city_id") or "").strip()
    slug = _normalize_slug(data.get("slug") or name)
    allow_suggestions = bool(data.get("allow_suggestions", True))
    passcode = (data.get("passcode") or "").strip()
    symbol = (data.get("symbol") or "").strip()
    # Visual theme (basemap + accent + heat ramp). Falls back to the neutral
    # default for anything the client doesn't recognize.
    style = (data.get("style") or "").strip()
    if style not in _VALID_MAP_STYLES:
        style = "default"

    # Network: what the map votes on. "streets" (default, every city) is the
    # routable walk graph; station networks (e.g. "ebikes") are NYC-only fixed
    # point sets with no routing.
    network = (data.get("network") or "streets").strip()

    if not name:
        return jsonify({"error": "Map name is required"}), 400
    if not get_city(city_id):
        return jsonify({"error": f"Unknown city '{city_id}'"}), 400
    if network != "streets" and network not in STATION_NETWORKS:
        return jsonify({"error": f"Unknown network '{network}'"}), 400
    if network in STATION_NETWORKS and city_id != "nyc":
        return jsonify({"error": f"The '{network}' network is only available for NYC"}), 400
    if not slug:
        return jsonify({"error": "Could not derive a valid slug"}), 400
    if not slug_available(slug):
        return jsonify({"error": f"Slug '{slug}' is already taken"}), 409

    # Vote-type list: either an existing preset id, or an inline custom list.
    vote_type_list_id = data.get("vote_type_list_id")
    custom_vote_types = data.get("custom_vote_types")
    if not vote_type_list_id and not custom_vote_types:
        return jsonify({"error": "Provide vote_type_list_id or custom_vote_types"}), 400
    if custom_vote_types and not isinstance(custom_vote_types, list):
        return jsonify({"error": "custom_vote_types must be a list"}), 400
    # Every authored vote type must declare its route/point kind — the top-
    # proposal split (PBTP vs RBTP) and the selector's mode filter key off it,
    # and a kindless entry would silently fall into "unknown" forever.
    if custom_vote_types:
        for vt in custom_vote_types:
            if not isinstance(vt, dict) or not (vt.get("label") or "").strip():
                return jsonify({"error": "Each custom vote type needs a label"}), 400
            if not normalize_point_type(vt.get("pointType")):
                return jsonify({
                    "error": "Each custom vote type needs pointType 'route' or 'point'"
                }), 400

    # Subtitle defaults to "<city> · <vote-type-list name>". The custom-list name
    # defaults to its first vote type's label.
    city = get_city(city_id)
    list_name = ""
    if vote_type_list_id:
        list_name = next((l["name"] for l in list_vote_type_lists()
                          if l["id"] == int(vote_type_list_id)), "")
    elif custom_vote_types:
        list_name = (custom_vote_types[0].get("label") or "").strip()
    subtitle = (data.get("subtitle") or "").strip()
    if not subtitle:
        subtitle = " · ".join(p for p in (city.name if city else city_id, list_name) if p)

    passcode_hash = generate_password_hash(passcode) if passcode else None
    created = create_map(
        slug=slug, name=name, city_id=city_id, subtitle=subtitle,
        vote_type_list_id=vote_type_list_id,
        custom_vote_types=custom_vote_types,
        allow_suggestions=allow_suggestions,
        passcode_hash=passcode_hash,
        created_by_ip_hash=get_client_ip(),
        symbol=symbol or None,
        style=style,
        network=network,
    )
    if not created:
        return jsonify({"error": "Failed to create map"}), 500

    # A pre-creation probe of this slug may have cached a miss.
    invalidate_map_cache(slug)

    # Stamp the authored kinds into the global registry now, so a later cast
    # (whose selection kind may differ) isn't the first to flag these labels.
    # A custom label that cannot be minted (the 24-bit vote-key id space is
    # full) is reported to the author rather than swallowed — the map exists,
    # but they need to know that type will never carry a vote.
    try:
        for vt in (custom_vote_types or []):
            vote_store.get_vote_type_id(vt["label"].strip(), vt.get("pointType"))
    except vote_store.VoteTypeLimitError as e:
        logger.error(f"[MAPS] '{slug}' created, but a custom vote type was refused: {e}")
        return jsonify({"error": str(e), "vote_type_limit": True,
                        "map_created": True, "slug": slug}), 507

    city = get_city(city_id)
    if city:
        created["city"] = city.to_public()
    return jsonify(created), 201


@app.route("/api/maps/<slug>/auth", methods=["POST"])
def map_auth(slug):
    data = request.get_json() or {}
    passcode = (data.get("passcode") or "").strip()
    h = get_map_passcode_hash(slug)
    if not h:
        # No passcode set on this map → trivially authorized.
        return jsonify({"token": _issue_passcode_token(slug, None)})

    # Throttle guessing before doing any hash work.
    rl_key = f"auth_attempts:{slug}:{get_client_ip()}"
    try:
        attempts = redis_client.get(rl_key)
        if attempts is not None and int(attempts) >= AUTH_MAX_ATTEMPTS:
            return jsonify({
                "error": "Too many attempts — wait a few minutes and try again.",
            }), 429
    except redis.RedisError:
        pass  # fail open on the counter; never block on the limiter itself

    if passcode and check_password_hash(h, passcode):
        try:
            redis_client.delete(rl_key)  # reset the window on success
        except redis.RedisError:
            pass
        return jsonify({"token": _issue_passcode_token(slug, h)})

    # Wrong passcode — count the miss; lengthen the window into a lockout at the cap.
    try:
        n = redis_client.incr(rl_key)
        redis_client.expire(
            rl_key, AUTH_LOCKOUT_SECONDS if n >= AUTH_MAX_ATTEMPTS else AUTH_WINDOW_SECONDS
        )
    except redis.RedisError:
        pass
    return jsonify({"error": "That passcode doesn't match — check it and try again."}), 403


# ── WebSocket (delta-based, per-map) ─────────────────────────────────────────

def _ws_handle_inbound(ws, slug: str, sub, raw, conn=None) -> None:
    """Handle one client→server WS message. Never raises into the pump loop.

    Four messages exist:

      {"type":"hello","bin":1}     late fallback for announcing binary
                                   support. The handshake `?bin=1` is the
                                   real mechanism (it can't race the first
                                   flush); this exists so capability can be
                                   changed mid-connection.
      {"type":"sync","since":N}    repair: send back everything that changed
                                   after revision N as ONE coalesced SYNC
                                   frame. The client fires this on foreground
                                   and on reconnect — the two moments it knows
                                   it may have missed deltas but has no delta
                                   arriving to notice the gap with. Before
                                   this, a backgrounded tab stayed stale until
                                   the NEXT vote tripped gap detection, and
                                   then paid a full ~57 KB refetch.
      {"type":"here","visible":B}  this tab became visible / hidden. Presence
                                   counts people who are LOOKING, and a
                                   backgrounded tab in this app is throttled
                                   to a standstill, so a hidden tab leaves the
                                   room (presence.py). The socket stays open.
      {"type":"view","t":"label",   a proposal card of type `t` spanning
                "b":[N,...]}        blocks `b` has been open long enough to
                                    count as read. Records the viewer into
                                    every one of those blocks' sketches and
                                    replies with the union-deduped audience
                                    (view_counts.py).

    `conn` carries the per-connection state the last two need (the viewer's
    counting identity and the visibility flag); it is None only in tests that
    exercise the first two.
    """
    try:
        if isinstance(raw, bytes):
            return  # no binary client→server messages in frame version 1
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return
    kind = msg.get("type")
    if kind == "hello":
        sub.binary = bool(msg.get("bin"))
        return
    if kind == "here" and conn is not None:
        visible = bool(msg.get("visible"))
        if visible == conn["visible"]:
            return
        conn["visible"] = visible
        if visible:
            presence_registry.arrive(slug, conn["identity"])
        else:
            presence_registry.depart(slug, conn["identity"])
        # Force the next tick to re-read and re-push: the caller's own
        # arrival/departure is the one change it can't learn about from a
        # count it is itself part of.
        conn["last_presence_sent"] = None
        return
    if kind == "view" and conn is not None:
        label = msg.get("t")
        blocks = view_counts.normalize_blocks(msg.get("b"))
        if not isinstance(label, str) or not label or not blocks:
            return
        label = label[:VIEW_LABEL_MAX]
        # A view from a hidden tab is not a view. The client already refuses to
        # send one, but presence is the server's fact and the client's is only
        # a claim, so the check lives here too.
        if not conn["visible"]:
            return
        # Token bucket: bursts of real UI activity get through, a firehose
        # does not. See VIEW_BURST.
        now = time.time()
        conn["view_tokens"] = min(
            VIEW_BURST,
            conn["view_tokens"] + (now - conn["last_view_at"]) * VIEW_REFILL_PER_SEC)
        conn["last_view_at"] = now
        if conn["view_tokens"] < 1.0:
            return
        conn["view_tokens"] -= 1.0
        view_counts.record_view(redis_client, slug, label, blocks, conn["identity"])
        n, exact = view_counts.count_views(redis_client, slug, label, blocks)
        try:
            # `k` echoes the client's own request key so a reply that arrives
            # after the reader has moved to another card is discarded rather
            # than painted onto the wrong proposal.
            ws.send(json.dumps({"type": "views", "k": msg.get("k"),
                                "n": n, "exact": exact}))
        except Exception:
            pass
        return
    if kind == "sync":
        try:
            since = int(msg.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        try:
            frame = vote_store.build_sync_frame(redis_client, slug, since)
        except Exception as e:
            logger.warning(f"[WS] sync build failed for {slug}: {e}")
            return
        # A client that can't read binary gets told to refetch — the same
        # answer TRUNCATED gives, and the path it already implements.
        if not sub.binary:
            ws.send(json.dumps({"type": "resync", "reason": "no-binary"}))
            return
        ws.send(frame)


@sock.route("/ws")
def ws(ws):
    """Push a single map's vote deltas to a client in real time.

    Delivery comes from the process-wide DeltaHub: ONE Redis pubsub listener
    feeds every client, and deltas arrive merged ({"type": "deltas", ...}) at
    the hub's flush cadence instead of one send per vote per client. This
    handler just blocks on its queue — no per-client Redis connection and no
    10Hz busy poll (finding #4 of changelog/2026-07-08-agent-load-test.html).
    """
    slug = (request.args.get("map") or DEFAULT_MAP_SLUG).strip()

    # Gate the live stream exactly like the REST content: a locked map's deltas
    # never leave the server without a valid `?token=` on the handshake.
    m = get_map(slug)
    if m and m.get("requiresPasscode") and not _passcode_ok(slug):
        try:
            ws.send(json.dumps({"type": "error", "error": "locked",
                                "requires_passcode": True}))
        except Exception:
            pass
        return

    # Binary capability rides in the HANDSHAKE, not in a hello message. The
    # inbound pump below only drains once per q.get timeout, so a hello sent
    # right after connect can lose the race with the first flush and get JSON
    # back — harmless (the client reads both) but sloppy, and it would make
    # "which encoding did this client get" depend on timing. The query string
    # is known before the first byte is sent, the same way `map` and `token`
    # already are.
    sub = delta_hub.subscribe(slug, binary=request.args.get("bin") == "1")

    # Per-connection state for the two audience features. `identity` is the
    # COUNTING identity (vote_identity.py) — the hashed IP, NOT device_id. It
    # is read from the handshake and never from anything the client sends,
    # which is both the honest choice (a client cannot inflate a count by
    # claiming to be several people) and the private one (the client is never
    # asked for an identifier at all).
    conn = {
        "identity": get_client_ip(),
        "visible": True,   # a tab that just opened a socket is looking at it
        "last_presence_sent": None,
        "last_view_at": time.time(),
        "view_tokens": VIEW_BURST,
    }
    presence_registry.arrive(slug, conn["identity"])
    last_heartbeat = time.time()
    last_presence_poll = 0.0

    try:
        rev = int(redis_client.get(vote_store.revision_key(slug)) or 0)
        # `bin` echoes back what the server decided, so the client can tell
        # whether to expect frames. An old server omits it and keeps sending
        # JSON, which the client still applies.
        ws.send(json.dumps({"type": "init", "rev": rev, "map": slug,
                            "bin": 1 if sub.binary else 0}))

        last_push = time.time()
        KEEPALIVE = 30
        while True:
            # Pump inbound frames: a client close must be noticed promptly, and
            # this is also where every client→server message arrives —
            # `hello` (binary capability), `sync` (repair after a backgrounded
            # tab), `here` (this tab became visible/hidden) and `view` (a
            # proposal card was read). See _ws_handle_inbound.
            try:
                inbound = ws.receive(timeout=0)
                if inbound:
                    _ws_handle_inbound(ws, slug, sub, inbound, conn)
            except ConnectionClosed:
                # The client is GONE, and this is the only place we find out
                # promptly — the close frame arrives on the inbound pump.
                #
                # It used to fall into the filter below, where "connection
                # closed" is on the ignore list, so the loop kept spinning and
                # the handler only unwound ~30s later when the keepalive send
                # finally raised. Harmless when this loop only pushed votes.
                # Not harmless now: for those 30 seconds the departed viewer
                # was still in the presence room, so everyone left on the map
                # was told they had company that had closed the tab. Measured
                # at 30.2s against a real browser close before this line
                # existed. Leaving now makes `finally`'s ZREM what the design
                # says it is — an explicit departure, not an expiry.
                break
            except Exception as e:
                s = str(e).lower()
                if "timed out" not in s and "no data" not in s and "connection closed" not in s:
                    logger.warning(f"[WS] receive exception: {e}")

            # Presence maintenance, BEFORE the queue drain: the `continue` on a
            # successful drain skips the rest of the loop, so anything placed
            # below it silently stops running on a busy map — which is exactly
            # when people are most likely to be here together.
            now = time.time()
            if conn["visible"] and now - last_heartbeat >= presence.HEARTBEAT_INTERVAL:
                presence_registry.heartbeat(slug, conn["identity"])
                last_heartbeat = now
            # Only a VISIBLE tab is told the count. A hidden one is not in the
            # set, so `n` would exclude it — and the client renders "n − 1
            # others" on the assumption that it is counted. Pushing to a hidden
            # tab therefore both under-reports by one AND leaves a stale strip
            # on a map nobody is looking at. Silence while hidden; becoming
            # visible clears last_presence_sent and forces a fresh push.
            if conn["visible"] and now - last_presence_poll >= presence.PUSH_INTERVAL:
                last_presence_poll = now
                n = presence_registry.count(slug)
                # Edge-triggered: a steady room sends nothing at all. Which is
                # also why the send has to succeed BEFORE we record it as sent
                # — marking it first means one dropped frame is remembered as
                # delivered, and the client is then stuck on the previous
                # number until the room's size happens to change again. On a
                # small map that can be forever.
                if n != conn["last_presence_sent"]:
                    try:
                        ws.send(json.dumps({"type": "presence", "n": n}))
                        conn["last_presence_sent"] = n
                        last_push = now
                    except Exception:
                        pass

            if sub.overflowed.is_set():
                # This client lagged past its queue bound; a reconnect plus
                # the client's gap refetch is cheaper than a catch-up replay.
                logger.warning(f"[WS] {slug}: subscriber overflowed — closing")
                break

            try:
                payload = sub.q.get(timeout=1.0)
                ws.send(payload)
                last_push = time.time()
                continue
            except queue.Empty:
                pass

            if time.time() - last_push > KEEPALIVE:
                ws.send('{"type":"keepalive"}')
                last_push = time.time()
    finally:
        delta_hub.unsubscribe(slug, sub)
        if conn["visible"]:
            presence_registry.depart(slug, conn["identity"])


# ── Routes API ─────────────────────────────────────────────────────────────

@app.route("/api/routes", methods=["POST"])
def calculate_route():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    start = data.get("start")
    end = data.get("end")
    waypoints = data.get("waypoints", [])
    if not start or not end:
        return jsonify({"error": "Missing start or end coordinates"}), 400

    rmap = resolve_map(data.get("map"))

    # Station networks have no routable street graph — you vote on points, not
    # paths. Decline cleanly (the client also gates this and never calls it).
    if rmap.network in STATION_NETWORKS:
        return jsonify({"error": "Routing is disabled for this map",
                        "routing_disabled": True}), 400

    try:
        waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
        logger.debug(
            f"[ROUTE] {rmap.slug}: start={start} end={end} "
            f"waypoints={len(waypoints_tuples)} -> OSRM"
        )
        route = rmap.osrm.calculate_route(
            start=(start[0], start[1]), end=(end[0], end[1]),
            mode="walk", waypoints=waypoints_tuples,
        )

        # OSRM is the ONLY router. The Python/Dijkstra fallback is intentionally
        # unlinked: a failed OSRM route surfaces as a 404 (the client then draws a
        # straight connector for that one segment) instead of being silently masked
        # by a second routing engine with different snapping/topology. Masking is
        # exactly what hid the proposal-midwaypoint routing failures.
        if "error" in route:
            logger.warning(
                f"[ROUTE] {rmap.slug}: OSRM could not route this request "
                f"({route.get('error')}) — returning 404 (no Python fallback)"
            )
            return jsonify(route), 404

        segments = extract_all_segments(route.get("geometry"))

        # Map OSRM route → graph edge IDs for fast voting + optimistic updates.
        # OSRM annotations are the ONLY mapping — the graph is built from the
        # same PBF + foot filter, so node ids resolve directly. The old
        # coordinate-rounding fallback masked real mapping failures and is gone;
        # an empty edge_ids list now surfaces the problem instead of hiding it.
        rmap.graph.ensure_loaded()
        edge_ids = []
        osm_node_ids = route.get("osm_node_ids", [])
        if osm_node_ids:
            edge_ids = vote_store.osm_nodes_to_edge_ids(
                osm_node_ids, rmap.graph.osm_to_graph_idx, rmap.graph.node_pair_to_edge,
            )
        # A route that resolves to NOTHING is loud; one that resolves to HALF was
        # silent, and it loses exactly as many votes per unmapped metre. It means
        # OSRM routed over ways the votable graph doesn't carry — in practice the
        # graph and OSRM were built from different OSM extracts, and the ways
        # retagged in between (e.g. Central Park's drives, cycleway →
        # unclassified+foot=yes) flip foot-routability.
        pairs = max(len(osm_node_ids) - 1, 0)
        if not edge_ids:
            logger.warning(f"[ROUTE] {rmap.slug}: OSRM route resolved to 0 edge ids")
        elif pairs and len(edge_ids) < ROUTE_EDGE_COVERAGE_MIN * pairs:
            logger.warning(
                f"[ROUTE] {rmap.slug}: only {len(edge_ids)}/{pairs} routed node pairs "
                f"mapped to votable edges — votes along the unmapped stretch are LOST. "
                f"Rebuild so graph and OSRM share one extract; "
                f"tests/validate_osrm_topology.py measures the gap."
            )

        return jsonify({
            "route": route,
            "desire_path": None,
            "desire_path_segments": segments,
            "edge_ids": edge_ids,
            "vote_mode": "walk",
        })

    except Exception as e:
        logger.error(f"[ROUTE] Error: {e}")
        import traceback
        logger.error(f"[ROUTE] {traceback.format_exc()}")
        return jsonify({"error": f"Routing failed: {str(e)}"}), 500


# ── Vote API ───────────────────────────────────────────────────────────────

@app.route("/api/vote", methods=["POST"])
def cast_vote():
    """Cast (or reverse, or remove) votes on a map — the single vote codepath.

    Body: { map, mode, vote_type, voter_id, edge_ids: [int], direction }
      direction: +1 vote for · -1 vote against · 0 remove this voter's vote.

    Every cast is directional, per-voter, and block-scoped (docs/three-layer-
    model.md §4): the voter's prior same-type rows are read, a clear-then-cast
    plan is computed (vote_semantics.plan_block_vote) that first removes their
    votes across every touched block, then casts on exactly the selection edges
    — so a block never holds both directions from one device. Maps without a
    block layer degrade to per-edge semantics via singleton blocks. The DB
    write is synchronous (under a lock) so the next vote observes the committed
    prior direction, and the broadcast carries authoritative [up, down] counts so
    clients SET — never increment — and can't drift or double-count. The
    response's `cleared` lists edges unvoted beyond the cast set so the client
    reconciles its local store.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    # Every vote must carry a voter_id (the web client always mints one in
    # localStorage; imports send the ride_id). We no longer silently fall back to
    # the raw IP as identity, so a missing id is a client bug, not anonymous use.
    if not data.get("voter_id"):
        return jsonify({"error": "voter_id required"}), 400

    rmap = resolve_map(data.get("map"))

    # Passcode gate: a vote needs the same valid token that unlocked the map.
    if _locked(rmap):
        return _locked_response()

    edge_ids = data.get("edge_ids", [])
    if not edge_ids and data.get("edge_id") is not None:
        edge_ids = [int(data["edge_id"])]
    # `point` is a fallback for clients that can't resolve a click to an edge
    # themselves; the client normally sends edge_ids (snapped via the same path
    # it uses for hover), so the server and client never disagree on the target.
    point = data.get("point")
    mode = data.get("mode", "walk")
    vt_label = (data.get("vote_type", "") or "").strip()
    # Kind of the cast's target — 'route' (corridor selection) or 'point'. Only
    # consulted when vt_label is a brand-new suggestion: the creating cast is the
    # one witness of the kind a free-text type was suggested as. Existing labels
    # keep their stored kind.
    vt_point_type = normalize_point_type(data.get("point_type"))

    # Reject unknown vote types when the map disallows user suggestions.
    if (vt_label and not rmap.allow_suggestions
            and rmap.vote_type_labels and vt_label not in rmap.vote_type_labels):
        return jsonify({"error": "This map does not accept custom vote types"}), 403

    # direction: +1 (for) / -1 (against) / 0 (remove). Default +1.
    raw_dir = data.get("direction", 1)
    direction = 0 if raw_dir == 0 else (vote_store.UP if (raw_dir or 1) >= 0 else vote_store.DOWN)

    rmap.graph.ensure_loaded()
    if not edge_ids and point:
        edge_ids = rmap.graph.snap_point_to_edge(point[0], point[1])
    try:
        edge_ids = [int(e) for e in edge_ids]
    except (TypeError, ValueError):
        return jsonify({"error": "edge_ids must be integers"}), 400
    if not edge_ids:
        return jsonify({"error": "No edges to vote on"}), 400

    device_id, ip_hash = _resolve_user(data, ip_from_voter=bool(data.get("ip_from_voter")))
    # Two identities, deliberately (vote_identity.py): `device_id` OWNS the row
    # — it decides what this press means, what it may clear, and what
    # /api/my-votes hands back — while `identity` is what the block layer counts
    # as one person. They differ whenever a browser's localStorage doesn't
    # survive the visit, which is the common case on a QR scan.
    identity = counting_identity(device_id, ip_hash)
    mode_int = vote_store.mode_to_int(mode)  # Redis key only; DB scopes by map_slug
    slug = rmap.slug

    # Backpressure: shed instead of queueing once the instance is saturated —
    # a 503 the client retries beats a 45s hang that dies at the timeout.
    t_handler0 = time.monotonic()
    if not _vote_inflight.acquire(timeout=VOTE_QUEUE_WAIT_SECONDS):
        return _shed_response("inflight", retry_after=1)
    stripe_acquired = False
    lock_wait_s = 0.0
    try:
        try:
            vt_id = vote_store.get_vote_type_id(vt_label, vt_point_type) if vt_label else 0
        except vote_store.VoteTypeLimitError as e:
            # A NEW free-text suggestion whose id would not fit the packed vote
            # key. Refuse the cast with the reason: an id past the 24-bit field
            # overflows onto the direction bit and records the vote as its own
            # opposite, so accepting it would be worse than declining it.
            logger.error(f"[VOTE:{slug}] refused new vote type {vt_label!r}: {e}")
            return jsonify({"error": str(e), "vote_type_limit": True}), 507

        # Soft per-IP cap: how many OTHER devices this IP already has on each
        # edge+type. A brand-new vote from a fresh device is dropped once the IP
        # is at the cap (re-votes/reversals by the same device are exempt).
        # ip_from_voter casts skip the lookup entirely: their ip_hash is unique
        # per voter by construction, so no other device can share it and the
        # count is always zero — the query is pure cost on bulk imports.
        ip_device_counts = count_devices_per_ip_for_edges(
            slug, edge_ids, vt_id, ip_hash, device_id
        ) if direction != 0 and not data.get("ip_from_voter") else {}

        # voter_lock serializes this voter's read-modify-write ACROSS instances
        # (Redis); the stripe serializes it within THIS process. Both are scoped
        # per (slug, COUNTING identity) — different voters proceed concurrently
        # — and together they hold whether the app runs one worker or a fleet. A
        # stripe held past the shed timeout (a wedged same-voter cast) sheds
        # rather than parking this request thread behind it.
        #
        # Scoped to the counting identity, NOT the device, because that is the
        # granularity at which two casts can now collide: co-NAT devices share a
        # `bd:` field, and block_votes' deferred HDEL would drop one of them from
        # the hash while the aggregate kept its +1. Strictly coarser than the
        # device lock it replaces, so the per-device read-modify-write below
        # stays just as protected.
        ebid = rmap.graph.edge_block_id  # None unless this map has a block layer
        stripe = _voter_lock_stripe(slug, identity)
        if not stripe.acquire(timeout=VOTE_LOCK_SHED_SECONDS):
            return _shed_response("stripe", retry_after=2)
        lock_wait_s = time.monotonic() - t_handler0
        stripe_acquired = True
        with vote_store.voter_lock(redis_client, slug, identity):
            # Block-scoped clear-then-cast (docs/three-layer-model.md §4): the
            # plan clears this device's same-type rows across every touched
            # block, then casts on exactly the selection edges.
            prior = get_voter_type_rows(slug, vt_id, device_id)
            plan = vote_semantics.plan_block_vote(edge_ids, direction, prior, ebid)

            at_cap: list[int] = []  # fresh device, IP at cap → LRU takeover below
            casts: list[tuple[int, int]] = []
            for eid, prev in plan.cast:
                # The soft per-IP cap gates only FRESH casts; reversals and
                # clears by the same device are exempt (as before the plan).
                if (prev == 0
                        and ip_device_counts.get(eid, 0) >= MAX_DEVICES_PER_IP_PER_EDGE):
                    at_cap.append(eid)
                    continue
                casts.append((eid, prev))

            # Apply the whole plan batched — one pipeline for the edge counters,
            # two for the block mirror — instead of ~2 round trips per edge
            # (the measured bulk of vote latency at scale; changelog
            # 2026-07-08-agent-load-test.html finding #2). Order is plan order:
            # clears then casts, matching the per-edge loop this replaces.
            ops = ([(eid, prev, 0) for eid, prev in plan.clear]
                   + [(eid, prev, direction) for eid, prev in casts])
            vote_store.apply_directional_batch(
                redis_client, slug, ops, mode_int, vt_id
            )
            # Mirror onto each edge's block (deduped per device); no-op without
            # a block layer or for edges outside any block.
            if ebid is not None:
                n_edges = len(ebid)
                block_ops = [(int(ebid[eid]), prev, new)
                             for eid, prev, new in ops if 0 <= eid < n_edges]
                block_votes.apply_block_deltas_batch(
                    redis_client, slug, mode_int, block_ops, vt_id, identity
                )
            changed = [eid for eid, _, _ in ops]
            reversed_any = any(prev in (vote_store.UP, vote_store.DOWN)
                               for _, prev in casts)
            cleared = [eid for eid, _ in plan.clear]
            cast_ids = [eid for eid, _ in casts]

            # At the cap, take over the IP's least-recently-active device vote
            # rather than declining: ownership moves to this device (so the
            # voter's button reflects their vote) but the direction/count are
            # untouched (the total doesn't move). {edge_id: direction now owned}.
            evicted = evict_lru_devices_for_edges(
                slug, at_cap, vt_id, ip_hash, device_id
            ) if at_cap else {}
            # Anything still at cap with no device to take over is genuinely
            # declined; the client rolls its optimistic vote back.
            capped = [e for e in at_cap if e not in evicted]

            if evicted:
                logger.info(f"[VOTE:{slug}] LRU takeover on {len(evicted)} edge(s) for "
                            f"ip={ip_hash[:8]}… (>= {MAX_DEVICES_PER_IP_PER_EDGE} devices)")
            if capped:
                logger.info(f"[VOTE:{slug}] capped {len(capped)} edge(s) for "
                            f"ip={ip_hash[:8]}… (no device to take over)")
            logger.debug(f"[VOTE:{slug}] dir={direction} changed={len(changed)} "
                         f"cleared={len(cleared)} blocks={len(plan.touched_blocks)} "
                         f"vt={vt_id} ({vt_label!r}) ip={ip_hash[:8]}…")

            if changed:
                # Persist synchronously so the next vote reads the new direction.
                try:
                    if cleared:
                        delete_edge_votes(slug, cleared, vt_id, device_id)
                    if cast_ids:
                        coords = rmap.graph.edge_midpoints(cast_ids)  # migration anchor
                        record_edge_votes(slug, cast_ids, vt_id, device_id, ip_hash,
                                          direction, coords=coords)
                except Exception as e:
                    logger.error(f"[VOTE] DB persist failed: {e}")
                # Broadcast authoritative post-write [up, down] so clients SET.
                vt_counts = vote_store.read_edge_vt_counts(
                    redis_client, slug, changed, mode_int, vt_id
                )
                block_counts = block_votes.read_block_vt_counts(
                    redis_client, slug, mode_int,
                    (b for b in plan.touched_blocks if isinstance(b, int)), vt_id,
                ) if ebid is not None else None
                vote_store.publish_delta(
                    redis_client, slug, changed, mode_int, vt_id,
                    direction=direction or vote_store.UP, reversed_vote=reversed_any,
                    vt_counts=vt_counts, block_counts=block_counts,
                )
                # No cache invalidation here: publish_delta bumped the revision,
                # which makes the cached graph-votes snapshot rev-stale on its
                # own; the endpoint's debounce decides whether to briefly keep
                # serving it. Hard invalidation is reserved for structural
                # changes (resnap, block re-bake, admin rebuild).

        _vote_metrics_record(lock_wait_s, time.monotonic() - t_handler0)
        return jsonify({
            "success": True, "edge_ids": edge_ids, "changed": changed,
            "cleared": cleared,  # edges unvoted (block-scoped clear) but not cast
            "capped": capped, "direction": direction, "reversed": reversed_any,
            "evicted": {str(eid): d for eid, d in evicted.items()},
        })

    except Exception as e:
        logger.error(f"[VOTE] Error: {e}")
        return jsonify({"error": f"Vote failed: {str(e)}"}), 500
    finally:
        if stripe_acquired:
            stripe.release()
        _vote_inflight.release()


def _resnap_city_maps(city_id: str) -> None:
    """Re-snap every map of a city onto its just-reloaded graph, then refresh Redis.

    Votes are keyed by edge id, which is only valid for one graph build. After a
    rebuild we reload the city graph from disk and re-anchor each map's votes by
    lat/lon (see vote_migration). Without this a rebuild silently mis-points every
    vote. Self-heals on the graph_reload signal; the same logic backfills via the
    migrate_votes.py CLI.
    """
    graph_registry.reload_city(city_id)
    vote_store.load_vote_types()  # repair may have registered new types
    for m in list_maps():
        if m.get("cityId") != city_id:
            continue
        slug = m["slug"]
        try:
            city = get_city(city_id)
            cg = graph_registry.get(city, m.get("network") or "streets")
            mode_int = vote_store.mode_to_int(m.get("mode", "walk"))
            labels = [vt["label"] for vt in (m.get("voteTypes") or []) if vt.get("label")]
            vote_migration.migrate_map(cg, redis_client, slug, mode_int, labels)
            # Re-snapped edge ids invalidate the derived block state (bd:/bagg:
            # are keyed by edge→block of the OLD graph). Purge it; the next
            # /api/graph-votes build lazily rebuilds from Postgres (§2.6).
            if cg.edge_block_id is not None:
                block_votes.clear(redis_client, slug, mode_int)
            _invalidate_vote_cache(slug)
        except Exception as e:
            logger.error(f"[RESNAP:{slug}] failed: {e}")


def _start_graph_reload_listener():
    """Auto-heal votes when a city's graph is rebuilt (refresh_osm publishes here)."""
    def listener():
        ps_client = redis.Redis(host=redis_host, port=redis_port, db=0, decode_responses=True)
        ps = ps_client.pubsub()
        ps.subscribe("graph_reload")
        logger.info("[PUBSUB] Subscribed to graph_reload")
        for msg in ps.listen():
            if msg["type"] != "message":
                continue
            try:
                payload = json.loads(msg["data"])
                city_id = payload.get("city")
            except (ValueError, TypeError):
                continue
            if city_id:
                logger.info(f"[PUBSUB] graph_reload for '{city_id}' → re-snapping votes")
                try:
                    _resnap_city_maps(city_id)
                except Exception as e:
                    logger.error(f"[PUBSUB] graph_reload handling failed: {e}")

    _PrewarmThread(target=listener, name="graph-reload-listener", daemon=True).start()


_start_graph_reload_listener()


@app.route("/api/my-votes", methods=["GET"])
def my_votes():
    """Return the requesting voter's directional votes on a map.

    With edge_ids: just those edges (the modal-open reconcile). Without:
    the voter's FULL vote set for the map — the client resets its local
    store from this on load, so a stale localStorage can never claim votes
    the server lacks (which would flip casts into block-unvotes)."""
    rmap_slug = (request.args.get("map") or DEFAULT_MAP_SLUG).strip()

    raw = request.args.get("edge_ids")
    edge_ids: list[int] | None = None
    if raw is not None and raw.strip() != "":
        try:
            edge_ids = [int(x) for x in raw.split(",") if x.strip() != ""]
        except ValueError:
            return jsonify({"error": "edge_ids must be comma-separated integers"}), 400

    device_id, _ip_hash = _resolve_user(request.args)
    by_edge = get_voter_edge_directions(rmap_slug, edge_ids, device_id)
    votes: dict[str, dict[str, int]] = {}
    for edge_id, vt_map in by_edge.items():
        labels: dict[str, int] = {}
        for vt_id, direction in vt_map.items():
            label = vote_store.resolve_vote_type(vt_id)
            if label:
                labels[label] = int(direction)
        if labels:
            votes[str(edge_id)] = labels

    return jsonify({"votes": votes})


# Cap on how many edge ids one /api/route-votes request scans. A long route
# through merged blocks can union thousands of edges; the client caps what it
# sends too, so past this the count degrades gracefully (undercounts).
ROUTE_VOTES_EDGE_CAP = 20000

# Batch form (`sets`): the map asks for every labelled corridor at once, so the
# budget is on the WHOLE request. Sets past either cap are answered `null` —
# "not counted" — never an empty row list, which would read as "nobody voted".
ROUTE_VOTES_MAX_SETS = 32
ROUTE_VOTES_BATCH_EDGE_CAP = 60000


def _voter_rows(counts: list[tuple[int, int, int]]) -> list[dict]:
    """(vote_type_id, direction, count) tuples → card/label rows, by label."""
    by_label: dict[str, dict[str, int]] = {}
    for vt_id, direction, count in counts:
        label = vote_store.resolve_vote_type(vt_id)
        if not label:
            continue
        row = by_label.setdefault(label, {"up": 0, "down": 0})
        row["down" if direction < 0 else "up"] += count
    rows = [{"label": label, "up": v["up"], "down": v["down"]}
            for label, v in by_label.items()]
    rows.sort(key=lambda r: r["down"] - r["up"])
    return rows


@app.route("/api/route-votes", methods=["POST"])
def route_votes():
    """Distinct-voter vote rows for route selections.

    Body: { map, edge_ids: [int] } → { rows }, or { map, sets: [[int], …] } →
    { results: [{ rows } | { rows: null }, …] } aligned with `sets`.

    Counts DISTINCT devices per (vote type, direction) across a WHOLE edge set
    — a route cast fans one device's vote onto every edge of every block it
    covers, so per-edge/per-block sums count the same person once per block.
    Every corridor number the client shows comes from here: the route-summary
    card, the hovered diamond's card, and the floating map label all read these
    rows, which is what keeps them from disagreeing about one corridor.
    POST because a selection's block-edge union routinely exceeds URL limits.
    """
    data = request.get_json(silent=True) or {}
    rmap = resolve_map(data.get("map"))
    if _locked(rmap):
        return _locked_response()

    raw_sets = data.get("sets")
    if raw_sets is not None:
        if not isinstance(raw_sets, list):
            return jsonify({"error": "sets must be a list of edge-id lists"}), 400
        try:
            sets = [[int(e) for e in s[:ROUTE_VOTES_EDGE_CAP]]
                    for s in raw_sets if isinstance(s, list)]
        except (TypeError, ValueError):
            return jsonify({"error": "sets must be a list of edge-id lists"}), 400
        if len(sets) != len(raw_sets):
            return jsonify({"error": "sets must be a list of edge-id lists"}), 400

        # Spend the budget on the sets in the order asked (the client sends them
        # strongest-first), and say out loud which ones went uncounted.
        counted: list[int] = []
        budget = ROUTE_VOTES_BATCH_EDGE_CAP
        for i, s in enumerate(sets):
            if len(counted) >= ROUTE_VOTES_MAX_SETS or len(s) > budget:
                continue
            budget -= len(s)
            counted.append(i)
        if len(counted) < len(sets):
            logger.warning(
                f"[ROUTE-VOTES] {rmap.slug}: counted {len(counted)}/{len(sets)} "
                f"edge sets (caps: {ROUTE_VOTES_MAX_SETS} sets, "
                f"{ROUTE_VOTES_BATCH_EDGE_CAP} edges)")

        answers = count_unique_voters_for_edge_sets(
            rmap.slug, [sets[i] for i in counted])
        results: list[dict] = [{"rows": None} for _ in sets]
        for i, counts in zip(counted, answers):
            results[i] = {"rows": _voter_rows(counts)}
        return jsonify({"results": results})

    raw = data.get("edge_ids")
    if not isinstance(raw, list):
        return jsonify({"error": "edge_ids must be a list of integers"}), 400
    try:
        edge_ids = [int(e) for e in raw[:ROUTE_VOTES_EDGE_CAP]]
    except (TypeError, ValueError):
        return jsonify({"error": "edge_ids must be a list of integers"}), 400

    return jsonify(
        {"rows": _voter_rows(count_unique_voters_for_edges(rmap.slug, edge_ids))})


# ── Graph data APIs ──────────────────────────────────────────────────────────

def _compose_address(props: dict) -> str:
    """Build a clean label from Photon's structured fields.

    Photon returns address parts separately, so we space-join the house number
    and street ("410 Riverside Drive") and comma-separate logical components,
    rather than echoing a fully comma-joined string ("410, Riverside Drive, …").
    """
    name = props.get("name")
    street = props.get("street")
    house = props.get("housenumber")
    parts: list[str] = []
    # POIs carry a name distinct from the street; lead with it.
    if name and name != street:
        parts.append(name)
    if house and street:
        parts.append(f"{house} {street}")
    elif street:
        parts.append(street)
    elif not parts and name:
        parts.append(name)
    # One level of locality context to disambiguate similar streets.
    locality = props.get("locality") or props.get("district") or props.get("city")
    if locality and locality not in parts:
        parts.append(locality)
    return ", ".join(parts)


@lru_cache(maxsize=512)
def _photon_features(query: str, bbox: str, lat: float, lon: float) -> tuple:
    """One Photon (OSM autocomplete geocoder) request, biased to a city, cached.

    Photon does prefix matching ("410 river" -> "410 Riverside Drive"), which
    Nominatim's /search does not. Its bbox is a soft bias, so we hard-filter
    results to the city's bounds to keep out-of-city hits out of the dropdown.
    Returns raw features so callers can read structured fields (street/name/
    housenumber), not just the composed label.
    """
    resp = requests.get(
        "https://photon.komoot.io/api/",
        params={"q": query, "limit": 10, "bbox": bbox, "lat": lat, "lon": lon},
        headers={"User-Agent": (
            "CityEdit/1.0 "
            "(https://github.com/edbltn/city-edit; "
            "eric.didier.bolton@gmail.com)")},
        timeout=5,
    )
    resp.raise_for_status()
    minlon, minlat, maxlon, maxlat = (float(v) for v in bbox.split(","))
    feats = []
    for feat in resp.json().get("features", []):
        coords = feat.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        flon, flat = float(coords[0]), float(coords[1])
        if not (minlat <= flat <= maxlat and minlon <= flon <= maxlon):
            continue
        feats.append({"lat": flat, "lon": flon, "props": feat.get("properties", {})})
    return tuple(feats)


def _to_result(feat: dict) -> dict | None:
    """Compose a feature into the {lat, lon, display_name} client shape."""
    display = _compose_address(feat["props"])
    if not display:
        return None
    return {"lat": feat["lat"], "lon": feat["lon"], "display_name": display}


def _matches_fragment(text: str, fragment: str) -> bool:
    """True if any word in `text` starts with the fragment's first word.

    Keeps fallback results relevant ("Riv" -> "Riverside Drive") while dropping
    unrelated hits Photon returns when matching a bare fragment near the center.
    """
    head = fragment.lower().split()[0]
    return any(w.startswith(head) for w in re.findall(r"[a-z0-9]+", text.lower()))


def _photon_search(query: str, bbox: str, lat: float, lon: float) -> tuple:
    """Search Photon, recovering the exact address for "<house> <short fragment>".

    Photon won't match a house number followed by a very short street fragment
    ("410 Riv"), though it matches the fragment alone. When the direct query is
    empty we discover the street name from the bare fragment, then re-ask Photon
    for that street WITH the house number, so the exact "410 Riverside Drive" is
    returned. If no such house number exists, we show street-level hits instead.
    """
    direct = [r for f in _photon_features(query, bbox, lat, lon) if (r := _to_result(f))]
    if direct:
        return tuple(direct[:6])

    m = re.match(r"(\d+)\s+(.+)", query)
    if not m:
        return ()
    house, fragment = m.group(1), m.group(2).strip()
    if len(fragment) < 2:
        return ()

    frag_feats = _photon_features(fragment, bbox, lat, lon)
    # Prefer real street names; fall back to place names if none prefix-match.
    streets: list[str] = []
    names: list[str] = []
    for f in frag_feats:
        street = f["props"].get("street")
        if street and _matches_fragment(street, fragment) and street not in streets:
            streets.append(street)
        name = f["props"].get("name")
        if name and _matches_fragment(name, fragment) and name not in names:
            names.append(name)

    # Re-query each candidate street with the house number; keep exact matches.
    exact: list[dict] = []
    seen: set[str] = set()
    for street in (streets or names)[:3]:
        for f in _photon_features(f"{house} {street}", bbox, lat, lon):
            if f["props"].get("housenumber") != house:
                continue
            r = _to_result(f)
            if r and r["display_name"] not in seen:
                seen.add(r["display_name"])
                exact.append(r)
    if exact:
        return tuple(exact[:6])

    # No exact house number on a matching street; show street-level hits.
    street_level = [r for f in frag_feats
                    if (r := _to_result(f)) and _matches_fragment(r["display_name"], fragment)]
    return tuple(street_level[:6])


@app.route("/api/geocode", methods=["GET"])
def geocode():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400
    rmap = resolve_map(request.args.get("map"))
    try:
        clat, clon = rmap.city.center
        results = _photon_search(query, rmap.city.geocode_bbox, clat, clon)
        return jsonify({"results": list(results)})
    except requests.RequestException as e:
        logger.error(f"[GEOCODE] Geocoding error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reverse-geocode", methods=["GET"])
def reverse_geocode():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400
    try:
        rmap = resolve_map(request.args.get("map"))
        # Station networks carry their own names (the intersection) in the
        # topology, and have no street graph to reverse-geocode against.
        if rmap.network in STATION_NETWORKS:
            return jsonify({"address": None, "lat": lat, "lng": lon})
        address = rmap.graph.provider.reverse_geocode(lat, lon)
        return jsonify({"address": address or None, "lat": lat, "lng": lon})
    except Exception as e:
        logger.error(f"[GEOCODE] Reverse geocoding error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/tiles/<path:filename>")
def serve_tiles(filename):
    tiles_dir = os.path.join(os.path.dirname(__file__), "osm_data")
    response = send_from_directory(tiles_dir, filename)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "public, max-age=604800"
    response.headers["Access-Control-Allow-Origin"] = "*"
    # Content-Range isn't a CORS-safelisted response header: without exposing
    # it, the pmtiles client (cross-origin in local dev, :3000 → :5001) can't
    # size the archive and MapLibre's style never finishes loading — the whole
    # block layer silently never renders. Prod (nginx, same-origin) never hits
    # this, which is why it only bit local dev.
    response.headers["Access-Control-Expose-Headers"] = (
        "Content-Range, Accept-Ranges, Content-Length, ETag"
    )
    return response


# ── z/x/y block tiles ────────────────────────────────────────────────────────
# Browsers never HTTP-cache the pmtiles:// protocol's range requests (206
# responses), so warm visits re-download the whole viewport's block tiles
# (~1MB). These discrete tile URLs are cacheable by the browser, nginx, and any
# CDN; the client appends ?v=<blocksVersion> so a re-baked archive busts every
# cache. tippecanoe stores tiles gzip-compressed, so the hot path is a
# pass-through of stored bytes; the gzip LRU only fills for uncompressed
# archives.

_TILE_GZIP_LRU_MAX = 512

_tile_readers: dict[str, tuple] = {}  # city_id → (reader, header, mtime_ns)
_tile_gzip_lru: "OrderedDict[tuple, bytes]" = OrderedDict()
_tile_lock = threading.Lock()


def _blocks_tile_reader(city_id: str) -> tuple:
    """(reader, header, mtime_ns) for a city's blocks.pmtiles archive.

    blocks.pmtiles is re-baked IN PLACE (lazy re-bake), so the cache is keyed
    on st_mtime_ns and the archive is reopened whenever it changes. Raises
    OSError (incl. FileNotFoundError) when the city has no archive.
    """
    path = os.path.join(os.path.dirname(__file__), "osm_data", city_id, "blocks.pmtiles")
    mtime_ns = os.stat(path).st_mtime_ns
    entry = _tile_readers.get(city_id)
    if entry and entry[2] == mtime_ns:
        return entry
    with _tile_lock:
        entry = _tile_readers.get(city_id)
        if entry and entry[2] == mtime_ns:
            return entry
        f = open(path, "rb")  # noqa: SIM115 — held open for the mmap's lifetime
        reader = PMTilesReader(MmapSource(f))
        header = reader.header()
        # Drop gzip-LRU entries built from the previous bake. The old reader's
        # file object is intentionally leaked: another greenlet may still be
        # mid-read through its mmap, and re-bakes are rare.
        for key in [k for k in _tile_gzip_lru if k[0] == city_id]:
            del _tile_gzip_lru[key]
        entry = (reader, header, mtime_ns)
        _tile_readers[city_id] = entry
        logger.info(
            f"[TILES] opened {city_id}/blocks.pmtiles "
            f"(z{header['min_zoom']}-{header['max_zoom']}, "
            f"compression={header['tile_compression'].name})"
        )
        return entry


@app.route("/api/tile/<city_id>/blocks/<int:z>/<int:x>/<int:y>.mvt")
def blocks_tile(city_id, z, x, y):
    # city_id feeds a filesystem path — only registered cities pass.
    if get_city(city_id) is None:
        return jsonify({"error": f"Unknown city '{city_id}'"}), 404
    try:
        reader, header, mtime_ns = _blocks_tile_reader(city_id)
    except Exception:
        # OSError when the city has no archive; anything else = a mid-re-bake
        # read of the in-place-written file (header deserialization fails).
        return jsonify({"error": f"No block tiles for city '{city_id}'"}), 404
    if not (header["min_zoom"] <= z <= header["max_zoom"]):
        return jsonify({"error": "Zoom out of range"}), 404
    # zxy_to_tileid raises ValueError past (1<<z)-1 — scanners would 500.
    if not (0 <= x < (1 << z) and 0 <= y < (1 << z)):
        return jsonify({"error": "Tile out of range"}), 404

    # Strong per-tile ETag; the ?v= cache-buster (blocksVersion) makes each URL
    # unique per bake, so the body behind it is effectively immutable.
    etag = f'"bt-{city_id}-{z}-{x}-{y}-{mtime_ns}"'
    cache_control = "public, max-age=31536000, immutable"
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = cache_control
        return resp

    stored_gzip = header["tile_compression"] == Compression.GZIP
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    key = (city_id, z, x, y)
    body = None
    if accepts_gzip:
        # Serves both roles: gzip-on-demand cache for uncompressed archives AND
        # a hot-tile bytes cache for stored-gzip pass-through — Reader.get
        # re-parses the root+leaf directories in pure Python (~5ms, no gevent
        # yields) on EVERY call, so a cold-nginx-cache burst would otherwise
        # head-of-line-block the worker. Purged on re-bake by mtime key change.
        with _tile_lock:
            body = _tile_gzip_lru.get(key)
            if body is not None:
                _tile_gzip_lru.move_to_end(key)

    if body is None:
        try:
            data = reader.get(z, x, y)
        except Exception:
            # Mid-re-bake reads of the in-place-written archive can surface as
            # deserialization errors — treat as absent rather than 500.
            logger.warning(f"[TILES] unreadable tile {city_id}/{z}/{x}/{y}")
            return jsonify({"error": "Tile unreadable"}), 404
        if data is None or not data:
            # 204: MapLibre treats an empty 2xx as an empty tile (no error, no
            # parent-tile probing) and, unlike a 404, it is cacheable — blocks
            # coverage stops at the street network, so water/edge tiles inside
            # the pan bounds land here on every pan otherwise.
            resp = app.response_class(status=204)
            resp.headers["ETag"] = etag
            resp.headers["Cache-Control"] = cache_control
            return resp
        if stored_gzip:
            # tippecanoe already gzipped the stored tiles — pass them through
            # (decompress only for the rare client that doesn't accept gzip).
            body = data if accepts_gzip else gzip.decompress(data)
        elif accepts_gzip:
            body = gzip.compress(data, compresslevel=6)
        else:
            body = data
        if accepts_gzip and len(body) <= 131072:
            with _tile_lock:
                _tile_gzip_lru[key] = body
                while len(_tile_gzip_lru) > _TILE_GZIP_LRU_MAX:
                    _tile_gzip_lru.popitem(last=False)

    resp = app.response_class(response=body, mimetype="application/vnd.mapbox-vector-tile")
    if accepts_gzip:
        # In every accepts_gzip branch above, body holds gzip bytes.
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = cache_control
    resp.headers["ETag"] = etag
    return resp


# ── Map preview images ───────────────────────────────────────────────────────
# Previews are captured daily into a private GCS bucket (project enforces Public
# Access Prevention, so objects can't be made public). nginx proxies /previews/
# here; we stream the object using the Cloud Run service account's metadata
# token. On any miss we return 404 so nginx falls back to the build-time baked
# copy under /var/www/html/previews.
PREVIEW_BUCKET = os.environ.get("PREVIEW_BUCKET", "")
_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)
_gcs_token = {"value": "", "expires_at": 0.0}


def _gcs_access_token() -> str | None:
    """Cached GCP access token from the metadata server (refreshed 5min early)."""
    now = time.time()
    if _gcs_token["value"] and now < _gcs_token["expires_at"]:
        return _gcs_token["value"]
    try:
        r = requests.get(
            _METADATA_TOKEN_URL,
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("[PREVIEWS] Could not fetch GCS access token: %s", e)
        return None
    _gcs_token["value"] = data["access_token"]
    _gcs_token["expires_at"] = now + int(data.get("expires_in", 3600)) - 300
    return _gcs_token["value"]


@app.route("/previews/<path:name>")
def serve_preview(name):
    """Stream a map preview PNG from the private GCS bucket (auth via the Cloud
    Run SA token). 404 on any miss so nginx serves the baked copy instead."""
    if not PREVIEW_BUCKET:
        return "", 404
    token = _gcs_access_token()
    if not token:
        return "", 404
    url = f"https://storage.googleapis.com/{PREVIEW_BUCKET}/{name}"
    try:
        r = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=10
        )
    except requests.RequestException as e:
        logger.warning("[PREVIEWS] Preview fetch failed for %s: %s", name, e)
        return "", 404
    if r.status_code != 200:
        return "", 404
    resp = Response(r.content, mimetype=r.headers.get("Content-Type", "image/png"))
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/graph-version", methods=["GET"])
def graph_version():
    """Cheap content hash of a city's topology, used as a client cache key."""
    rmap = resolve_map(request.args.get("map"))
    if _locked(rmap):
        return _locked_response()
    rmap.graph.ensure_loaded()
    if rmap.graph.topology_etag is None:
        return jsonify({"error": "Graph not loaded"}), 500
    # `blocks` versions the edge→block mapping SEPARATELY from the topology: a
    # re-baked block set (same graph → same `version`) must still bust the
    # client's cached GTB2 blob, whose trailing section carries edge_block_id.
    resp = jsonify({
        "version": rmap.graph.topology_etag,
        "blocks": rmap.graph.blocks_version,
    })
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/graph-topology", methods=["GET"])
def graph_topology():
    rmap = resolve_map(request.args.get("map"))
    if _locked(rmap):
        return _locked_response()
    rmap.graph.ensure_loaded()

    # Binary topology (?format=bin): a compact ArrayBuffer the client decodes
    # without JSON.parse-ing the ~150MB string (which OOM-crashes mobile Safari).
    # Its ETag is distinct from the JSON one so an intermediary never crosses them.
    if request.args.get("format") == "bin":
        # -bin2: GTB2 blob (adds n_blocks + trailing edge_block_id section);
        # distinct suffix so clients holding the GTB1 blob refetch. The blocks
        # version rides along so a re-baked mapping (same topology) busts too.
        blocks_tag = f"-{rmap.graph.blocks_version}" if rmap.graph.blocks_version else ""
        bin_etag = (rmap.graph.topology_etag or '"x"')[:-1] + f'-bin2{blocks_tag}"'
        # Tolerate a weak validator: responses gzipped by nginx historically
        # reached clients with W/-prefixed etags.
        inm = (request.headers.get("If-None-Match") or "").removeprefix("W/")
        if inm == bin_etag:
            resp = app.response_class(status=304)
            resp.headers["ETag"] = bin_etag
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
        # Prefer brotli (~17% smaller than the gzip twin on the NYC blob —
        # 15.6MB vs 18.8MB — every current browser advertises br); gzip is the
        # fallback, identity last. Token-parse the header so "br" can't match
        # inside another coding name.
        codings = {t.strip().split(";")[0]
                   for t in (request.headers.get("Accept-Encoding") or "").lower().split(",")}
        body_enc = None
        served_bin = None
        if "br" in codings:
            served_bin = rmap.graph.topology_binary_br()
            if served_bin is not None:
                body_enc = "br"
        if served_bin is None and "gzip" in codings:
            served_bin = rmap.graph.topology_binary_gz()
            body_enc = "gzip"
        if served_bin is None:
            served_bin = rmap.graph.topology_binary()
        resp = app.response_class(
            response=served_bin, status=200, mimetype="application/octet-stream",
        )
        if body_enc:
            resp.headers["Content-Encoding"] = body_enc
        resp.headers["Vary"] = "Accept-Encoding"
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["ETag"] = bin_etag
        return resp

    # JSON topology exists only for station networks (tiny, names matter);
    # street networks are binary-only — the client always requests format=bin
    # for them, and the ~150MB JSON string is no longer kept resident.
    if rmap.graph.topology_json is None:
        return jsonify({"error": "JSON topology not served for street networks; "
                                 "use format=bin"}), 410

    etag = rmap.graph.topology_etag
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    resp = app.response_class(
        response=rmap.graph.topology_json, status=200, mimetype="application/json",
    )
    resp.headers["Cache-Control"] = "public, max-age=86400"
    if etag:
        resp.headers["ETag"] = etag
    return resp


def _graph_votes_etag(
    slug: str, mode: str | None, entry: dict, gzipped: bool, sparse: bool = False,
) -> str:
    """ETag derived from the SERVED entry (its rev + blocks stamp), never from
    the live revision — the etag must always describe the body it rides with
    (a live-rev etag over a debounced body is the 304-pins-stale-body bug of
    the blocks re-bake incident). The encoding AND the format ride in the tag
    because gzip/identity and sparse/dense are different bytes."""
    bstamp = entry.get("bstamp")
    return (f'"v-{slug}-{mode or "all"}-{entry["rev"]}'
            f'{f"-b{bstamp}" if bstamp else ""}'
            f'{"-sp" if sparse else ""}{"-gz" if gzipped else ""}"')


@app.route("/api/client-timing", methods=["POST"])
def client_timing():
    """First-map-load beacon (navigator.sendBeacon from the client once the
    loader dismisses). Emits one [MAPLOAD] log line per page load; the
    log-based distribution metric `cityedit_map_load_ms`
    (terraform/monitoring.tf) extracts `ms=` from it to chart P50/P95/P99
    first-load latency on the System Health dashboard. Unauthenticated and
    fire-and-forget by design: bad payloads are dropped silently (204 always)
    so the endpoint can never become an error-noise or retry source."""
    try:
        payload = orjson.loads(request.get_data(cache=False) or b"{}")
        ms = float(payload.get("ms"))
    except (ValueError, TypeError, AttributeError, orjson.JSONDecodeError):
        return "", 204
    if not (0 < ms <= 600_000):  # >10min is a suspended tab, not a load
        return "", 204
    slug = re.sub(r"[^a-zA-Z0-9_-]", "", str(payload.get("map") or ""))[:64] or "unknown"
    nav = re.sub(r"[^a-z_]", "", str(payload.get("nav") or ""))[:16] or "unknown"
    cached = 1 if payload.get("cachedTopo") else 0
    # Campaign source (?src=… captured by the client, e.g. "qr-poster") —
    # extracted as the `src` label on cityedit_map_load_ms, so visits-by-source
    # is a Metrics Explorer group-by. "direct" = untagged visit.
    src = re.sub(r"[^a-zA-Z0-9_-]", "", str(payload.get("src") or ""))[:32] or "direct"
    logger.info(f"[MAPLOAD] map={slug} ms={int(ms)} cached_topo={cached} nav={nav} src={src}")
    return "", 204


# ── Printed stickers (QR codes on poles) ────────────────────────────────────

@app.route("/api/stickers/<code>", methods=["GET"])
def sticker_lookup(code):
    """What a scan of a printed sticker should do.

    Two answers, and which one comes back is the whole design:

      * unresolved — nobody has voted from this sticker yet, so we do not know
        which pole it is on. The client asks the scanner to share their
        location.
      * resolved — someone already did, and the sticker is pinned to that spot.
        The client goes straight there with the vote type preselected, and never
        asks for location again.

    Reading also counts the scan (see database.get_sticker): scans-per-sticker
    is how you tell a busy corner from a wall nobody walks past, and it is the
    only place that number exists.

    Never cached. A sticker flips from unresolved to resolved exactly once, and
    a CDN holding the old answer would keep prompting people for a location that
    is already known.
    """
    sticker = get_sticker(code, count_scan=True)
    if not sticker:
        return jsonify({"error": "Unknown sticker code"}), 404
    resp = jsonify(sticker)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/stickers/<code>/resolve", methods=["POST"])
def sticker_resolve(code):
    """Pin a sticker to the place it turned out to be.

    Posted by the client after the scan's FIRST vote lands, not when the browser
    hands over a location. A shared location says "I am here"; a cast vote says
    "and this is the thing that is wrong here", which is what the sticker is
    claiming to be about. It also means a scanner who shares location and then
    wanders off leaves the sticker free for whoever actually uses it.

    Write-once and race-safe in SQL (database.resolve_sticker). A second caller
    gets 200 with the location that won, because from the client's point of view
    "the sticker now has a location" is the same outcome either way.
    """
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data["lat"])
        lon = float(data["lng"] if "lng" in data else data["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400

    # The rest of the binding. All optional: a client from before this change
    # posts only a point, and that still resolves the code to a place.
    map_slug = re.sub(r"[^a-zA-Z0-9_-]", "", str(data.get("map_slug") or ""))[:64]
    vote_type = str(data.get("vote_type") or "")[:120]
    # `w` is the canonical selection string. Kept as text rather than parsed
    # into columns: the client owns that grammar (selection/serialize.ts) and it
    # already carries route waypoints and forced-corridor tokens the server has
    # no reason to understand. Character-class it so nothing but a selection can
    # be stored, and bound it so a hostile caller cannot post a novel.
    w = str(data.get("w") or "")[:400]
    if w and not re.fullmatch(r"[0-9.,;\-a-fA-F]*", w):
        w = ""

    device_id, _ip_hash = _resolve_user(data)
    resolved = resolve_sticker(code, lat, lon, device_id,
                               map_slug=map_slug, vote_type=vote_type, w=w)
    if not resolved:
        return jsonify({"error": "Unknown sticker code or bad coordinates"}), 404
    return jsonify(resolved)


@app.route("/api/graph-votes", methods=["GET"])
def graph_votes():
    """Return per-edge and per-node vote arrays for a map.

    Indices match /api/graph-topology. Served from a per-(map, mode) snapshot
    cache: a snapshot serves while its revision is current OR for a short
    debounce window after it staled (clients reconcile forward from WS deltas,
    which carry authoritative counts) — under sustained voting the revision
    bumps per cast, and rebuilding the full arrays per cast was finding #3 of
    changelog/2026-07-08-agent-load-test.html. Bodies are pre-gzipped once per
    rebuild and shared by every client.
    """
    rmap = resolve_map(request.args.get("map"))
    if _locked(rmap):
        return _locked_response()

    mode = request.args.get("mode") or None
    cache_key = f"{rmap.slug}:{mode}" if mode else rmap.slug
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    # blocks_stamp (a stat, no graph load) gates servability: a blocks re-bake
    # renumbers block ids WITHOUT bumping rev, so an entry built against the
    # previous block set must not serve even inside the debounce window.
    bstamp = rmap.graph.blocks_stamp()

    def _stamp_revs(resp, served_rev: int):
        """Tell the client WHICH revision the body it just got describes, and
        which one is live.

        The debounce + stale-while-revalidate paths below deliberately serve a
        snapshot that is one or more revisions behind: a live session
        reconciles forward from WS deltas, so staleness is invisible there. A
        PAGE LOAD has no deltas to reconcile from — it paints exactly what this
        response says — so a reader who just voted and hit refresh saw their
        own vote missing (or an unvote still standing) with nothing to correct
        it. These headers let the client notice it is behind and schedule one
        refetch once the background rebuild has had time to land."""
        resp.headers["X-Vote-Rev"] = str(served_rev)
        resp.headers["X-Vote-Rev-Current"] = str(rev)
        # Custom headers aren't CORS-safelisted; local dev reads the API
        # cross-origin (:3000 → :5001), where an unexposed header is simply
        # invisible to fetch() (same trap as the tiles Content-Range above).
        resp.headers["Access-Control-Expose-Headers"] = (
            "X-Vote-Rev, X-Vote-Rev-Current, ETag"
        )
        return resp

    entry = _vote_cache_peek(cache_key)
    if entry is not None and entry.get("bstamp") != bstamp:
        entry = None
    servable = entry is not None and (
        entry["rev"] == rev
        or time.monotonic() - entry["built"] < _VOTE_CACHE_DEBOUNCE
    )

    if not servable:
        lock = _build_lock_for(cache_key)
        if entry is not None:
            # Stale-while-revalidate, for real: serve the snapshot we hold NOW
            # and rebuild on a background OS thread (the GIL hands off every few
            # ms, so the gevent hub keeps serving meanwhile). The rebuild used to
            # run inline on whichever request drew the short straw — +~6s on a
            # big map, the dominant cold-load term in the [MAPLOAD] P99, while
            # every OTHER concurrent caller already served stale. Staleness stays
            # bounded by the build duration either way; clients reconcile forward
            # from WS deltas, which carry authoritative counts.
            _PrewarmThread(target=_rebuild_votes_snapshot,
                           args=(rmap, mode, cache_key, lock),
                           name=f"votes-swr-{cache_key}", daemon=True).start()
        else:
            # Nothing cached at all (fresh instance past prewarm, LRU eviction,
            # a blocks re-bake) — there is nothing to serve stale, so this one
            # request builds inline behind the single-flight lock.
            with lock:
                try:
                    rmap.graph.ensure_loaded()
                    fresh_rev = int(
                        redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
                    entry = (_vote_cache_get(cache_key, fresh_rev)
                             or _build_graph_votes_body_locked(
                                 rmap, mode, fresh_rev, cache_key))
                except Exception as e:
                    return jsonify({"error": str(e)}), 500

    # format=sparse: nonzero-only body decoded into typed arrays client-side
    # (~50x smaller download + parse; the dense body OOM'd mobile Safari on
    # vote-heavy maps). Dense stays the default for old clients still cached.
    sparse_fmt = (request.args.get("format") == "sparse"
                  and entry.get("sp_body") is not None)
    wants_gzip = "gzip" in (request.headers.get("Accept-Encoding") or "").lower()
    etag = _graph_votes_etag(rmap.slug, mode, entry, wants_gzip, sparse_fmt)
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=5"
        resp.headers["Vary"] = "Accept-Encoding"
        # A 304 confirms the client's cached body — which is the SERVED entry,
        # so it can be behind exactly like a 200 is. Stamp it too, or a
        # revalidating client never learns it's stale.
        return _stamp_revs(resp, entry["rev"])

    if sparse_fmt:
        served = entry["sp_gz"] if wants_gzip else entry["sp_body"]
    else:
        served = entry["gz"] if wants_gzip else entry["body"]
    resp = app.response_class(response=served, status=200, mimetype="application/json")
    if wants_gzip:
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = "public, max-age=5"
    resp.headers["ETag"] = etag
    return _stamp_revs(resp, entry["rev"])


# ── Admin ──────────────────────────────────────────────────────────────────

@app.route("/api/admin/maps/<slug>/subdomain", methods=["POST", "DELETE"])
def admin_set_subdomain(slug):
    """Attach (POST {subdomain}) or clear (DELETE) a map's vanity subdomain.

    Requires the X-Admin-Token header to match ADMIN_TOKEN. Once set, the map is
    reachable at <subdomain>.<root> and apex/slug visitors redirect there. DNS +
    TLS for the subdomain must already point at this service (see docs).
    """
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 403
    if request.method == "DELETE":
        ok, msg = set_map_subdomain(slug, None)
    else:
        sub = _normalize_slug((request.get_json(silent=True) or {}).get("subdomain") or "")
        if not sub:
            return jsonify({"error": "subdomain is required"}), 400
        ok, msg = set_map_subdomain(slug, sub)
    if ok:
        invalidate_map_cache(slug)
    return jsonify({"ok": ok, "slug": slug, "message": msg}), (200 if ok else 400)


@app.route("/api/admin/maps/<slug>/vote-sources", methods=["POST", "DELETE"])
def admin_set_vote_sources(slug):
    """Register (POST) or clear (DELETE) where a map's imported votes came from.

    Body: {sources: [{voter_id, url, title?}], merge?: bool} — the SAME
    voter_id the import cast with, hashed here exactly as /api/vote hashes it,
    so the registry keys match the stored rows. Replaces the whole registry for
    the map unless `merge` is set, in which case the entries are folded into
    the existing registry — what the weekly importer needs, since it only knows
    the proposals it just cast.

    An empty `sources` is an error for a wholesale write (use DELETE to clear,
    so no caller wipes a registry by sending nothing) but a no-op under
    `merge`. That asymmetry is deliberate: it gives a merging client a probe it
    can send safely, since a build predating `merge` rejects it and so reveals
    that a wholesale write is all this server would do.

    Requires the X-Admin-Token header to match ADMIN_TOKEN.
    """
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 403
    if request.method == "DELETE":
        ok, msg = set_vote_sources(slug, [])
    else:
        body = request.get_json(silent=True) or {}
        sources = body.get("sources")
        merge = bool(body.get("merge"))
        if not isinstance(sources, list) or (not sources and not merge):
            return jsonify({"error": "sources list is required"}), 400
        resolved = []
        for s in sources:
            if not isinstance(s, dict) or not s.get("voter_id"):
                return jsonify({"error": f"missing voter_id in {s!r}"}), 400
            resolved.append({
                "device_id": hashlib.sha256(
                    str(s["voter_id"]).encode()).hexdigest()[:16],
                "url": s.get("url"),
                "title": s.get("title"),
            })
        ok, msg = set_vote_sources(slug, resolved, replace=not merge)
    return jsonify({"ok": ok, "slug": slug, "message": msg}), (200 if ok else 400)


@app.route("/api/vote-sources", methods=["POST"])
def vote_sources():
    """Source documents behind the votes on a selection's edges.

    Body: { map, edge_ids: [int] } → { sources: {label: [{url, title}]} }, the
    proposals whose imported votes actually landed on those edges (see
    fetch_vote_sources_for_edges). Empty for maps with no source registry.
    POST for the same reason as /api/route-votes: block-edge unions are large.
    """
    data = request.get_json(silent=True) or {}
    rmap = resolve_map(data.get("map"))
    if _locked(rmap):
        return _locked_response()

    raw = data.get("edge_ids")
    if not isinstance(raw, list):
        return jsonify({"error": "edge_ids must be a list of integers"}), 400
    try:
        edge_ids = [int(e) for e in raw[:ROUTE_VOTES_EDGE_CAP]]
    except (TypeError, ValueError):
        return jsonify({"error": "edge_ids must be a list of integers"}), 400

    by_label: dict[str, list[dict]] = {}
    for label, url, title, _n in fetch_vote_sources_for_edges(rmap.slug, edge_ids):
        by_label.setdefault(label, []).append({"url": url, "title": title or ""})
    return jsonify({"sources": by_label})


@app.route("/api/admin/maps/<slug>/promote-vote-types", methods=["POST"])
def admin_promote_vote_types(slug):
    """Snapshot a map's vote-type set into a featured vote_type_lists row so it
    becomes a selectable option in the Propose-a-Map picker.

    Requires the X-Admin-Token header to match ADMIN_TOKEN. Optional JSON
    {name} overrides the list's display name (defaults to the map's name).
    """
    if not _admin_authorized():
        return jsonify({"error": "Unauthorized"}), 403
    name = (request.get_json(silent=True) or {}).get("name") or ""
    ok, msg = promote_vote_types(slug, name)
    if ok:
        invalidate_map_cache(slug)
    return jsonify({"ok": ok, "slug": slug, "message": msg}), (200 if ok else 400)


@app.route("/api/admin/refresh-osm", methods=["POST"])
def admin_refresh_osm():
    logger.info("[ADMIN] OSM refresh triggered")
    city_id = (request.get_json(silent=True) or {}).get("city", "nyc")
    if not get_city(city_id):
        return jsonify({"error": f"Unknown city '{city_id}'"}), 400
    try:
        import subprocess
        result = subprocess.run(
            ["python", "refresh_osm.py", "--city", city_id, "--force"],
            capture_output=True, text=True, timeout=1800,
        )
        logger.info(f"[ADMIN] Refresh exit code {result.returncode}")
        if result.returncode == 0:
            graph_registry.get(get_city(city_id))  # reload into registry
            return jsonify({"status": "success",
                            "stdout": result.stdout[-2000:] if result.stdout else ""})
        return jsonify({"status": "failed",
                        "stdout": result.stdout[-2000:] if result.stdout else "",
                        "stderr": result.stderr[-2000:] if result.stderr else ""}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Refresh timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/stats", methods=["GET"])
def admin_stats():
    result = {"timestamp": time.time()}
    try:
        info = redis_client.info()
        result["redis"] = {
            "connected": True,
            "version": info.get("redis_version"),
            "used_memory_human": info.get("used_memory_human"),
            "connected_clients": info.get("connected_clients"),
        }
    except redis.ConnectionError:
        result["redis"] = {"connected": False}

    result["database"] = database.get_admin_counts() if DATABASE_URL else {"connected": False}

    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = round(ru / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)

    result["app"] = {
        "loaded_cities": graph_registry.loaded_ids(),
        "cities": list(CITIES),
        "vote_types_cached": len(vote_store.all_vote_types()),
        "peak_rss_mb": rss_mb,
    }
    # The saturation signals /health can't show (it stayed 1-2ms through the
    # July melt): vote throughput, lock waits, handler latency, shed counts,
    # and live WS fan-out size.
    result["votes"] = _vote_metrics_snapshot()
    result["ws"] = {"subscribers": delta_hub.subscriber_count()}
    result["graph_votes_cache"] = {
        "entries": len(_vote_cache),
        "mb": round(_vote_cache_bytes / (1024 * 1024), 1),
        "budget_mb": _VOTE_CACHE_MAX_MB,
    }
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
