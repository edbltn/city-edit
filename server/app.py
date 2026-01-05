import json
import os
import time
import requests
import redis
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock
from roam_cache import (
    cache_path, get_cached_path, register_path_tiles, extract_waypoint_tiles,
    cache_mst_segments, build_tile_to_coords_map
)
from tiles import coords_to_tile
from desire_path_voting import compute_desire_path_votes, cast_desire_path_votes

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# Redis connection
redis_host = os.environ.get('REDIS_HOST', 'localhost')
redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
SEGMENT_VOTES_KEY = "segment_votes"

# Log Redis connection info at startup
print(f"[REDIS] Connecting to Redis at: {redis_host}:6379")
try:
    redis_info = redis_client.info("server")
    print(f"[REDIS] Connected successfully - Redis version: {redis_info.get('redis_version', 'unknown')}")
    if redis_host != 'localhost':
        print(f"[REDIS] Using CLOUD Redis (Memorystore) at {redis_host}")
    else:
        print(f"[REDIS] Using LOCAL Redis at {redis_host}")
except redis.ConnectionError as e:
    print(f"[REDIS] WARNING: Could not connect to Redis at {redis_host}: {e}")

# OpenRouteService API key (get free key at https://openrouteservice.org/)
ORS_API_KEY = os.environ.get('ORS_API_KEY', '')

# OpenRouteService profile mapping
ORS_PROFILES = {
    "bike": "cycling-regular",
    "walk": "foot-walking",
    "drive": "driving-car"
}


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
    """Build map state with segment overlay, filtered by mode."""
    segment_overlay = get_segment_overlay(mode_filter)

    return {
        "revision": rev,
        "overlays": {
            "desire_paths": segment_overlay
        }
    }


@sock.route("/ws")
def ws(ws):
    import select
    rev = 1
    current_mode = "bike"  # Default mode filter
    while True:
        # Check for incoming messages (non-blocking)
        try:
            # Use select to check if there's data available
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
                        print(f"Mode filter set to: {current_mode}")
        except Exception as e:
            # Timeout or no data is expected, only log actual errors
            if "timed out" not in str(e).lower():
                pass

        # Push current state filtered by mode
        state_msg = {"type": "map_state", "state": make_state(rev, current_mode)}
        ws.send(json.dumps(state_msg))
        rev += 1
        time.sleep(1)


