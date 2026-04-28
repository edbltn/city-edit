import json
import logging
import os
import sys
import time
import traceback
import hashlib
import threading
import redis
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
from desire_path_voting import (
    compute_desire_path_votes,
    cast_desire_path_votes,
    cast_node_votes,
    extract_all_segments,
    extract_unique_node_coords,
)
from database import init_db, record_segment_votes, record_node_votes

# Configure logging for Cloud Run (unbuffered, structured)
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Force unbuffered output for Cloud Run
sys.stdout.reconfigure(line_buffering=True)

# Import Python router
from python_router import PythonRouter
logger.info("[STARTUP] Using Python router (osmnx + rustworkx)")

# Debug: log all environment variables related to Redis
logger.info(f"[DEBUG] REDIS_HOST from env: {os.environ.get('REDIS_HOST', 'NOT_SET')}")
logger.info(f"[DEBUG] REDIS_PORT from env: {os.environ.get('REDIS_PORT', 'NOT_SET')}")

# Load environment variables from .env file (won't override existing env vars)
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# Redis connection
redis_host = os.environ.get('REDIS_HOST', 'localhost')
redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
SEGMENT_VOTES_KEY = "segment_votes"
NODE_VOTES_KEY = "node_votes"
NODE_VOTE_TYPES_KEY = "node_vote_types"
REDIS_CHANNEL = "state_updates"

# Bumped on every vote (locally) and via pubsub from peer instances.
# /api/graph-votes uses this to cache its JSON response between writes.
_votes_revision = 0
# Cache keyed by mode-filter string ("" for unfiltered). Each entry holds the
# revision it was built from and the serialized JSON body.
_graph_votes_cache: dict = {}


def _bump_votes_revision():
    global _votes_revision
    _votes_revision += 1

def publish_votes_changed():
    """Publish votes_changed message to trigger WebSocket broadcasts."""
    _bump_votes_revision()
    try:
        rev = redis_client.get("revision") or 1
        redis_client.publish(REDIS_CHANNEL, json.dumps({
            "type": "votes_changed",
            "revision": int(rev)
        }))
    except redis.ConnectionError:
        pass


def start_pubsub_listener():
    """Background thread that listens for pub/sub messages."""
    def listener():
        # Create separate connection for pub/sub (required by redis-py)
        pubsub_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
        pubsub = pubsub_client.pubsub()
        pubsub.subscribe(REDIS_CHANNEL)
        logger.info(f"[PUBSUB] Subscribed to channel: {REDIS_CHANNEL}")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                msg_type = data.get("type")

                if msg_type == "votes_changed":
                    # Peer instance cast a vote; invalidate our graph-votes
                    # response cache. The WebSocket handler reads pubsub
                    # separately for its own broadcasting needs.
                    _bump_votes_revision()

            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[PUBSUB] Invalid message: {e}")

    thread = threading.Thread(target=listener, daemon=True, name="pubsub-listener")
    thread.start()
    return thread


# Log Redis connection info at startup
logger.info(f"[REDIS] Connecting to Redis at: {redis_host}:6379")
try:
    redis_info = redis_client.info("server")
    logger.info(f"[REDIS] Connected successfully - Redis version: {redis_info.get('redis_version', 'unknown')}")
    if redis_host != 'localhost':
        logger.info(f"[REDIS] Using CLOUD Redis (Memorystore) at {redis_host}")
    else:
        logger.info(f"[REDIS] Using LOCAL Redis at {redis_host}")
except redis.ConnectionError as e:
    logger.error(f"[REDIS] WARNING: Could not connect to Redis at {redis_host}: {e}")

# Initialize Python router
router = PythonRouter(data_dir="osm_data", redis_client=redis_client)
logger.info("[STARTUP] Python router initialized")

# Cache graph data for serving as GeoJSON
_graph_cache = None
_graph_topology_json: str | None = None

def _load_graph_cache():
    global _graph_cache, _graph_topology_json
    logger.info("[STARTUP] Pre-loading graph geometry...")
    try:
        # get_graph_for_bbox(south, west, north, east) — full Manhattan
        data = router.get_graph_for_bbox(40.700, -74.020, 40.880, -73.907)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # Pre-compute the segment-key strings for every edge once. Without
        # this, /api/graph-votes was burning ~2.5M f-string format ops per
        # request rebuilding these keys for all 313K edges.
        # Note: the walk graph is a MultiDiGraph, so multiple edges can share
        # the same endpoints (one-way pairs, parallel cycleway+sidewalk on the
        # same street, etc.). The reverse index must be a list so vote data is
        # broadcast to every twin — otherwise some twins are drawn with no
        # votes even though the segment is lit up under the cursor.
        edge_coord_keys: list[tuple[str, str]] = []
        coord_to_edge_idx: dict[tuple[str, str], list[int]] = {}
        for i, edge in enumerate(edges):
            from_idx, to_idx = edge[0], edge[1]
            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]
            c1 = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2 = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            edge_coord_keys.append((c1, c2))
            # Reverse index — both directions point at every twin sharing
            # this coord pair.
            coord_to_edge_idx.setdefault((c1, c2), []).append(i)
            if c1 != c2:
                coord_to_edge_idx.setdefault((c2, c1), []).append(i)

        # "lon,lat" (5dp) -> node index. Mirrors edge endpoint encoding so
        # node-vote keys produced by node_key() resolve directly.
        coord_to_node_idx: dict[str, int] = {}
        for i, node in enumerate(nodes):
            lat, lon = node[0], node[1]
            coord_to_node_idx[f"{round(lon, 5)},{round(lat, 5)}"] = i

        data["edge_coord_keys"] = edge_coord_keys
        data["coord_to_edge_idx"] = coord_to_edge_idx
        data["coord_to_node_idx"] = coord_to_node_idx
        _graph_cache = data

        # Strip highway/length_m from edges — client only uses [from, to, name]
        edges_slim = [[e[0], e[1], e[2]] for e in edges]
        _graph_topology_json = json.dumps({"nodes": nodes, "edges": edges_slim})
        topo_mb = len(_graph_topology_json) / (1024 * 1024)
        logger.info(
            f"[STARTUP] Cached graph: {len(nodes)} nodes, {len(edges)} edges, "
            f"{len(coord_to_edge_idx)} edge coord entries, "
            f"{len(coord_to_node_idx)} node coord entries, "
            f"topology JSON {topo_mb:.1f} MB"
        )
    except Exception as e:
        logger.error(f"[STARTUP] Failed to load graph cache: {e}")

_load_graph_cache()

# Initialize database (creates tables if needed)
init_db()


