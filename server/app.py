import json
import logging
import os
import re
import sys
import time
import hashlib
import threading
from collections import OrderedDict
from functools import lru_cache

import redis
import requests
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

import vote_store
import vote_semantics
import vote_migration
import block_votes
import database
from cities import CITIES, DEFAULT_CITY_ID, get_city, all_cities
from graph_registry import GraphRegistry, OsrmRegistry, STATION_NETWORKS
from osrm_router import extract_all_segments
from database import (
    init_db, get_cursor, record_edge_votes, delete_edge_votes,
    get_voter_edge_directions, get_voter_type_rows,
    count_devices_per_ip_for_edges, evict_lru_devices_for_edges,
    seed_presets, list_maps, get_map, get_map_by_subdomain, slug_available,
    create_map, get_map_passcode_hash, list_vote_type_lists, set_map_subdomain,
    promote_vote_types, fetch_voted_vote_type_labels,
    DATABASE_URL,
)

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

# Soft per-IP abuse cap. device_id stays the hard dedup key, but a single IP may
# hold at most this many DISTINCT devices voting a given (map, edge, vote_type).
# This is a speed bump against one person clearing localStorage to mint fresh
# device_ids and re-vote the same edge — while a threshold > 1 still lets a few
# genuine people behind one NAT/router each vote. Imports key ip_hash per-ride
# (ip_from_voter), so they're unique on both axes and never trip this.
#
# Past the cap a fresh device doesn't add a vote (which would let one IP inflate
# a total without bound) — it takes over the IP's least-recently-active device
# vote on that edge (evict_lru_devices_for_edges), so the voter's own state
# registers the vote while the total stays put.
MAX_DEVICES_PER_IP_PER_EDGE = int(os.environ.get("MAX_DEVICES_PER_IP_PER_EDGE", "10"))


def _admin_authorized() -> bool:
    """True when ADMIN_TOKEN is configured and matches the request header."""
    return bool(ADMIN_TOKEN) and request.headers.get("X-Admin-Token") == ADMIN_TOKEN

DEFAULT_MAP_SLUG = "nyc-walkways"

# ── Redis ──────────────────────────────────────────────────────────────────

redis_host = os.environ.get('REDIS_HOST', 'localhost')
redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)

logger.info(f"[REDIS] Connecting to Redis at: {redis_host}:6379")
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


def resolve_map(slug: str | None) -> ResolvedMap:
    """Resolve a map slug → city, graph, OSRM router, and policy.

    Falls back gracefully so the app still serves a city graph when the map row
    is missing (e.g. DB unavailable, or slug is a bare city id).
    """
    slug = (slug or DEFAULT_MAP_SLUG).strip()

    m = get_map(slug)
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

# Bounded LRU of built /api/graph-votes bodies, keyed by "<slug>:<mode>". This
# used to be an unbounded dict: every (map, mode) ever requested kept its full
# JSON body resident forever, so a multi-tenant server with many maps leaked
# memory until the worker OOM-crashed (exactly the "more than one tenant → crash"
# symptom). The cap evicts the least-recently-served body so memory stays flat
# regardless of how many maps exist. Access is guarded by _vote_cache_lock since
# several greenlets/requests touch it concurrently.
_VOTE_CACHE_MAX = int(os.environ.get("VOTE_CACHE_MAX", "64"))
_vote_cache: "OrderedDict[str, dict]" = OrderedDict()
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
# rapid +/− toggle can't read a stale prior direction (see /api/vote). This is a
# PER-PROCESS lock; across Flask instances/workers the Redis lock in cast_vote
# (vote_store.voter_lock) provides the cross-instance guarantee.
_proposal_vote_lock = threading.Lock()


def _vote_cache_get(cache_key: str, rev: int) -> str | None:
    """Return a cached body for this key+revision, marking it most-recently-used."""
    with _vote_cache_lock:
        cached = _vote_cache.get(cache_key)
        if cached and cached["rev"] == rev:
            _vote_cache.move_to_end(cache_key)
            return cached["body"]
    return None


def _vote_cache_put(cache_key: str, rev: int, body: str) -> None:
    """Store a built body, evicting the least-recently-used entry past the cap."""
    with _vote_cache_lock:
        _vote_cache[cache_key] = {"rev": rev, "body": body}
        _vote_cache.move_to_end(cache_key)
        while len(_vote_cache) > _VOTE_CACHE_MAX:
            _vote_cache.popitem(last=False)


