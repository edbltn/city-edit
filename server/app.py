import gzip
import json
import logging
import math
import queue
import os
import re
import sys
import time
import hashlib
import threading
from collections import OrderedDict
from functools import lru_cache

from pmtiles.reader import Reader as PMTilesReader, MmapSource
from pmtiles.tile import Compression

import redis
import requests
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
from itsdangerous import URLSafeTimedSerializer, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash

import vote_store
import database
from cities import CITIES, DEFAULT_CITY_ID, get_city, all_cities
from graph_registry import GraphRegistry, OsrmRegistry
from database import (
    init_db, get_cursor, record_edge_votes, record_point_vote,
    get_voter_edge_direction, get_voter_edge_directions,
    seed_presets, list_maps, get_map, get_map_by_subdomain, slug_available,
    create_map, get_map_passcode_hash, list_vote_type_lists,
    DATABASE_URL,
)

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
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

graph_registry = GraphRegistry(redis_client=redis_client, max_loaded=3)
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

    __slots__ = ("slug", "city", "graph", "osrm", "allow_suggestions",
                 "requires_passcode", "vote_type_labels")

    def __init__(self, slug, city, graph, osrm, allow_suggestions,
                 requires_passcode, vote_type_labels):
        self.slug = slug
        self.city = city
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
        vote_types = m.get("voteTypes") or []
        labels = {vt.get("label") for vt in vote_types if vt.get("label")}
        allow_suggestions = m.get("allowSuggestions", True)
        requires_passcode = m.get("requiresPasscode", False)
    else:
        city = get_city(slug) or get_city(DEFAULT_CITY_ID)
        labels = set()
        allow_suggestions = True
        requires_passcode = False

    graph = graph_registry.get(city)
    osrm = osrm_registry.get(city)
    return ResolvedMap(slug, city, graph, osrm, allow_suggestions,
                       requires_passcode, labels)


def _passcode_ok(slug: str) -> bool:
    """True if the request carries a valid passcode token for this map."""
    token = request.headers.get("X-Map-Passcode") or (
        request.get_json(silent=True) or {}).get("passcode_token")
    if not token:
        return False
    try:
        return _passcode_serializer.loads(token, max_age=PASSCODE_TOKEN_MAX_AGE) == slug
    except (BadSignature, Exception):
        return False


# ── Per-map vote response cache ───────────────────────────────────────────

_vote_cache: dict[str, dict] = {}

# Serializes the read-modify-write of a voter's per-proposal direction so a
# rapid +/− toggle can't read a stale prior direction (see /api/vote).
_proposal_vote_lock = threading.Lock()


def _build_graph_votes_body(rmap: ResolvedMap, mode: str | None = None) -> dict:
    """Build the /api/graph-votes payload for one map+mode, cached by its revision.

    Returns the cache entry: {"rev", "body" (JSON str), "gz" (gzipped bytes)}.
    The gzipped variant exists because the vote arrays are mostly zeros and
    compress ~100x — worth caching once per revision rather than re-encoding
    per request.

    `mode` scopes the legend and heatmap to a single vote namespace (e.g.
    "walkways") so proposals cast under other modes on the same map don't leak
    in. When omitted, all modes are aggregated (legacy behavior).
    """
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    cache_key = f"{rmap.slug}:{mode}" if mode else rmap.slug
    cached = _vote_cache.get(cache_key)
    if cached and cached["rev"] == rev:
        return cached

    rmap.graph.ensure_loaded()
    votes = vote_store.read_all(redis_client, rmap.slug)
    mode_filter = vote_store.mode_to_int(mode) if mode else None
    arrays = vote_store.build_arrays(
        votes, len(rmap.graph.edges), len(rmap.graph.nodes), rmap.graph.node_adj,
        mode_filter=mode_filter,
    )
    arrays["rev"] = rev
    arrays["vote_types"] = {str(k): v for k, v in vote_store.all_vote_types().items()}
    body = json.dumps(arrays)

    entry = {
        "rev": rev,
        "body": body,
        "gz": gzip.compress(body.encode(), compresslevel=6),
        # Kept for /api/heat so it can build its GeoJSON without re-reading Redis.
        "edge_votes": arrays["edge_votes"],
    }
    _vote_cache[cache_key] = entry
    return entry


