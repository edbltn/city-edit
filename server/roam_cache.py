"""
Redis-based path caching for Roam router.

Caches computed routes at tile granularity for fast subsequent lookups.
Supports cache invalidation when tiles change (via inverted index).

MST-style caching: When a route A→B→C→D is computed, we cache not just A→D
but also all intermediate segments: A→B, A→C, B→C, B→D, C→D, etc.
This means future queries for any sub-path will hit the cache.

Redis key schema:
- roam:path:{mode}:{from_tile}:{to_tile} -> JSON path data
- roam:tile_paths:{tile_id} -> SET of "mode:from_tile:to_tile" keys
- roam:tile:{tile_id}:hash -> hash string for change detection
- roam:stats:hits -> counter
- roam:stats:misses -> counter
"""

import json
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Redis key prefixes
PATH_PREFIX = "roam:path:"
TILE_PATHS_PREFIX = "roam:tile_paths:"
TILE_HASH_PREFIX = "roam:tile:"
STATS_PREFIX = "roam:stats:"


def cache_path(redis_client, from_tile: str, to_tile: str, mode: str, path_data: dict) -> bool:
    """
    Cache a computed path between two tiles for a specific mode.

    Args:
        redis_client: Redis client instance
        from_tile: Starting tile ID (e.g., "40.710_-74.005")
        to_tile: Ending tile ID
        mode: Transport mode ("walk", "bike", "drive", "roam")
        path_data: Route data to cache (geometry, distance, duration, segments)

    Returns:
        True if cached successfully
    """
    if redis_client is None:
        return False

    key = f"{PATH_PREFIX}{mode}:{from_tile}:{to_tile}"

    # Add cache metadata
    cache_entry = {
        **path_data,
        "_cached_at": datetime.utcnow().isoformat(),
        "_from_tile": from_tile,
        "_to_tile": to_tile,
        "_mode": mode
    }

    try:
        redis_client.set(key, json.dumps(cache_entry))
        return True
    except Exception as e:
        print(f"Cache write error: {e}")
        return False


def get_cached_path(redis_client, from_tile: str, to_tile: str, mode: str) -> Optional[dict]:
    """
    Retrieve a cached path between two tiles for a specific mode.

    Args:
        redis_client: Redis client instance
        from_tile: Starting tile ID
        to_tile: Ending tile ID
        mode: Transport mode

    Returns:
        Cached path data dict, or None if not found
    """
    if redis_client is None:
        return None

    key = f"{PATH_PREFIX}{mode}:{from_tile}:{to_tile}"

    try:
        data = redis_client.get(key)
        if data:
            _increment_stat(redis_client, "hits")
            return json.loads(data)
        else:
            _increment_stat(redis_client, "misses")
            return None
    except Exception as e:
        print(f"Cache read error: {e}")
        return None


def cache_mst_segments(redis_client, waypoint_tiles: list[str], mode: str,
                       full_path_data: dict, tile_to_coords: dict) -> int:
    """
    Cache all intermediate tile-to-tile segments from a computed path (MST-style).

    When a path passes through tiles [A, B, C, D], we cache:
    - A→B, A→C, A→D
    - B→C, B→D
    - C→D

    This means any future query for a sub-path will hit the cache.

    Args:
        redis_client: Redis client instance
        waypoint_tiles: Ordered list of tile IDs the path passes through
        mode: Transport mode
        full_path_data: The complete route data
        tile_to_coords: Dict mapping tile_id -> list of coordinates in that tile

    Returns:
        Number of segments cached
    """
    if redis_client is None or len(waypoint_tiles) < 2:
        logger.warning(f"[MST] Skipping: redis_client={redis_client is not None}, tiles={len(waypoint_tiles)}")
        return 0

    # Remove duplicates while preserving order
    unique_tiles = []
    seen = set()
    for tile in waypoint_tiles:
        if tile not in seen:
            unique_tiles.append(tile)
            seen.add(tile)

    if len(unique_tiles) < 2:
        logger.warning(f"[MST] Skipping: only {len(unique_tiles)} unique tiles after dedup")
        return 0

    logger.info(f"[MST] Caching segments for {len(unique_tiles)} unique tiles")
    cached_count = 0
    skipped_count = 0
    geometry_coords = full_path_data.get("geometry", {}).get("coordinates", [])

    try:
        pipe = redis_client.pipeline()

        # Cache all pairs (i, j) where i < j
        for i in range(len(unique_tiles)):
            for j in range(i + 1, len(unique_tiles)):
                from_tile = unique_tiles[i]
                to_tile = unique_tiles[j]

                # Extract sub-path geometry between these tiles
                sub_coords = _extract_subpath_coords(
                    geometry_coords, from_tile, to_tile, tile_to_coords
                )

                if sub_coords and len(sub_coords) >= 2:
                    # Calculate distance for this segment
                    sub_distance = _calculate_path_distance(sub_coords)

                    segment_data = {
                        "geometry": {
                            "type": "LineString",
                            "coordinates": sub_coords
                        },
                        "distance": sub_distance,
                        "duration": full_path_data.get("duration", 0) * (sub_distance / max(full_path_data.get("distance", 1), 1)),
                        "mode": mode,
                        "segments": [{
                            "mode": mode,
                            "coordinates": sub_coords,
                            "distance": sub_distance,
                            "duration": 0
                        }],
                        "_cached_at": datetime.utcnow().isoformat(),
                        "_from_tile": from_tile,
                        "_to_tile": to_tile,
                        "_mode": mode,
                        "_mst_cached": True
                    }

                    key = f"{PATH_PREFIX}{mode}:{from_tile}:{to_tile}"
                    pipe.set(key, json.dumps(segment_data))
                    cached_count += 1
                else:
                    skipped_count += 1

        pipe.execute()
        logger.info(f"[MST] Cached {cached_count} segments, skipped {skipped_count} (no coords)")
        return cached_count

    except Exception as e:
        logger.error(f"[MST] Cache error: {e}")
        return 0