def _migrate_segment_votes_on_startup():
    """Replay segment votes from Postgres into Redis if Redis is underpopulated.

    Postgres stores segment_key at 6dp; Redis uses 5dp. We normalize via
    segment_key() to match the canonical format.
    """
    from database import DATABASE_URL, get_cursor
    from desire_path_voting import segment_key as make_segment_key

    if not DATABASE_URL:
        return

    try:
        redis_count = redis_client.hlen(SEGMENT_VOTES_KEY)
        # Only run if Redis has fewer than 1000 segment keys (likely wiped)
        if redis_count >= 1000:
            logger.info(f"[MIGRATE] segment_votes has {redis_count} keys, skipping replay")
            return

        with get_cursor() as cursor:
            cursor.execute("""
                SELECT segment_key, COUNT(*) as cnt
                FROM votes
                GROUP BY segment_key
            """)
            rows = cursor.fetchall()

        if not rows:
            return

        logger.info(f"[MIGRATE] Replaying {len(rows)} segment groups from Postgres into Redis (had {redis_count} keys)...")

        # Normalize 6dp Postgres keys to 5dp Redis keys and aggregate
        vote_counts: dict[str, int] = {}
        for seg_key_raw, cnt in rows:
            parts = seg_key_raw.split("|")
            if len(parts) < 3 or not parts[1]:
                continue
            try:
                c1 = [float(x) for x in parts[0].split(",")]
                c2 = [float(x) for x in parts[1].split(",")]
            except (ValueError, IndexError):
                continue
            mode = parts[2]
            norm_key = make_segment_key(c1, c2, mode)
            vote_counts[norm_key] = vote_counts.get(norm_key, 0) + cnt

        # Write to Redis in batches
        pipe = redis_client.pipeline()
        for i, (key, count) in enumerate(vote_counts.items()):
            pipe.hset(SEGMENT_VOTES_KEY, key, count)
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[MIGRATE] Replayed {len(vote_counts)} segment keys into Redis")
    except Exception as e:
        logger.error(f"[MIGRATE] segment_votes replay failed: {e}")

_migrate_segment_votes_on_startup()


def _migrate_segment_vote_types_on_startup():
    """Populate segment_vote_types Redis hash from Postgres for existing votes.

    Normalizes Postgres 6dp keys to 5dp via segment_key() and writes all
    vote_type data. Skips only when well-populated (>= 1000 entries), matching
    the same threshold used by _migrate_segment_votes_on_startup().
    """
    from database import DATABASE_URL, get_cursor
    from desire_path_voting import SEGMENT_VOTE_TYPES_KEY, segment_key

    if not DATABASE_URL:
        return

    try:
        # Skip only if already well-populated (same threshold as segment_votes migration)
        existing_count = redis_client.hlen(SEGMENT_VOTE_TYPES_KEY)
        if existing_count >= 1000:
            logger.info(f"[MIGRATE] segment_vote_types has {existing_count} keys, skipping replay")
            return

        with get_cursor() as cursor:
            cursor.execute("""
                SELECT segment_key, vote_type, COUNT(*) as cnt
                FROM votes
                WHERE vote_type IS NOT NULL AND vote_type != ''
                GROUP BY segment_key, vote_type
            """)
            rows = cursor.fetchall()

        if not rows:
            logger.info("[MIGRATE] No votes with vote_type in DB, skipping segment_vote_types migration")
            return

        # Build per-segment vote type maps. We normalize Postgres 6dp keys to
        # 5dp via segment_key() without cross-checking against live Redis keys —
        # the cross-check was unreliable due to double-rounding (float → 6dp
        # string → 5dp can differ from float → 5dp directly).
        seg_vt_maps: dict[str, dict[str, int]] = {}
        for seg_key_raw, vote_type, cnt in rows:
            parts = seg_key_raw.split("|")
            if len(parts) < 3 or not parts[1]:
                continue
            try:
                c1 = [float(x) for x in parts[0].split(",")]
                c2 = [float(x) for x in parts[1].split(",")]
            except (ValueError, IndexError):
                continue
            mode = parts[2]
            norm_key = segment_key(c1, c2, mode)
            if norm_key not in seg_vt_maps:
                seg_vt_maps[norm_key] = {}
            seg_vt_maps[norm_key][vote_type] = seg_vt_maps[norm_key].get(vote_type, 0) + cnt

        # Write to Redis in batches
        pipe = redis_client.pipeline()
        for i, (key, vt_map) in enumerate(seg_vt_maps.items()):
            pipe.hset(SEGMENT_VOTE_TYPES_KEY, key, json.dumps(vt_map))
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[MIGRATE] Populated segment_vote_types for {len(seg_vt_maps)} segments from {len(rows)} DB rows (had {existing_count} existing)")
    except Exception as e:
        logger.error(f"[MIGRATE] segment_vote_types migration failed: {e}")

_migrate_segment_vote_types_on_startup()


def _backfill_node_votes_table_from_segments():
    """One-time derivation of the node_votes table from existing segment votes.

    For each segment row in `votes`, both endpoints become node rows. The
    UNIQUE (node_key, ip_hash, vote_type) constraint dedupes intra-route
    overlap (a node touched by N segments still gets one row per voter).
    Skips entirely if `node_votes` is already populated.
    """
    from database import DATABASE_URL, get_cursor
    from psycopg2.extras import execute_values

    if not DATABASE_URL:
        return

    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM node_votes")
            existing = cursor.fetchone()[0]
            if existing > 0:
                logger.info(f"[BACKFILL] node_votes table has {existing} rows, skipping derivation")
                return

            cursor.execute("""
                SELECT segment_key, mode, ip_hash, vote_type, MIN(created_at) AS first_seen
                FROM votes
                WHERE segment_key LIKE '%|%|%'
                  AND split_part(segment_key, '|', 2) <> ''
                GROUP BY segment_key, mode, ip_hash, vote_type
            """)
            rows = cursor.fetchall()

        if not rows:
            return

        # (node_key, ip_hash, vote_type) -> (mode, earliest_seen)
        derived: dict[tuple, tuple] = {}
        for seg_key, mode, ip_hash, vote_type, first_seen in rows:
            parts = seg_key.split("|")
            if len(parts) < 3 or not parts[1]:
                continue
            for endpoint in (parts[0], parts[1]):
                nk = f"{endpoint}|{parts[2]}"
                key = (nk, ip_hash, vote_type)
                prev = derived.get(key)
                if prev is None or first_seen < prev[1]:
                    derived[key] = (mode, first_seen)

        if not derived:
            return

        data = [
            (nk, mode, ip_hash, 1.0, vt, ca)
            for (nk, ip_hash, vt), (mode, ca) in derived.items()
        ]

        with get_cursor() as cursor:
            execute_values(
                cursor,
                """INSERT INTO node_votes (node_key, mode, ip_hash, weight, vote_type, created_at) VALUES %s
                   ON CONFLICT (node_key, ip_hash, vote_type) DO NOTHING""",
                data,
            )
        logger.info(f"[BACKFILL] Inserted {len(data)} node_votes rows from existing segment votes")
    except Exception as e:
        logger.error(f"[BACKFILL] node_votes derivation failed: {e}")

_backfill_node_votes_table_from_segments()


