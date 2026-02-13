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

# H3 resolutions: 6 levels (10-15), directly mapped from zoom
H3_RESOLUTIONS = [10, 11, 12, 13, 14, 15]
H3_FINEST_RESOLUTION = 15  # Store at highest res, aggregate down
H3_COARSEST_RESOLUTION = 10
H3_RESOLUTION = H3_FINEST_RESOLUTION  # Default for backwards compatibility

# Zoom level to H3 resolution mapping: changes every 2 zoom levels
# zoom 13-14 → res 10, zoom 15-16 → res 11, ..., zoom 23+ → res 15
def zoom_to_resolution(zoom: int) -> int:
    """Convert zoom level to H3 resolution (every 2 zoom levels)."""
    res = H3_COARSEST_RESOLUTION + (max(zoom, 13) - 13) // 2
    if res <= H3_COARSEST_RESOLUTION:
        return H3_COARSEST_RESOLUTION
    if res >= H3_FINEST_RESOLUTION:
        return H3_FINEST_RESOLUTION
    return res

# Legacy dict for backwards compatibility
ZOOM_TO_RESOLUTION = {z: zoom_to_resolution(z) for z in range(0, 23)}

# Sample interval in degrees (~1.5m at NYC latitude, smaller than finest hex edge)
SAMPLE_INTERVAL_DEG = 0.000015

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


def aggregate_to_resolution(hex_votes: dict, target_res: int) -> dict:
    """
    Aggregate fine-resolution hex votes to coarser resolution.

    Uses h3.cell_to_parent() to find parent hex at target resolution.
    Sums weights from all child hexes into parent.

    Args:
        hex_votes: Dict of hex_id -> weight at finest resolution
        target_res: Target H3 resolution to aggregate to

    Returns:
        Dict of hex_id -> aggregated_weight at target resolution
    """
    if not hex_votes:
        return {}

    aggregated = {}
    for hex_id, weight in hex_votes.items():
        try:
            current_res = h3.get_resolution(hex_id)
            if current_res == target_res:
                parent = hex_id
            elif current_res > target_res:
                parent = h3.cell_to_parent(hex_id, target_res)
            else:
                # Already coarser than target, skip
                continue
            aggregated[parent] = aggregated.get(parent, 0.0) + weight
        except Exception:
            # Skip invalid hex IDs
            continue
    return aggregated


def aggregate_suggestions_to_resolution(hex_suggestions: dict, target_res: int) -> dict:
    """
    Aggregate per-hex suggestion counts to a coarser resolution.

    Args:
        hex_suggestions: Dict of hex_id -> {vote_type: count} at finest resolution
        target_res: Target H3 resolution to aggregate to

    Returns:
        Dict of parent_hex_id -> {vote_type: aggregated_count}
    """
    if not hex_suggestions:
        return {}

    aggregated = {}
    for hex_id, type_counts in hex_suggestions.items():
        try:
            current_res = h3.get_resolution(hex_id)
            if current_res == target_res:
                parent = hex_id
            elif current_res > target_res:
                parent = h3.cell_to_parent(hex_id, target_res)
            else:
                continue
            if parent not in aggregated:
                aggregated[parent] = {}
            for vtype, count in type_counts.items():
                aggregated[parent][vtype] = aggregated[parent].get(vtype, 0) + count
        except Exception:
            continue
    return aggregated


def _parse_suggestion_hash(raw_data: dict) -> dict:
    """
    Parse Redis hash of "hex_id|vote_type" -> count into
    {hex_id: {vote_type: count}}.
    """
    result = {}
    for field, count in raw_data.items():
        parts = field.split("|", 1)
        if len(parts) != 2:
            continue
        hex_id, vote_type = parts
        if hex_id not in result:
            result[hex_id] = {}
        result[hex_id][vote_type] = int(count)
    return result


def _store_suggestion_hash(pipe, key: str, hex_suggestions: dict):
    """
    Store {hex_id: {vote_type: count}} into Redis hash as "hex_id|vote_type" -> count.
    """
    pipe.delete(key)
    for hex_id, type_counts in hex_suggestions.items():
        for vote_type, count in type_counts.items():
            pipe.hset(key, f"{hex_id}|{vote_type}", str(count))


