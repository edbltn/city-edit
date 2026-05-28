import json
import logging
import os
import sys
import time
import hashlib
import threading
import redis
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock

import vote_store
from database import (
    init_db, get_cursor, record_edge_votes, record_point_vote,
    DATABASE_URL,
)

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(line_buffering=True)

# ── Routers ────────────────────────────────────────────────────────────────

from python_router import PythonRouter
from osrm_router import OsrmRouter
logger.info("[STARTUP] Using OSRM router + Python graph provider")

# ── Environment ────────────────────────────────────────────────────────────

logger.info(f"[DEBUG] REDIS_HOST from env: {os.environ.get('REDIS_HOST', 'NOT_SET')}")
logger.info(f"[DEBUG] REDIS_PORT from env: {os.environ.get('REDIS_PORT', 'NOT_SET')}")
load_dotenv()

# ── Flask ──────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# ── Redis ──────────────────────────────────────────────────────────────────

redis_host = os.environ.get('REDIS_HOST', 'localhost')
redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)

logger.info(f"[REDIS] Connecting to Redis at: {redis_host}:6379")
try:
    redis_info = redis_client.info("server")
    logger.info(f"[REDIS] Connected — v{redis_info.get('redis_version', '?')}")
except redis.ConnectionError as e:
    logger.error(f"[REDIS] Could not connect: {e}")

# ── Routers init ───────────────────────────────────────────────────────────

osrm_host = os.environ.get('OSRM_HOST', 'localhost')
osrm_port = int(os.environ.get('OSRM_PORT', '5000'))
route_router = OsrmRouter(host=osrm_host, port=osrm_port)
graph_provider = PythonRouter(data_dir="osm_data")
logger.info("[STARTUP] OSRM router + graph provider initialized")

# ── Graph cache ────────────────────────────────────────────────────────────

_graph_cache = None
_graph_topology_json: str | None = None
_node_adj: list[list[int]] = []


def _load_graph_cache():
    global _graph_cache, _graph_topology_json, _node_adj
    logger.info("[STARTUP] Pre-loading graph geometry...")
    try:
        data = graph_provider.get_graph_for_bbox(40.700, -74.020, 40.880, -73.907)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # coord → edge index reverse map (both directions for undirected lookup)
        edge_coord_keys: list[tuple[str, str]] = []
        coord_to_edge_idx: dict[tuple[str, str], list[int]] = {}
        for i, edge in enumerate(edges):
            from_idx, to_idx = edge[0], edge[1]
            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]
            c1 = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2 = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            edge_coord_keys.append((c1, c2))
            coord_to_edge_idx.setdefault((c1, c2), []).append(i)
            if c1 != c2:
                coord_to_edge_idx.setdefault((c2, c1), []).append(i)

        coord_to_node_idx: dict[str, int] = {}
        for i, node in enumerate(nodes):
            lat, lon = node[0], node[1]
            coord_to_node_idx[f"{round(lon, 5)},{round(lat, 5)}"] = i

        # Node adjacency: node_id → [edge_ids]
        adj: list[list[int]] = [[] for _ in range(len(nodes))]
        for i, edge in enumerate(edges):
            adj[edge[0]].append(i)
            adj[edge[1]].append(i)
        _node_adj = adj

        data["edge_coord_keys"] = edge_coord_keys
        data["coord_to_edge_idx"] = coord_to_edge_idx
        data["coord_to_node_idx"] = coord_to_node_idx
        _graph_cache = data

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

# ── Database + vote types ──────────────────────────────────────────────────

init_db()

if DATABASE_URL:
    try:
        with get_cursor() as cursor:
            vote_store.load_vote_types(cursor)
    except Exception as e:
        logger.error(f"[STARTUP] Failed to load vote types: {e}")


# ── Migration: old coordinate-keyed votes → packed-key edge_votes ─────────