def _migrate_node_votes_on_startup():
    """Replay node_votes from Postgres into Redis if Redis is underpopulated."""
    from database import DATABASE_URL, get_cursor
    from desire_path_voting import NODE_VOTES_KEY as NK_KEY, node_key as make_node_key

    if not DATABASE_URL:
        return

    try:
        existing = redis_client.hlen(NK_KEY)
        if existing >= 1000:
            logger.info(f"[MIGRATE] node_votes has {existing} keys, skipping replay")
            return

        with get_cursor() as cursor:
            cursor.execute("""
                SELECT node_key, COUNT(*) AS cnt
                FROM node_votes
                GROUP BY node_key
            """)
            rows = cursor.fetchall()

        if not rows:
            return

        vote_counts: dict[str, int] = {}
        for nk_raw, cnt in rows:
            parts = nk_raw.split("|")
            if len(parts) < 2:
                continue
            try:
                coord = [float(x) for x in parts[0].split(",")]
            except (ValueError, IndexError):
                continue
            mode = parts[1]
            norm_key = make_node_key(coord, mode)
            vote_counts[norm_key] = vote_counts.get(norm_key, 0) + cnt

        pipe = redis_client.pipeline()
        for i, (key, count) in enumerate(vote_counts.items()):
            pipe.hset(NK_KEY, key, count)
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[MIGRATE] Replayed {len(vote_counts)} node keys into Redis")
    except Exception as e:
        logger.error(f"[MIGRATE] node_votes replay failed: {e}")

_migrate_node_votes_on_startup()


def _migrate_node_vote_types_on_startup():
    """Populate node_vote_types Redis hash from Postgres for existing votes."""
    from database import DATABASE_URL, get_cursor
    from desire_path_voting import NODE_VOTE_TYPES_KEY as NVT_KEY, node_key as make_node_key

    if not DATABASE_URL:
        return

    try:
        existing = redis_client.hlen(NVT_KEY)
        if existing >= 1000:
            logger.info(f"[MIGRATE] node_vote_types has {existing} keys, skipping replay")
            return

        with get_cursor() as cursor:
            cursor.execute("""
                SELECT node_key, vote_type, COUNT(*) AS cnt
                FROM node_votes
                WHERE vote_type IS NOT NULL AND vote_type != ''
                GROUP BY node_key, vote_type
            """)
            rows = cursor.fetchall()

        if not rows:
            logger.info("[MIGRATE] No node votes with vote_type in DB, skipping node_vote_types migration")
            return

        node_vt_maps: dict[str, dict[str, int]] = {}
        for nk_raw, vote_type, cnt in rows:
            parts = nk_raw.split("|")
            if len(parts) < 2:
                continue
            try:
                coord = [float(x) for x in parts[0].split(",")]
            except (ValueError, IndexError):
                continue
            mode = parts[1]
            norm_key = make_node_key(coord, mode)
            node_vt_maps.setdefault(norm_key, {})
            node_vt_maps[norm_key][vote_type] = node_vt_maps[norm_key].get(vote_type, 0) + cnt

        pipe = redis_client.pipeline()
        for i, (key, vt_map) in enumerate(node_vt_maps.items()):
            pipe.hset(NVT_KEY, key, json.dumps(vt_map))
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[MIGRATE] Populated node_vote_types for {len(node_vt_maps)} nodes from {len(rows)} DB rows")
    except Exception as e:
        logger.error(f"[MIGRATE] node_vote_types migration failed: {e}")

_migrate_node_vote_types_on_startup()


# Start pub/sub listener for cross-instance cache synchronization
start_pubsub_listener()


