"""
Desire Path Voting Algorithm
============================

Simple approach: vote for every segment along the desire path (walk route).
This shows where people actually want to go.
"""

# Redis key for segment votes (must match app.py)
SEGMENT_VOTES_KEY = "segment_votes"


def extract_all_segments(geometry: dict) -> list[list]:
    """
    Extract all segments from a route geometry.

    Args:
        geometry: Route geometry with coordinates [[lon, lat], ...]

    Returns:
        List of [[coord1, coord2], ...] segments
    """
    if not geometry:
        return []

    coords = geometry.get("coordinates", [])
    if len(coords) < 2:
        return []

    segments = []
    for i in range(len(coords) - 1):
        segments.append([coords[i], coords[i + 1]])

    return segments


def compute_desire_path_votes(route_desired: dict, route_walk: dict, desired_mode: str) -> tuple:
    """
    Vote for every segment along the desire path (walk route).

    Args:
        route_desired: Route dict with geometry for desired mode (unused now)
        route_walk: Route dict with geometry for walk mode
        desired_mode: The mode user selected (bike/drive/walk)

    Returns:
        Tuple of (vote_segments, vote_mode, desire_tiles)
    """
    walk_geometry = route_walk.get("geometry") if route_walk else None

    if not walk_geometry:
        return [], desired_mode, []

    # Vote for every segment along the walk (desire) path
    segments = extract_all_segments(walk_geometry)

    return segments, desired_mode, []


def segment_key(coord1: list, coord2: list, mode: str) -> str:
    """
    Create a canonical key for a segment (order-independent), including mode.
    """
    p1 = (round(coord1[0], 5), round(coord1[1], 5))
    p2 = (round(coord2[0], 5), round(coord2[1], 5))
    if p1 < p2:
        return f"{p1[0]},{p1[1]}|{p2[0]},{p2[1]}|{mode}"
    else:
        return f"{p2[0]},{p2[1]}|{p1[0]},{p1[1]}|{mode}"


def cast_desire_path_votes(redis_client, segments: list, mode: str, ip_hash: str = None) -> int:
    """
    Cast votes for desire path segments in Redis.

    Args:
        redis_client: Redis client
        segments: List of [[coord1, coord2], ...] from compute_desire_path_votes
        mode: Mode to tag votes with (the desired mode)
        ip_hash: Hashed IP address for weighted voting (optional, defaults to "system")

    Returns:
        Number of votes cast
    """
    if not redis_client or not segments:
        return 0

    # Use "system" for legacy/migration votes
    ip_hash = ip_hash or "system"

    try:
        pipe = redis_client.pipeline()

        for segment in segments:
            if len(segment) < 2:
                continue
            coord1, coord2 = segment[0], segment[1]
            key = segment_key(coord1, coord2, mode)
            pipe.hincrby(SEGMENT_VOTES_KEY, key, 1)

        # Track total votes cast by this IP for weighted calculation
        pipe.hincrby("ip_vote_counts", ip_hash, len(segments))

        pipe.execute()
        return len(segments)

    except Exception as e:
        print(f"Vote casting error: {e}")
        return 0
