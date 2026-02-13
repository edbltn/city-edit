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
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock
from desire_path_voting import compute_desire_path_votes, cast_desire_path_votes, extract_all_segments
from hex_voting import (
    build_hex_overlay_from_segments,
    get_cached_hex_overlay,
    update_hex_cache_incremental,
    regenerate_hex_cache,
    rebuild_weighted_hex_cache,
    ZOOM_TO_RESOLUTION,
    H3_RESOLUTIONS,
)
from database import init_db, record_segment_votes

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
REDIS_CHANNEL = "state_updates"

# In-memory cache for hex overlays (keyed by "mode:resolution")
# This avoids Redis round-trips for every WebSocket push
hex_cache = {}
hex_cache_revision = 0  # Incremented when cache is invalidated


def invalidate_hex_cache_local():
    """Clear in-memory hex cache (local instance only)."""
    global hex_cache, hex_cache_revision
    hex_cache = {}
    hex_cache_revision += 1


def invalidate_hex_cache():
    """Clear in-memory hex cache and notify other instances via pub/sub."""
    invalidate_hex_cache_local()
    # Publish invalidation message so other Flask instances clear their cache
    try:
        redis_client.publish(REDIS_CHANNEL, json.dumps({"type": "cache_invalidate"}))
    except redis.ConnectionError:
        pass  # If Redis is down, just clear local cache


def publish_votes_changed():
    """Publish votes_changed message to trigger WebSocket broadcasts."""
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

                if msg_type == "cache_invalidate":
                    # Another instance invalidated cache, clear ours too
                    invalidate_hex_cache_local()
                    logger.info("[PUBSUB] Received cache_invalidate, cleared local cache")
                elif msg_type == "votes_changed":
                    # This triggers WebSocket push - handled by WS handler
                    pass

            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"[PUBSUB] Invalid message: {e}")

    thread = threading.Thread(target=listener, daemon=True, name="pubsub-listener")
    thread.start()
    return thread

def get_hex_overlay_cached(mode_filter: str, resolution: int) -> dict:
    """Get hex overlay from in-memory cache, falling back to Redis."""
    global hex_cache
    cache_key = f"{mode_filter or 'all'}:{resolution}"

    if cache_key in hex_cache:
        return hex_cache[cache_key]

    # Load from Redis
    result = get_cached_hex_overlay(redis_client, mode_filter, resolution)
    hex_cache[cache_key] = result
    return result

def clear_hex_cache():
    """Clear in-memory hex cache to force reload from Redis."""
    global hex_cache
    hex_cache = {}
    logger.info("[HEX_CACHE] Cleared in-memory cache")

def preload_hex_cache():
    """Preload all resolutions into memory on startup."""
    global hex_cache
    hex_cache = {}  # Clear first
    logger.info("[HEX_CACHE] Preloading hex cache for all resolutions...")
    for mode in ["bike", "walk", "drive", None]:
        for res in H3_RESOLUTIONS:
            cache_key = f"{mode or 'all'}:{res}"
            result = get_cached_hex_overlay(redis_client, mode, res)
            hex_count = len(result.get("hexes", {}))
            hex_cache[cache_key] = result
            logger.info(f"[HEX_CACHE] {cache_key}: {hex_count} hexes")
    logger.info(f"[HEX_CACHE] Preloaded {len(hex_cache)} cache entries")

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

# Preload hex cache into memory
preload_hex_cache()

# Initialize database (creates tables if needed)
init_db()

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


def get_hex_overlay(mode_filter=None, resolution=None):
    """Get hex overlay from in-memory cache, falling back to Redis if needed."""
    try:
        # Use in-memory cache for fast access
        result = get_hex_overlay_cached(mode_filter, resolution)

        # If cache is empty but we have segment votes, regenerate
        if not result.get("hexes"):
            segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)
            if segment_votes:
                logger.info("[HEX_CACHE] Cache empty but segment votes exist, regenerating...")
                regenerate_hex_cache(redis_client)
                invalidate_hex_cache()  # Clear in-memory cache
                result = get_hex_overlay_cached(mode_filter, resolution)

        return result
    except redis.ConnectionError:
        return {"hexes": {}, "max_votes": 1, "res": resolution or 13, "suggestions": {}}