def get_client_ip() -> str:
    """
    Get hashed client IP for privacy-preserving vote tracking.

    Uses X-Forwarded-For header (set by nginx) or falls back to remote_addr.
    Returns first 16 chars of SHA-256 hash.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For can be "client, proxy1, proxy2" - take first
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.remote_addr or "unknown"

    # Hash for privacy (first 16 chars of SHA-256)
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
    return ip_hash


# Percentage to shrink each edge on both ends to prevent overlap at nodes
EDGE_SHRINK = 0.08


def shrink_edge(coord1, coord2):
    """Shrink an edge by moving both endpoints toward the center."""
    # Move coord1 toward coord2 by EDGE_SHRINK
    new_coord1 = [
        coord1[0] + EDGE_SHRINK * (coord2[0] - coord1[0]),
        coord1[1] + EDGE_SHRINK * (coord2[1] - coord1[1])
    ]
    # Move coord2 toward coord1 by EDGE_SHRINK
    new_coord2 = [
        coord2[0] + EDGE_SHRINK * (coord1[0] - coord2[0]),
        coord2[1] + EDGE_SHRINK * (coord1[1] - coord2[1])
    ]
    return new_coord1, new_coord2


def get_segment_overlay(mode_filter=None):
    """Build GeoJSON overlay from voted edges, optionally filtered by mode."""
    try:
        votes = redis_client.hgetall(SEGMENT_VOTES_KEY)
    except redis.ConnectionError:
        votes = {}

    if not votes:
        return {
            "type": "geojson",
            "data": {
                "type": "FeatureCollection",
                "features": []
            }
        }

    # Build features
    features = []
    for key, count in votes.items():
        # Parse key: "lon1,lat1|lon2,lat2|mode"
        parts = key.split("|")
        if len(parts) < 3:
            continue  # Skip old-format keys without mode

        mode = parts[2]

        # Filter by mode if specified
        if mode_filter and mode != mode_filter:
            continue

        count = int(count)
        coord1 = [float(x) for x in parts[0].split(",")]
        coord2 = [float(x) for x in parts[1].split(",")]

        # Shrink edge to prevent overlap at nodes
        shrunk1, shrunk2 = shrink_edge(coord1, coord2)

        features.append({
            "type": "Feature",
            "properties": {
                "votes": count,
                "mode": mode
            },
            "geometry": {
                "type": "LineString",
                "coordinates": [shrunk1, shrunk2]
            }
        })

    return {
        "type": "geojson",
        "data": {
            "type": "FeatureCollection",
            "features": features
        }
    }


def make_state(rev: int, mode_filter=None):
    """Build map state with the segment overlay."""
    segment_overlay = get_segment_overlay(mode_filter)
    return {
        "revision": rev,
        "overlays": {
            "desire_paths": segment_overlay
        }
    }


@app.route("/health")
def health():
    """Health check endpoint for load balancers and monitoring."""
    if _graph_topology_json is None:
        return jsonify({"status": "unhealthy", "graph": "not loaded"}), 503
    try:
        redis_client.ping()
        return jsonify({"status": "healthy", "redis": "connected"}), 200
    except redis.ConnectionError:
        return jsonify({"status": "unhealthy", "redis": "disconnected"}), 503


@sock.route("/ws")
def ws(ws):
    """WebSocket handler using Redis pub/sub for efficient push updates."""
    rev = 1
    last_push_time = 0
    KEEPALIVE_INTERVAL = 30  # Send keepalive every 30 seconds

    # Create pub/sub subscription for this connection
    ws_pubsub_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    pubsub = ws_pubsub_client.pubsub()
    pubsub.subscribe(REDIS_CHANNEL)

    # Send initial state immediately on connect
    state_msg = {"type": "map_state", "state": make_state(rev)}
    ws.send(json.dumps(state_msg))
    rev += 1
    last_push_time = time.time()

    try:
        while True:
            # Drain any inbound messages (we don't act on them, but keep the
            # socket healthy by reading).
            try:
                ws.receive(timeout=0)
            except Exception as e:
                err_str = str(e).lower()
                if "timed out" not in err_str and "no data" not in err_str and "connection closed" not in err_str:
                    logger.warning(f"[WS] Exception in receive: {e}")

            # Check for pub/sub messages (with short timeout)
            redis_msg = pubsub.get_message(timeout=0.1)
            should_push = False

            if redis_msg and redis_msg["type"] == "message":
                try:
                    data = json.loads(redis_msg["data"])
                    if data.get("type") == "votes_changed":
                        should_push = True
                except (json.JSONDecodeError, KeyError):
                    pass

            # Send keepalive if no push in KEEPALIVE_INTERVAL seconds
            if not should_push and (time.time() - last_push_time) > KEEPALIVE_INTERVAL:
                should_push = True

            if should_push:
                state_msg = {"type": "map_state", "state": make_state(rev)}
                ws.send(json.dumps(state_msg))
                rev += 1
                last_push_time = time.time()

    finally:
        # Clean up pub/sub on disconnect
        pubsub.unsubscribe(REDIS_CHANNEL)
        pubsub.close()
        ws_pubsub_client.close()


@app.route("/api/routes", methods=["POST"])
def calculate_route():
    """
    Calculate route using router interface with desire path voting.

    For bike/drive modes:
    - Computes BOTH the desired mode route AND a walk route
    - Votes for walk segments that diverge from the desired route (desire paths)
    - Returns both routes so frontend can display them

    For walk mode:
    - Computes walk route only
    - Votes for entire walk path
    """
    logger.info(f"[ROUTE] Received request, REDIS_HOST: {redis_host}")

    data = request.get_json()
    if not data:
        logger.error("[ROUTE] Error: Missing request body")
        return jsonify({"error": "Missing request body"}), 400

    start = data.get("start")  # [lat, lon]
    end = data.get("end")      # [lat, lon]
    waypoints = data.get("waypoints", [])  # List of [lat, lon] pairs

    logger.info(f"[ROUTE] Request: start={start}, end={end}, waypoints={len(waypoints)}")

    if not start or not end:
        logger.error("[ROUTE] Error: Missing coordinates")
        return jsonify({"error": "Missing start or end coordinates"}), 400

    try:
        # Compute route with optional waypoints
        if waypoints:
            waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
            route = router.calculate_route(
                start=(start[0], start[1]),
                end=(end[0], end[1]),
                mode="walk",
                waypoints=waypoints_tuples
            )
        else:
            route = router.calculate_route(
                start=(start[0], start[1]),
                end=(end[0], end[1]),
                mode="walk",
                waypoints=[]
            )

        if "error" in route:
            logger.error(f"[ROUTE] Routing failed: {route['error']}")
            return jsonify(route), 404

        # Extract segments for voting (client casts votes on user action)
        vote_segments = extract_all_segments(route.get("geometry"))

        return jsonify({
            "route": route,
            "desire_path": None,
            "desire_path_segments": vote_segments,
            "vote_mode": "walk"
        })

    except Exception as e:
        logger.error(f"[ROUTE] Unexpected error: {e}")
        logger.error(f"[ROUTE] Traceback:\n{traceback.format_exc()}")
        return jsonify({"error": f"Routing failed: {str(e)}"}), 500


@app.route("/api/vote", methods=["POST"])
def cast_vote():
    """
    Cast votes for desire path segments or single points.

    Expects JSON body with:
    - segments: List of [[coord1, coord2], ...] segments (for route votes)
    - point: [lat, lon] single point (for point votes)
    - mode: Transport mode (bike, walk, drive)
    - vote_type: Natural language description of what user is voting for

    Uses IP-weighted voting: each IP contributes exactly 1.0 total weight
    across all their votes.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    segments = data.get("segments", [])
    point = data.get("point")
    mode = data.get("mode", "walk")
    vote_type = data.get("vote_type", "")

    # Validate: need either segments or point
    if not segments and not point:
        return jsonify({"error": "No segments or point to vote on"}), 400

    # Get hashed voter identity: use voter_id override if provided, else IP
    voter_id = data.get("voter_id")
    if voter_id:
        ip_hash = hashlib.sha256(str(voter_id).encode()).hexdigest()[:16]
    else:
        ip_hash = get_client_ip()

    try:
        if point:
            # Point votes are persisted to the DB but not currently surfaced on
            # the map (the hex visualisation that consumed them was removed).
            from database import record_point_vote
            record_point_vote(point, mode, ip_hash, vote_type)
            logger.info(f"[VOTE] Recorded point vote for '{vote_type}' at {point} as '{mode}' from IP {ip_hash[:8]}...")

            redis_client.incr("revision")
            publish_votes_changed()

            return jsonify({
                "success": True,
                "point_vote": True,
                "vote_type": vote_type
            })

        # Route vote — write to Redis segment_votes (consumed by /api/graph-votes)
        # and persist to the DB for durability.
        vote_count = cast_desire_path_votes(redis_client, segments, mode, ip_hash, vote_type=vote_type)
        logger.info(f"[VOTE] Cast {vote_count} segment votes for '{vote_type}' as '{mode}' from IP {ip_hash[:8]}...")

        weight_per_segment = 1.0 / len(segments) if segments else 1.0
        record_segment_votes(segments, mode, ip_hash, weight_per_segment, vote_type)

        # Mirror to node-level storage so every node along the path gets a
        # vote too (one per unique node, not per touching segment).
        node_coords = extract_unique_node_coords(segments)
        node_count = cast_node_votes(redis_client, node_coords, mode, ip_hash, vote_type=vote_type)
        if node_count:
            weight_per_node = 1.0 / node_count
            record_node_votes(node_coords, mode, ip_hash, weight_per_node, vote_type)
            logger.info(f"[VOTE] Cast {node_count} node votes for '{vote_type}' as '{mode}'")

        redis_client.incr("revision")
        publish_votes_changed()

        return jsonify({
            "success": True,
            "segments_voted": vote_count,
            "vote_type": vote_type
        })

    except Exception as e:
        logger.error(f"[VOTE] Error casting votes: {e}")
        return jsonify({"error": f"Vote failed: {str(e)}"}), 500


# =============================================================================
# Admin Endpoints
# =============================================================================

@app.route("/api/admin/refresh-osm", methods=["POST"])
def admin_refresh_osm():
    """
    Trigger OSM data refresh and graph rebuild.

    Called by Cloud Scheduler weekly, or manually for testing.
    Requires OIDC authentication in production.
    """
    # Log the request
    logger.info("[ADMIN] OSM refresh triggered")

    # In production, verify the caller is Cloud Scheduler
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        logger.info("[ADMIN] Request has Authorization header (Cloud Scheduler)")

    try:
        import subprocess
        result = subprocess.run(
            ["python", "refresh_osm.py", "--region", "manhattan"],
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout for graph building
        )

        logger.info(f"[ADMIN] Refresh completed with exit code {result.returncode}")

        if result.returncode == 0:
            # Reload router if it supports hot-reload
            if hasattr(router, 'reload'):
                router.reload()
                logger.info("[ADMIN] Router reloaded")

            return jsonify({
                "status": "success",
                "stdout": result.stdout[-2000:] if result.stdout else "",  # Last 2000 chars
            })
        else:
            return jsonify({
                "status": "failed",
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }), 500

    except subprocess.TimeoutExpired:
        logger.error("[ADMIN] Refresh timed out after 30 minutes")
        return jsonify({"error": "Refresh timed out"}), 500
    except Exception as e:
        logger.error(f"[ADMIN] Refresh failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/migrate-vote-types", methods=["POST"])