# ── Heatmap fast path ───────────────────────────────────────────────────────
# Server-built GeoJSON of voted edges (norm/tHot/tPeak baked in, mirroring the
# client's maplibreHeat.ts math) so the first heat paint doesn't wait for the
# multi-MB topology download. Cached per (map, mode, revision), gzipped.

_heat_cache: dict[str, dict] = {}


def _build_heat_entry(rmap: ResolvedMap, mode: str | None = None) -> dict:
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    cache_key = f"{rmap.slug}:{mode}" if mode else rmap.slug
    cached = _heat_cache.get(cache_key)
    if cached and cached["rev"] == rev:
        return cached

    votes_entry = _build_graph_votes_body(rmap, mode)
    edge_votes = votes_entry["edge_votes"]
    nodes = rmap.graph.nodes
    edges = rmap.graph.edges

    max_votes = 1
    for v in edge_votes:
        if v > max_votes:
            max_votes = v
    log_max = math.log(max_votes + 1)

    features = []
    for i, v in enumerate(edge_votes):
        if v <= 0 or i >= len(edges):
            continue
        a = nodes[edges[i][0]]
        b = nodes[edges[i][1]]
        norm = math.log(v + 1) / log_max
        features.append({
            "type": "Feature",
            "id": i,
            "properties": {
                "norm": norm,
                "tHot": (norm - 0.2) / 0.8 if norm > 0.2 else 0,
                "tPeak": (norm - 0.7) / 0.3 if norm > 0.7 else 0,
            },
            "geometry": {
                "type": "LineString",
                # GeoJSON is [lng, lat]; graph nodes are [lat, lng].
                "coordinates": [[a[1], a[0]], [b[1], b[0]]],
            },
        })

    body = json.dumps({"type": "FeatureCollection", "features": features})
    entry = {
        "rev": rev,
        "body": body,
        "gz": gzip.compress(body.encode(), compresslevel=6),
    }
    _heat_cache[cache_key] = entry
    return entry


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
        pipe = redis_client.pipeline()
        for i, (field, cnt) in enumerate(fields):
            pipe.hset(h, str(field), cnt)
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()
        logger.info(f"[POPULATE] Loaded {len(fields)} fields into {h}")


def _prewarm():
    """Preload preset NYC maps so the first request is fast."""
    try:
        for slug in ("nyc-walkways", "nyc-bikes", "nyc-trees"):
            _build_graph_votes_body(resolve_map(slug))
        logger.info("[STARTUP] Pre-warmed preset NYC maps")
    except Exception as e:
        logger.warning(f"[STARTUP] Pre-warm failed: {e}")


# SKIP_WARMUP=1 skips the heavy graph preload/replay (used by tests + fast boots).
# _prewarm() builds the ~65MB topology JSON for the preset NYC maps, which takes
# minutes. Running it inline would block the single gevent worker past the startup
# probe / gunicorn worker-timeout / healthcheck watchdog windows, so the container
# never goes Ready. Run it in a background daemon thread instead: the worker becomes
# Ready immediately (graphs still load on demand via ensure_loaded), and the preset
# caches warm up shortly after. _populate_redis() stays inline — it's idempotent and
# fast, and serving before it completes could return empty vote data.
if os.environ.get("SKIP_WARMUP") != "1":
    _populate_redis()
    threading.Thread(target=_prewarm, name="prewarm", daemon=True).start()


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
                _vote_cache.pop(slug, None)

    t = threading.Thread(target=listener, daemon=True, name="delta-listener")
    t.start()
    return t


_start_delta_listener()


# ── Helpers ────────────────────────────────────────────────────────────────

def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _resolve_user(data_or_args) -> tuple[str, str]:
    """Return (device_id, ip_hash). device_id is the hashed device id when the
    client sends one (the dedup key), falling back to the IP for anonymous
    voters; ip_hash is always the hashed IP (recorded for abuse/analytics)."""
    ip_hash = get_client_ip()
    voter_id = data_or_args.get("voter_id")
    device_id = hashlib.sha256(str(voter_id).encode()).hexdigest()[:16] if voter_id else ip_hash
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
    return jsonify({"cities": [c.to_public() for c in all_cities()]})


@app.route("/api/vote-type-lists", methods=["GET"])
def vote_type_lists():
    return jsonify({"lists": list_vote_type_lists()})


@app.route("/api/maps", methods=["GET"])
def maps_list():
    maps = list_maps()
    # Enrich with the city's public view config for the landing grid.
    for m in maps:
        city = get_city(m["cityId"])
        if city:
            m["city"] = city.to_public()
    return jsonify({"maps": maps})