def _extract_subpath_coords(all_coords: list, from_tile: str, to_tile: str,
                            tile_to_coords: dict) -> list:
    """Extract coordinates for a sub-path between two tiles."""
    from tiles import coords_to_tile

    # Find start and end indices
    start_idx = None
    end_idx = None

    for i, coord in enumerate(all_coords):
        if len(coord) >= 2:
            lon, lat = coord[0], coord[1]
            tile = coords_to_tile(lat, lon)

            if tile == from_tile and start_idx is None:
                start_idx = i
            if tile == to_tile:
                end_idx = i

    if start_idx is not None and end_idx is not None and start_idx <= end_idx:
        return all_coords[start_idx:end_idx + 1]

    return []


def _calculate_path_distance(coords: list) -> float:
    """Calculate total distance of a path in meters."""
    import math

    def haversine(lat1, lon1, lat2, lon2):
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    total = 0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i][0], coords[i][1]
        lon2, lat2 = coords[i+1][0], coords[i+1][1]
        total += haversine(lat1, lon1, lat2, lon2)

    return total


def register_path_tiles(redis_client, from_tile: str, to_tile: str,
                        mode: str, waypoint_tiles: list[str]) -> bool:
    """
    Register a path in the inverted index for all tiles it passes through.

    This allows us to invalidate the cache when any tile along the path changes.

    Args:
        redis_client: Redis client instance
        from_tile: Starting tile ID
        to_tile: Ending tile ID
        mode: Transport mode
        waypoint_tiles: List of all tile IDs the path passes through

    Returns:
        True if registered successfully
    """
    if redis_client is None:
        return False

    path_key = f"{mode}:{from_tile}:{to_tile}"

    try:
        # Add path to each tile's set
        for tile_id in waypoint_tiles:
            index_key = f"{TILE_PATHS_PREFIX}{tile_id}"
            redis_client.sadd(index_key, path_key)

        # Also register start and end tiles
        redis_client.sadd(f"{TILE_PATHS_PREFIX}{from_tile}", path_key)
        redis_client.sadd(f"{TILE_PATHS_PREFIX}{to_tile}", path_key)

        return True
    except Exception as e:
        print(f"Path registration error: {e}")
        return False


def invalidate_tile(redis_client, tile_id: str) -> int:
    """
    Invalidate all cached paths that pass through a tile.

    Args:
        redis_client: Redis client instance
        tile_id: Tile ID to invalidate

    Returns:
        Number of paths invalidated
    """
    if redis_client is None:
        return 0

    index_key = f"{TILE_PATHS_PREFIX}{tile_id}"

    try:
        # Get all paths through this tile
        path_keys = redis_client.smembers(index_key)
        count = 0

        for path_key in path_keys:
            full_key = f"{PATH_PREFIX}{path_key}"
            if redis_client.delete(full_key):
                count += 1

        # Clear the tile's path set
        redis_client.delete(index_key)

        # Clear the tile hash
        redis_client.delete(f"{TILE_HASH_PREFIX}{tile_id}:hash")

        return count
    except Exception as e:
        print(f"Invalidation error: {e}")
        return 0


def invalidate_tiles(redis_client, tile_ids: list[str]) -> int:
    """
    Invalidate multiple tiles at once.

    Args:
        redis_client: Redis client instance
        tile_ids: List of tile IDs to invalidate

    Returns:
        Total number of paths invalidated
    """
    total = 0
    for tile_id in tile_ids:
        total += invalidate_tile(redis_client, tile_id)
    return total


def set_tile_hash(redis_client, tile_id: str, hash_value: str) -> bool:
    """
    Store a tile's hash for change detection.

    Args:
        redis_client: Redis client instance
        tile_id: Tile ID
        hash_value: Computed hash of tile's nodes/edges

    Returns:
        True if stored successfully
    """
    if redis_client is None:
        return False

    key = f"{TILE_HASH_PREFIX}{tile_id}:hash"
    try:
        redis_client.set(key, hash_value)
        return True
    except Exception as e:
        print(f"Hash storage error: {e}")
        return False