def admin_migrate_vote_types():
    """Force re-populate segment_vote_types from Postgres.

    Useful when the Redis key was evicted or partially lost without a server
    restart (which would normally trigger _migrate_segment_vote_types_on_startup).
    """
    from database import DATABASE_URL, get_cursor
    from desire_path_voting import SEGMENT_VOTE_TYPES_KEY, segment_key as sk

    if not DATABASE_URL:
        return jsonify({"error": "DATABASE_URL not configured"}), 500

    try:
        with get_cursor() as cursor:
            cursor.execute("""
                SELECT segment_key, vote_type, COUNT(*) as cnt
                FROM votes
                WHERE vote_type IS NOT NULL AND vote_type != ''
                GROUP BY segment_key, vote_type
            """)
            rows = cursor.fetchall()

        seg_vt_maps: dict[str, dict[str, int]] = {}
        skipped = 0
        for seg_key_raw, vote_type, cnt in rows:
            parts = seg_key_raw.split("|")
            if len(parts) < 3 or not parts[1]:
                skipped += 1
                continue
            try:
                c1 = [float(x) for x in parts[0].split(",")]
                c2 = [float(x) for x in parts[1].split(",")]
            except (ValueError, IndexError):
                skipped += 1
                continue
            mode = parts[2]
            norm_key = sk(c1, c2, mode)
            if norm_key not in seg_vt_maps:
                seg_vt_maps[norm_key] = {}
            seg_vt_maps[norm_key][vote_type] = seg_vt_maps[norm_key].get(vote_type, 0) + cnt

        pipe = redis_client.pipeline()
        for i, (key, vt_map) in enumerate(seg_vt_maps.items()):
            pipe.hset(SEGMENT_VOTE_TYPES_KEY, key, json.dumps(vt_map))
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[ADMIN] Migrated vote types: {len(seg_vt_maps)} segments from {len(rows)} DB rows ({skipped} skipped)")
        return jsonify({
            "status": "ok",
            "segments_written": len(seg_vt_maps),
            "db_rows_processed": len(rows),
            "skipped": skipped,
        })
    except Exception as e:
        logger.error(f"[ADMIN] migrate-vote-types failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/vote-match-diagnostic", methods=["GET"])