def rebuild_all_resolutions(redis_client, mode: str):
    """
    Build hex caches at all resolutions from finest resolution data.

    Reads from hex_votes_weighted:{mode}:res14, aggregates to coarser
    resolutions, and stores each in hex_votes_weighted:{mode}:res{N}.

    Args:
        redis_client: Redis client
        mode: Transport mode (bike, walk, drive)
    """
    if not redis_client:
        return

    try:
        # Get finest resolution data
        finest_key = f"hex_votes_weighted:{mode}:res{H3_FINEST_RESOLUTION}"
        finest_data = redis_client.hgetall(finest_key)

        if not finest_data:
            return

        finest_hexes = {k: float(v) for k, v in finest_data.items()}

        # Get finest resolution raw counts
        raw_key = f"hex_votes_raw:{mode}:res{H3_FINEST_RESOLUTION}"
        raw_data = redis_client.hgetall(raw_key)
        finest_raw = {k: int(v) for k, v in raw_data.items()} if raw_data else {}

        # Aggregate to each coarser resolution
        pipe = redis_client.pipeline()
        for res in H3_RESOLUTIONS:
            if res == H3_FINEST_RESOLUTION:
                continue  # Already stored

            aggregated = aggregate_to_resolution(finest_hexes, res)
            key = f"hex_votes_weighted:{mode}:res{res}"
            pipe.delete(key)
            for hex_id, weight in aggregated.items():
                pipe.hset(key, hex_id, f"{weight:.6f}")

            # Aggregate raw counts to coarser resolution
            if finest_raw:
                agg_raw = aggregate_to_resolution(
                    {k: float(v) for k, v in finest_raw.items()}, res
                )
                rk = f"hex_votes_raw:{mode}:res{res}"
                pipe.delete(rk)
                for hex_id, cnt in agg_raw.items():
                    pipe.hset(rk, hex_id, str(int(cnt)))
        pipe.execute()

        # Also rebuild suggestion caches at coarser resolutions
        finest_sug_key = f"hex_suggestions:{mode}:res{H3_FINEST_RESOLUTION}"
        finest_sug_data = redis_client.hgetall(finest_sug_key)
        if finest_sug_data:
            finest_suggestions = _parse_suggestion_hash(finest_sug_data)
            pipe2 = redis_client.pipeline()
            for res in H3_RESOLUTIONS:
                if res == H3_FINEST_RESOLUTION:
                    continue
                aggregated = aggregate_suggestions_to_resolution(finest_suggestions, res)
                _store_suggestion_hash(pipe2, f"hex_suggestions:{mode}:res{res}", aggregated)
            pipe2.execute()

    except Exception as e:
        print(f"Error rebuilding resolutions for {mode}: {e}")


def update_hex_cache_incremental(redis_client, segments: list, mode: str, ip_hash: str = None, defer_rebuild: bool = False, vote_type: str = ""):
    """
    Add votes for new segments to hex cache using deterministic jitter.

    This incrementally updates the cached hex votes when new segments are voted on.
    Uses IP-weighted voting: stores per-IP hex votes for weighted aggregation.

    Args:
        redis_client: Redis client
        segments: List of [[coord1, coord2], ...] segments
        mode: Transport mode (bike, walk, drive)
        ip_hash: Hashed IP for weighted voting (optional, defaults to "system")
        defer_rebuild: If True, skip the blocking rebuild (caller handles it)
        vote_type: Natural language description of what user is voting for
    """
    if not redis_client or not segments:
        return

    # Use "system" for legacy/migration votes
    ip_hash = ip_hash or "system"

    pipe = redis_client.pipeline()

    for segment in segments:
        if len(segment) < 2:
            continue

        coord1, coord2 = segment[0], segment[1]
        seg_key = segment_key_from_coords(coord1, coord2, mode)

        # Find exact hexes that intersect this segment
        exact_hexes = list(line_to_hexes_exact(coord1, coord2))

        # For each hex, apply deterministic jitter and increment per-IP vote
        for vote_idx, center_hex in enumerate(exact_hexes):
            jittered = get_jittered_hex_deterministic(center_hex, seg_key, vote_idx)
            # Store per-IP hex votes: key is "hex_id|ip_hash"
            per_ip_key = f"{jittered}|{ip_hash}"
            pipe.hincrby(f"hex_votes_by_ip:{mode}", per_ip_key, 1)
            # Store suggestion count for this hex
            if vote_type:
                pipe.hincrby(f"hex_suggestions:{mode}:res{H3_FINEST_RESOLUTION}", f"{jittered}|{vote_type}", 1)

    pipe.execute()

    # Rebuild weighted cache after adding new votes (unless deferred)
    if not defer_rebuild:
        rebuild_weighted_hex_cache(redis_client, mode)


