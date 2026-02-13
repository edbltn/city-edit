"""
Python-based OSM routing using osmnx + rustworkx.

This module provides local routing without external API dependencies.
Uses osmnx for OSM graph building and rustworkx for fast Dijkstra routing.
"""
import json
import logging
import pickle
import time
from pathlib import Path
from typing import Optional

from router_interface import RouterInterface

logger = logging.getLogger(__name__)

# Try to import required libraries
try:
    import osmnx as ox
    import rustworkx as rx
    HAS_PYTHON_ROUTER = True
    logger.info("Python router dependencies loaded (osmnx, rustworkx)")
except ImportError as e:
    HAS_PYTHON_ROUTER = False
    ox = None
    rx = None
    logger.warning(f"Python router not available: {e}")


class PythonRouter(RouterInterface):
    """
    OSM routing using osmnx for graph building and rustworkx for fast routing.

    All modes return the same walk path (the "desire path").
    """

    def __init__(self, data_dir: str = "osm_data", redis_client=None):
        """
        Initialize the Python router.

        Args:
            data_dir: Directory containing graph pickle files
            redis_client: Redis client for cache invalidation signals
        """
        self.data_dir = Path(data_dir)
        self.redis = redis_client
        self._graph = None
        self._nx_graph = None
        self._node_index = {}
        self._index_to_node = {}
        self._version = None
        self._cache_ttl = 86400  # 24 hours

    # Grid precision for coordinate snapping (3 decimals = ~100 meters, roughly half an avenue block)
    COORD_SNAP_PRECISION = 3

    def _snap_to_grid(self, lat: float, lon: float) -> tuple[float, float]:
        """Snap coordinates to grid for cache key consistency."""
        return (
            round(lat, self.COORD_SNAP_PRECISION),
            round(lon, self.COORD_SNAP_PRECISION)
        )

    def _route_cache_key(self, start_node_id: int, end_node_id: int) -> str:
        """Generate Redis cache key for a route between two OSM nodes."""
        return f"route:walk:{start_node_id}:{end_node_id}"

    def _coord_cache_key(
        self,
        start: tuple[float, float],
        end: tuple[float, float]
    ) -> str:
        """Generate Redis cache key from snapped coordinates."""
        return f"route:coord:{start[0]}:{start[1]}:{end[0]}:{end[1]}"

    def _get_cached_route_by_coords(
        self,
        start: tuple[float, float],
        end: tuple[float, float]
    ) -> Optional[dict]:
        """Check Redis cache using snapped coordinates (fast path)."""
        if not self.redis:
            return None

        key = self._coord_cache_key(start, end)
        try:
            cached = self.redis.get(key)
            if cached:
                result = json.loads(cached)
                result["_cache_hit"] = True
                logger.info(f"[PYTHON_ROUTER] Cache hit: {key}")
                return result
        except Exception as e:
            logger.warning(f"[PYTHON_ROUTER] Cache read error: {e}")

        return None

    def _cache_route_by_coords(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        result: dict
    ):
        """Store a computed route in Redis cache using snapped coordinates."""
        if not self.redis or "error" in result:
            return

        key = self._coord_cache_key(start, end)
        cache_data = {k: v for k, v in result.items() if k != "_cache_hit"}

        try:
            self.redis.setex(key, self._cache_ttl, json.dumps(cache_data))
            logger.debug(f"[PYTHON_ROUTER] Cached route: {key}")
        except Exception as e:
            logger.warning(f"[PYTHON_ROUTER] Cache write error: {e}")

    def _get_cached_route(self, start_idx: int, end_idx: int) -> Optional[dict]:
        """Check Redis cache for a previously computed route."""
        if not self.redis:
            return None

        start_node_id = self._index_to_node[start_idx]
        end_node_id = self._index_to_node[end_idx]
        key = self._route_cache_key(start_node_id, end_node_id)

        try:
            cached = self.redis.get(key)
            if cached:
                result = json.loads(cached)
                result["_cache_hit"] = True
                logger.info(f"[PYTHON_ROUTER] Cache hit: {key}")
                return result
        except Exception as e:
            logger.warning(f"[PYTHON_ROUTER] Cache read error: {e}")

        return None

    def _cache_route(self, start_idx: int, end_idx: int, result: dict):
        """Store a computed route in Redis cache."""
        if not self.redis or "error" in result:
            return

        start_node_id = self._index_to_node[start_idx]
        end_node_id = self._index_to_node[end_idx]
        key = self._route_cache_key(start_node_id, end_node_id)

        # Don't cache the _cache_hit field
        cache_data = {k: v for k, v in result.items() if k != "_cache_hit"}

        try:
            self.redis.setex(key, self._cache_ttl, json.dumps(cache_data))
            logger.debug(f"[PYTHON_ROUTER] Cached route: {key}")
        except Exception as e:
            logger.warning(f"[PYTHON_ROUTER] Cache write error: {e}")

    def _ensure_loaded(self):
        """Lazy-load the graph on first request."""
        if self._graph is not None:
            return

        if not HAS_PYTHON_ROUTER:
            raise RuntimeError(
                "Python router dependencies not available. "
                "Install with: pip install osmnx rustworkx"
            )

        graph_path = self.data_dir / "walk_graph.pkl"
        if not graph_path.exists():
            raise RuntimeError(
                f"Graph file not found: {graph_path}. "
                "Run: python refresh_osm.py --region fidi --force"
            )

        logger.info(f"Loading Python router graph from {graph_path}")
        with open(graph_path, "rb") as f:
            data = pickle.load(f)

        self._nx_graph = data["graph"]
        self._node_index = data["node_index"]
        self._index_to_node = data["index_to_node"]
        self._version = data.get("version", "unknown")

        # Build rustworkx graph from networkx graph
        self._graph = rx.PyDiGraph()
        node_count = len(self._node_index)
        self._graph.add_nodes_from(range(node_count))

        # Add edges with weights (length in meters)
        for u, v, edge_data in self._nx_graph.edges(data=True):
            if u in self._node_index and v in self._node_index:
                weight = edge_data.get("length", 1.0)
                u_idx = self._node_index[u]
                v_idx = self._node_index[v]
                self._graph.add_edge(u_idx, v_idx, weight)

        logger.info(
            f"Python router loaded: {self._graph.num_nodes()} nodes, "
            f"{self._graph.num_edges()} edges, version: {self._version}"
        )

    def calculate_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        mode: str,
        waypoints: Optional[list[tuple[float, float]]] = None
    ) -> dict:
        """
        Calculate a route using Python routing engine.

        All modes return the same walk path (the "desire path").

        Args:
            start: Starting point as (lat, lon)
            end: Ending point as (lat, lon)
            mode: Transport mode (ignored - always returns walk path)
            waypoints: Optional intermediate points

        Returns:
            dict with geometry, distance, duration, mode, _cache_hit
        """
        self._ensure_loaded()

        logger.info(
            f"[PYTHON_ROUTER] Route request: start={start}, end={end}, "
            f"mode={mode}, waypoints={len(waypoints) if waypoints else 0}"
        )

        try:
            if not waypoints:
                result = self._route_direct(start, end)
            else:
                result = self._route_with_waypoints(start, end, waypoints)

            # Always return walk mode since all modes show desire path
            result["mode"] = "walk"

            if "error" in result:
                logger.warning(f"[PYTHON_ROUTER] Route failed: {result['error']}")
            else:
                logger.info(
                    f"[PYTHON_ROUTER] Route success: {result['distance']:.0f}m, "
                    f"{len(result['geometry']['coordinates'])} coords"
                )

            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[PYTHON_ROUTER] Unexpected error: {e}")
            return {"error": error_msg}

    def _find_nearest_node(self, lat: float, lon: float) -> int:
        """Find the nearest graph node to a point."""
        node_id = ox.distance.nearest_nodes(self._nx_graph, lon, lat)
        if node_id not in self._node_index:
            raise ValueError(f"Node {node_id} not in index")
        return self._node_index[node_id]

    def _route_direct(
        self,
        start: tuple[float, float],
        end: tuple[float, float]
    ) -> dict:
        """Route directly between two points."""
        t0 = time.perf_counter()

        # Snap to grid for cache consistency
        start_snapped = self._snap_to_grid(start[0], start[1])
        end_snapped = self._snap_to_grid(end[0], end[1])

        # Check coordinate cache FIRST (fast path - skips nearest_node lookup)
        cached = self._get_cached_route_by_coords(start_snapped, end_snapped)
        if cached:
            logger.info(f"[PYTHON_ROUTER] Cache lookup took {(time.perf_counter() - t0)*1000:.1f}ms")
            return cached

        # Cache miss - find nearest nodes (expensive)
        t1 = time.perf_counter()
        try:
            start_idx = self._find_nearest_node(start_snapped[0], start_snapped[1])
        except Exception:
            return {"error": f"Could not find start node near {start}"}

        try:
            end_idx = self._find_nearest_node(end_snapped[0], end_snapped[1])
        except Exception:
            return {"error": f"Could not find end node near {end}"}
        t2 = time.perf_counter()

        # Run Dijkstra
        try:
            path_dict = rx.dijkstra_shortest_paths(
                self._graph,
                start_idx,
                end_idx,
                weight_fn=lambda e: e
            )
        except Exception as e:
            return {"error": f"Dijkstra failed: {e}"}
        t3 = time.perf_counter()

        logger.info(f"[PYTHON_ROUTER] nearest_nodes: {(t2-t1)*1000:.0f}ms, dijkstra: {(t3-t2)*1000:.0f}ms")

        if end_idx not in path_dict:
            return {"error": f"No route found between {start} and {end}"}

        path_indices = path_dict[end_idx]

        # Convert to coordinates
        coords = []
        total_distance = 0.0

        for i, idx in enumerate(path_indices):
            node_id = self._index_to_node[idx]
            node_data = self._nx_graph.nodes[node_id]
            lat = node_data["y"]
            lon = node_data["x"]
            coords.append([lon, lat])

            # Calculate distance
            if i > 0:
                prev_idx = path_indices[i - 1]
                prev_node_id = self._index_to_node[prev_idx]
                edge_data = self._nx_graph.get_edge_data(prev_node_id, node_id)
                if edge_data:
                    total_distance += edge_data.get("length", 0)

        # Estimate duration (walking speed ~5 km/h = 1.4 m/s)
        duration = total_distance / 1.4

        result = {
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "distance": total_distance,
            "duration": duration,
            "mode": "walk",
            "_cache_hit": False
        }

        # Cache the result by coordinates
        self._cache_route_by_coords(start_snapped, end_snapped, result)

        return result

    def _route_with_waypoints(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        waypoints: list[tuple[float, float]]
    ) -> dict:
        """Route through multiple waypoints."""
        # Snap all points to grid for cache consistency
        all_points = [self._snap_to_grid(*start)]
        all_points += [self._snap_to_grid(wp[0], wp[1]) for wp in waypoints]
        all_points.append(self._snap_to_grid(*end))
        combined_coords = []
        total_distance = 0.0
        total_duration = 0.0
        all_cached = True

        for i in range(len(all_points) - 1):
            p1, p2 = all_points[i], all_points[i + 1]
            result = self._route_direct(p1, p2)

            if "error" in result:
                return result

            if not result.get("_cache_hit", False):
                all_cached = False

            # Avoid duplicating junction points
            coords = result["geometry"]["coordinates"]
            if combined_coords and coords:
                coords = coords[1:]

            combined_coords.extend(coords)
            total_distance += result["distance"]
            total_duration += result["duration"]

        return {
            "geometry": {
                "type": "LineString",
                "coordinates": combined_coords
            },
            "distance": total_distance,
            "duration": total_duration,
            "mode": "walk",
            "_cache_hit": all_cached
        }

    def get_version(self) -> str:
        """Get current graph version for cache invalidation."""
        self._ensure_loaded()
        return self._version

    def reload(self):
        """Force reload of graph (called after OSM refresh)."""
        logger.info("Reloading Python router...")
        self._graph = None
        self._nx_graph = None
        self._node_index = {}
        self._index_to_node = {}
        self._version = None
        self._ensure_loaded()

    def stats(self) -> dict:
        """Get graph statistics."""
        self._ensure_loaded()
        return {
            "nodes": self._graph.num_nodes(),
            "edges": self._graph.num_edges(),
            "version": self._version
        }