@app.route("/api/maps/<slug>", methods=["GET"])
def map_get(slug):
    m = get_map(slug)
    if not m:
        return jsonify({"error": "Map not found"}), 404
    city = get_city(m["cityId"])
    if city:
        m["city"] = city.to_public()
        # z/x/y tile source for the client's GL map (browser/CDN cacheable,
        # unlike pmtiles range requests).
        m["city"]["tiles"] = city_tiles_meta(city.id)
    return jsonify(m)


@app.route("/api/maps/check-slug", methods=["GET"])
def map_check_slug():
    slug = (request.args.get("slug") or "").strip().lower()
    return jsonify({"slug": slug, "available": bool(slug) and slug_available(slug)})


def _normalize_slug(raw: str) -> str:
    import re
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return s


@app.route("/api/maps", methods=["POST"])
def map_create():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    city_id = (data.get("city_id") or "").strip()
    slug = _normalize_slug(data.get("slug") or name)
    allow_suggestions = bool(data.get("allow_suggestions", True))
    passcode = (data.get("passcode") or "").strip()

    if not name:
        return jsonify({"error": "Map name is required"}), 400
    if not get_city(city_id):
        return jsonify({"error": f"Unknown city '{city_id}'"}), 400
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
        # No passcode required → trivially authorized.
        return jsonify({"token": _passcode_serializer.dumps(slug)})
    if passcode and check_password_hash(h, passcode):
        return jsonify({"token": _passcode_serializer.dumps(slug)})
    return jsonify({"error": "Incorrect passcode"}), 403


# ── WebSocket (delta-based, per-map) ─────────────────────────────────────────

# ── WebSocket fanout ────────────────────────────────────────────────────────
# One Redis pubsub listener per channel per process, fanning messages out to
# per-client stdlib queues. The old design gave every client its own Redis
# connection and a 10Hz get_message() poll — at hundreds of clients that's
# thousands of wakeups/sec and hundreds of Redis connections on one event
# loop. stdlib Queue/Thread are gevent-cooperative under gunicorn's
# monkeypatch and plain threads under the dev server, so this works in both.

WS_KEEPALIVE_S = 30
# Bounded so one stalled client can't buffer unbounded deltas; on overflow the
# client is dropped (it reconnects and recovers via the rev-gap refetch).
WS_QUEUE_MAX = 256

_ws_subscribers: dict[str, set["queue.Queue[str]"]] = {}
_ws_listeners: set[str] = set()
_ws_lock = threading.Lock()


def _ws_listen(channel: str) -> None:
    """Long-lived listener: one Redis pubsub → all subscribed client queues."""
    while True:
        try:
            client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
            pubsub = client.pubsub()
            pubsub.subscribe(channel)
            for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                with _ws_lock:
                    subs = list(_ws_subscribers.get(channel, ()))
                for q in subs:
                    try:
                        q.put_nowait(msg["data"])
                    except queue.Full:
                        # Slow client — mark it dead; its handler will drop it.
                        q.dead = True  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning(f"[WS] listener for {channel} died: {e}; retrying in 1s")
            time.sleep(1)


def _ws_subscribe(channel: str) -> "queue.Queue[str]":
    q: "queue.Queue[str]" = queue.Queue(maxsize=WS_QUEUE_MAX)
    q.dead = False  # type: ignore[attr-defined]
    with _ws_lock:
        _ws_subscribers.setdefault(channel, set()).add(q)
        if channel not in _ws_listeners:
            _ws_listeners.add(channel)
            threading.Thread(target=_ws_listen, args=(channel,), daemon=True).start()
    return q


def _ws_unsubscribe(channel: str, q: "queue.Queue[str]") -> None:
    with _ws_lock:
        _ws_subscribers.get(channel, set()).discard(q)


