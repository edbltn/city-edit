"""
Hex Voting Module - H3 Hexagonal Grid Voting
=============================================

Converts route geometries to H3 hexagonal cells with jitter applied at render time.
Uses resolution 13 (~6.8m edge length, ~14m diameter) for visible hexes.

Algorithm:
1. Find exact hexes that intersect each edge (source of truth)
2. For each intersecting hex, vote on a random neighbor hex selected
   with a normal distribution around that hex
3. This creates a fuzzy corridor effect from accumulated votes
"""

import math
import random
import h3

# H3 resolution 13: ~6.8m edge length, ~14m diameter
H3_RESOLUTION = 13

# Sample interval in degrees (~5m at NYC latitude, smaller than hex edge)
SAMPLE_INTERVAL_DEG = 0.000045

# Jitter radius standard deviation in hex ring units
# stddev=0.4 means moderate spread with most votes near center
JITTER_RING_STDDEV = 0.4


def line_to_hexes_exact(coord1: list, coord2: list, resolution: int = H3_RESOLUTION) -> set:
    """
    Find all H3 hexes that a line segment intersects (exact, no jitter).

    Samples points along the line at intervals smaller than hex edge length
    to ensure we capture all intersecting hexes.

    Args:
        coord1: [lon, lat] start of segment
        coord2: [lon, lat] end of segment
        resolution: H3 resolution (default 15)

    Returns:
        Set of H3 hex IDs that the line intersects
    """
    lon1, lat1 = coord1
    lon2, lat2 = coord2

    dx = lon2 - lon1
    dy = lat2 - lat1
    length = math.sqrt(dx * dx + dy * dy)

    # Sample along the line at intervals smaller than hex edge
    num_samples = max(2, int(math.ceil(length / SAMPLE_INTERVAL_DEG)) + 1)

    hexes = set()
    for i in range(num_samples):
        t = i / (num_samples - 1) if num_samples > 1 else 0
        lat = lat1 + t * dy
        lon = lon1 + t * dx
        hexes.add(h3.latlng_to_cell(lat, lon, resolution))

    return hexes


def get_jittered_hex(center_hex: str, stddev: float = JITTER_RING_STDDEV) -> str:
    """
    Get a random hex near center_hex using normal distribution.

    The distance (in ring units) from center is sampled from a half-normal
    distribution (absolute value of normal), then a random hex at that
    ring distance is selected.

    Args:
        center_hex: The H3 hex ID to jitter from
        stddev: Standard deviation in ring units (default 2)

    Returns:
        H3 hex ID of the jittered location
    """
    # Sample distance from half-normal distribution (always >= 0)
    # Using absolute value of normal gives half-normal
    ring_distance = abs(random.gauss(0, stddev))

    # Round to nearest integer ring
    k = int(round(ring_distance))

    if k == 0:
        return center_hex

    # Get the hex ring at distance k (just the ring, not filled disk)
    try:
        ring_hexes = list(h3.grid_ring(center_hex, k))
        if ring_hexes:
            return random.choice(ring_hexes)
    except Exception:
        # If ring fails (e.g., at resolution boundary), return center
        pass

    return center_hex


def get_jittered_hex_deterministic(center_hex: str, segment_key: str, vote_index: int) -> str:
    """
    Get a deterministic jittered hex using segment key as seed.

    This ensures the same jitter is applied when rebuilding the cache from
    segment votes, making the hex overlay reproducible.

    Args:
        center_hex: The H3 hex ID to jitter from
        segment_key: Canonical segment key (used as part of seed)
        vote_index: Index of this hex in the segment (used as part of seed)

    Returns:
        H3 hex ID of the jittered location
    """
    # Create deterministic seed from segment key, hex, and index
    seed_str = f"{segment_key}:{center_hex}:{vote_index}"
    seed = hash(seed_str) & 0xFFFFFFFF
    rng = random.Random(seed)

    # Sample distance from half-normal distribution
    ring_distance = abs(rng.gauss(0, JITTER_RING_STDDEV))
    k = int(round(ring_distance))

    if k == 0:
        return center_hex

    try:
        ring_hexes = list(h3.grid_ring(center_hex, k))
        if ring_hexes:
            return rng.choice(ring_hexes)
    except Exception:
        pass

    return center_hex


def segment_key_from_coords(coord1: list, coord2: list, mode: str) -> str:
    """
    Create a canonical key for a segment (order-independent), including mode.
    Matches the format in desire_path_voting.py.
    """
    p1 = (round(coord1[0], 5), round(coord1[1], 5))
    p2 = (round(coord2[0], 5), round(coord2[1], 5))
    if p1 < p2:
        return f"{p1[0]},{p1[1]}|{p2[0]},{p2[1]}|{mode}"
    else:
        return f"{p2[0]},{p2[1]}|{p1[0]},{p1[1]}|{mode}"


