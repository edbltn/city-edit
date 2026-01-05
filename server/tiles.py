"""
Lat-lon degree-based tile system for Roam routing.

Tiles are identified by their southwest corner coordinates, truncated to 3 decimal places.
Example: "40.710_-74.005" covers (40.710, -74.005) to (40.715, -74.000).

Design rationale:
- Human-readable tile IDs (you can tell "40.75_-73.99" is Midtown by looking at it)
- Simple math: tile = floor(coord / TILE_SIZE) * TILE_SIZE
- Easy neighbor lookup: north tile is tile_lat + TILE_SIZE
- No projection formulas needed (unlike Web Mercator z/x/y)
"""

import math
import hashlib
import json
from typing import Optional

# Tile size in degrees (~80m at NYC latitude, roughly one block)
TILE_SIZE = 0.00072

# Portal threshold in degrees (~111m lat, ~85m lon at NYC)
PORTAL_THRESHOLD = 0.001

# Manhattan bounding box (much smaller than all of NYC)
# This makes initial graph download ~30x faster
NYC_BBOX = {
    "north": 40.882,  # Inwood
    "south": 40.700,  # Battery Park
    "east": -73.907,  # East River
    "west": -74.020   # Hudson River
}


def coords_to_tile(lat: float, lon: float) -> str:
    """
    Convert coordinates to tile ID (southwest corner).

    Args:
        lat: Latitude in degrees
        lon: Longitude in degrees

    Returns:
        Tile ID string like "40.710_-74.005"

    Examples:
        >>> coords_to_tile(40.7128, -74.0060)
        '40.710_-74.010'
        >>> coords_to_tile(40.7580, -73.9855)
        '40.755_-73.990'
    """
    tile_lat = math.floor(lat / TILE_SIZE) * TILE_SIZE
    tile_lon = math.floor(lon / TILE_SIZE) * TILE_SIZE
    return f"{tile_lat:.3f}_{tile_lon:.3f}"


def tile_to_bbox(tile_id: str) -> tuple[float, float, float, float]:
    """
    Get bounding box for a tile.

    Args:
        tile_id: Tile ID string like "40.710_-74.005"

    Returns:
        Tuple of (min_lat, min_lon, max_lat, max_lon)
    """
    tile_lat, tile_lon = parse_tile_id(tile_id)
    return (
        tile_lat,
        tile_lon,
        tile_lat + TILE_SIZE,
        tile_lon + TILE_SIZE
    )


def tile_center(tile_id: str) -> tuple[float, float]:
    """
    Get center coordinates of a tile.

    Args:
        tile_id: Tile ID string

    Returns:
        Tuple of (lat, lon) at tile center
    """
    tile_lat, tile_lon = parse_tile_id(tile_id)
    return (
        tile_lat + TILE_SIZE / 2,
        tile_lon + TILE_SIZE / 2
    )


def parse_tile_id(tile_id: str) -> tuple[float, float]:
    """
    Parse tile ID into lat/lon coordinates.

    Args:
        tile_id: Tile ID string like "40.710_-74.005"

    Returns:
        Tuple of (lat, lon)
    """
    parts = tile_id.split("_")
    return float(parts[0]), float(parts[1])


def get_tile_neighbors(tile_id: str) -> list[str]:
    """
    Get all 8 neighboring tiles (N, S, E, W, NE, NW, SE, SW).

    Args:
        tile_id: Center tile ID

    Returns:
        List of 8 neighbor tile IDs
    """
    lat, lon = parse_tile_id(tile_id)
    neighbors = []
    for dlat in [-TILE_SIZE, 0, TILE_SIZE]:
        for dlon in [-TILE_SIZE, 0, TILE_SIZE]:
            if dlat == 0 and dlon == 0:
                continue
            neighbors.append(f"{lat + dlat:.3f}_{lon + dlon:.3f}")
    return neighbors


def get_all_nyc_tiles() -> list[str]:
    """
    Generate all tile IDs covering the NYC bounding box.

    Returns:
        List of tile IDs (~9,600 tiles)
    """
    tiles = []
    lat = math.floor(NYC_BBOX["south"] / TILE_SIZE) * TILE_SIZE
    while lat < NYC_BBOX["north"]:
        lon = math.floor(NYC_BBOX["west"] / TILE_SIZE) * TILE_SIZE
        while lon < NYC_BBOX["east"]:
            tiles.append(f"{lat:.3f}_{lon:.3f}")
            lon += TILE_SIZE
        lat += TILE_SIZE
    return tiles