@sock.route("/ws")
def ws(ws):
    """Push a single map's vote deltas to a client in real time."""
    slug = (request.args.get("map") or DEFAULT_MAP_SLUG).strip()
    channel = vote_store.channel_key(slug)

    q = _ws_subscribe(channel)
    try:
        rev = int(redis_client.get(vote_store.revision_key(slug)) or 0)
        ws.send(json.dumps({"type": "init", "rev": rev, "map": slug}))

        while True:
            if getattr(q, "dead", False):
                break  # overflowed — force a reconnect so the client refetches
            try:
                data = q.get(timeout=WS_KEEPALIVE_S)
                ws.send(data)
            except queue.Empty:
                # Idle: keepalive doubles as dead-peer detection (send raises).
                ws.send('{"type":"keepalive"}')
    finally:
        _ws_unsubscribe(channel, q)


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

    try:
        waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
        route = rmap.osrm.calculate_route(
            start=(start[0], start[1]), end=(end[0], end[1]),
            mode="walk", waypoints=waypoints_tuples,
        )

        if "error" in route:
            # The Python router is CPU-bound (can take seconds) and blocks the
            # whole gevent event loop — every WebSocket and request on this
            # worker stalls while it runs. Fine in dev (no OSRM container);
            # set PYTHON_ROUTING_FALLBACK=0 in production so an OSRM outage
            # degrades to a routing error instead of freezing the server.
            if os.environ.get("PYTHON_ROUTING_FALLBACK", "1") != "0":
                logger.info("[ROUTE] OSRM failed, falling back to Python router")
                route = rmap.graph.provider.calculate_route(
                    start=(start[0], start[1]), end=(end[0], end[1]),
                    mode="walk", waypoints=waypoints_tuples,
                )

        if "error" in route:
            return jsonify(route), 404

        from desire_path_voting import extract_all_segments
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
    """Cast votes (packed integer keys), scoped to a map."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    rmap = resolve_map(data.get("map"))

    # Passcode gate: voting (not viewing) requires a valid token for this map.
    if rmap.requires_passcode and not _passcode_ok(rmap.slug):
        return jsonify({"error": "Passcode required", "requires_passcode": True}), 401

    edge_ids = data.get("edge_ids", [])
    if not edge_ids and data.get("edge_id") is not None:
        edge_ids = [int(data["edge_id"])]
    segments = data.get("segments", [])
    point = data.get("point")
    mode = data.get("mode", "walk")
    vt_label = (data.get("vote_type", "") or "").strip()

    # Reject unknown vote types when the map disallows user suggestions.
    if (vt_label and not rmap.allow_suggestions
            and rmap.vote_type_labels and vt_label not in rmap.vote_type_labels):
        return jsonify({"error": "This map does not accept custom vote types"}), 403

    is_proposal = data.get("direction") is not None
    direction = vote_store.UP if (data.get("direction") or 1) >= 0 else vote_store.DOWN

    rmap.graph.ensure_loaded()
    if not edge_ids and segments:
        edge_ids = vote_store.coords_to_edge_ids(segments, rmap.graph.coord_to_edge_idx)

    if not edge_ids and not point:
        return jsonify({"error": "No edge_ids or point to vote on"}), 400

    device_id, ip_hash = _resolve_user(data)
    mode_int = vote_store.mode_to_int(mode)  # Redis key only; DB scopes by map_slug
    slug = rmap.slug

    try:
        if point and not edge_ids:
            edge_ids = rmap.graph.snap_point_to_edge(point[0], point[1])
            if not is_proposal:
                threading.Thread(
                    target=lambda: record_point_vote(point, mode, ip_hash, vt_label),
                    daemon=True,
                ).start()
            if not edge_ids:
                return jsonify({"success": True, "point_vote": True})

        vt_id = vote_store.get_vote_type_id(vt_label) if vt_label else 0

        # ── Directional single-proposal vote (modal +/-) ──
        # Read-modify-write of the voter's stored direction must be serialized
        # and persisted SYNCHRONOUSLY: otherwise a quick +/− sees a stale prior
        # direction (its predecessor's write was still in flight), so a reversal
        # is mistaken for a fresh vote — the Redis up count is never decremented
        # and the server's `reversed` flag stops matching the client's optimistic
        # update. The lock + in-request persist make each vote observe the
        # committed result of the previous one, so `−` correctly overwrites `+`.
        if is_proposal:
            with _proposal_vote_lock:
                reversed_any = False
                changed: list[int] = []
                for eid in edge_ids:
                    prev = get_voter_edge_direction(slug, eid, vt_id, device_id)
                    if prev != direction:
                        vote_store.apply_directional(
                            redis_client, slug, eid, mode_int, vt_id, direction, prev
                        )
                        changed.append(eid)
                        if prev in (vote_store.UP, vote_store.DOWN):
                            reversed_any = True

                logger.info(f"[VOTE:{slug}] proposal dir={direction} changed={changed} "
                            f"vt={vt_id} ({vt_label!r}) ip={ip_hash[:8]}…")

                if changed:
                    # Persist before publishing so the next vote reads the new
                    # direction (not a background-thread race).
                    try:
                        record_edge_votes(slug, changed, vt_id, device_id, ip_hash, direction)
                    except Exception as e:
                        logger.error(f"[VOTE] DB persist failed: {e}")
                    # Broadcast authoritative post-write counts so clients SET
                    # (not increment) — immune to optimistic drift / double-count.
                    vt_counts = vote_store.read_edge_vt_counts(
                        redis_client, slug, changed, mode_int, vt_id
                    )
                    vote_store.publish_delta(
                        redis_client, slug, changed, mode_int, vt_id,
                        direction=direction, reversed_vote=reversed_any,
                        vt_counts=vt_counts,
                    )
                    _vote_cache.pop(slug, None)

            return jsonify({
                "success": True, "edge_ids": edge_ids, "changed": changed,
                "direction": direction, "reversed": reversed_any,
            })

        # ── Bulk upvote (route/point cast) ──
        count = vote_store.cast(redis_client, slug, edge_ids, mode_int, vt_id)
        logger.info(f"[VOTE:{slug}] {count} edges, vt={vt_id} ({vt_label!r}), ip={ip_hash[:8]}…")
        vote_store.publish_delta(redis_client, slug, edge_ids, mode_int, vt_id)
        _vote_cache.pop(slug, None)

        def _persist():
            try:
                record_edge_votes(slug, edge_ids, vt_id, device_id, ip_hash)
            except Exception as e:
                logger.error(f"[VOTE] DB persist failed: {e}")

        threading.Thread(target=_persist, daemon=True).start()
        return jsonify({"success": True, "edges_voted": count, "edge_ids": edge_ids})

    except Exception as e:
        logger.error(f"[VOTE] Error: {e}")
        return jsonify({"error": f"Vote failed: {str(e)}"}), 500


@app.route("/api/my-votes", methods=["GET"])
def my_votes():
    """Return the requesting voter's directional votes for the given edges on a map."""
    rmap_slug = (request.args.get("map") or DEFAULT_MAP_SLUG).strip()

    raw = request.args.get("edge_ids", "")
    try:
        edge_ids = [int(x) for x in raw.split(",") if x.strip() != ""]
    except ValueError:
        return jsonify({"error": "edge_ids must be comma-separated integers"}), 400
    if not edge_ids:
        return jsonify({"votes": {}})

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
    return response


