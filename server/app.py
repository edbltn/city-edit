import json
import os
import time
import random
import math
import requests
import redis
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sock import Sock
from hybrid_router import get_router

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# Redis connection
redis_host = os.environ.get('REDIS_HOST', 'localhost')
redis_client = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
SEGMENT_VOTES_KEY = "segment_votes"

# OpenRouteService API key (get free key at https://openrouteservice.org/)
ORS_API_KEY = os.environ.get('ORS_API_KEY', '')

# OpenRouteService profile mapping
ORS_PROFILES = {
    "bike": "cycling-regular",
    "walk": "foot-walking",
    "drive": "driving-car"
}


def segment_key(coord1, coord2):
    """Create a canonical key for a segment (order-independent)."""
    # Round to 5 decimal places (~1m precision) to group nearby points
    p1 = (round(coord1[0], 5), round(coord1[1], 5))
    p2 = (round(coord2[0], 5), round(coord2[1], 5))
    # Sort to make key order-independent (A->B same as B->A)
    if p1 < p2:
        return f"{p1[0]},{p1[1]}|{p2[0]},{p2[1]}"
    else:
        return f"{p2[0]},{p2[1]}|{p1[0]},{p1[1]}"


def haversine_distance(coord1, coord2):
    """Calculate distance in meters between two [lon, lat] coordinates."""
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    R = 6371000  # Earth radius in meters

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def random_point_on_segment(coord1, coord2):
    """Generate a random point along the segment between two coordinates."""
    t = random.random()  # Random value between 0 and 1
    lon = coord1[0] + t * (coord2[0] - coord1[0])
    lat = coord1[1] + t * (coord2[1] - coord1[1])
    return f"{round(lon, 6)},{round(lat, 6)}"


# Meters per dot - controls dot density
METERS_PER_DOT = 50


def increment_segment_votes(geometry):
    """Cast votes as random points along each segment, count proportional to length."""
    if not geometry or geometry.get("type") != "LineString":
        return

    coords = geometry.get("coordinates", [])
    if len(coords) < 2:
        return

    # For each segment, cast multiple votes proportional to segment length
    pipe = redis_client.pipeline()
    for i in range(len(coords) - 1):
        coord1 = coords[i]
        coord2 = coords[i + 1]

        # Calculate segment length and number of dots
        distance = haversine_distance(coord1, coord2)
        num_dots = max(1, int(distance / METERS_PER_DOT))

        # Place random dots along segment
        for _ in range(num_dots):
            key = random_point_on_segment(coord1, coord2)
            pipe.hincrby(SEGMENT_VOTES_KEY, key, 1)

    pipe.execute()


def get_segment_overlay():
    """Build GeoJSON overlay from all voted points."""
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
        count = int(count)
        # Parse key back to coordinates
        coords = [float(x) for x in key.split(",")]

        features.append({
            "type": "Feature",
            "properties": {
                "votes": count
            },
            "geometry": {
                "type": "Point",
                "coordinates": coords
            }
        })

    return {
        "type": "geojson",
        "data": {
            "type": "FeatureCollection",
            "features": features
        }
    }


def make_state(rev: int):
    """Build map state with segment overlay."""
    segment_overlay = get_segment_overlay()

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
    while True:
        # Check for incoming messages (non-blocking)
        try:
            # Use select to check if there's data available
            msg = ws.receive(timeout=0)
            if msg:
                data = json.loads(msg)
                if data.get("type") == "cast_votes":
                    geometry = data.get("geometry")
                    if geometry:
                        increment_segment_votes(geometry)
                        print(f"Votes cast for route with {len(geometry.get('coordinates', []))} points")
        except Exception as e:
            # Timeout or no data is expected, only log actual errors
            if "timed out" not in str(e).lower():
                pass

        # Push current state
        state_msg = {"type": "map_state", "state": make_state(rev)}
        ws.send(json.dumps(state_msg))
        rev += 1
        time.sleep(1)


@app.route("/api/routes", methods=["POST"])
def calculate_route():
    """Calculate route using OpenRouteService API"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    start = data.get("start")  # [lat, lon]
    end = data.get("end")      # [lat, lon]
    mode = data.get("mode", "bike")

    if not start or not end:
        return jsonify({"error": "Missing start or end coordinates"}), 400

    if not ORS_API_KEY:
        return jsonify({"error": "ORS_API_KEY environment variable not set"}), 500

    # Map mode to ORS profile
    profile = ORS_PROFILES.get(mode, "cycling-regular")

    try:
        # Use OpenRouteService API
        url = f"https://api.openrouteservice.org/v2/directions/{profile}/geojson"
        response = requests.post(
            url,
            json={
                "coordinates": [
                    [start[1], start[0]],  # ORS expects [lon, lat]
                    [end[1], end[0]]
                ]
            },
            headers={
                "Authorization": ORS_API_KEY,
                "Content-Type": "application/json"
            },
            timeout=10
        )
        response.raise_for_status()
        result = response.json()

        features = result.get("features", [])
        if not features:
            return jsonify({"error": "No route found"}), 404

        route = features[0]
        geometry = route.get("geometry")
        properties = route.get("properties", {})
        summary = properties.get("summary", {})
        duration = summary.get("duration", 0)  # seconds
        distance = summary.get("distance", 0)  # meters

        return jsonify({
            "geometry": geometry,
            "duration": duration,
            "distance": distance,
            "mode": mode
        })

    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/routes/local", methods=["POST"])
def calculate_local_route():
    """Calculate route using local OSM-based router (no external API)"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    start = data.get("start")  # [lat, lon]
    end = data.get("end")      # [lat, lon]
    mode = data.get("mode", "bike")

    if not start or not end:
        return jsonify({"error": "Missing start or end coordinates"}), 400

    router = get_router()

    try:
        if mode == "hybrid":
            result = router.route_hybrid(start[0], start[1], end[0], end[1])
        else:
            result = router.route_single_mode(start[0], start[1], end[0], end[1], mode)

        if "error" in result:
            return jsonify(result), 404

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Run: python app.py
    app.run(host="0.0.0.0", port=5001, debug=False)

