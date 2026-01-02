"""
Hybrid routing using OSM street network.

Supports walk, bike, drive modes individually or as an optimal hybrid
that can switch between modes at any intersection.
"""

import os
import pickle
import heapq
import math
from pathlib import Path

import osmnx as ox
import networkx as nx

# Cache directory for graph data
CACHE_DIR = Path(__file__).parent / "graph_cache"
CACHE_DIR.mkdir(exist_ok=True)

# NYC bounding box (same as config)
NYC_BBOX = {
    "north": 40.92,
    "south": 40.49,
    "east": -73.70,
    "west": -74.26
}

# Mode configurations
MODE_CONFIG = {
    "walk": {
        "network_type": "walk",
        "speed_kmh": 5.0,
        "cache_file": "nyc_walk.gpickle"
    },
    "bike": {
        "network_type": "bike",
        "speed_kmh": 18.0,
        "cache_file": "nyc_bike.gpickle"
    },
    "drive": {
        "network_type": "drive",
        "speed_kmh": 35.0,
        "cache_file": "nyc_drive.gpickle"
    }
}

# Mode switch penalty in seconds (time to park bike, get in car, etc.)
MODE_SWITCH_PENALTY = 120  # 2 minutes


class HybridRouter:
    """Router supporting single-mode and hybrid multi-mode routing."""

    def __init__(self):
        self.graphs = {}
        self.node_coords = {}  # node_id -> (lat, lon)

    def load_graph(self, mode: str) -> nx.MultiDiGraph:
        """Load or download graph for a specific mode."""
        if mode in self.graphs:
            return self.graphs[mode]

        config = MODE_CONFIG[mode]
        cache_path = CACHE_DIR / config["cache_file"]

        if cache_path.exists():
            print(f"Loading cached {mode} graph...")
            with open(cache_path, "rb") as f:
                G = pickle.load(f)
        else:
            print(f"Downloading {mode} network from OSM (this may take a few minutes)...")
            G = ox.graph_from_bbox(
                bbox=(NYC_BBOX["north"], NYC_BBOX["south"],
                      NYC_BBOX["east"], NYC_BBOX["west"]),
                network_type=config["network_type"],
                simplify=True
            )
            # Add travel time to edges based on mode speed
            G = ox.add_edge_speeds(G)
            G = ox.add_edge_travel_times(G)

            # Cache for future use
            print(f"Caching {mode} graph...")
            with open(cache_path, "wb") as f:
                pickle.dump(G, f)

        self.graphs[mode] = G

        # Build node coordinate lookup
        for node, data in G.nodes(data=True):
            self.node_coords[node] = (data["y"], data["x"])  # lat, lon

        print(f"Loaded {mode} graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        return G

    def load_all_graphs(self):
        """Load graphs for all modes."""
        for mode in MODE_CONFIG:
            self.load_graph(mode)

    def get_nearest_node(self, lat: float, lon: float, mode: str) -> int:
        """Find the nearest graph node to a coordinate."""
        G = self.load_graph(mode)
        return ox.nearest_nodes(G, lon, lat)

    def haversine(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance in meters between two coordinates."""
        R = 6371000
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi / 2) ** 2 + \
            math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    def route_single_mode(self, start_lat: float, start_lon: float,
                          end_lat: float, end_lon: float, mode: str) -> dict:
        """Calculate route using a single mode."""
        G = self.load_graph(mode)

        # Find nearest nodes
        start_node = ox.nearest_nodes(G, start_lon, start_lat)
        end_node = ox.nearest_nodes(G, end_lon, end_lat)

        if start_node == end_node:
            return self._point_route(start_lat, start_lon, mode)

        try:
            # Use networkx shortest path with travel time as weight
            route_nodes = nx.shortest_path(G, start_node, end_node, weight="travel_time")
        except nx.NetworkXNoPath:
            return {"error": f"No {mode} route found"}

        # Build geometry and calculate stats
        return self._build_route_result(G, route_nodes, mode)

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
            "segments": []
        }

    def _build_route_result(self, G: nx.MultiDiGraph, route_nodes: list,
                            mode: str) -> dict:
        """Build route result from node list."""
        coords = []
        total_distance = 0
        total_time = 0

        for i, node in enumerate(route_nodes):
            data = G.nodes[node]
            coords.append([data["x"], data["y"]])  # lon, lat for GeoJSON

            if i < len(route_nodes) - 1:
                next_node = route_nodes[i + 1]
                # Get edge data (take first edge if multiple)
                edge_data = G.get_edge_data(node, next_node)
                if edge_data:
                    edge = list(edge_data.values())[0]
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

    def route_hybrid(self, start_lat: float, start_lon: float,
                     end_lat: float, end_lon: float) -> dict:
        """
        Calculate optimal hybrid route that can switch between modes.

        Uses a multi-layer graph approach where each physical node exists
        in each mode layer, with mode-switch edges connecting layers.
        """
        # Ensure all graphs are loaded
        self.load_all_graphs()

        modes = list(MODE_CONFIG.keys())

        # Find nearest nodes in each mode's graph
        start_nodes = {}
        end_nodes = {}
        for mode in modes:
            G = self.graphs[mode]
            start_nodes[mode] = ox.nearest_nodes(G, start_lon, start_lat)
            end_nodes[mode] = ox.nearest_nodes(G, end_lon, end_lat)

        # Build unified node set (nodes that exist in all graphs)
        # For simplicity, we'll use the walk graph as the base since it has the most nodes
        base_graph = self.graphs["walk"]

        # A* with mode-switching capability
        # State: (mode, node_id)
        # Priority queue: (f_score, g_score, state, path)

        def heuristic(node_id: int) -> float:
            """Estimate time to destination (straight line at bike speed)."""
            if node_id not in self.node_coords:
                return float("inf")
            lat, lon = self.node_coords[node_id]
            dist = self.haversine(lat, lon, end_lat, end_lon)
            # Use bike speed for optimistic estimate
            return dist / (MODE_CONFIG["bike"]["speed_kmh"] * 1000 / 3600)

        # Initialize with all starting modes
        pq = []
        visited = set()
        g_scores = {}

        for mode in modes:
            start_node = start_nodes[mode]
            state = (mode, start_node)
            g = 0
            h = heuristic(start_node)
            heapq.heappush(pq, (g + h, g, state, [(mode, start_node)]))
            g_scores[state] = g

        best_path = None
        best_cost = float("inf")

        while pq:
            f, g, state, path = heapq.heappop(pq)

            if f >= best_cost:
                continue

            current_mode, current_node = state

            if state in visited:
                continue
            visited.add(state)

            # Check if we've reached destination
            if current_node == end_nodes[current_mode]:
                if g < best_cost:
                    best_cost = g
                    best_path = path
                continue

            G = self.graphs[current_mode]

            # Expand neighbors in current mode
            if current_node in G:
                for neighbor in G.neighbors(current_node):
                    edge_data = G.get_edge_data(current_node, neighbor)
                    if edge_data:
                        edge = list(edge_data.values())[0]
                        travel_time = edge.get("travel_time", 0)

                        new_state = (current_mode, neighbor)
                        new_g = g + travel_time

                        if new_state not in g_scores or new_g < g_scores[new_state]:
                            g_scores[new_state] = new_g
                            h = heuristic(neighbor)
                            new_path = path + [(current_mode, neighbor)]
                            heapq.heappush(pq, (new_g + h, new_g, new_state, new_path))

            # Mode switching at current node
            for new_mode in modes:
                if new_mode == current_mode:
                    continue

                new_G = self.graphs[new_mode]
                # Find nearest node in new mode's graph to current position
                if current_node in self.node_coords:
                    lat, lon = self.node_coords[current_node]
                    try:
                        new_node = ox.nearest_nodes(new_G, lon, lat)
                        # Only switch if the node is reasonably close (within 100m)
                        new_lat, new_lon = self.node_coords.get(new_node, (lat, lon))
                        if self.haversine(lat, lon, new_lat, new_lon) < 100:
                            new_state = (new_mode, new_node)
                            new_g = g + MODE_SWITCH_PENALTY

                            if new_state not in g_scores or new_g < g_scores[new_state]:
                                g_scores[new_state] = new_g
                                h = heuristic(new_node)
                                new_path = path + [(new_mode, new_node)]
                                heapq.heappush(pq, (new_g + h, new_g, new_state, new_path))
                    except Exception:
                        pass

        if best_path is None:
            return {"error": "No hybrid route found"}

        return self._build_hybrid_result(best_path)

    def _build_hybrid_result(self, path: list) -> dict:
        """Build route result from hybrid path."""
        if not path:
            return {"error": "Empty path"}

        # Group consecutive nodes by mode into segments
        segments = []
        current_segment = {"mode": path[0][0], "nodes": [path[0][1]]}

        for mode, node in path[1:]:
            if mode == current_segment["mode"]:
                current_segment["nodes"].append(node)
            else:
                segments.append(current_segment)
                current_segment = {"mode": mode, "nodes": [node]}
        segments.append(current_segment)

        # Build geometry for each segment
        all_coords = []
        total_distance = 0
        total_duration = 0
        result_segments = []

        for seg in segments:
            mode = seg["mode"]
            nodes = seg["nodes"]
            G = self.graphs[mode]

            seg_coords = []
            seg_distance = 0
            seg_duration = 0

            for i, node in enumerate(nodes):
                if node in G.nodes:
                    data = G.nodes[node]
                    coord = [data["x"], data["y"]]
                    seg_coords.append(coord)
                    if not all_coords or coord != all_coords[-1]:
                        all_coords.append(coord)

                    if i < len(nodes) - 1:
                        next_node = nodes[i + 1]
                        if G.has_edge(node, next_node):
                            edge_data = G.get_edge_data(node, next_node)
                            if edge_data:
                                edge = list(edge_data.values())[0]
                                seg_distance += edge.get("length", 0)
                                seg_duration += edge.get("travel_time", 0)

            if seg_coords:
                result_segments.append({
                    "mode": mode,
                    "coordinates": seg_coords,
                    "distance": seg_distance,
                    "duration": seg_duration
                })
                total_distance += seg_distance
                total_duration += seg_duration

        # Add mode switch penalties to total duration
        num_switches = len(segments) - 1
        total_duration += num_switches * MODE_SWITCH_PENALTY

        return {
            "geometry": {
                "type": "LineString",
                "coordinates": all_coords
            },
            "distance": total_distance,
            "duration": total_duration,
            "mode": "hybrid",
            "segments": result_segments
        }


# Global router instance
_router = None


def get_router() -> HybridRouter:
    """Get or create the global router instance."""
    global _router
    if _router is None:
        _router = HybridRouter()
    return _router