def _migrate_old_votes():
    """One-time migration from the old votes/node_votes tables to edge_votes.

    Parses coordinate-string segment keys, maps them to graph edge IDs via
    coord_to_edge_idx, creates vote_type records, and inserts into edge_votes.
    Skips entirely when edge_votes is already populated.
    """
    if not DATABASE_URL or _graph_cache is None:
        return

    try:
        with get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM edge_votes")
            if cursor.fetchone()[0] > 0:
                logger.info("[MIGRATE] edge_votes already populated, skipping")
                return

            cursor.execute("SELECT COUNT(*) FROM votes")
            old_count = cursor.fetchone()[0]
            if old_count == 0:
                logger.info("[MIGRATE] No old votes to migrate")
                return

            logger.info(f"[MIGRATE] Migrating {old_count} old votes → edge_votes...")

            # Ensure all distinct vote_type strings have IDs
            cursor.execute("""
                SELECT DISTINCT vote_type FROM votes
                WHERE vote_type IS NOT NULL AND vote_type != ''
            """)
            for (label,) in cursor.fetchall():
                vote_store.get_vote_type_id(label, cursor)

            coord_to_edge = _graph_cache["coord_to_edge_idx"]

            batch_size = 5000
            offset = 0
            total_migrated = 0
            total_skipped = 0

            while True:
                cursor.execute(
                    "SELECT segment_key, mode, ip_hash, vote_type "
                    "FROM votes ORDER BY id LIMIT %s OFFSET %s",
                    (batch_size, offset),
                )
                rows = cursor.fetchall()
                if not rows:
                    break

                insert_data = []
                for seg_key, mode, ip_hash, vt_label in rows:
                    parts = seg_key.split("|")
                    if len(parts) < 3 or not parts[1]:
                        total_skipped += 1
                        continue
                    try:
                        c1_parts = parts[0].split(",")
                        c2_parts = parts[1].split(",")
                        c1 = f"{round(float(c1_parts[0]), 5)},{round(float(c1_parts[1]), 5)}"
                        c2 = f"{round(float(c2_parts[0]), 5)},{round(float(c2_parts[1]), 5)}"
                    except (ValueError, IndexError):
                        total_skipped += 1
                        continue

                    edge_ids = coord_to_edge.get((c1, c2), [])
                    if not edge_ids:
                        total_skipped += 1
                        continue

                    mode_int = vote_store.mode_to_int(mode or "walk")
                    vt_id = vote_store.get_vote_type_id(vt_label or "", cursor)
                    for eid in edge_ids:
                        pk = vote_store.pack(eid, mode_int, vt_id)
                        insert_data.append((pk, eid, mode_int, vt_id, ip_hash))

                if insert_data:
                    from psycopg2.extras import execute_values
                    execute_values(
                        cursor,
                        """INSERT INTO edge_votes
                           (packed_key, edge_id, mode, vote_type_id, ip_hash)
                           VALUES %s
                           ON CONFLICT (packed_key, ip_hash) DO NOTHING""",
                        insert_data,
                    )
                    total_migrated += len(insert_data)

                offset += batch_size
                if len(rows) < batch_size:
                    break

            logger.info(
                f"[MIGRATE] Done: {total_migrated} edge_votes inserted, "
                f"{total_skipped} old rows skipped"
            )

    except Exception as e:
        logger.error(f"[MIGRATE] Migration failed: {e}")

_migrate_old_votes()


# ── Populate Redis from Postgres ───────────────────────────────────────────