def get_tile_hash(redis_client, tile_id: str) -> Optional[str]:
    """
    Get a tile's stored hash.

    Args:
        redis_client: Redis client instance
        tile_id: Tile ID

    Returns:
        Hash string, or None if not found
    """
    if redis_client is None:
        return None

    key = f"{TILE_HASH_PREFIX}{tile_id}:hash"
    try:
        return redis_client.get(key)
    except Exception as e:
        print(f"Hash retrieval error: {e}")
        return None


def get_all_tile_hashes(redis_client) -> dict[str, str]:
    """
    Get all stored tile hashes.

    Args:
        redis_client: Redis client instance

    Returns:
        Dict mapping tile_id -> hash
    """
    if redis_client is None:
        return {}

    try:
        # Scan for all tile hash keys
        pattern = f"{TILE_HASH_PREFIX}*:hash"
        hashes = {}

        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=1000)
            for key in keys:
                # Extract tile ID from key
                # Key format: "roam:tile:40.710_-74.005:hash"
                parts = key.split(":")
                if len(parts) >= 3:
                    tile_id = parts[2]
                    hash_value = redis_client.get(key)
                    if hash_value:
                        hashes[tile_id] = hash_value

            if cursor == 0:
                break

        return hashes
    except Exception as e:
        print(f"Hash scan error: {e}")
        return {}


def set_all_tile_hashes(redis_client, hashes: dict[str, str]) -> int:
    """
    Store multiple tile hashes at once.

    Args:
        redis_client: Redis client instance
        hashes: Dict mapping tile_id -> hash

    Returns:
        Number of hashes stored
    """
    if redis_client is None:
        return 0

    count = 0
    try:
        # Use pipeline for efficiency
        pipe = redis_client.pipeline()
        for tile_id, hash_value in hashes.items():
            key = f"{TILE_HASH_PREFIX}{tile_id}:hash"
            pipe.set(key, hash_value)
            count += 1
        pipe.execute()
        return count
    except Exception as e:
        print(f"Batch hash storage error: {e}")
        return 0


def get_cache_stats(redis_client) -> dict:
    """
    Get cache statistics.

    Returns:
        Dict with hits, misses, hit_rate, and cached_paths count
    """
    if redis_client is None:
        return {"error": "No Redis client"}

    try:
        hits = int(redis_client.get(f"{STATS_PREFIX}hits") or 0)
        misses = int(redis_client.get(f"{STATS_PREFIX}misses") or 0)
        total = hits + misses

        # Count cached paths
        pattern = f"{PATH_PREFIX}*"
        cached_count = 0
        cursor = 0
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=1000)
            cached_count += len(keys)
            if cursor == 0:
                break

        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": hits / total if total > 0 else 0,
            "cached_paths": cached_count
        }
    except Exception as e:
        print(f"Stats error: {e}")
        return {"error": str(e)}


def clear_cache(redis_client) -> int:
    """
    Clear all Roam cache data.

    Args:
        redis_client: Redis client instance

    Returns:
        Number of keys deleted
    """
    if redis_client is None:
        return 0

    try:
        patterns = [
            f"{PATH_PREFIX}*",
            f"{TILE_PATHS_PREFIX}*",
            f"{TILE_HASH_PREFIX}*",
            f"{STATS_PREFIX}*"
        ]

        total_deleted = 0
        for pattern in patterns:
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor, match=pattern, count=1000)
                if keys:
                    total_deleted += redis_client.delete(*keys)
                if cursor == 0:
                    break

        return total_deleted
    except Exception as e:
        print(f"Cache clear error: {e}")
        return 0


def _increment_stat(redis_client, stat_name: str):
    """Increment a stats counter."""
    if redis_client is None:
        return
    try:
        redis_client.incr(f"{STATS_PREFIX}{stat_name}")
    except Exception:
        pass  # Stats are non-critical


def extract_waypoint_tiles(path_data: dict) -> list[str]:
    """
    Extract ordered tile IDs from a path's geometry.

    Args:
        path_data: Route data with geometry

    Returns:
        Ordered list of tile IDs the path passes through (preserves order, removes consecutive duplicates)
    """
    from tiles import coords_to_tile

    tiles = []
    geometry = path_data.get("geometry", {})
    coordinates = geometry.get("coordinates", [])

    last_tile = None
    for coord in coordinates:
        if len(coord) >= 2:
            lon, lat = coord[0], coord[1]
            tile_id = coords_to_tile(lat, lon)
            # Only add if different from last (removes consecutive duplicates)
            if tile_id != last_tile:
                tiles.append(tile_id)
                last_tile = tile_id

    return tiles


def build_tile_to_coords_map(path_data: dict) -> dict:
    """
    Build a mapping from tile ID to list of coordinates in that tile.

    Args:
        path_data: Route data with geometry

    Returns:
        Dict mapping tile_id -> list of [lon, lat] coordinates
    """
    from tiles import coords_to_tile

    tile_to_coords = {}
    geometry = path_data.get("geometry", {})
    coordinates = geometry.get("coordinates", [])

    for coord in coordinates:
        if len(coord) >= 2:
            lon, lat = coord[0], coord[1]
            tile_id = coords_to_tile(lat, lon)
            if tile_id not in tile_to_coords:
                tile_to_coords[tile_id] = []
            tile_to_coords[tile_id].append(coord)

    return tile_to_coords