# ── z/x/y vector tiles ──────────────────────────────────────────────────────
# Browsers never HTTP-cache range requests, so the pmtiles:// protocol
# re-downloads the whole viewport's tiles on every visit. These discrete tile
# URLs are cacheable by the browser, nginx, and any CDN. Tiles in the archive
# are uncompressed MVT; gzipped copies are kept in a small LRU since the
# viewport hot set is tiny and pbf compresses ~4x.

_TILE_CACHE_MAX = 512

_tile_readers: dict[str, tuple] = {}
_tile_gzip_lru: "OrderedDict[tuple, bytes]" = OrderedDict()
_tile_lock = threading.Lock()


def _tile_reader(city_id: str) -> tuple:
    """(reader, header, etag) for a city's pmtiles archive, cached."""
    entry = _tile_readers.get(city_id)
    if entry:
        return entry
    with _tile_lock:
        entry = _tile_readers.get(city_id)
        if entry:
            return entry
        path = os.path.join(os.path.dirname(__file__), "osm_data", city_id, "graph.pmtiles")
        f = open(path, "rb")  # noqa: SIM115 — held open for the mmap's lifetime
        reader = PMTilesReader(MmapSource(f))
        header = reader.header()
        st = os.stat(path)
        etag = f'"t-{int(st.st_mtime)}-{st.st_size}"'
        entry = (reader, header, etag)
        _tile_readers[city_id] = entry
        return entry


def city_tiles_meta(city_id: str) -> dict | None:
    """Client-facing z/x/y tile source description (None when no archive)."""
    try:
        _, header, _ = _tile_reader(city_id)
    except (FileNotFoundError, OSError):
        return None
    return {
        "template": f"/api/tile/{city_id}/{{z}}/{{x}}/{{y}}.mvt",
        "minzoom": header["min_zoom"],
        "maxzoom": header["max_zoom"],
    }