def _invalidate_vote_cache(slug: str) -> None:
    """Drop every cached body for a map — the bare slug AND each "<slug>:<mode>".

    The cache is keyed per mode ("<slug>:<mode>"), but writes only know the slug,
    so a plain `pop(slug)` left the mode-scoped bodies stale. (The revision check
    in _build_graph_votes_body still kept responses CORRECT, but the stale entries
    lingered in memory and forced an extra rebuild.) Clearing all of a slug's
    variants makes invalidation actually free the right entries."""
    prefix = f"{slug}:"
    with _vote_cache_lock:
        for key in [k for k in _vote_cache if k == slug or k.startswith(prefix)]:
            _vote_cache.pop(key, None)


def _build_lock_for(cache_key: str) -> threading.Lock:
    with _build_locks_guard:
        lock = _build_locks.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _build_locks[cache_key] = lock
        return lock


def _build_graph_votes_body(rmap: ResolvedMap, mode: str | None = None) -> str:
    """Build the /api/graph-votes JSON for one map+mode, cached by its revision.

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
) -> str:
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
        votes, len(rmap.graph.edges), len(rmap.graph.nodes), rmap.graph.node_adj,
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
    arrays["n_edges"] = len(rmap.graph.edges)
    arrays["n_nodes"] = len(rmap.graph.nodes)
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
        bagg_cold = redis_client.exists(block_votes.bagg_key(rmap.slug, block_mode)) == 0
        bagg_stale = (redis_client.get(bver_key) or "") != (rmap.graph.blocks_version or "")
        if (bagg_cold or bagg_stale) and redis_client.hlen(vote_store.hash_key(rmap.slug)) > 0:
            block_votes.rebuild_from_db(
                redis_client, rmap.slug, block_mode, rmap.graph.edge_block_id,
                database.fetch_edge_vote_devices(rmap.slug))
        if bagg_cold or bagg_stale:
            redis_client.set(bver_key, rmap.graph.blocks_version or "")
        arrays.update(block_votes.build_block_arrays(
            redis_client, rmap.slug, block_mode, rmap.graph.n_blocks))
        arrays["blocks_version"] = rmap.graph.blocks_version

    body = json.dumps(arrays)

    _vote_cache_put(cache_key, rev, body)
    return body


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
                _build_graph_votes_body(resolve_map(m["slug"]))
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


# ── Pubsub delta listener (invalidates per-map vote cache on peer writes) ───

def _start_delta_listener():
    def listener():
        ps_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
        ps = ps_client.pubsub()
        ps.psubscribe("vote_deltas:*")
        logger.info("[PUBSUB] Subscribed to vote_deltas:*")
        for msg in ps.listen():
            if msg["type"] != "pmessage":
                continue
            channel = msg["channel"]
            slug = channel.split(":", 1)[1] if ":" in channel else None
            if slug:
                _invalidate_vote_cache(slug)

    t = threading.Thread(target=listener, daemon=True, name="delta-listener")
    t.start()
    return t


_start_delta_listener()


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
    device and IP axes, so the per-IP cap never collapses an import."""
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


@app.route("/api/maps", methods=["GET"])
def maps_list():
    maps = list_maps()
    # Enrich with the city's public view config for the landing grid.
    for m in maps:
        city = get_city(m["cityId"])
        if city:
            m["city"] = city.to_public()
    # Short public cache: this gatekeeps every cold page load (it hit Postgres
    # uncached on each one); the map list changes rarely, so 60s is safe.
    resp = jsonify({"maps": maps})
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


def _map_response(m: dict):
    """Enrich a map dict with its city's public config and JSON-ify it."""
    city = get_city(m["cityId"])
    if city:
        m["city"] = city.to_public()
    # Custom vote types people have already voted here — surfaced by the selector
    # only when searched, never in the default list. Drop the map's own defaults.
    default_labels = {vt.get("label") for vt in (m.get("voteTypes") or [])}
    m["searchVoteTypes"] = [
        lbl for lbl in fetch_voted_vote_type_labels(m["slug"])
        if lbl not in default_labels
    ]
    resp = jsonify(m)
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


@app.route("/api/maps/<slug>", methods=["GET"])
def map_get(slug):
    m = get_map(slug)
    if not m:
        return jsonify({"error": "Map not found"}), 404
    if m.get("requiresPasscode") and not _passcode_ok(slug):
        return _locked_stub(slug)
    return _map_response(m)


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