def update_hex_cache_incremental(redis_client, segments: list, mode: str):
    """
    Add votes for new segments to hex cache using deterministic jitter.

    This incrementally updates the cached hex votes when new segments are voted on.

    Args:
        redis_client: Redis client
        segments: List of [[coord1, coord2], ...] segments
        mode: Transport mode (bike, walk, drive)
    """
    if not redis_client or not segments:
        return

    pipe = redis_client.pipeline()

    for segment in segments:
        if len(segment) < 2:
            continue

        coord1, coord2 = segment[0], segment[1]
        seg_key = segment_key_from_coords(coord1, coord2, mode)

        # Find exact hexes that intersect this segment
        exact_hexes = list(line_to_hexes_exact(coord1, coord2))

        # For each hex, apply deterministic jitter and increment vote
        for vote_idx, center_hex in enumerate(exact_hexes):
            jittered = get_jittered_hex_deterministic(center_hex, seg_key, vote_idx)
            pipe.hincrby(f"hex_votes:{mode}", jittered, 1)
            pipe.hincrby("hex_votes:all", jittered, 1)

    pipe.execute()


def get_cached_hex_overlay(redis_client, mode_filter: str = None) -> dict:
    """
    Read hex votes from cache.

    Args:
        redis_client: Redis client
        mode_filter: Optional mode to filter by (bike, walk, drive)

    Returns:
        Dict with "hexes" (hex_id -> vote_count) and "max_votes"
    """
    try:
        key = f"hex_votes:{mode_filter}" if mode_filter else "hex_votes:all"
        hex_votes = redis_client.hgetall(key)
        hex_votes = {k: int(v) for k, v in hex_votes.items()}
        max_votes = max(hex_votes.values()) if hex_votes else 1
        return {"hexes": hex_votes, "max_votes": max_votes}
    except Exception:
        return {"hexes": {}, "max_votes": 1}


def regenerate_hex_cache(redis_client):
    """
    Full rebuild of hex cache from segment votes.

    Clears existing hex caches and rebuilds deterministically from all
    segment votes. Useful for recovery or when changing hex resolution.

    Args:
        redis_client: Redis client
    """
    if not redis_client:
        return

    # Get all segment votes
    try:
        segment_votes = redis_client.hgetall("segment_votes")
    except Exception:
        return

    if not segment_votes:
        return

    # Clear existing hex caches
    pipe = redis_client.pipeline()
    for mode in ["bike", "walk", "drive", "all"]:
        pipe.delete(f"hex_votes:{mode}")
    pipe.execute()

    # Rebuild from segment votes
    for key, count in segment_votes.items():
        parts = key.split("|")
        if len(parts) < 3:
            continue

        mode = parts[2]
        vote_count = int(count)

        try:
            coord1 = [float(x) for x in parts[0].split(",")]
            coord2 = [float(x) for x in parts[1].split(",")]
        except (ValueError, IndexError):
            continue

        # Simulate the original votes using deterministic jitter
        exact_hexes = list(line_to_hexes_exact(coord1, coord2))

        pipe = redis_client.pipeline()
        for _ in range(vote_count):
            for vote_idx, center_hex in enumerate(exact_hexes):
                jittered = get_jittered_hex_deterministic(center_hex, key, vote_idx)
                pipe.hincrby(f"hex_votes:{mode}", jittered, 1)
                pipe.hincrby("hex_votes:all", jittered, 1)
        pipe.execute()


def build_hex_overlay_from_segments(segment_votes: dict, mode_filter: str = None) -> dict:
    """
    Build hex overlay from segment votes, applying jitter at render time.

    Algorithm:
    1. For each segment, find all exact hexes it intersects
    2. For each intersecting hex × N votes, vote on a random neighbor
       selected with normal distribution
    3. Return aggregated hex vote counts

    Args:
        segment_votes: Dict of "lon1,lat1|lon2,lat2|mode" -> vote_count
        mode_filter: Optional mode to filter by (bike, walk, drive)

    Returns:
        Dict with "hexes" (hex_id -> vote_count) and "max_votes"
    """
    hex_votes = {}

    for key, count in segment_votes.items():
        # Parse key: "lon1,lat1|lon2,lat2|mode"
        parts = key.split("|")
        if len(parts) < 3:
            continue  # Skip malformed keys

        mode = parts[2]

        # Filter by mode if specified
        if mode_filter and mode != mode_filter:
            continue

        vote_count = int(count)

        # Parse coordinates
        try:
            coord1 = [float(x) for x in parts[0].split(",")]
            coord2 = [float(x) for x in parts[1].split(",")]
        except (ValueError, IndexError):
            continue  # Skip malformed coordinates

        # Step 1: Find exact hexes that intersect this segment
        exact_hexes = line_to_hexes_exact(coord1, coord2)

        # Step 2: For each vote, for each intersecting hex:
        #   Vote ONLY on a jittered position (creates natural corridor)
        #   With half-normal distribution (stddev=3), ~19% land at distance 0
        #   (exact hex), creating concentration while spreading corridor
        for _ in range(vote_count):
            for center_hex in exact_hexes:
                jittered_hex = get_jittered_hex(center_hex)
                hex_votes[jittered_hex] = hex_votes.get(jittered_hex, 0) + 1

    max_votes = max(hex_votes.values()) if hex_votes else 1

    return {"hexes": hex_votes, "max_votes": max_votes}