def make_state(rev: int, mode_filter=None, resolution=None):
    """Build map state with segment overlay and a single hex overlay.

    Sends only the requested resolution to keep payload small (~80-120 KB
    instead of ~500 KB for all 6). Client merges and caches resolutions
    as they arrive.
    Uses compact format for hex overlay to reduce bandwidth:
    - 'res': resolution level
    - 'h': array of [hex_id, weight] tuples (more compact than object)
    - 'm': max votes
    """
    segment_overlay = get_segment_overlay(mode_filter)

    # Build hex overlay for requested resolution only
    target_res = resolution or 13
    hex_overlays = {}
    hex_data = get_hex_overlay(mode_filter, target_res)
    hex_list = [[hex_id, round(weight, 4)] for hex_id, weight in hex_data.get("hexes", {}).items()]

    # Build suggestion legend and per-hex indices (top 3 per hex)
    suggestions = hex_data.get("suggestions", {})
    legend = []
    legend_index = {}
    hex_sug = {}
    for hex_id, type_counts in suggestions.items():
        sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        indices = []
        for vtype, _count in sorted_types:
            if vtype not in legend_index:
                legend_index[vtype] = len(legend)
                legend.append(vtype)
            indices.append(legend_index[vtype])
        if indices:
            hex_sug[hex_id] = indices

    overlay = {
        "res": target_res,
        "h": hex_list,
        "m": hex_data.get("max_votes", 1.0)
    }
    if legend:
        overlay["sl"] = legend
        overlay["s"] = hex_sug
    hex_overlays[target_res] = overlay

    return {
        "revision": rev,
        "overlays": {
            "desire_paths": segment_overlay
        },
        "hex_overlays": hex_overlays
    }


@app.route("/health")
def health():
    """Health check endpoint for load balancers and monitoring."""
    try:
        redis_client.ping()
        return jsonify({"status": "healthy", "redis": "connected"}), 200
    except redis.ConnectionError:
        return jsonify({"status": "unhealthy", "redis": "disconnected"}), 503