def rebuild_weighted_hex_cache(redis_client, mode: str = None):
    """
    Rebuild weighted hex cache from per-IP hex votes.

    Computes weighted sums where each IP contributes 1.0 total weight:
    weight = count / total_votes_by_ip

    Stores at finest resolution, then aggregates to all coarser resolutions.

    Args:
        redis_client: Redis client
        mode: Transport mode to rebuild (if None, rebuilds all modes)
    """
    if not redis_client:
        return

    modes = [mode] if mode else ["bike", "walk", "drive"]

    for m in modes:
        try:
            # Get all per-IP hex votes for this mode
            hex_votes_by_ip = redis_client.hgetall(f"hex_votes_by_ip:{m}")
            if not hex_votes_by_ip:
                continue

            # Get all IP vote counts
            ip_vote_counts = redis_client.hgetall("ip_vote_counts")

            # Compute weighted sums
            weighted_hexes = {}
            raw_counts = {}
            for hex_ip_key, count in hex_votes_by_ip.items():
                # Parse "hex_id|ip_hash"
                parts = hex_ip_key.rsplit("|", 1)
                if len(parts) != 2:
                    continue

                hex_id, ip_hash = parts
                count = int(count)

                # Track raw vote count (sum across all IPs)
                raw_counts[hex_id] = raw_counts.get(hex_id, 0) + count

                # Get total votes for this IP (default to count if not found)
                total_votes = int(ip_vote_counts.get(ip_hash, count))
                if total_votes == 0:
                    total_votes = 1

                # Weight = count / total_votes (IP contributes 1.0 total)
                weight = count / total_votes
                weighted_hexes[hex_id] = weighted_hexes.get(hex_id, 0.0) + weight

            # Store at finest resolution
            if weighted_hexes:
                pipe = redis_client.pipeline()
                finest_key = f"hex_votes_weighted:{m}:res{H3_FINEST_RESOLUTION}"
                pipe.delete(finest_key)
                for hex_id, weight in weighted_hexes.items():
                    pipe.hset(finest_key, hex_id, f"{weight:.6f}")
                # Store raw counts at finest resolution
                raw_key = f"hex_votes_raw:{m}:res{H3_FINEST_RESOLUTION}"
                pipe.delete(raw_key)
                for hex_id, cnt in raw_counts.items():
                    pipe.hset(raw_key, hex_id, str(cnt))
                # Also maintain legacy key for backwards compatibility
                pipe.delete(f"hex_votes_weighted:{m}")
                for hex_id, weight in weighted_hexes.items():
                    pipe.hset(f"hex_votes_weighted:{m}", hex_id, f"{weight:.6f}")
                pipe.execute()

                # Aggregate to all coarser resolutions
                rebuild_all_resolutions(redis_client, m)

        except Exception as e:
            print(f"Error rebuilding weighted hex cache for {m}: {e}")

    # Also rebuild the "all" mode aggregated cache
    if mode:
        rebuild_all_modes_weighted_cache(redis_client)