@sock.route("/ws")
def ws(ws):
    """Push a single map's vote deltas to a client in real time."""
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

    channel = vote_store.channel_key(slug)

    ws_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    pubsub = ws_client.pubsub()
    pubsub.subscribe(channel)

    rev = int(redis_client.get(vote_store.revision_key(slug)) or 0)
    ws.send(json.dumps({"type": "init", "rev": rev, "map": slug}))

    last_push = time.time()
    KEEPALIVE = 30
    try:
        while True:
            try:
                ws.receive(timeout=0)
            except Exception as e:
                s = str(e).lower()
                if "timed out" not in s and "no data" not in s and "connection closed" not in s:
                    logger.warning(f"[WS] receive exception: {e}")

            msg = pubsub.get_message(timeout=0.1)
            if msg and msg["type"] == "message":
                ws.send(msg["data"])
                last_push = time.time()

            if time.time() - last_push > KEEPALIVE:
                ws.send('{"type":"keepalive"}')
                last_push = time.time()
    finally:
        pubsub.unsubscribe(channel)
        pubsub.close()
        ws_client.close()


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
        logger.info(
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
        rmap.graph.ensure_loaded()
        edge_ids = []
        osm_node_ids = route.get("osm_node_ids", [])
        if osm_node_ids:
            edge_ids = vote_store.osm_nodes_to_edge_ids(
                osm_node_ids, rmap.graph.osm_to_graph_idx, rmap.graph.node_pair_to_edge,
            )
        if not edge_ids:
            edge_ids = vote_store.coords_to_edge_ids(segments, rmap.graph.coord_to_edge_idx)

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
    mode_int = vote_store.mode_to_int(mode)  # Redis key only; DB scopes by map_slug
    slug = rmap.slug

    try:
        vt_id = vote_store.get_vote_type_id(vt_label) if vt_label else 0

        # Soft per-IP cap: how many OTHER devices this IP already has on each
        # edge+type. A brand-new vote from a fresh device is dropped once the IP
        # is at the cap (re-votes/reversals by the same device are exempt).
        ip_device_counts = count_devices_per_ip_for_edges(
            slug, edge_ids, vt_id, ip_hash, device_id
        ) if direction != 0 else {}

        # voter_lock serializes this voter's read-modify-write ACROSS instances
        # (Redis); _proposal_vote_lock serializes it within THIS process. Together
        # they hold whether the app runs one worker or a horizontally-scaled fleet.
        ebid = rmap.graph.edge_block_id  # None unless this map has a block layer
        with vote_store.voter_lock(redis_client, slug, device_id), _proposal_vote_lock:
            # Block-scoped clear-then-cast (docs/three-layer-model.md §4): the
            # plan clears this device's same-type rows across every touched
            # block, then casts on exactly the selection edges.
            prior = get_voter_type_rows(slug, vt_id, device_id)
            plan = vote_semantics.plan_block_vote(edge_ids, direction, prior, ebid)

            reversed_any = False
            changed: list[int] = []
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

            def _apply(eid: int, prev: int, new: int) -> None:
                vote_store.apply_directional(
                    redis_client, slug, eid, mode_int, vt_id, new, prev
                )
                # Mirror onto the edge's block (deduped per device); no-op without
                # a block layer or for edges outside any block.
                if ebid is not None and 0 <= eid < len(ebid):
                    b = int(ebid[eid])
                    if b >= 0:
                        block_votes.apply_block_delta(
                            redis_client, slug, mode_int, b, vt_id,
                            new, prev, device_id)
                changed.append(eid)

            for eid, prev in plan.clear:
                _apply(eid, prev, 0)
            for eid, prev in casts:
                _apply(eid, prev, direction)
                if prev in (vote_store.UP, vote_store.DOWN):
                    reversed_any = True
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
            logger.info(f"[VOTE:{slug}] dir={direction} changed={len(changed)} "
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
                _invalidate_vote_cache(slug)

        return jsonify({
            "success": True, "edge_ids": edge_ids, "changed": changed,
            "cleared": cleared,  # edges unvoted (block-scoped clear) but not cast
            "capped": capped, "direction": direction, "reversed": reversed_any,
            "evicted": {str(eid): d for eid, d in evicted.items()},
        })

    except Exception as e:
        logger.error(f"[VOTE] Error: {e}")
        return jsonify({"error": f"Vote failed: {str(e)}"}), 500


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
        ps_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
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


# ── Graph data APIs ──────────────────────────────────────────────────────────

@app.route("/api/nearest-node", methods=["GET"])
def nearest_node():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400
    try:
        rmap = resolve_map(request.args.get("map"))
        node_lat, node_lon = rmap.graph.provider.nearest_node_coords(lat, lon)
        return jsonify({"lat": node_lat, "lng": node_lon})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        logger.error(f"Geocoding error: {e}")
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
        logger.error(f"Reverse geocoding error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph", methods=["GET"])
def graph_data():
    """Return graph topology for a bounding box (no vote data)."""
    try:
        bbox = request.args["bbox"]
        west, south, east, north = [float(v) for v in bbox.split(",")]
    except (KeyError, ValueError):
        return jsonify({"error": "bbox=minLon,minLat,maxLon,maxLat required"}), 400
    try:
        rmap = resolve_map(request.args.get("map"))
        if _locked(rmap):
            return _locked_response()
        data = rmap.graph.provider.get_graph_for_bbox(south, west, north, east)
        # Only the JSON-serializable topology goes over the wire; the lookup maps
        # (node_pair_to_edge keyed by int tuples, osm_to_graph_idx) are server-side
        # routing helpers and can't be JSON-encoded.
        return jsonify({"nodes": data["nodes"], "edges": data["edges"]})
    except Exception as e:
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
        logger.warning("Could not fetch GCS access token: %s", e)
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
        logger.warning("Preview fetch failed for %s: %s", name, e)
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
    if rmap.graph.topology_json is None:
        return jsonify({"error": "Graph not loaded"}), 500

    # Binary topology (?format=bin): a compact ArrayBuffer the client decodes
    # without JSON.parse-ing the ~150MB string (which OOM-crashes mobile Safari).
    # Its ETag is distinct from the JSON one so an intermediary never crosses them.
    if request.args.get("format") == "bin":
        # -bin2: GTB2 blob (adds n_blocks + trailing edge_block_id section);
        # distinct suffix so clients holding the GTB1 blob refetch. The blocks
        # version rides along so a re-baked mapping (same topology) busts too.
        blocks_tag = f"-{rmap.graph.blocks_version}" if rmap.graph.blocks_version else ""
        bin_etag = (rmap.graph.topology_etag or '"x"')[:-1] + f'-bin2{blocks_tag}"'
        if request.headers.get("If-None-Match") == bin_etag:
            resp = app.response_class(status=304)
            resp.headers["ETag"] = bin_etag
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
        resp = app.response_class(
            response=rmap.graph.topology_binary(), status=200,
            mimetype="application/octet-stream",
        )
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["ETag"] = bin_etag
        return resp

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


@app.route("/api/graph-votes", methods=["GET"])
def graph_votes():
    """Return per-edge and per-node vote arrays for a map.

    Indices match /api/graph-topology. Cached by the map's revision.
    """
    rmap = resolve_map(request.args.get("map"))
    if _locked(rmap):
        return _locked_response()
    rmap.graph.ensure_loaded()

    mode = request.args.get("mode") or None
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    etag = f'"v-{rmap.slug}-{mode or "all"}-{rev}"'
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=5"
        return resp

    try:
        body = _build_graph_votes_body(rmap, mode)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    resp = app.response_class(response=body, status=200, mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=5"
    resp.headers["ETag"] = etag
    return resp


@app.route("/api/graph.geojson", methods=["GET"])
def graph_geojson():
    rmap = resolve_map(request.args.get("map"))
    if _locked(rmap):
        return _locked_response()
    rmap.graph.ensure_loaded()
    nodes = rmap.graph.nodes
    edges = rmap.graph.edges
    features = []

    for edge in edges:
        from_idx, to_idx = edge[0], edge[1]
        name = edge[2] if len(edge) > 2 else ""
        highway = edge[3] if len(edge) > 3 else ""
        length = edge[4] if len(edge) > 4 else 0
        from_lat, from_lon = nodes[from_idx]
        to_lat, to_lon = nodes[to_idx]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[from_lon, from_lat], [to_lon, to_lat]]},
            "properties": {"type": "edge", "from_idx": from_idx, "to_idx": to_idx,
                           "name": name, "highway": highway, "length": length},
        })

    for node_idx, (lat, lon) in enumerate(nodes):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"type": "node", "node_id": node_idx,
                           "lat": round(lat, 6), "lon": round(lon, 6)},
        })

    return jsonify({"type": "FeatureCollection", "features": features})


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
    return jsonify({"ok": ok, "slug": slug, "message": msg}), (200 if ok else 400)


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

    result["app"] = {
        "loaded_cities": graph_registry.loaded_ids(),
        "cities": list(CITIES),
        "vote_types_cached": len(vote_store.all_vote_types()),
    }
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