@app.route("/api/tile/<city_id>/<int:z>/<int:x>/<int:y>.mvt")
def vector_tile(city_id, z, x, y):
    try:
        reader, header, etag = _tile_reader(city_id)
    except (FileNotFoundError, OSError):
        return jsonify({"error": f"No tiles for city '{city_id}'"}), 404

    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    key = (city_id, z, x, y)
    body = None
    if accepts_gzip:
        with _tile_lock:
            body = _tile_gzip_lru.get(key)
            if body is not None:
                _tile_gzip_lru.move_to_end(key)

    if body is None:
        data = reader.get(z, x, y)
        if data is None:
            # Empty tile — 204 renders as "no features" in MapLibre.
            resp = app.response_class(status=204)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            resp.headers["ETag"] = etag
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        if header["tile_compression"] == Compression.GZIP:
            # Archive already gzipped its tiles — pass through.
            body = data
        elif accepts_gzip:
            body = gzip.compress(data, compresslevel=6)
            with _tile_lock:
                _tile_gzip_lru[key] = body
                while len(_tile_gzip_lru) > _TILE_CACHE_MAX:
                    _tile_gzip_lru.popitem(last=False)
        else:
            body = data

    resp = app.response_class(response=body, mimetype="application/vnd.mapbox-vector-tile")
    if accepts_gzip or header["tile_compression"] == Compression.GZIP:
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = "public, max-age=86400"
    resp.headers["ETag"] = etag
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/api/graph-version", methods=["GET"])
def graph_version():
    """Cheap content hash of a city's topology, used as a client cache key."""
    rmap = resolve_map(request.args.get("map"))
    rmap.graph.ensure_loaded()
    if rmap.graph.topology_etag is None:
        return jsonify({"error": "Graph not loaded"}), 500
    resp = jsonify({"version": rmap.graph.topology_etag})
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/graph-topology", methods=["GET"])
def graph_topology():
    rmap = resolve_map(request.args.get("map"))
    rmap.graph.ensure_loaded()
    if rmap.graph.topology_json is None:
        return jsonify({"error": "Graph not loaded"}), 500

    etag = rmap.graph.topology_etag
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["Vary"] = "Accept-Encoding"
        return resp

    # Serve the pre-compressed variant to gzip-accepting clients (~5MB instead
    # of ~24MB for NYC). Compressed once at graph load; see graph_registry.
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if accepts_gzip and rmap.graph.topology_gzip is not None:
        resp = app.response_class(
            response=rmap.graph.topology_gzip, status=200, mimetype="application/json",
        )
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = app.response_class(
            response=rmap.graph.topology_json, status=200, mimetype="application/json",
        )
    resp.headers["Vary"] = "Accept-Encoding"
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
        entry = _build_graph_votes_body(rmap, mode)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Vote arrays are mostly zeros — the cached gzip variant is ~100x smaller.
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if accepts_gzip and entry.get("gz") is not None:
        resp = app.response_class(
            response=entry["gz"], status=200, mimetype="application/json",
        )
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = app.response_class(
            response=entry["body"], status=200, mimetype="application/json",
        )
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = "public, max-age=5"
    resp.headers["ETag"] = etag
    return resp


@app.route("/api/heat", methods=["GET"])
def heat():
    """Voted-edges GeoJSON for the heatmap's first paint (no topology needed).

    Same feature shape the client builds locally (maplibreHeat.ts); the client
    switches to its locally-built collection once topology + votes arrive.
    """
    rmap = resolve_map(request.args.get("map"))
    rmap.graph.ensure_loaded()

    mode = request.args.get("mode") or None
    rev = int(redis_client.get(vote_store.revision_key(rmap.slug)) or 0)
    etag = f'"h-{rmap.slug}-{mode or "all"}-{rev}"'
    if request.headers.get("If-None-Match") == etag:
        resp = app.response_class(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "public, max-age=5"
        return resp

    try:
        entry = _build_heat_entry(rmap, mode)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if accepts_gzip and entry.get("gz") is not None:
        resp = app.response_class(
            response=entry["gz"], status=200, mimetype="application/geo+json",
        )
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = app.response_class(
            response=entry["body"], status=200, mimetype="application/geo+json",
        )
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["Cache-Control"] = "public, max-age=5"
    resp.headers["ETag"] = etag
    return resp


@app.route("/api/graph.geojson", methods=["GET"])
def graph_geojson():
    rmap = resolve_map(request.args.get("map"))
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
    app.run(host="0.0.0.0", port=5001, debug=os.environ.get("FLASK_DEBUG") == "1")