@app.route("/api/routes", methods=["POST"])
def calculate_route():
    """
    Calculate route using OpenRouteService API with desire path voting.

    For bike/drive modes:
    - Computes BOTH the desired mode route AND a walk route
    - Votes for walk segments that diverge from the desired route (desire paths)
    - Returns both routes so frontend can display them

    For walk mode:
    - Computes walk route only
    - Votes for entire walk path
    """
    print(f"[ROUTE] Received request, ORS_API_KEY set: {bool(ORS_API_KEY)}, REDIS_HOST: {redis_host}")

    data = request.get_json()
    if not data:
        print("[ROUTE] Error: Missing request body")
        return jsonify({"error": "Missing request body"}), 400

    start = data.get("start")  # [lat, lon]
    end = data.get("end")      # [lat, lon]
    mode = data.get("mode", "bike")

    print(f"[ROUTE] Request: start={start}, end={end}, mode={mode}")

    if not start or not end:
        print("[ROUTE] Error: Missing coordinates")
        return jsonify({"error": "Missing start or end coordinates"}), 400

    # Validate ORS API key
    if not ORS_API_KEY or ORS_API_KEY == "your-api-key-here":
        print("[ROUTE] Error: ORS API key not configured")
        return jsonify({"error": "ORS API key not configured"}), 500

    # Get tile IDs for cache lookup
    start_tile = coords_to_tile(start[0], start[1])
    end_tile = coords_to_tile(end[0], end[1])

    # Helper to fetch route from ORS API (with caching and exponential backoff)
    def fetch_ors_route(route_mode: str, max_retries: int = 5) -> dict:
        """Fetch a route from ORS API, using cache if available. Retries with exponential backoff."""
        # Check cache first
        cached = get_cached_path(redis_client, start_tile, end_tile, route_mode)
        if cached:
            cached["_cache_hit"] = True
            return cached

        profile = ORS_PROFILES.get(route_mode, "cycling-regular")
        url = f"https://api.openrouteservice.org/v2/directions/{profile}/geojson"

        # Build request body
        request_body = {
            "coordinates": [
                [start[1], start[0]],  # ORS expects [lon, lat]
                [end[1], end[0]]
            ]
        }

        # Avoid ferries for all modes
        request_body["options"] = {"avoid_features": ["ferries"]}

        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    url,
                    json=request_body,
                    headers={
                        "Authorization": ORS_API_KEY,
                        "Content-Type": "application/json"
                    },
                    timeout=10
                )

                # Handle rate limiting (429) with exponential backoff
                if response.status_code == 429:
                    wait_time = (2 ** attempt) + (time.time() % 1)  # 1, 2, 4, 8, 16 + jitter
                    print(f"[ORS] Rate limited (429), retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue

                # Handle other errors
                if response.status_code >= 400:
                    error_msg = f"ORS API error {response.status_code}: {response.text[:200]}"
                    print(f"[ORS] {error_msg}")
                    if response.status_code >= 500:
                        # Server error - retry
                        wait_time = (2 ** attempt) + (time.time() % 1)
                        print(f"[ORS] Server error, retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        # Client error (4xx except 429) - don't retry
                        return {"error": error_msg}

                response.raise_for_status()
                result = response.json()

                features = result.get("features", [])
                if not features:
                    return {"error": "No route found"}

                route = features[0]
                geometry = route.get("geometry")
                properties = route.get("properties", {})
                summary = properties.get("summary", {})

                route_data = {
                    "geometry": geometry,
                    "duration": summary.get("duration", 0),
                    "distance": summary.get("distance", 0),
                    "mode": route_mode,
                    "_cache_hit": False
                }

                # Cache the route
                cache_path(redis_client, start_tile, end_tile, route_mode, route_data)

                # Cache MST segments for intermediate tile pairs
                waypoint_tiles = extract_waypoint_tiles(route_data)
                tile_to_coords = build_tile_to_coords_map(route_data)
                if len(waypoint_tiles) >= 2:
                    cache_mst_segments(redis_client, waypoint_tiles, route_mode, route_data, tile_to_coords)

                # Register for cache invalidation
                register_path_tiles(redis_client, start_tile, end_tile, route_mode, waypoint_tiles)

                return route_data

            except requests.Timeout as e:
                last_error = f"Timeout: {e}"
                wait_time = (2 ** attempt) + (time.time() % 1)
                print(f"[ORS] Timeout, retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
            except requests.RequestException as e:
                last_error = f"Request error: {e}"
                wait_time = (2 ** attempt) + (time.time() % 1)
                print(f"[ORS] {last_error}, retrying in {wait_time:.1f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)

        # All retries exhausted
        print(f"[ORS] All {max_retries} retries failed for {route_mode} route. Last error: {last_error}")
        return {"error": f"ORS API failed after {max_retries} retries: {last_error}"}

    try:
        # Compute desired mode route
        route_desired = fetch_ors_route(mode)
        if "error" in route_desired:
            print(f"[ROUTE] Failed to get {mode} route: {route_desired['error']}")
            return jsonify(route_desired), 404

        # For walk mode, walk route is the same as desired route
        if mode == "walk":
            route_walk = route_desired
        else:
            # For bike/drive, also compute walk route
            route_walk = fetch_ors_route("walk")
            if "error" in route_walk:
                print(f"[ROUTE] Failed to get walk route: {route_walk['error']}, returning desired route only")
                # If walk route fails, just return desired route without voting
                return jsonify({
                    "route": route_desired,
                    "desire_path": None,
                    "desire_tiles": []
                })

        # Compute and cast desire path votes
        vote_segments, vote_mode, desire_tiles = compute_desire_path_votes(
            route_desired, route_walk, mode
        )

        if vote_segments:
            vote_count = cast_desire_path_votes(redis_client, vote_segments, vote_mode)
            print(f"[VOTE] Cast {vote_count} votes as '{vote_mode}'")

        # Return both routes to frontend
        return jsonify({
            "route": route_desired,
            "desire_path": route_walk if mode != "walk" else None,
            "desire_tiles": desire_tiles
        })

    except Exception as e:
        import traceback
        print(f"[ROUTE] Unexpected error: {e}")
        print(f"[ROUTE] Traceback:\n{traceback.format_exc()}")
        return jsonify({"error": f"Routing failed: {str(e)}"}), 500


if __name__ == "__main__":
    # Run: python app.py
    app.run(host="0.0.0.0", port=5001, debug=False)

