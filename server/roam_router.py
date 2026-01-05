"""
Roam: Multi-modal routing engine with unified super-graph.

"Freedom to Roam" - treats all edges as bidirectional and allows
seamless mode switching between walk, bike, and drive networks.

Key differences from hybrid_router.py:
- All edges are bidirectional (ignores one-way restrictions)
- Zero mode-switch penalty (vs 120s in hybrid router)
- Uses degree-based portal detection (fast, no haversine)
- Integrates with tile-based caching system
"""

import os
import pickle
import heapq
import math
import time
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

import networkx as nx


def _log(msg: str):
    """Log with timestamp for performance debugging."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# Maximum cache age before automatic refresh (30 days)
MAX_CACHE_AGE_DAYS = 30

from tiles import (
    TILE_SIZE, PORTAL_THRESHOLD, NYC_BBOX,
    coords_to_tile, are_within_portal_threshold
)

# Try to import osmnx (optional for testing without OSM data)
try:
    import osmnx as ox
    HAS_OSMNX = True
except ImportError:
    HAS_OSMNX = False
    print("Warning: osmnx not available. Graph loading disabled.")

# Cache directory for graph data
CACHE_DIR = Path(__file__).parent / "graph_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Mode configurations
MODE_CONFIG = {
    "walk": {
        "network_type": "walk",
        "speed_kmh": 5.0,
        "cache_file": "roam_walk.gpickle"
    },
    "bike": {
        "network_type": "bike",
        "speed_kmh": 18.0,
        "cache_file": "roam_bike.gpickle"
    },
    "drive": {
        "network_type": "drive",
        "speed_kmh": 35.0,
        "cache_file": "roam_drive.gpickle"
    }
}

# Unified graph cache file
UNIFIED_CACHE_FILE = "roam_unified.gpickle"
PORTALS_DEBUG_FILE = "roam_portals.json"


class RoamRouter:
    """
    Multi-modal router with bidirectional edges and seamless mode switching.

    The router builds a "super-graph" that combines walk, bike, and drive
    networks with portal edges connecting nodes that are close together
    across different mode networks.
    """

    def __init__(self, redis_client=None):
        """
        Initialize the Roam router.

        Args:
            redis_client: Optional Redis client for caching (can be None)
        """
        self.redis = redis_client
        self.graphs = {}  # mode -> bidirectional NetworkX graph (for Roam)
        self.graphs_directional = {}  # mode -> original directional graph (for single-mode)
        self.super_graph = None  # Unified graph with portals
        self.node_coords = {}  # (mode, node_id) -> (lat, lon)
        self.portals = []  # List of portal edges for debugging

    def load_or_build(self, force_rebuild: bool = False) -> nx.DiGraph:
        """
        Load cached super-graph or build from OSM data.

        Automatically rebuilds if cache is older than MAX_CACHE_AGE_DAYS.

        Args:
            force_rebuild: If True, rebuild even if cache exists

        Returns:
            The unified super-graph
        """
        cache_path = CACHE_DIR / UNIFIED_CACHE_FILE

        # Check if cache exists and is fresh enough
        if cache_path.exists() and not force_rebuild:
            cache_age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            if cache_age > timedelta(days=MAX_CACHE_AGE_DAYS):
                _log(f"Cache is {cache_age.days} days old (>{MAX_CACHE_AGE_DAYS}), rebuilding...")
                force_rebuild = True
            else:
                _log(f"Loading cached Roam super-graph ({cache_age.days} days old)...")
                with open(cache_path, "rb") as f:
                    data = pickle.load(f)
                    self.super_graph = data["graph"]
                    self.node_coords = data["node_coords"]
                    self.portals = data.get("portals", [])
                    # Also load directional graphs if available
                    if "graphs_directional" in data:
                        self.graphs_directional = data["graphs_directional"]
                _log(f"Loaded super-graph: {self.super_graph.number_of_nodes()} nodes, "
                      f"{self.super_graph.number_of_edges()} edges, "
                      f"{len(self.portals)} portals")
                return self.super_graph

        # Build from scratch
        _log("Building Roam super-graph (this may take several minutes)...")
        self._load_mode_graphs()
        self._build_super_graph()
        self._save_cache()

        return self.super_graph

    def _load_mode_graphs(self):
        """Load or download graphs for all modes."""
        if not HAS_OSMNX:
            raise RuntimeError("osmnx is required to load OSM graphs")

        for mode, config in MODE_CONFIG.items():
            cache_path = CACHE_DIR / config["cache_file"]

            if cache_path.exists():
                _log(f"Loading cached {mode} graph from {cache_path}...")
                t0 = time.time()
                with open(cache_path, "rb") as f:
                    G = pickle.load(f)
                _log(f"Loaded {mode} graph in {time.time() - t0:.1f}s")
            else:
                _log(f"No cached {mode} graph found at {cache_path}")
                _log(f"Downloading {mode} network from OSM (this may take a while)...")
                t0 = time.time()
                G = ox.graph_from_bbox(
                    bbox=(NYC_BBOX["north"], NYC_BBOX["south"],
                          NYC_BBOX["east"], NYC_BBOX["west"]),
                    network_type=config["network_type"],
                    simplify=True
                )
                # Add travel time based on mode speed
                G = ox.add_edge_speeds(G)
                G = ox.add_edge_travel_times(G)

                _log(f"Downloaded in {time.time() - t0:.1f}s, caching...")
                with open(cache_path, "wb") as f:
                    pickle.dump(G, f)
                _log(f"Cached {mode} graph to {cache_path}")

            # Store original directional graph for single-mode routing
            self.graphs_directional[mode] = G
            print(f"Loaded {mode} (directional): {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

            # Make a copy and make it bidirectional for Roam routing
            G_bidir = G.copy()
            G_bidir = self._make_bidirectional(G_bidir, config["speed_kmh"])
            self.graphs[mode] = G_bidir
            print(f"Loaded {mode} (bidirectional): {G_bidir.number_of_nodes()} nodes, {G_bidir.number_of_edges()} edges")

    def _make_bidirectional(self, G: nx.MultiDiGraph, speed_kmh: float) -> nx.MultiDiGraph:
        """
        Make all edges bidirectional by adding reverse edges.

        Args:
            G: Original graph (may have one-way edges)
            speed_kmh: Speed for calculating travel time on new edges

        Returns:
            Graph with all edges bidirectional
        """
        edges_to_add = []
        speed_ms = speed_kmh * 1000 / 3600  # Convert to m/s

        for u, v, key, data in G.edges(keys=True, data=True):
            # Check if reverse edge exists
            if not G.has_edge(v, u):
                # Create reverse edge with same attributes
                reverse_data = data.copy()
                reverse_data["reversed"] = True  # Mark as synthetic

                # Recalculate travel time if length is available
                if "length" in data:
                    reverse_data["travel_time"] = data["length"] / speed_ms

                edges_to_add.append((v, u, reverse_data))

        # Add all reverse edges
        for v, u, data in edges_to_add:
            G.add_edge(v, u, **data)

        return G

    def _build_super_graph(self):
        """
        Build unified super-graph with portal edges between modes.

        The super-graph uses (mode, node_id) tuples as node identifiers,
        allowing the same physical location to exist in multiple mode layers.
        """
        self.super_graph = nx.DiGraph()
        self.node_coords = {}
        self.portals = []

        # Add all nodes and edges from each mode graph
        for mode, G in self.graphs.items():
            speed_ms = MODE_CONFIG[mode]["speed_kmh"] * 1000 / 3600

            # Add nodes
            for node, data in G.nodes(data=True):
                super_node = (mode, node)
                lat, lon = data.get("y"), data.get("x")
                self.super_graph.add_node(super_node, lat=lat, lon=lon, mode=mode)
                self.node_coords[super_node] = (lat, lon)

            # Add edges
            for u, v, data in G.edges(data=True):
                super_u = (mode, u)
                super_v = (mode, v)
                travel_time = data.get("travel_time", data.get("length", 100) / speed_ms)
                self.super_graph.add_edge(
                    super_u, super_v,
                    weight=travel_time,
                    length=data.get("length", 0),
                    mode=mode,
                    edge_type="road"
                )

        print(f"Super-graph before portals: {self.super_graph.number_of_nodes()} nodes, "
              f"{self.super_graph.number_of_edges()} edges")

        # Build portal edges
        self._build_portal_edges()

        print(f"Super-graph after portals: {self.super_graph.number_of_nodes()} nodes, "
              f"{self.super_graph.number_of_edges()} edges, "
              f"{len(self.portals)} portal edges")

    def _build_portal_edges(self):
        """
        Create portal edges between close nodes in different mode networks.

        Uses degree-based proximity check for speed (no haversine).
        Portal edges allow mode switching with zero penalty.
        """
        modes = list(self.graphs.keys())

        # Build spatial index for each mode
        # Using simple list-based approach (can optimize with R-tree if needed)
        mode_nodes = {}
        for mode, G in self.graphs.items():
            mode_nodes[mode] = [
                (node, G.nodes[node].get("y"), G.nodes[node].get("x"))
                for node in G.nodes()
                if G.nodes[node].get("y") is not None
            ]

        portal_count = 0
        walk_speed_ms = MODE_CONFIG["walk"]["speed_kmh"] * 1000 / 3600

        # For each pair of modes, find nearby nodes
        for i, mode1 in enumerate(modes):
            for mode2 in modes[i + 1:]:
                print(f"Building portals: {mode1} <-> {mode2}...")

                for node1, lat1, lon1 in mode_nodes[mode1]:
                    for node2, lat2, lon2 in mode_nodes[mode2]:
                        if are_within_portal_threshold(lat1, lon1, lat2, lon2):
                            # Calculate actual distance for travel time
                            dist = self._haversine(lat1, lon1, lat2, lon2)
                            travel_time = dist / walk_speed_ms  # Walk between portals

                            super_node1 = (mode1, node1)
                            super_node2 = (mode2, node2)

                            # Add bidirectional portal edges
                            self.super_graph.add_edge(
                                super_node1, super_node2,
                                weight=travel_time,
                                length=dist,
                                mode="portal",
                                edge_type="portal",
                                from_mode=mode1,
                                to_mode=mode2
                            )
                            self.super_graph.add_edge(
                                super_node2, super_node1,
                                weight=travel_time,
                                length=dist,
                                mode="portal",
                                edge_type="portal",
                                from_mode=mode2,
                                to_mode=mode1
                            )

                            self.portals.append({
                                "from": super_node1,
                                "to": super_node2,
                                "distance": dist
                            })
                            portal_count += 1

        print(f"Created {portal_count} portal connections")

    def _haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance in meters between two coordinates."""
        R = 6371000  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = (math.sin(delta_phi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def _save_cache(self):
        """Save super-graph and metadata to cache."""
        cache_path = CACHE_DIR / UNIFIED_CACHE_FILE
        _log(f"Saving super-graph to {cache_path}...")

        data = {
            "graph": self.super_graph,
            "node_coords": self.node_coords,
            "portals": self.portals,
            "graphs_directional": self.graphs_directional  # For single-mode routing
        }
        with open(cache_path, "wb") as f:
            pickle.dump(data, f)

        # Also save portals as JSON for debugging
        import json
        portals_path = CACHE_DIR / PORTALS_DEBUG_FILE
        portals_json = [
            {
                "from_mode": p["from"][0],
                "from_node": p["from"][1],
                "to_mode": p["to"][0],
                "to_node": p["to"][1],
                "distance": p["distance"]
            }
            for p in self.portals[:1000]  # Limit for file size
        ]
        with open(portals_path, "w") as f:
            json.dump(portals_json, f, indent=2)

        print("Cache saved successfully")

    def route(self, start_lat: float, start_lon: float,
              end_lat: float, end_lon: float) -> dict:
        """
        Find optimal multi-modal route between two points.

        Args:
            start_lat, start_lon: Starting coordinates
            end_lat, end_lon: Ending coordinates

        Returns:
            Route dict with geometry, distance, duration, and mode segments
        """
        t0 = time.time()
        _log(f"route (roam): from ({start_lat:.5f}, {start_lon:.5f}) to ({end_lat:.5f}, {end_lon:.5f})")

        if self.super_graph is None:
            _log("Loading super-graph (first request)...")
            self.load_or_build()
            _log(f"Super-graph loaded in {time.time() - t0:.2f}s")

        # Find nearest nodes in each mode
        t1 = time.time()
        _log(f"Finding start nodes in {len(self.node_coords)} total nodes...")
        start_nodes = self._find_nearest_nodes(start_lat, start_lon)
        _log(f"Found {len(start_nodes)} start nodes in {time.time() - t1:.2f}s")

        t2 = time.time()
        end_nodes = self._find_nearest_nodes(end_lat, end_lon)
        _log(f"Found {len(end_nodes)} end nodes in {time.time() - t2:.2f}s")

        if not start_nodes or not end_nodes:
            return {"error": "Could not find nearby nodes"}

        # Run A* search
        t3 = time.time()
        _log(f"Running A* search...")
        path = self._astar_search(start_nodes, end_nodes, end_lat, end_lon)
        _log(f"A* complete in {time.time() - t3:.2f}s")

        if path is None:
            return {"error": "No route found"}

        result = self._build_route_result(path, start_lat, start_lon, end_lat, end_lon)
        _log(f"Total roam route time: {time.time() - t0:.2f}s")
        return result

    def _find_nearest_nodes(self, lat: float, lon: float) -> list[tuple]:
        """
        Find nearest super-graph nodes to a coordinate.

        Returns nodes from all modes that are within reasonable distance.
        """
        candidates = []
        max_dist = 500  # meters

        for super_node, (node_lat, node_lon) in self.node_coords.items():
            if node_lat is None or node_lon is None:
                continue
            dist = self._haversine(lat, lon, node_lat, node_lon)
            if dist < max_dist:
                candidates.append((dist, super_node))

        # Sort by distance and return closest from each mode
        candidates.sort()
        seen_modes = set()
        result = []
        for dist, super_node in candidates:
            mode = super_node[0]
            if mode not in seen_modes:
                result.append(super_node)
                seen_modes.add(mode)
            if len(result) >= 3:  # One per mode
                break

        return result

    def _astar_search(self, start_nodes: list[tuple], end_nodes: list[tuple],
                      end_lat: float, end_lon: float) -> Optional[list[tuple]]:
        """
        A* search on super-graph.

        Args:
            start_nodes: List of possible starting super-nodes
            end_nodes: List of possible ending super-nodes
            end_lat, end_lon: End coordinates for heuristic

        Returns:
            List of super-nodes representing the path, or None
        """
        end_set = set(end_nodes)

        # Heuristic: straight-line distance at bike speed
        bike_speed = MODE_CONFIG["bike"]["speed_kmh"] * 1000 / 3600

        def heuristic(super_node: tuple) -> float:
            coords = self.node_coords.get(super_node)
            if coords is None:
                return float("inf")
            lat, lon = coords
            dist = self._haversine(lat, lon, end_lat, end_lon)
            return dist / bike_speed

        # Priority queue: (f_score, g_score, node, path)
        pq = []
        g_scores = {}
        visited = set()

        # Initialize with all start nodes
        for node in start_nodes:
            g = 0
            h = heuristic(node)
            heapq.heappush(pq, (g + h, g, node, [node]))
            g_scores[node] = g

        best_path = None
        best_cost = float("inf")

        while pq:
            f, g, node, path = heapq.heappop(pq)

            if f >= best_cost:
                continue

            if node in visited:
                continue
            visited.add(node)

            # Check if we reached destination
            if node in end_set:
                if g < best_cost:
                    best_cost = g
                    best_path = path
                continue

            # Expand neighbors
            if node in self.super_graph:
                for neighbor in self.super_graph.neighbors(node):
                    if neighbor in visited:
                        continue

                    edge_data = self.super_graph.get_edge_data(node, neighbor)
                    edge_weight = edge_data.get("weight", 1)

                    new_g = g + edge_weight

                    if neighbor not in g_scores or new_g < g_scores[neighbor]:
                        g_scores[neighbor] = new_g
                        h = heuristic(neighbor)
                        new_path = path + [neighbor]
                        heapq.heappush(pq, (new_g + h, new_g, neighbor, new_path))

        return best_path

    def _build_route_result(self, path: list[tuple],
                            start_lat: float, start_lon: float,
                            end_lat: float, end_lon: float) -> dict:
        """
        Build route result from path.

        Groups consecutive nodes by mode into segments.
        """
        if not path:
            return {"error": "Empty path"}

        # Group path into segments by mode
        segments = []
        current_segment = {
            "mode": path[0][0],
            "nodes": [path[0]]
        }

        for node in path[1:]:
            mode = node[0]
            # Check if this is a portal transition
            if mode != current_segment["mode"]:
                segments.append(current_segment)
                current_segment = {"mode": mode, "nodes": [node]}
            else:
                current_segment["nodes"].append(node)
        segments.append(current_segment)

        # Build geometry for each segment
        all_coords = []
        total_distance = 0
        total_duration = 0
        result_segments = []

        for seg in segments:
            mode = seg["mode"]
            nodes = seg["nodes"]

            seg_coords = []
            seg_distance = 0
            seg_duration = 0

            for i, super_node in enumerate(nodes):
                coords = self.node_coords.get(super_node)
                if coords:
                    lat, lon = coords
                    coord = [lon, lat]  # GeoJSON is [lon, lat]
                    seg_coords.append(coord)
                    if not all_coords or coord != all_coords[-1]:
                        all_coords.append(coord)

                # Calculate edge stats
                if i < len(nodes) - 1:
                    next_node = nodes[i + 1]
                    if self.super_graph.has_edge(super_node, next_node):
                        edge_data = self.super_graph.get_edge_data(super_node, next_node)
                        seg_distance += edge_data.get("length", 0)
                        seg_duration += edge_data.get("weight", 0)

            if seg_coords:
                result_segments.append({
                    "mode": mode,
                    "coordinates": seg_coords,
                    "distance": seg_distance,
                    "duration": seg_duration
                })
                total_distance += seg_distance
                total_duration += seg_duration

        # Build tile information
        start_tile = coords_to_tile(start_lat, start_lon)
        end_tile = coords_to_tile(end_lat, end_lon)

        return {
            "geometry": {
                "type": "LineString",
                "coordinates": all_coords
            },
            "distance": total_distance,
            "duration": total_duration,
            "mode": "roam",
            "segments": result_segments,
            "tiles": {
                "start": start_tile,
                "end": end_tile
            }
        }

    def get_mode_graph(self, mode: str) -> Optional[nx.MultiDiGraph]:
        """Get the underlying graph for a specific mode."""
        return self.graphs.get(mode)

    def route_single_mode(self, start_lat: float, start_lon: float,
                          end_lat: float, end_lon: float, mode: str) -> dict:
        """
        Find route using a single mode (respects one-way streets).

        Unlike Roam routing, single-mode routing uses the original directional
        graph from OSM, respecting one-way restrictions.

        Args:
            start_lat, start_lon: Starting coordinates
            end_lat, end_lon: Ending coordinates
            mode: Transport mode ("walk", "bike", or "drive")

        Returns:
            Route dict with geometry, distance, duration
        """
        t0 = time.time()
        _log(f"route_single_mode: {mode} from ({start_lat:.5f}, {start_lon:.5f}) to ({end_lat:.5f}, {end_lon:.5f})")

        if mode not in MODE_CONFIG:
            return {"error": f"Unknown mode: {mode}"}

        # Ensure graphs are loaded
        if not self.graphs_directional:
            _log("Loading graphs (first request)...")
            self.load_or_build()
            _log(f"Graphs loaded in {time.time() - t0:.2f}s")

        G = self.graphs_directional.get(mode)
        if G is None:
            return {"error": f"Graph not loaded for mode: {mode}"}

        # Find nearest nodes
        t1 = time.time()
        _log(f"Finding nearest start node in {G.number_of_nodes()} nodes...")
        start_node = self._find_nearest_node_in_graph(G, start_lat, start_lon)
        _log(f"Start node found in {time.time() - t1:.2f}s")

        t2 = time.time()
        _log(f"Finding nearest end node...")
        end_node = self._find_nearest_node_in_graph(G, end_lat, end_lon)
        _log(f"End node found in {time.time() - t2:.2f}s")

        if start_node is None or end_node is None:
            return {"error": "Could not find nearby nodes"}

        if start_node == end_node:
            return self._point_route(start_lat, start_lon, mode)

        # Run Dijkstra's algorithm
        t3 = time.time()
        _log(f"Running shortest path algorithm...")
        try:
            path = nx.shortest_path(G, start_node, end_node, weight="travel_time")
            _log(f"Path found ({len(path)} nodes) in {time.time() - t3:.2f}s")
        except nx.NetworkXNoPath:
            return {"error": f"No {mode} route found"}

        result = self._build_single_mode_result(G, path, mode)
        _log(f"Total route time: {time.time() - t0:.2f}s")
        return result

    def _find_nearest_node_in_graph(self, G, lat: float, lon: float) -> Optional[int]:
        """Find the nearest node in a specific graph."""
        best_node = None
        best_dist = float("inf")

        for node, data in G.nodes(data=True):
            node_lat = data.get("y")
            node_lon = data.get("x")
            if node_lat is None or node_lon is None:
                continue

            dist = self._haversine(lat, lon, node_lat, node_lon)
            if dist < best_dist:
                best_dist = dist
                best_node = node

        # Only return if within reasonable distance (500m)
        if best_dist < 500:
            return best_node
        return None

    def _point_route(self, lat: float, lon: float, mode: str) -> dict:
        """Return a degenerate route for same start/end."""
        return {
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat], [lon, lat]]
            },
            "distance": 0,
            "duration": 0,
            "mode": mode,
            "segments": [{
                "mode": mode,
                "coordinates": [[lon, lat], [lon, lat]],
                "distance": 0,
                "duration": 0
            }]
        }

    def _build_single_mode_result(self, G, path: list, mode: str) -> dict:
        """Build route result from single-mode path."""
        coords = []
        total_distance = 0
        total_time = 0

        for i, node in enumerate(path):
            data = G.nodes[node]
            coords.append([data["x"], data["y"]])  # [lon, lat] for GeoJSON

            if i < len(path) - 1:
                next_node = path[i + 1]
                edge_data = G.get_edge_data(node, next_node)
                if edge_data:
                    # MultiDiGraph may have multiple edges, take first
                    if isinstance(edge_data, dict) and 0 in edge_data:
                        edge = edge_data[0]
                    else:
                        edge = list(edge_data.values())[0] if edge_data else {}
                    total_distance += edge.get("length", 0)
                    total_time += edge.get("travel_time", 0)

        return {
            "geometry": {
                "type": "LineString",
                "coordinates": coords
            },
            "distance": total_distance,
            "duration": total_time,
            "mode": mode,
            "segments": [{
                "mode": mode,
                "coordinates": coords,
                "distance": total_distance,
                "duration": total_time
            }]
        }


# Global router instance
_router = None


def get_router(redis_client=None) -> RoamRouter:
    """Get or create the global Roam router instance."""
    global _router
    if _router is None:
        _router = RoamRouter(redis_client)
    return _router