def build_graph(bbox: tuple, output_dir: str) -> dict:
    """
    Build walk graph from OSM using osmnx.

    Args:
        bbox: (south, west, north, east) bounding box
        output_dir: Directory to save the graph pickle

    Returns:
        dict with build statistics
    """
    if not HAS_PYTHON_ROUTER:
        raise RuntimeError("osmnx not available. Install with: pip install osmnx")

    import time
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    south, west, north, east = bbox
    logger.info(f"Building walk graph for bbox: {bbox}")

    start_time = time.time()

    # Download OSM walk network
    # osmnx 2.0+ uses bbox as tuple: (west, south, east, north)
    # Use "all" network type to include bridges/tunnels that connect regions
    logger.info("Downloading OSM network (all types for connectivity)...")
    G = ox.graph_from_bbox(
        bbox=(west, south, east, north),
        network_type="all",   # Include all roads for bridge connectivity
        simplify=True,
        retain_all=True,      # Keep all nodes, even if not connected to main graph
        truncate_by_edge=True  # Include edges that cross bbox boundary
    )

    # Create node index mappings
    node_index = {}
    index_to_node = {}
    for i, node_id in enumerate(G.nodes()):
        node_index[node_id] = i
        index_to_node[i] = node_id

    # Generate version timestamp
    version = time.strftime("%Y%m%d_%H%M%S")

    # Save as pickle
    graph_path = output_path / "walk_graph.pkl"
    data = {
        "graph": G,
        "node_index": node_index,
        "index_to_node": index_to_node,
        "version": version,
        "bbox": bbox
    }

    with open(graph_path, "wb") as f:
        pickle.dump(data, f)

    elapsed = time.time() - start_time

    stats = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "version": version,
        "elapsed_seconds": elapsed,
        "graph_path": str(graph_path)
    }

    logger.info(
        f"Walk graph built: {stats['nodes']} nodes, {stats['edges']} edges, "
        f"saved to {graph_path} ({elapsed:.1f}s)"
    )

    return stats


def is_available() -> bool:
    """Check if the Python router is available."""
    return HAS_PYTHON_ROUTER