def vote_match_diagnostic():
    """Diagnose why Redis segment keys may not match graph edges."""
    try:
        segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)
        total_redis_keys = len(segment_votes)
        total_redis_votes = sum(int(v) for v in segment_votes.values())

        # Build set of all graph edge coord pairs
        nodes = _graph_cache["nodes"]
        edges = _graph_cache["edges"]
        graph_pairs = set()
        for from_idx, to_idx, *rest in edges:
            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]
            c1 = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2 = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            graph_pairs.add((c1, c2))
            graph_pairs.add((c2, c1))

        # Check how many Redis keys match
        matched_keys = 0
        matched_votes = 0
        unmatched_samples = []
        for seg_key, vote_count in segment_votes.items():
            parts = seg_key.split("|")
            if len(parts) >= 3:
                c1, c2 = parts[0], parts[1]
                if (c1, c2) in graph_pairs:
                    matched_keys += 1
                    matched_votes += int(vote_count)
                elif len(unmatched_samples) < 5:
                    unmatched_samples.append({"key": seg_key, "votes": int(vote_count)})

        # Sample graph edge coords for comparison
        graph_samples = []
        for from_idx, to_idx, *rest in edges[:5]:
            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]
            graph_samples.append(f"{round(from_lon, 5)},{round(from_lat, 5)}|{round(to_lon, 5)},{round(to_lat, 5)}")

        return jsonify({
            "redis_segment_keys": total_redis_keys,
            "redis_total_votes": total_redis_votes,
            "graph_edges": len(edges),
            "graph_unique_coord_pairs": len(graph_pairs) // 2,
            "matched_keys": matched_keys,
            "matched_votes": matched_votes,
            "unmatched_sample_keys": unmatched_samples,
            "graph_sample_edges": graph_samples,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/retag-mode", methods=["POST"])
def admin_retag_mode():
    """One-shot migration: rewrite every vote's mode to the target value.

    Touches Redis (segment_votes, segment_vote_types, node_votes,
    node_vote_types) and Postgres (votes table). Idempotent — running it
    twice with the same target is a no-op. Optional `from_modes` filter
    restricts which modes are migrated; if omitted, EVERY mode that isn't
    already the target is rewritten.

    Body (JSON):
        {
          "to": "bikepaths",
          "from_modes": ["bike", "bikepath"]   // optional
        }
    """
    from desire_path_voting import SEGMENT_VOTE_TYPES_KEY

    data = request.get_json(silent=True) or {}
    target = (data.get("to") or "").strip()
    if not target:
        return jsonify({"error": "Missing 'to' (target mode)"}), 400
    from_modes = data.get("from_modes")
    from_set = set(from_modes) if from_modes else None

    def should_migrate(old_mode: str) -> bool:
        if old_mode == target:
            return False
        return from_set is None or old_mode in from_set

    summary: dict = {"target": target}

    # ---- Redis: SEGMENT_VOTES_KEY (raw counts) ----
    seg_migrated = 0
    seg_collisions = 0
    try:
        all_keys = list(redis_client.hgetall(SEGMENT_VOTES_KEY).items())
        delete_keys: list[str] = []
        pipe = redis_client.pipeline()
        for seg_key, count_raw in all_keys:
            parts = seg_key.split("|")
            if len(parts) < 3:
                continue
            if not should_migrate(parts[2]):
                continue
            new_key = f"{parts[0]}|{parts[1]}|{target}"
            try:
                count = int(count_raw)
            except ValueError:
                continue
            if redis_client.hexists(SEGMENT_VOTES_KEY, new_key):
                seg_collisions += 1
            pipe.hincrby(SEGMENT_VOTES_KEY, new_key, count)
            delete_keys.append(seg_key)
            seg_migrated += 1
        if delete_keys:
            pipe.hdel(SEGMENT_VOTES_KEY, *delete_keys)
        pipe.execute()
        summary["redis_segment_votes"] = {
            "migrated": seg_migrated,
            "merged_into_existing": seg_collisions,
        }
    except Exception as e:
        summary["redis_segment_votes_error"] = str(e)

    # ---- Redis: SEGMENT_VOTE_TYPES_KEY (per-segment vote_type → count JSON) ----
    vt_migrated = 0
    try:
        all_vt = list(redis_client.hgetall(SEGMENT_VOTE_TYPES_KEY).items())
        for seg_key, vt_json in all_vt:
            parts = seg_key.split("|")
            if len(parts) < 3:
                continue
            if not should_migrate(parts[2]):
                continue
            new_key = f"{parts[0]}|{parts[1]}|{target}"
            try:
                old_dict = json.loads(vt_json) if vt_json else {}
            except json.JSONDecodeError:
                old_dict = {}
            existing_raw = redis_client.hget(SEGMENT_VOTE_TYPES_KEY, new_key)
            try:
                merged = json.loads(existing_raw) if existing_raw else {}
            except json.JSONDecodeError:
                merged = {}
            for vt, cnt in old_dict.items():
                merged[vt] = int(merged.get(vt, 0)) + int(cnt)
            redis_client.hset(SEGMENT_VOTE_TYPES_KEY, new_key, json.dumps(merged))
            redis_client.hdel(SEGMENT_VOTE_TYPES_KEY, seg_key)
            vt_migrated += 1
        summary["redis_vote_types"] = {"migrated": vt_migrated}
    except Exception as e:
        summary["redis_vote_types_error"] = str(e)

    # ---- Redis: NODE_VOTES_KEY (node-level raw counts, key = "coord|mode") ----
    nv_migrated = 0
    nv_collisions = 0
    try:
        all_nv = list(redis_client.hgetall(NODE_VOTES_KEY).items())
        delete_nv: list[str] = []
        pipe = redis_client.pipeline()
        for nk, count_raw in all_nv:
            parts = nk.split("|")
            if len(parts) < 2:
                continue
            if not should_migrate(parts[1]):
                continue
            new_key = f"{parts[0]}|{target}"
            try:
                count = int(count_raw)
            except ValueError:
                continue
            if redis_client.hexists(NODE_VOTES_KEY, new_key):
                nv_collisions += 1
            pipe.hincrby(NODE_VOTES_KEY, new_key, count)
            delete_nv.append(nk)
            nv_migrated += 1
        if delete_nv:
            pipe.hdel(NODE_VOTES_KEY, *delete_nv)
        pipe.execute()
        summary["redis_node_votes"] = {
            "migrated": nv_migrated,
            "merged_into_existing": nv_collisions,
        }
    except Exception as e:
        summary["redis_node_votes_error"] = str(e)

    # ---- Redis: NODE_VOTE_TYPES_KEY (per-node vote_type → count JSON) ----
    nvt_migrated = 0
    try:
        all_nvt = list(redis_client.hgetall(NODE_VOTE_TYPES_KEY).items())
        for nk, vt_json in all_nvt:
            parts = nk.split("|")
            if len(parts) < 2:
                continue
            if not should_migrate(parts[1]):
                continue
            new_key = f"{parts[0]}|{target}"
            try:
                old_dict = json.loads(vt_json) if vt_json else {}
            except json.JSONDecodeError:
                old_dict = {}
            existing_raw = redis_client.hget(NODE_VOTE_TYPES_KEY, new_key)
            try:
                merged = json.loads(existing_raw) if existing_raw else {}
            except json.JSONDecodeError:
                merged = {}
            for vt, cnt in old_dict.items():
                merged[vt] = int(merged.get(vt, 0)) + int(cnt)
            redis_client.hset(NODE_VOTE_TYPES_KEY, new_key, json.dumps(merged))
            redis_client.hdel(NODE_VOTE_TYPES_KEY, nk)
            nvt_migrated += 1
        summary["redis_node_vote_types"] = {"migrated": nvt_migrated}
    except Exception as e:
        summary["redis_node_vote_types_error"] = str(e)

    # ---- Postgres: votes table ----
    try:
        from database import DATABASE_URL, get_cursor
        if not DATABASE_URL:
            summary["postgres"] = "DATABASE_URL not set, skipped"
        else:
            with get_cursor() as cursor:
                if from_set:
                    cursor.execute(
                        """
                        INSERT INTO votes (segment_key, mode, ip_hash, weight, vote_type, created_at)
                        SELECT
                          regexp_replace(segment_key, '\\|[^|]+$', '|' || %s),
                          %s, ip_hash, weight, vote_type, created_at
                        FROM votes
                        WHERE mode <> %s AND mode = ANY(%s)
                        ON CONFLICT (segment_key, ip_hash, vote_type) DO NOTHING
                        """,
                        (target, target, target, list(from_set)),
                    )
                    inserted = cursor.rowcount
                    cursor.execute(
                        "DELETE FROM votes WHERE mode <> %s AND mode = ANY(%s)",
                        (target, list(from_set)),
                    )
                    deleted = cursor.rowcount
                else:
                    cursor.execute(
                        """
                        INSERT INTO votes (segment_key, mode, ip_hash, weight, vote_type, created_at)
                        SELECT
                          regexp_replace(segment_key, '\\|[^|]+$', '|' || %s),
                          %s, ip_hash, weight, vote_type, created_at
                        FROM votes
                        WHERE mode <> %s
                        ON CONFLICT (segment_key, ip_hash, vote_type) DO NOTHING
                        """,
                        (target, target, target),
                    )
                    inserted = cursor.rowcount
                    cursor.execute(
                        "DELETE FROM votes WHERE mode <> %s",
                        (target,),
                    )
                    deleted = cursor.rowcount
                summary["postgres"] = {"inserted": inserted, "deleted": deleted}
    except Exception as e:
        summary["postgres_error"] = str(e)

    # Bump revision so any cached graph-votes responses are invalidated and
    # peer flask instances flush their caches via pubsub.
    publish_votes_changed()
    summary["votes_revision"] = _votes_revision
    return jsonify(summary)


@app.route("/api/admin/router-stats", methods=["GET"])
def admin_router_stats():
    """Get statistics about the current router."""
    try:
        if hasattr(router, 'stats'):
            stats = router.stats()
            return jsonify({
                "router": "python",
                "version": router.get_version() if hasattr(router, 'get_version') else "unknown",
                "stats": stats
            })
        else:
            return jsonify({
                "router": "python",
                "stats": "not available"
            })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/nearest-node", methods=["GET"])
def nearest_node():
    """Return the lat/lng of the nearest walk graph node to the given point."""
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400

    try:
        node_lat, node_lon = router.nearest_node_coords(lat, lon)
        return jsonify({"lat": node_lat, "lng": node_lon})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reverse-geocode", methods=["GET"])
def reverse_geocode():
    """Reverse geocode lat/lon to address using the local OSM graph."""
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400

    try:
        address = router.reverse_geocode(lat, lon)
        return jsonify({
            "address": address or None,
            "lat": lat,
            "lng": lon
        })
    except Exception as e:
        logger.error(f"Reverse geocoding error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph", methods=["GET"])
def graph_tiles():
    """Return OSM walk graph nodes and edges within a bounding box, with vote counts."""
    try:
        bbox = request.args["bbox"]  # minLon,minLat,maxLon,maxLat
        west, south, east, north = [float(v) for v in bbox.split(",")]
    except (KeyError, ValueError):
        return jsonify({"error": "bbox=minLon,minLat,maxLon,maxLat required"}), 400

    try:
        data = router.get_graph_for_bbox(south, west, north, east)

        # Fetch vote counts from Redis and aggregate to nodes/edges
        segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)

        # Build lookup dict: coord_pair -> vote_count (much faster than iterating all for each)
        # coord_pair is rounded to 5dp for matching
        vote_lookup = {}
        for seg_key, vote_count in segment_votes.items():
            parts = seg_key.split("|")
            if len(parts) >= 3:
                c1 = parts[0]  # "lon,lat"
                c2 = parts[1]  # "lon,lat"
                vote_count = int(vote_count)
                # Store both directions for efficient lookup
                vote_lookup[(c1, c2)] = vote_lookup.get((c1, c2), 0) + vote_count
                vote_lookup[(c2, c1)] = vote_lookup.get((c2, c1), 0) + vote_count

        # Compute vote counts for each node (max of adjacent edge votes)
        node_votes = [0] * len(data["nodes"])
        for from_idx, to_idx, *rest in data["edges"]:
            from_lat, from_lon = data["nodes"][from_idx]
            to_lat, to_lon = data["nodes"][to_idx]
            c1_str = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2_str = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            votes = vote_lookup.get((c1_str, c2_str), 0)
            if votes > node_votes[from_idx]:
                node_votes[from_idx] = votes
            if votes > node_votes[to_idx]:
                node_votes[to_idx] = votes

        # Compute vote counts for each edge (O(1) lookup)
        edge_votes = []
        for from_idx, to_idx, *rest in data["edges"]:
            from_lat, from_lon = data["nodes"][from_idx]
            to_lat, to_lon = data["nodes"][to_idx]
            c1_str = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2_str = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            edge_votes.append(vote_lookup.get((c1_str, c2_str), 0))

        data["node_votes"] = node_votes
        data["edge_votes"] = edge_votes

        # Fetch vote types from Redis (segment_vote_types hash)
        edge_vote_types = [[] for _ in data["edges"]]
        try:
            from desire_path_voting import SEGMENT_VOTE_TYPES_KEY
            raw_vt = redis_client.hgetall(SEGMENT_VOTE_TYPES_KEY)

            # Build lookup: (c1_str, c2_str) -> {vote_type: count}
            # Use raw strings from Redis keys (already rounded to 5dp by segment_key())
            vt_lookup: dict[tuple[str, str], dict[str, int]] = {}
            for seg_key, vt_json in raw_vt.items():
                parts = seg_key.split("|")
                if len(parts) < 3 or not parts[1]:
                    continue
                c1 = parts[0]  # "lon,lat" already at 5dp
                c2 = parts[1]
                vt_map = json.loads(vt_json)
                # Both directions
                for pair in [(c1, c2), (c2, c1)]:
                    if pair not in vt_lookup:
                        vt_lookup[pair] = {}
                    for vt, cnt in vt_map.items():
                        vt_lookup[pair][vt] = vt_lookup[pair].get(vt, 0) + cnt

            # Build legend + per-edge indices
            legend = []
            legend_idx: dict[str, int] = {}
            for i, (from_idx, to_idx, *rest) in enumerate(data["edges"]):
                from_lat, from_lon = data["nodes"][from_idx]
                to_lat, to_lon = data["nodes"][to_idx]
                c1_str = f"{round(from_lon, 5)},{round(from_lat, 5)}"
                c2_str = f"{round(to_lon, 5)},{round(to_lat, 5)}"
                vt_map = vt_lookup.get((c1_str, c2_str))
                if vt_map:
                    # Sort by count descending
                    sorted_vts = sorted(vt_map.items(), key=lambda x: -x[1])
                    indices = []
                    for vt, cnt in sorted_vts:
                        if vt not in legend_idx:
                            legend_idx[vt] = len(legend)
                            legend.append(vt)
                        indices.append([legend_idx[vt], cnt])
                    edge_vote_types[i] = indices

            data["vote_type_legend"] = legend
            matched_edges = sum(1 for vt in edge_vote_types if vt)
            logger.info(f"[GRAPH] Vote types: {len(raw_vt)} segments in Redis, {len(vt_lookup)//2} in lookup, {matched_edges} edges matched, {len(legend)} unique types")
        except Exception as e:
            logger.warning(f"[GRAPH] Failed to fetch vote types: {e}")

        data["edge_vote_types"] = edge_vote_types

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tiles/<path:filename>")
def serve_tiles(filename):
    """Serve PMTiles files for local development. In production, nginx serves these."""
    tiles_dir = os.path.join(os.path.dirname(__file__), "osm_data")
    response = send_from_directory(tiles_dir, filename)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "public, max-age=604800"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.route("/api/graph-topology", methods=["GET"])
def graph_topology():
    """Serve full graph topology (nodes + edges, no votes). Highly cacheable."""
    if _graph_topology_json is None:
        return jsonify({"error": "Graph not loaded"}), 500

    resp = app.response_class(response=_graph_topology_json, status=200, mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


def _build_graph_votes_body(mode_filter: str | None) -> str:
    """Compute the /api/graph-votes JSON body for a given mode filter.

    Extracted so startup pre-warming can populate `_graph_votes_cache` without
    going through the Flask request stack.
    """
    global _graph_cache, _graph_votes_cache, _votes_revision

    edges = _graph_cache["edges"]
    nodes = _graph_cache["nodes"]
    coord_to_edge_idx: dict[tuple[str, str], list[int]] = _graph_cache["coord_to_edge_idx"]
    coord_to_node_idx: dict[str, int] = _graph_cache.get("coord_to_node_idx", {})

    segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)

    # Walk the (small) vote rows, not the (huge) edge list. A coord pair
    # can map to multiple parallel edges in a multigraph; broadcast the
    # vote count to every twin so all of them light up consistently.
    edge_votes = [0] * len(edges)
    for seg_key, vote_count in segment_votes.items():
        parts = seg_key.split("|")
        if len(parts) < 3 or not parts[1]:
            continue
        if mode_filter is not None and parts[2] != mode_filter:
            continue
        v = int(vote_count)
        for edge_idx in coord_to_edge_idx.get((parts[0], parts[1]), ()):
            edge_votes[edge_idx] += v

    # Node votes — read directly from the dedicated NODE_VOTES_KEY hash.
    # Each route vote increments one count per unique node it touches.
    node_votes = [0] * len(nodes)
    try:
        raw_nv = redis_client.hgetall(NODE_VOTES_KEY)
        for nk, vote_count in raw_nv.items():
            parts = nk.split("|")
            if len(parts) < 2:
                continue
            if mode_filter is not None and parts[1] != mode_filter:
                continue
            node_idx = coord_to_node_idx.get(parts[0])
            if node_idx is not None:
                node_votes[node_idx] += int(vote_count)
    except Exception as e:
        logger.warning(f"[GRAPH-VOTES] Failed to fetch node votes: {e}")

    # Vote types — same trick: iterate the small vote-types hash
    edge_vote_types: list = [None] * len(edges)
    node_vote_types: list = [None] * len(nodes)
    legend: list[str] = []
    legend_idx: dict[str, int] = {}

    def _merge_vote_types(target_list, idx, vt_map):
        existing = target_list[idx]
        merged: dict[str, int] = {}
        if existing:
            for li, cnt in existing:
                merged[legend[li]] = cnt
        for vt, cnt in vt_map.items():
            merged[vt] = merged.get(vt, 0) + cnt
        sorted_vts = sorted(merged.items(), key=lambda x: -x[1])
        encoded = []
        for vt, cnt in sorted_vts:
            if vt not in legend_idx:
                legend_idx[vt] = len(legend)
                legend.append(vt)
            encoded.append([legend_idx[vt], cnt])
        target_list[idx] = encoded

    try:
        from desire_path_voting import SEGMENT_VOTE_TYPES_KEY
        raw_vt = redis_client.hgetall(SEGMENT_VOTE_TYPES_KEY)
        for seg_key, vt_json in raw_vt.items():
            parts = seg_key.split("|")
            if len(parts) < 3 or not parts[1]:
                continue
            if mode_filter is not None and parts[2] != mode_filter:
                continue
            indices = coord_to_edge_idx.get((parts[0], parts[1]))
            if not indices:
                continue
            vt_map = json.loads(vt_json)
            for edge_idx in indices:
                _merge_vote_types(edge_vote_types, edge_idx, vt_map)
    except Exception as e:
        logger.warning(f"[GRAPH-VOTES] Failed to fetch edge vote types: {e}")

    try:
        raw_nvt = redis_client.hgetall(NODE_VOTE_TYPES_KEY)
        for nk, vt_json in raw_nvt.items():
            parts = nk.split("|")
            if len(parts) < 2:
                continue
            if mode_filter is not None and parts[1] != mode_filter:
                continue
            node_idx = coord_to_node_idx.get(parts[0])
            if node_idx is None:
                continue
            _merge_vote_types(node_vote_types, node_idx, json.loads(vt_json))
    except Exception as e:
        logger.warning(f"[GRAPH-VOTES] Failed to fetch node vote types: {e}")

    # Replace remaining None placeholders with [] for the JSON contract
    for i in range(len(edge_vote_types)):
        if edge_vote_types[i] is None:
            edge_vote_types[i] = []
    for i in range(len(node_vote_types)):
        if node_vote_types[i] is None:
            node_vote_types[i] = []

    body = json.dumps({
        "edge_votes": edge_votes,
        "node_votes": node_votes,
        "vote_type_legend": legend,
        "edge_vote_types": edge_vote_types,
        "node_vote_types": node_vote_types,
    })
    _graph_votes_cache[mode_filter or ""] = {"revision": _votes_revision, "body": body}
    return body


@app.route("/api/graph-votes", methods=["GET"])
def graph_votes():
    """Return vote data for the full cached graph topology.

    No bbox needed — indices match /api/graph-topology.
    Returns edge_votes, node_votes, vote_type_legend, edge_vote_types.

    Two optimizations vs. naive implementation:
      1. Iterates over the (small) Redis vote rows and looks up the affected
         edge index via a precomputed coord_to_edge_idx table. Avoids walking
         the full 313K-edge graph on every request.
      2. Caches the serialized response keyed by `_votes_revision`. While no
         votes have been cast (or received via pubsub), every request short-
         circuits to the cached body.
    """
    global _graph_cache, _graph_votes_cache, _votes_revision
    if _graph_cache is None:
        return jsonify({"error": "Graph not loaded"}), 500

    # Optional ?mode=X filter scopes the heatmap to a single theme namespace.
    mode_filter = (request.args.get("mode") or "").strip() or None
    cache_key = mode_filter or ""

    cached = _graph_votes_cache.get(cache_key)
    if cached and cached["revision"] == _votes_revision:
        body = cached["body"]
    else:
        try:
            body = _build_graph_votes_body(mode_filter)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    resp = app.response_class(response=body, status=200, mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


# Pre-warm the /api/graph-votes cache so the first page load after a deploy
# doesn't pay for a full Redis-scan + JSON-serialize round trip. Without this,
# each Flask replica builds its cache lazily on the first request — which is
# exactly when the user is staring at an unpopulated heatmap.
def _prewarm_graph_votes_cache():
    if _graph_cache is None:
        return
    # The unfiltered view (cache_key="") plus each theme's mode namespace.
    # Keep this list in sync with client-react/src/themes.ts.
    modes_to_warm = [None, "bikepaths", "trees", "walkways"]
    for mode in modes_to_warm:
        try:
            _build_graph_votes_body(mode)
        except Exception as e:
            logger.warning(f"[STARTUP] Failed to pre-warm graph-votes cache for mode={mode!r}: {e}")
    logger.info(f"[STARTUP] Pre-warmed graph-votes cache for {len(modes_to_warm)} mode(s)")

_prewarm_graph_votes_cache()


@app.route("/api/graph.geojson", methods=["GET"])
def graph_geojson():
    """Serve graph geometry as GeoJSON (cached from startup)."""
    try:
        global _graph_cache
        if _graph_cache is None:
            return jsonify({"error": "Graph cache not loaded"}), 500

        nodes = _graph_cache.get("nodes", [])
        edges = _graph_cache.get("edges", [])

        features = []

        # Add edge features
        for edge_idx, edge in enumerate(edges):
            from_idx, to_idx = edge[0], edge[1]
            name = edge[2] if len(edge) > 2 else ""
            highway = edge[3] if len(edge) > 3 else ""
            length = edge[4] if len(edge) > 4 else 0

            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[from_lon, from_lat], [to_lon, to_lat]]
                },
                "properties": {
                    "type": "edge",
                    "from_idx": from_idx,
                    "to_idx": to_idx,
                    "name": name,
                    "highway": highway,
                    "length": length,
                }
            })

        # Add node features
        for node_idx, (lat, lon) in enumerate(nodes):
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "type": "node",
                    "node_id": node_idx,
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                }
            })

        geojson = {
            "type": "FeatureCollection",
            "features": features
        }

        return jsonify(geojson)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/votes", methods=["GET"])