@sock.route("/ws")
def ws(ws):
    """WebSocket handler using Redis pub/sub for efficient push updates."""
    rev = 1
    current_mode = "bike"  # Default mode filter
    current_zoom = 14  # Default zoom level
    current_resolution = ZOOM_TO_RESOLUTION.get(current_zoom, 13)
    last_push_time = 0
    KEEPALIVE_INTERVAL = 30  # Send keepalive every 30 seconds

    # Create pub/sub subscription for this connection
    ws_pubsub_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    pubsub = ws_pubsub_client.pubsub()
    pubsub.subscribe(REDIS_CHANNEL)

    # Send initial state immediately on connect
    state_msg = {"type": "map_state", "state": make_state(rev, current_mode, current_resolution)}
    ws.send(json.dumps(state_msg))
    rev += 1
    last_push_time = time.time()

    try:
        while True:
            # Check for incoming WebSocket messages (non-blocking)
            try:
                msg = ws.receive(timeout=0)
                if msg:
                    data = json.loads(msg)
                    if data.get("type") == "cast_votes":
                        # Votes are now cast server-side in /api/routes endpoint
                        # This message type is kept for backwards compatibility
                        pass
                    elif data.get("type") == "set_mode":
                        new_mode = data.get("mode")
                        if new_mode in ("bike", "walk", "drive"):
                            current_mode = new_mode
                            logger.info(f"Mode filter set to: {current_mode}")
                            # Immediately send updated state with new mode filter
                            state_msg = {"type": "map_state", "state": make_state(rev, current_mode, current_resolution)}
                            ws.send(json.dumps(state_msg))
                            rev += 1
                            last_push_time = time.time()
                    elif data.get("type") == "set_zoom":
                        new_zoom = data.get("zoom", 14)
                        if isinstance(new_zoom, (int, float)) and 0 <= new_zoom <= 22:
                            current_zoom = int(new_zoom)
                            new_resolution = ZOOM_TO_RESOLUTION.get(current_zoom, 13)
                            if new_resolution != current_resolution:
                                current_resolution = new_resolution
                                state_msg = {"type": "map_state", "state": make_state(rev, current_mode, current_resolution)}
                                ws.send(json.dumps(state_msg))
                                rev += 1
                                last_push_time = time.time()
            except Exception as e:
                # Timeout, no data, and connection closed are expected - don't log
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
                state_msg = {"type": "map_state", "state": make_state(rev, current_mode, current_resolution)}
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
    mode = data.get("mode", "bike")
    waypoints = data.get("waypoints", [])  # List of [lat, lon] pairs

    logger.info(f"[ROUTE] Request: start={start}, end={end}, mode={mode}, waypoints={len(waypoints)}")

    if not start or not end:
        logger.error("[ROUTE] Error: Missing coordinates")
        return jsonify({"error": "Missing start or end coordinates"}), 400

    try:
        # Compute main route WITHOUT waypoints (start → end only)
        # Waypoints only affect the desire path, not the main route
        route_desired = router.calculate_route(
            start=(start[0], start[1]),
            end=(end[0], end[1]),
            mode="walk",  # Force walk mode for all routes
            waypoints=[]
        )
        if "error" in route_desired:
            logger.error(f"[ROUTE] Failed to get {mode} route: {route_desired['error']}")
            return jsonify(route_desired), 404

        # Compute desire path WITH waypoints (walking route through user's waypoints)
        if mode == "walk":
            # For walk mode, the desire path includes waypoints
            if waypoints:
                waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
                route_walk = router.calculate_route(
                    start=(start[0], start[1]),
                    end=(end[0], end[1]),
                    mode="walk",
                    waypoints=waypoints_tuples
                )
            else:
                route_walk = route_desired
        else:
            # For bike/drive, compute walk route with waypoints
            waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
            route_walk = router.calculate_route(
                start=(start[0], start[1]),
                end=(end[0], end[1]),
                mode="walk",
                waypoints=waypoints_tuples
            )
            if "error" in route_walk:
                logger.warning(f"[ROUTE] Failed to get walk route: {route_walk['error']}, returning desired route only")
                # If walk route fails, just return desired route without voting
                return jsonify({
                    "route": route_desired,
                    "desire_path": None,
                    "desire_tiles": []
                })

        # Compute desire path segments (but do NOT cast votes - user must click "Cast Vote")
        vote_segments, vote_mode, desire_tiles = compute_desire_path_votes(
            route_desired, route_walk, mode  # Use user's selected mode
        )

        # Return both routes and the segments for voting
        # Client will POST to /api/vote when user clicks "Cast Vote"
        return jsonify({
            "route": route_desired,
            "desire_path": route_walk if mode != "walk" else None,
            "desire_tiles": desire_tiles,
            "desire_path_segments": vote_segments,
            "vote_mode": vote_mode
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
    mode = data.get("mode", "bike")
    vote_type = data.get("vote_type", "")

    # Validate: need either segments or point
    if not segments and not point:
        return jsonify({"error": "No segments or point to vote on"}), 400

    # Get hashed IP for weighted voting
    ip_hash = get_client_ip()

    try:
        if point:
            # Single-point vote (no route, just a location)
            from hex_voting import cast_point_vote
            cast_point_vote(redis_client, point, mode, ip_hash, defer_rebuild=True, vote_type=vote_type)
            logger.info(f"[VOTE] Cast point vote for '{vote_type}' at {point} as '{mode}' from IP {ip_hash[:8]}...")

            # Record to database for persistence (vote_type only stored here)
            from database import record_point_vote
            record_point_vote(point, mode, ip_hash, vote_type)

            # Invalidate in-memory cache so next request gets fresh data
            invalidate_hex_cache()

            # Increment revision and notify all instances
            redis_client.incr("revision")
            publish_votes_changed()

            # Run expensive hex cache rebuild in background thread
            def background_rebuild():
                try:
                    rebuild_weighted_hex_cache(redis_client, mode)
                    invalidate_hex_cache()
                    publish_votes_changed()
                    logger.info(f"[VOTE] Background hex rebuild complete for point vote mode={mode}")
                except Exception as e:
                    logger.error(f"[VOTE] Background rebuild failed: {e}")

            threading.Thread(target=background_rebuild, daemon=True).start()

            return jsonify({
                "success": True,
                "point_vote": True,
                "vote_type": vote_type
            })
        else:
            # Route vote (existing logic)
            # Cast segment votes (updates ip_vote_counts)
            vote_count = cast_desire_path_votes(redis_client, segments, mode, ip_hash)
            logger.info(f"[VOTE] Cast {vote_count} segment votes for '{vote_type}' as '{mode}' from IP {ip_hash[:8]}...")

            # Update hex cache incrementally with IP-weighted votes (defer expensive rebuild)
            update_hex_cache_incremental(redis_client, segments, mode, ip_hash, defer_rebuild=True, vote_type=vote_type)
            logger.info(f"[VOTE] Updated hex cache with {len(segments)} segments")

            # Record to database for persistence (async, doesn't block response)
            # Weight per segment = 1.0 / (previous votes from this IP + 1)
            weight_per_segment = 1.0 / len(segments) if segments else 1.0
            record_segment_votes(segments, mode, ip_hash, weight_per_segment, vote_type)

            # Invalidate in-memory cache so next request gets fresh data
            invalidate_hex_cache()

            # Increment revision and notify all instances to push WebSocket updates
            redis_client.incr("revision")
            publish_votes_changed()

            # Run expensive hex cache rebuild in background thread
            def background_rebuild():
                try:
                    rebuild_weighted_hex_cache(redis_client, mode)
                    invalidate_hex_cache()
                    publish_votes_changed()
                    logger.info(f"[VOTE] Background hex rebuild complete for mode={mode}")
                except Exception as e:
                    logger.error(f"[VOTE] Background rebuild failed: {e}")

            threading.Thread(target=background_rebuild, daemon=True).start()

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


if __name__ == "__main__":
    # Run: python app.py
    app.run(host="0.0.0.0", port=5001, debug=False)