def is_in_nyc(lat: float, lon: float) -> bool:
    """Check if coordinates are within NYC bounding box."""
    return (NYC_BBOX["south"] <= lat <= NYC_BBOX["north"] and
            NYC_BBOX["west"] <= lon <= NYC_BBOX["east"])


def are_within_portal_threshold(lat1: float, lon1: float,
                                 lat2: float, lon2: float) -> bool:
    """
    Fast degree-based proximity check for portal detection.

    Uses simple degree comparison instead of haversine for speed.
    At NYC latitude, 0.001° ≈ 111m lat, 85m lon.

    Args:
        lat1, lon1: First coordinate
        lat2, lon2: Second coordinate

    Returns:
        True if points are within PORTAL_THRESHOLD degrees
    """
    return (abs(lat1 - lat2) < PORTAL_THRESHOLD and
            abs(lon1 - lon2) < PORTAL_THRESHOLD)


def get_tile_nodes(graph, tile_id: str) -> set[int]:
    """
    Get all node IDs from a graph that fall within a tile.

    Args:
        graph: NetworkX graph with node attributes 'x' (lon) and 'y' (lat)
        tile_id: Tile ID to query

    Returns:
        Set of node IDs within the tile
    """
    min_lat, min_lon, max_lat, max_lon = tile_to_bbox(tile_id)
    nodes = set()
    for node, data in graph.nodes(data=True):
        lat, lon = data.get("y"), data.get("x")
        if lat is None or lon is None:
            continue
        if min_lat <= lat < max_lat and min_lon <= lon < max_lon:
            nodes.add(node)
    return nodes


def get_tile_edges(graph, tile_id: str, node_set: Optional[set] = None) -> list[tuple]:
    """
    Get all edges that have at least one endpoint in the tile.

    Args:
        graph: NetworkX graph
        tile_id: Tile ID to query
        node_set: Pre-computed set of nodes in tile (optional optimization)

    Returns:
        List of (u, v, length) tuples
    """
    if node_set is None:
        node_set = get_tile_nodes(graph, tile_id)

    edges = []
    for u, v, data in graph.edges(data=True):
        if u in node_set or v in node_set:
            edges.append((u, v, data.get("length", 0)))
    return edges


def compute_tile_hash(graph, tile_id: str) -> str:
    """
    Compute deterministic hash of nodes/edges in a tile.

    Used for detecting graph changes and cache invalidation.

    Args:
        graph: NetworkX graph
        tile_id: Tile ID to hash

    Returns:
        16-character hex hash string
    """
    nodes = get_tile_nodes(graph, tile_id)
    edges = get_tile_edges(graph, tile_id, nodes)

    payload = {
        "nodes": sorted(nodes),
        "edges": sorted(edges)
    }
    data = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()[:16]


def compute_all_tile_hashes(graph) -> dict[str, str]:
    """
    Compute hashes for all NYC tiles.

    Args:
        graph: NetworkX graph covering NYC

    Returns:
        Dict mapping tile_id -> hash
    """
    hashes = {}
    for tile_id in get_all_nyc_tiles():
        hashes[tile_id] = compute_tile_hash(graph, tile_id)
    return hashes


def degrees_to_meters(degrees: float, latitude: float = 40.7) -> tuple[float, float]:
    """
    Convert degrees to approximate meters at a given latitude.

    Args:
        degrees: Distance in degrees
        latitude: Reference latitude for longitude scaling

    Returns:
        Tuple of (lat_meters, lon_meters)
    """
    lat_meters = degrees * 111_000  # 111 km per degree latitude
    lon_meters = degrees * 111_000 * math.cos(math.radians(latitude))
    return lat_meters, lon_meters


# Convenience constants for reference
TILE_SIZE_METERS = degrees_to_meters(TILE_SIZE)  # (~555m, ~421m)
PORTAL_THRESHOLD_METERS = degrees_to_meters(PORTAL_THRESHOLD)  # (~111m, ~85m)