def get_votes():
    """Get vote counts for nodes and edges in a bounding box.

    Query params:
      bbox: minLon,minLat,maxLon,maxLat (comma-separated floats)

    Returns:
      {
        "node_votes": { "node_id": vote_count, ... },
        "edge_votes": { "edge_id": vote_count, ... }
      }
    """
    try:
        bbox = request.args.get("bbox", "")
        if not bbox:
            return jsonify({"error": "bbox parameter required"}), 400

        west, south, east, north = [float(v) for v in bbox.split(",")]

        # Get graph data for bbox
        data = router.get_graph_for_bbox(south, west, north, east)

        # Fetch vote counts from Redis
        segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)

        # Build lookup dict for O(1) lookups
        vote_lookup = {}
        for seg_key, vote_count in segment_votes.items():
            parts = seg_key.split("|")
            if len(parts) >= 3:
                c1 = parts[0]
                c2 = parts[1]
                vote_count = int(vote_count)
                vote_lookup[(c1, c2)] = vote_lookup.get((c1, c2), 0) + vote_count
                vote_lookup[(c2, c1)] = vote_lookup.get((c2, c1), 0) + vote_count

        # Build node votes dict (using node indices as keys)
        node_votes = {}
        for from_idx, to_idx, *rest in data["edges"]:
            from_lat, from_lon = data["nodes"][from_idx]
            to_lat, to_lon = data["nodes"][to_idx]
            c1_str = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2_str = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            votes = vote_lookup.get((c1_str, c2_str), 0)
            node_votes[from_idx] = node_votes.get(from_idx, 0) + votes
            node_votes[to_idx] = node_votes.get(to_idx, 0) + votes

        # Build edge votes dict (using edge indices as keys)
        edge_votes = {}
        for edge_idx, (from_idx, to_idx, *rest) in enumerate(data["edges"]):
            from_lat, from_lon = data["nodes"][from_idx]
            to_lat, to_lon = data["nodes"][to_idx]
            c1_str = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2_str = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            edge_votes[edge_idx] = vote_lookup.get((c1_str, c2_str), 0)

        return jsonify({
            "node_votes": node_votes,
            "edge_votes": edge_votes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Run: python app.py
    app.run(host="0.0.0.0", port=5001, debug=False)