def rebuild_all_modes_weighted_cache(redis_client):
    """
    Rebuild the aggregated weighted hex cache across all modes.
    Builds at all resolutions.
    """
    if not redis_client:
        return

    try:
        # Build "all" mode at each resolution
        for res in H3_RESOLUTIONS:
            all_weighted = {}
            all_suggestions = {}
            all_raw = {}

            for m in ["bike", "walk", "drive"]:
                key = f"hex_votes_weighted:{m}:res{res}"
                hex_votes = redis_client.hgetall(key)
                for hex_id, weight in hex_votes.items():
                    all_weighted[hex_id] = all_weighted.get(hex_id, 0.0) + float(weight)

                # Merge raw counts across modes
                raw_key = f"hex_votes_raw:{m}:res{res}"
                raw_data = redis_client.hgetall(raw_key)
                for hex_id, cnt in raw_data.items():
                    all_raw[hex_id] = all_raw.get(hex_id, 0) + int(cnt)

                # Merge suggestions across modes
                sug_key = f"hex_suggestions:{m}:res{res}"
                sug_data = redis_client.hgetall(sug_key)
                if sug_data:
                    mode_suggestions = _parse_suggestion_hash(sug_data)
                    for hex_id, type_counts in mode_suggestions.items():
                        if hex_id not in all_suggestions:
                            all_suggestions[hex_id] = {}
                        for vtype, count in type_counts.items():
                            all_suggestions[hex_id][vtype] = all_suggestions[hex_id].get(vtype, 0) + count

            if all_weighted:
                pipe = redis_client.pipeline()
                res_key = f"hex_votes_weighted:all:res{res}"
                pipe.delete(res_key)
                for hex_id, weight in all_weighted.items():
                    pipe.hset(res_key, hex_id, f"{weight:.6f}")
                # Store merged raw counts
                raw_res_key = f"hex_votes_raw:all:res{res}"
                pipe.delete(raw_res_key)
                for hex_id, cnt in all_raw.items():
                    pipe.hset(raw_res_key, hex_id, str(cnt))
                # Store merged suggestions
                if all_suggestions:
                    _store_suggestion_hash(pipe, f"hex_suggestions:all:res{res}", all_suggestions)
                pipe.execute()

        # Also maintain legacy key for backwards compatibility (finest resolution)
        finest_key = f"hex_votes_weighted:all:res{H3_FINEST_RESOLUTION}"
        hex_votes = redis_client.hgetall(finest_key)
        if hex_votes:
            pipe = redis_client.pipeline()
            pipe.delete("hex_votes_weighted:all")
            for hex_id, weight in hex_votes.items():
                pipe.hset("hex_votes_weighted:all", hex_id, weight)
            pipe.execute()

    except Exception as e:
        print(f"Error rebuilding all-modes weighted cache: {e}")


def get_cached_hex_overlay(redis_client, mode_filter: str = None, resolution: int = None) -> dict:
    """
    Read weighted hex votes and suggestion counts from cache at specified resolution.

    Args:
        redis_client: Redis client
        mode_filter: Optional mode to filter by (bike, walk, drive)
        resolution: H3 resolution level (10-15), defaults to 13

    Returns:
        Dict with "hexes" (hex_id -> weighted_vote_float), "max_votes", "res",
        and "suggestions" (hex_id -> {vote_type: count})
    """
    if resolution is None:
        resolution = 13  # Default to res 13 for backwards compatibility

    try:
        # Try resolution-specific key first
        mode_part = mode_filter if mode_filter else "all"
        res_key = f"hex_votes_weighted:{mode_part}:res{resolution}"
        hex_votes = redis_client.hgetall(res_key)

        if hex_votes:
            hex_votes = {k: float(v) for k, v in hex_votes.items()}
            max_votes = max(hex_votes.values()) if hex_votes else 1.0
            suggestions = _read_suggestions(redis_client, mode_part, resolution)
            # Read raw vote counts
            raw_key = f"hex_votes_raw:{mode_part}:res{resolution}"
            raw_data = redis_client.hgetall(raw_key)
            raw_counts = {k: int(v) for k, v in raw_data.items()} if raw_data else {}
            return {"hexes": hex_votes, "max_votes": max_votes, "res": resolution, "suggestions": suggestions, "raw_counts": raw_counts}

        # Fall back to legacy key (non-resolution-specific)
        legacy_key = f"hex_votes_weighted:{mode_part}"
        hex_votes = redis_client.hgetall(legacy_key)

        if hex_votes:
            hex_votes = {k: float(v) for k, v in hex_votes.items()}
            max_votes = max(hex_votes.values()) if hex_votes else 1.0
            return {"hexes": hex_votes, "max_votes": max_votes, "res": resolution, "suggestions": {}}

        # Fall back to very old unweighted cache
        old_legacy_key = f"hex_votes:{mode_part}"
        hex_votes = redis_client.hgetall(old_legacy_key)
        if hex_votes:
            hex_votes = {k: float(v) for k, v in hex_votes.items()}
            max_votes = max(hex_votes.values()) if hex_votes else 1.0
            return {"hexes": hex_votes, "max_votes": max_votes, "res": resolution, "suggestions": {}}

        return {"hexes": {}, "max_votes": 1.0, "res": resolution, "suggestions": {}}
    except Exception:
        return {"hexes": {}, "max_votes": 1.0, "res": resolution, "suggestions": {}}