def _populate_redis():
    """Replay edge_votes from Postgres into Redis if Redis is underpopulated."""
    if not DATABASE_URL:
        return

    try:
        existing = redis_client.hlen(vote_store.REDIS_HASH)
        if existing >= 100:
            logger.info(f"[POPULATE] Redis '{vote_store.REDIS_HASH}' has {existing} keys, skipping")
            return

        with get_cursor() as cursor:
            cursor.execute("""
                SELECT packed_key, COUNT(*) AS cnt
                FROM edge_votes
                GROUP BY packed_key
            """)
            rows = cursor.fetchall()

        if not rows:
            logger.info("[POPULATE] No edge_votes in Postgres")
            return

        pipe = redis_client.pipeline()
        for i, (pk, cnt) in enumerate(rows):
            pipe.hset(vote_store.REDIS_HASH, str(pk), cnt)
            if (i + 1) % 5000 == 0:
                pipe.execute()
                pipe = redis_client.pipeline()
        pipe.execute()

        logger.info(f"[POPULATE] Loaded {len(rows)} packed keys into Redis")

    except Exception as e:
        logger.error(f"[POPULATE] Redis population failed: {e}")

_populate_redis()


# ── Vote response cache ───────────────────────────────────────────────────

_vote_cache: dict[str, dict] = {}


def _build_graph_votes_body(mode_filter_str: str | None) -> str:
    """Build the /api/graph-votes JSON response, with caching by revision."""
    global _vote_cache

    rev = int(redis_client.get(vote_store.REVISION_KEY) or 0)
    cache_key = mode_filter_str or ""
    cached = _vote_cache.get(cache_key)
    if cached and cached["rev"] == rev:
        return cached["body"]

    votes = vote_store.read_all(redis_client)
    mode_int = vote_store.mode_to_int(mode_filter_str) if mode_filter_str else None

    edges = _graph_cache["edges"]
    nodes = _graph_cache["nodes"]
    arrays = vote_store.build_arrays(
        votes, len(edges), len(nodes), _node_adj, mode_int,
    )
    arrays["rev"] = rev
    arrays["vote_types"] = {
        str(k): v for k, v in vote_store.all_vote_types().items()
    }
    body = json.dumps(arrays)

    _vote_cache[cache_key] = {"rev": rev, "body": body}
    return body


def _prewarm_graph_votes_cache():
    if _graph_cache is None:
        return
    for mode in [None, "bikepaths", "trees", "walkways"]:
        try:
            _build_graph_votes_body(mode)
        except Exception as e:
            logger.warning(f"[STARTUP] Pre-warm failed for mode={mode!r}: {e}")
    logger.info("[STARTUP] Pre-warmed graph-votes cache")

_prewarm_graph_votes_cache()


# ── Pubsub delta listener (invalidates vote cache on peer writes) ─────────

def _start_delta_listener():
    def listener():
        ps_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
        ps = ps_client.pubsub()
        ps.subscribe(vote_store.REDIS_CHANNEL)
        logger.info(f"[PUBSUB] Subscribed to {vote_store.REDIS_CHANNEL}")
        for msg in ps.listen():
            if msg["type"] != "message":
                continue
            # Invalidate the local vote cache so the next /api/graph-votes
            # request rebuilds from Redis. WebSocket clients are notified
            # via their own pubsub subscriptions inside the /ws handler.
            _vote_cache.clear()

    t = threading.Thread(target=listener, daemon=True, name="delta-listener")
    t.start()
    return t

_start_delta_listener()


# ── Helpers ────────────────────────────────────────────────────────────────

def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.remote_addr or "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    if _graph_topology_json is None:
        return jsonify({"status": "unhealthy", "graph": "not loaded"}), 503
    try:
        redis_client.ping()
        return jsonify({"status": "healthy", "redis": "connected"}), 200
    except redis.ConnectionError:
        return jsonify({"status": "unhealthy", "redis": "disconnected"}), 503


# ── WebSocket (delta-based) ───────────────────────────────────────────────

