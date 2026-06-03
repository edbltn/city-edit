"""
Python-based OSM routing using osmnx + rustworkx.

This module provides local routing without external API dependencies.
Uses osmnx for OSM graph building and rustworkx for fast Dijkstra routing.
"""
import gc
import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.spatial import cKDTree

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
        self._loaded = False
        self._graph = None  # rustworkx routing graph, built lazily (fallback only)
        self._node_index = {}    # osm id -> graph index
        self._index_to_node = {}  # graph index -> osm id
        self._version = None
        self._cache_ttl = 86400  # 24 hours
        self._bbox_cache: dict[tuple, dict] = {}
        self._kdtree: cKDTree | None = None
        # Compact graph representation (replaces the heavy networkx graph after
        # load). Coords indexed by graph index; edges as parallel arrays; names
        # interned to dedupe; adjacency as edge-positions per node.
        self._coords: np.ndarray | None = None      # [n, 2] -> (lat, lon)
        self._edge_u: np.ndarray | None = None       # [e] int32 from-index
        self._edge_v: np.ndarray | None = None       # [e] int32 to-index
        self._edge_len: np.ndarray | None = None      # [e] float32 length (m)
        self._edge_name: list[str] = []               # [e] interned street names
        self._edge_highway: list[str] = []            # [e] interned highway tags
        self._edge_adj: list[list[int]] = []          # node index -> [edge positions]

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

    def _precache_subpaths(
        self,
        start_snapped: tuple[float, float],
        end_snapped: tuple[float, float],
        coords: list,
        cumulative_distances: list[float],
        total_distance: float
    ):
        """Pre-cache sub-paths so split-path requests hit cache.

        After computing A→B, caches start→C and C→end for each intermediate
        vertex C that falls on a distinct snap-grid cell. Any sub-path of a
        shortest path is itself a shortest path, so slicing the geometry is
        correct.
        """
        if not self.redis or len(coords) < 3:
            return

        seen = {start_snapped, end_snapped}
        entries = []  # (snapped_coord, geometry_index)

        for i, coord in enumerate(coords):
            snapped = self._snap_to_grid(coord[1], coord[0])  # GeoJSON [lon, lat]
            if snapped not in seen:
                seen.add(snapped)
                entries.append((snapped, i))

        if not entries:
            return

        try:
            pipe = self.redis.pipeline(transaction=False)
            for snapped, idx in entries:
                # start → this vertex
                key_from_start = self._coord_cache_key(start_snapped, snapped)
                dist = cumulative_distances[idx]
                sub_start = {
                    "geometry": {"type": "LineString", "coordinates": coords[:idx + 1]},
                    "distance": dist,
                    "duration": dist / 1.4,
                    "mode": "walk",
                }
                pipe.setex(key_from_start, self._cache_ttl, json.dumps(sub_start))

                # this vertex → end
                key_to_end = self._coord_cache_key(snapped, end_snapped)
                remaining = total_distance - dist
                sub_end = {
                    "geometry": {"type": "LineString", "coordinates": coords[idx:]},
                    "distance": remaining,
                    "duration": remaining / 1.4,
                    "mode": "walk",
                }
                pipe.setex(key_to_end, self._cache_ttl, json.dumps(sub_end))

            pipe.execute()
            logger.info(
                f"[PYTHON_ROUTER] Pre-cached {len(entries)} sub-path pairs "
                f"({len(entries) * 2} keys)"
            )
        except Exception as e:
            logger.warning(f"[PYTHON_ROUTER] Sub-path pre-cache error: {e}")

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

    @staticmethod
    def _first_str(val) -> str:
        """OSM tags can be a string or a list of strings (multi-valued); take one."""
        if isinstance(val, list):
            return val[0] if val else ""
        return val or ""

    def _ensure_loaded(self):
        """Load the OSM graph into compact arrays + spatial index, then free networkx.

        The pickled networkx graph is the single biggest structure (a Python dict
        per node and per edge → several GB for NYC), but everything we actually
        need from it — node coords, edge endpoints/lengths/names, and adjacency —
        fits in compact numpy arrays + interned-string lists an order of magnitude
        smaller. We extract those, build the kdtree from them, drop networkx, and
        let gc reclaim it. The rustworkx routing graph is built separately and
        lazily (see _ensure_routing_graph) since OSRM handles routing and the
        fallback rarely runs.
        """
        if self._loaded:
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

        nx_graph = data["graph"]
        self._node_index = data["node_index"]
        self._index_to_node = data["index_to_node"]
        self._version = data.get("version", "unknown")
        data = None

        n = len(self._node_index)

        # Node coords indexed by graph index (osmnx stores y=lat, x=lon). The
        # kdtree is built on this array, so a query returns the graph index directly.
        coords = np.zeros((n, 2), dtype=np.float64)
        for osmid, idx in self._node_index.items():
            nd = nx_graph.nodes[osmid]
            coords[idx, 0] = nd["y"]
            coords[idx, 1] = nd["x"]

        # Compact edge arrays + per-node adjacency (edge positions touching a node).
        edge_u: list[int] = []
        edge_v: list[int] = []
        edge_len: list[float] = []
        edge_name: list[str] = []
        edge_highway: list[str] = []
        adj: list[list[int]] = [[] for _ in range(n)]

        for u, v, ed in nx_graph.edges(data=True):
            ui = self._node_index.get(u)
            vi = self._node_index.get(v)
            if ui is None or vi is None:
                continue
            pos = len(edge_u)
            edge_u.append(ui)
            edge_v.append(vi)
            edge_len.append(float(ed.get("length", 1.0)))
            # Intern names/highways: street names repeat heavily across edges, so
            # interning collapses millions of references onto a small unique set.
            edge_name.append(sys.intern(str(self._first_str(ed.get("name", "")))))
            edge_highway.append(sys.intern(str(self._first_str(ed.get("highway", "")))))
            adj[ui].append(pos)
            adj[vi].append(pos)

        self._coords = coords
        self._edge_u = np.array(edge_u, dtype=np.int32)
        self._edge_v = np.array(edge_v, dtype=np.int32)
        self._edge_len = np.array(edge_len, dtype=np.float32)
        self._edge_name = edge_name
        self._edge_highway = edge_highway
        self._edge_adj = adj
        self._kdtree = cKDTree(coords)

        # Free the heavy networkx graph — its data now lives in the compact arrays.
        del nx_graph
        gc.collect()
        self._loaded = True

        logger.info(
            f"Python router loaded: {n} nodes, {len(edge_u)} edges, "
            f"version: {self._version} (networkx freed; routing graph deferred)"
        )

    def _ensure_routing_graph(self):
        """Build the rustworkx routing graph — lazily, on the first fallback route.

        Skipped entirely when OSRM serves every route (the common case), which is
        why it's split out of _ensure_loaded. Built from the compact edge arrays
        (networkx is already gone by now).
        """
        self._ensure_loaded()
        if self._graph is not None:
            return

        logger.info("[PYTHON_ROUTER] Building rustworkx routing graph (OSRM fallback path)")
        self._graph = rx.PyDiGraph()
        self._graph.add_nodes_from(range(len(self._node_index)))
        # (from, to, weight=length_m) for every edge.
        self._graph.add_edges_from([
            (int(self._edge_u[i]), int(self._edge_v[i]), float(self._edge_len[i]))
            for i in range(len(self._edge_u))
        ])

        logger.info(
            f"[PYTHON_ROUTER] Routing graph built: {self._graph.num_nodes()} nodes, "
            f"{self._graph.num_edges()} edges"
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
        self._ensure_routing_graph()

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
        """Find the nearest graph node to a point using pre-built KDTree.

        The kdtree is built on the graph-index-ordered coord array, so its result
        IS the graph index — no osm-id indirection needed.
        """
        _, idx = self._kdtree.query([lat, lon])
        return int(idx)

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
        cumulative_distances = [0.0]
        total_distance = 0.0

        for i, idx in enumerate(path_indices):
            lat = float(self._coords[idx, 0])
            lon = float(self._coords[idx, 1])
            coords.append([lon, lat])

            # Calculate distance — the rustworkx edge weight IS the length (m).
            if i > 0:
                prev_idx = path_indices[i - 1]
                try:
                    total_distance += float(self._graph.get_edge_data(prev_idx, idx))
                except Exception:
                    pass
                cumulative_distances.append(total_distance)

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

        # Pre-cache sub-paths for split-path cache hits
        self._precache_subpaths(
            start_snapped, end_snapped,
            coords, cumulative_distances, total_distance
        )

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

    def get_graph_for_bbox(self, south: float, west: float, north: float, east: float) -> dict:
        """Return nodes and edges of the walk graph within a lat/lon bounding box.

        Results are cached by bbox rounded to 3 decimal places (~111m).
        """
        self._ensure_loaded()

        # Check cache (rounded to 3dp for ~111m granularity)
        cache_key = (round(south, 3), round(west, 3), round(north, 3), round(east, 3))
        if cache_key in self._bbox_cache:
            return self._bbox_cache[cache_key]

        # Nodes within bbox — vectorized mask over the coord array (coords[:,0]=lat).
        lat = self._coords[:, 0]
        lon = self._coords[:, 1]
        node_mask = (lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)
        included = np.nonzero(node_mask)[0]  # graph indices inside the bbox

        # graph index → position in node_list
        gidx_to_pos = {int(g): pos for pos, g in enumerate(included)}
        node_list = [[float(lat[g]), float(lon[g])] for g in included]  # [[lat, lon], ...]

        # Edges with both endpoints inside the bbox — filter via the node mask,
        # then build only the surviving rows (no full-graph Python scan).
        edge_mask = node_mask[self._edge_u] & node_mask[self._edge_v]
        edge_positions = np.nonzero(edge_mask)[0]

        edge_list = []  # [[from_idx, to_idx, name, highway, length_m], ...]
        for ep in edge_positions:
            ui = int(self._edge_u[ep])
            vi = int(self._edge_v[ep])
            edge_list.append([
                gidx_to_pos[ui], gidx_to_pos[vi],
                self._edge_name[ep], self._edge_highway[ep],
                round(float(self._edge_len[ep]), 1),
            ])

        # OSM node ID → position in node_list (for OSRM annotation-based edge mapping)
        osm_to_graph_idx = {
            int(self._index_to_node[int(g)]): pos for pos, g in enumerate(included)
        }

        # (graph_node_a, graph_node_b) → edge index (both directions for undirected)
        node_pair_to_edge: dict[tuple[int, int], int] = {}
        for i, edge in enumerate(edge_list):
            node_pair_to_edge[(edge[0], edge[1])] = i
            node_pair_to_edge[(edge[1], edge[0])] = i

        result = {
            "nodes": node_list,
            "edges": edge_list,
            "osm_to_graph_idx": osm_to_graph_idx,
            "node_pair_to_edge": node_pair_to_edge,
        }

        # Cache (limit to 64 entries to bound memory)
        if len(self._bbox_cache) >= 64:
            # Evict oldest entry
            oldest = next(iter(self._bbox_cache))
            del self._bbox_cache[oldest]
        self._bbox_cache[cache_key] = result

        return result

    def nearest_node_coords(self, lat: float, lon: float) -> tuple[float, float]:
        """Return (lat, lon) of the nearest graph node to the given point."""
        self._ensure_loaded()
        _, idx = self._kdtree.query([lat, lon])
        return float(self._coords[idx, 0]), float(self._coords[idx, 1])

    def reverse_geocode(self, lat: float, lon: float) -> str:
        """Return a street-level address for a point using the local OSM graph.

        Finds the nearest node, then BFS outward (up to 5 hops) to collect
        street names from nearby edges.  Walk graphs have many unnamed
        footways/crosswalks, so the immediate node often has none.

        Two distinct street names → "Broadway & 7th Ave" (intersection).
        One street name → that name.  No names → empty string.
        """
        self._ensure_loaded()
        _, idx = self._kdtree.query([lat, lon])
        start_node = int(idx)

        names: list[str] = []
        seen_names: set[str] = set()

        # BFS up to 5 hops from nearest node (pedestrian plazas can be wide),
        # walking the compact adjacency (node index → incident edge positions).
        visited: set = {start_node}
        frontier = [start_node]
        for _ in range(5):
            if len(names) >= 2:
                break
            next_frontier = []
            for node in frontier:
                for ep in self._edge_adj[node]:
                    name = self._edge_name[ep]
                    if name and name not in seen_names:
                        seen_names.add(name)
                        names.append(name)
                    u = int(self._edge_u[ep])
                    neighbor = int(self._edge_v[ep]) if u == node else u
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier

        if len(names) >= 2:
            return f"{names[0]} & {names[1]}"
        if len(names) == 1:
            return names[0]
        return ""

    def get_version(self) -> str:
        """Get current graph version for cache invalidation."""
        self._ensure_loaded()
        return self._version

    def reload(self):
        """Force reload of graph (called after OSM refresh)."""
        logger.info("Reloading Python router...")
        self._loaded = False
        self._graph = None
        self._node_index = {}
        self._index_to_node = {}
        self._version = None
        self._bbox_cache = {}
        self._kdtree = None
        self._coords = None
        self._edge_u = None
        self._edge_v = None
        self._edge_len = None
        self._edge_name = []
        self._edge_highway = []
        self._edge_adj = []
        gc.collect()
        self._ensure_loaded()

    def stats(self) -> dict:
        """Get graph statistics."""
        self._ensure_loaded()
        return {
            "nodes": len(self._node_index),
            "edges": int(len(self._edge_u)) if self._edge_u is not None else 0,
            "version": self._version
        }


def build_graph(bbox: tuple, output_dir: str, pbf_url: str | None = None) -> dict:
    """
    Build the walk graph from the same OSM PBF that feeds OSRM.

    Reads the city's `pbf_url` extract and keeps every way OSRM's foot profile would
    route (see osm_graph_builder / foot_profile), so the topology graph is a superset
    of OSRM's foot network and route votes map cleanly by OSM node id. Replaces the
    old live-Overpass osmnx download, which used a different snapshot and filter.

    Args:
        bbox: (south, west, north, east) bounding box
        output_dir: Directory to save the graph pickle (and cache the source PBF)
        pbf_url: Source OSM extract URL (same one OSRM uses; from cities.py)

    Returns:
        dict with build statistics
    """
    # build_graph is the only path that needs osmium; loading/routing does not.
    from osm_graph_builder import HAS_OSMIUM, build_walk_graph_from_pbf, ensure_pbf

    if not HAS_OSMIUM:
        raise RuntimeError("pyosmium not available. Install with: uv pip install osmium")
    if not pbf_url:
        raise RuntimeError("build_graph requires pbf_url (the source extract OSRM uses)")

    import time
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(f"Building walk graph for bbox: {bbox} from {pbf_url}")
    start_time = time.time()

    pbf_path = ensure_pbf(pbf_url, str(output_path))
    G = build_walk_graph_from_pbf(pbf_path, bbox)

    # Create node index mappings
    node_index = {}
    index_to_node = {}
    for i, node_id in enumerate(G.nodes()):
        node_index[node_id] = i
        index_to_node[i] = node_id

    # Generate version timestamp
    version = time.strftime("%Y%m%d_%H%M%S")

    # Save as pickle (atomic: write a temp then rename, so a killed build never
    # leaves a truncated walk_graph.pkl).
    graph_path = output_path / "walk_graph.pkl"
    tmp_path = output_path / "walk_graph.pkl.tmp"
    data = {
        "graph": G,
        "node_index": node_index,
        "index_to_node": index_to_node,
        "version": version,
        "bbox": bbox
    }

    with open(tmp_path, "wb") as f:
        pickle.dump(data, f)
    os.replace(tmp_path, graph_path)

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