def _read_suggestions(redis_client, mode_part: str, resolution: int) -> dict:
    """
    Read suggestion counts for a mode and resolution.

    Returns:
        Dict of hex_id -> {vote_type: count}
    """
    sug_key = f"hex_suggestions:{mode_part}:res{resolution}"
    sug_data = redis_client.hgetall(sug_key)
    if not sug_data:
        return {}

    return _parse_suggestion_hash(sug_data)


def regenerate_hex_cache(redis_client):
    """
    Full rebuild of hex cache from segment votes.

    Migrates legacy votes to "system" user and rebuilds weighted cache.
    Useful for recovery or when changing hex resolution.

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

    # Clear existing caches (both legacy and new weighted, all resolutions)
    pipe = redis_client.pipeline()
    for mode in ["bike", "walk", "drive", "all"]:
        pipe.delete(f"hex_votes:{mode}")
        pipe.delete(f"hex_votes_weighted:{mode}")
        pipe.delete(f"hex_votes_by_ip:{mode}")
        # Clear resolution-specific keys
        for res in H3_RESOLUTIONS:
            pipe.delete(f"hex_votes_weighted:{mode}:res{res}")
            pipe.delete(f"hex_suggestions:{mode}:res{res}")
            pipe.delete(f"hex_votes_raw:{mode}:res{res}")
    pipe.execute()

    # Count total hex votes that will be assigned to "system" user
    total_system_hex_votes = 0

    # Rebuild per-IP hex votes from segment votes
    # All legacy votes are attributed to "system" user
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

        # Find exact hexes that intersect this segment
        exact_hexes = list(line_to_hexes_exact(coord1, coord2))

        pipe = redis_client.pipeline()
        for _ in range(vote_count):
            for vote_idx, center_hex in enumerate(exact_hexes):
                jittered = get_jittered_hex_deterministic(center_hex, key, vote_idx)
                # Store as "system" user votes
                per_ip_key = f"{jittered}|system"
                pipe.hincrby(f"hex_votes_by_ip:{mode}", per_ip_key, 1)
                total_system_hex_votes += 1
        pipe.execute()

    # Set total vote count for "system" user
    if total_system_hex_votes > 0:
        redis_client.hset("ip_vote_counts", "system", total_system_hex_votes)

    # Rebuild weighted caches from per-IP data
    rebuild_weighted_hex_cache(redis_client, None)
    rebuild_all_modes_weighted_cache(redis_client)


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


def cast_point_vote(redis_client, point: list, mode: str, ip_hash: str, defer_rebuild: bool = False, vote_type: str = ""):
    """
    Cast a single-point vote at a specific location.

    Converts the point to an H3 hex and stores it with IP-weighted voting.
    Point votes contribute to the same heatmap as route votes.

    Args:
        redis_client: Redis client
        point: [lat, lon] coordinates
        mode: Transport mode (bike, walk, drive)
        ip_hash: Hashed client IP for weighted voting
        defer_rebuild: If True, skip the blocking rebuild (caller handles it)
        vote_type: Natural language description of what user is voting for
    """
    if not redis_client or not point or len(point) < 2:
        return

    lat, lon = point[0], point[1]

    # Convert point to H3 hex at finest resolution
    hex_id = h3.latlng_to_cell(lat, lon, H3_FINEST_RESOLUTION)

    # Apply deterministic jitter for consistency
    jittered = get_jittered_hex_deterministic(hex_id, f"point:{lat},{lon}:{mode}", 0)

    # Store per-IP hex vote
    per_ip_key = f"{jittered}|{ip_hash}"
    redis_client.hincrby(f"hex_votes_by_ip:{mode}", per_ip_key, 1)

    # Store suggestion count for this hex
    if vote_type:
        redis_client.hincrby(f"hex_suggestions:{mode}:res{H3_FINEST_RESOLUTION}", f"{jittered}|{vote_type}", 1)

    # Update IP vote count
    redis_client.hincrby("ip_vote_counts", ip_hash, 1)

    # Rebuild weighted cache (unless deferred)
    if not defer_rebuild:
        rebuild_weighted_hex_cache(redis_client, mode)