@sock.route("/ws")
def ws(ws):
    """Push vote deltas to clients in real time.

    On connect: send init message.
    On vote (via pubsub): forward delta verbatim.
    Every 30s: keepalive ping.
    """
    ws_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
    pubsub = ws_client.pubsub()
    pubsub.subscribe(vote_store.REDIS_CHANNEL)

    rev = int(redis_client.get(vote_store.REVISION_KEY) or 0)
    ws.send(json.dumps({"type": "init", "rev": rev}))

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
        pubsub.unsubscribe(vote_store.REDIS_CHANNEL)
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

    try:
        waypoints_tuples = [(wp[0], wp[1]) for wp in waypoints]
        route = route_router.calculate_route(
            start=(start[0], start[1]),
            end=(end[0], end[1]),
            mode="walk",
            waypoints=waypoints_tuples,
        )

        if "error" in route:
            return jsonify(route), 404

        from desire_path_voting import extract_all_segments
        segments = extract_all_segments(route.get("geometry"))

        # Map segments → graph edge IDs for fast voting
        edge_ids = []
        if _graph_cache:
            edge_ids = vote_store.coords_to_edge_ids(
                segments, _graph_cache["coord_to_edge_idx"]
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
    """Cast votes using packed integer keys.

    Accepts edge_ids (preferred) or segments (legacy, mapped to edge_ids).
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    edge_ids = data.get("edge_ids", [])
    segments = data.get("segments", [])
    point = data.get("point")
    mode = data.get("mode", "walk")
    vt_label = data.get("vote_type", "")

    # Legacy: map coordinate segments to edge IDs
    if not edge_ids and segments and _graph_cache:
        edge_ids = vote_store.coords_to_edge_ids(
            segments, _graph_cache["coord_to_edge_idx"]
        )

    if not edge_ids and not point:
        return jsonify({"error": "No edge_ids or point to vote on"}), 400

    voter_id = data.get("voter_id")
    if voter_id:
        ip_hash = hashlib.sha256(str(voter_id).encode()).hexdigest()[:16]
    else:
        ip_hash = get_client_ip()

    mode_int = vote_store.mode_to_int(mode)

    try:
        # Point vote — Postgres only, no heatmap
        if point and not edge_ids:
            threading.Thread(
                target=lambda: record_point_vote(point, mode, ip_hash, vt_label),
                daemon=True,
            ).start()
            return jsonify({"success": True, "point_vote": True})

        # Resolve vote type → integer ID
        vt_id = 0
        if vt_label:
            with get_cursor() as cursor:
                vt_id = vote_store.get_vote_type_id(vt_label, cursor)

        # Redis: one HINCRBY per edge (fast path)
        count = vote_store.cast(redis_client, edge_ids, mode_int, vt_id)
        logger.info(
            f"[VOTE] {count} edges, mode={mode}, "
            f"vt={vt_id} ({vt_label!r}), ip={ip_hash[:8]}…"
        )

        # Publish delta for instant cross-client sync
        vote_store.publish_delta(redis_client, edge_ids, mode_int, vt_id)

        # Invalidate local vote cache
        _vote_cache.clear()

        # Background Postgres persist
        def _persist():
            try:
                record_edge_votes(edge_ids, mode_int, vt_id, ip_hash)
            except Exception as e:
                logger.error(f"[VOTE] DB persist failed: {e}")

        threading.Thread(target=_persist, daemon=True).start()

        return jsonify({"success": True, "edges_voted": count})

    except Exception as e:
        logger.error(f"[VOTE] Error: {e}")
        return jsonify({"error": f"Vote failed: {str(e)}"}), 500


# ── Graph data APIs ────────────────────────────────────────────────────────

@app.route("/api/nearest-node", methods=["GET"])
def nearest_node():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400
    try:
        node_lat, node_lon = graph_provider.nearest_node_coords(lat, lon)
        return jsonify({"lat": node_lat, "lng": node_lon})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/reverse-geocode", methods=["GET"])
def reverse_geocode():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lng"])
    except (KeyError, ValueError):
        return jsonify({"error": "lat and lng are required"}), 400
    try:
        address = graph_provider.reverse_geocode(lat, lon)
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
        data = graph_provider.get_graph_for_bbox(south, west, north, east)
        return jsonify(data)
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


@app.route("/api/graph-topology", methods=["GET"])
def graph_topology():
    if _graph_topology_json is None:
        return jsonify({"error": "Graph not loaded"}), 500
    resp = app.response_class(
        response=_graph_topology_json, status=200, mimetype="application/json",
    )
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/api/graph-votes", methods=["GET"])
def graph_votes():
    """Return per-edge and per-node vote arrays.

    Indices match /api/graph-topology. Cached by revision — only rebuilds
    after a vote is cast.
    """
    if _graph_cache is None:
        return jsonify({"error": "Graph not loaded"}), 500

    mode_filter = (request.args.get("mode") or "").strip() or None
    try:
        body = _build_graph_votes_body(mode_filter)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    resp = app.response_class(response=body, status=200, mimetype="application/json")
    resp.headers["Cache-Control"] = "public, max-age=5"
    return resp


@app.route("/api/graph.geojson", methods=["GET"])
def graph_geojson():
    if _graph_cache is None:
        return jsonify({"error": "Graph cache not loaded"}), 500

    nodes = _graph_cache.get("nodes", [])
    edges = _graph_cache.get("edges", [])
    features = []

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
                "coordinates": [[from_lon, from_lat], [to_lon, to_lat]],
            },
            "properties": {
                "type": "edge",
                "from_idx": from_idx,
                "to_idx": to_idx,
                "name": name,
                "highway": highway,
                "length": length,
            },
        })

    for node_idx, (lat, lon) in enumerate(nodes):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "type": "node",
                "node_id": node_idx,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
            },
        })

    return jsonify({"type": "FeatureCollection", "features": features})


# ── Admin ──────────────────────────────────────────────────────────────────

@app.route("/api/admin/refresh-osm", methods=["POST"])
def admin_refresh_osm():
    logger.info("[ADMIN] OSM refresh triggered")
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        logger.info("[ADMIN] Request has Authorization header (Cloud Scheduler)")
    try:
        import subprocess
        result = subprocess.run(
            ["python", "refresh_osm.py", "--region", "manhattan"],
            capture_output=True, text=True, timeout=1800,
        )
        logger.info(f"[ADMIN] Refresh exit code {result.returncode}")
        if result.returncode == 0:
            graph_provider.reload()
            return jsonify({
                "status": "success",
                "stdout": result.stdout[-2000:] if result.stdout else "",
            })
        else:
            return jsonify({
                "status": "failed",
                "stdout": result.stdout[-2000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }), 500
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
            "hash_size_ev": redis_client.hlen(vote_store.REDIS_HASH),
            "revision": int(redis_client.get(vote_store.REVISION_KEY) or 0),
        }
    except redis.ConnectionError:
        result["redis"] = {"connected": False}

    if DATABASE_URL:
        try:
            with get_cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM edge_votes")
                ev_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM vote_types")
                vt_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(DISTINCT ip_hash) FROM edge_votes")
                unique_voters = cursor.fetchone()[0]
            result["database"] = {
                "edge_votes": ev_count,
                "vote_types": vt_count,
                "unique_voters": unique_voters,
            }
        except Exception as e:
            result["database"] = {"error": str(e)}
    else:
        result["database"] = {"connected": False}

    result["app"] = {
        "graph_loaded": _graph_cache is not None,
        "graph_nodes": len(_graph_cache["nodes"]) if _graph_cache else 0,
        "graph_edges": len(_graph_cache["edges"]) if _graph_cache else 0,
        "vote_types_cached": len(vote_store.all_vote_types()),
    }

    return jsonify(result)


@app.route("/api/admin/router-stats", methods=["GET"])
def admin_router_stats():
    try:
        stats = graph_provider.stats()
        return jsonify({
            "router": "osrm",
            "graph_provider": "python",
            "version": graph_provider.get_version(),
            "stats": stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=os.environ.get("FLASK_DEBUG") == "1")
