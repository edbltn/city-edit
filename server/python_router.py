"""
OSM graph provider using osmnx + rustworkx.

Provides graph topology for visualization, nearest-node snapping, and
reverse geocoding. Routing is handled by OSRM (see osrm_router.py).
"""
import logging
import pickle
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import osmnx as ox
    import rustworkx as rx
    HAS_PYTHON_ROUTER = True
    logger.info("Graph provider dependencies loaded (osmnx, rustworkx)")
except ImportError as e:
    HAS_PYTHON_ROUTER = False
    ox = None
    rx = None
    logger.warning(f"Graph provider not available: {e}")


class PythonRouter:
    """
    OSM graph provider for topology, snapping, and reverse geocoding.
    """

    def __init__(self, data_dir: str = "osm_data"):
        self.data_dir = Path(data_dir)
        self._graph = None
        self._nx_graph = None
        self._node_index = {}
        self._index_to_node = {}
        self._version = None
        self._bbox_cache: dict[tuple, dict] = {}

    def _ensure_loaded(self):
        """Lazy-load the graph on first request."""
        if self._graph is not None:
            return

        if not HAS_PYTHON_ROUTER:
            raise RuntimeError(
                "Graph provider dependencies not available. "
                "Install with: pip install osmnx rustworkx"
            )

        graph_path = self.data_dir / "walk_graph.pkl"
        if not graph_path.exists():
            raise RuntimeError(
                f"Graph file not found: {graph_path}. "
                "Run: python refresh_osm.py --region fidi --force"
            )

        logger.info(f"Loading graph from {graph_path}")
        with open(graph_path, "rb") as f:
            data = pickle.load(f)

        self._nx_graph = data["graph"]
        self._node_index = data["node_index"]
        self._index_to_node = data["index_to_node"]
        self._version = data.get("version", "unknown")

        self._graph = rx.PyDiGraph()
        node_count = len(self._node_index)
        self._graph.add_nodes_from(range(node_count))

        for u, v, edge_data in self._nx_graph.edges(data=True):
            if u in self._node_index and v in self._node_index:
                weight = edge_data.get("length", 1.0)
                u_idx = self._node_index[u]
                v_idx = self._node_index[v]
                self._graph.add_edge(u_idx, v_idx, weight)

        logger.info(
            f"Graph loaded: {self._graph.num_nodes()} nodes, "
            f"{self._graph.num_edges()} edges, version: {self._version}"
        )

    def get_graph_for_bbox(self, south: float, west: float, north: float, east: float) -> dict:
        """Return nodes and edges of the walk graph within a lat/lon bounding box.

        Results are cached by bbox rounded to 3 decimal places (~111m).
        """
        self._ensure_loaded()

        cache_key = (round(south, 3), round(west, 3), round(north, 3), round(east, 3))
        if cache_key in self._bbox_cache:
            return self._bbox_cache[cache_key]

        node_list = []
        node_id_to_idx = {}

        for node_id, data in self._nx_graph.nodes(data=True):
            lat = data.get("y")
            lon = data.get("x")
            if lat is None or lon is None:
                continue
            if south <= lat <= north and west <= lon <= east:
                node_id_to_idx[node_id] = len(node_list)
                node_list.append([lat, lon])

        edge_list = []
        for u, v, data in self._nx_graph.edges(data=True):
            if u not in node_id_to_idx or v not in node_id_to_idx:
                continue
            name = data.get("name", "") or ""
            if isinstance(name, list):
                name = name[0] if name else ""
            highway = data.get("highway", "") or ""
            if isinstance(highway, list):
                highway = highway[0] if highway else ""
            length = round(data.get("length", 0.0), 1)
            edge_list.append([node_id_to_idx[u], node_id_to_idx[v], name, highway, length])

        result = {"nodes": node_list, "edges": edge_list}

        if len(self._bbox_cache) >= 64:
            oldest = next(iter(self._bbox_cache))
            del self._bbox_cache[oldest]
        self._bbox_cache[cache_key] = result

        return result

    def nearest_node_coords(self, lat: float, lon: float) -> tuple[float, float]:
        """Return (lat, lon) of the nearest graph node to the given point."""
        self._ensure_loaded()
        node_id = ox.distance.nearest_nodes(self._nx_graph, lon, lat)
        node_data = self._nx_graph.nodes[node_id]
        return node_data["y"], node_data["x"]

    def reverse_geocode(self, lat: float, lon: float) -> str:
        """Return a street-level address for a point using the local OSM graph.

        Finds the nearest node, then BFS outward (up to 5 hops) to collect
        street names from nearby edges.

        Two distinct street names -> "Broadway & 7th Ave" (intersection).
        One street name -> that name.  No names -> empty string.
        """
        self._ensure_loaded()
        start_node = ox.distance.nearest_nodes(self._nx_graph, lon, lat)

        names: list[str] = []
        seen_names: set[str] = set()

        visited: set = {start_node}
        frontier = [start_node]
        for _ in range(5):
            if len(names) >= 2:
                break
            next_frontier = []
            for node_id in frontier:
                for _, neighbor, data in self._nx_graph.edges(node_id, data=True):
                    name = data.get("name", "") or ""
                    if isinstance(name, list):
                        name = name[0] if name else ""
                    if name and name not in seen_names:
                        seen_names.add(name)
                        names.append(name)
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
        logger.info("Reloading graph provider...")
        self._graph = None
        self._nx_graph = None
        self._node_index = {}
        self._index_to_node = {}
        self._version = None
        self._bbox_cache = {}
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

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    south, west, north, east = bbox
    logger.info(f"Building walk graph for bbox: {bbox}")

    start_time = time.time()

    logger.info("Downloading OSM network (all types for connectivity)...")
    G = ox.graph_from_bbox(
        bbox=(west, south, east, north),
        network_type="all",
        simplify=True,
        retain_all=True,
        truncate_by_edge=True
    )

    node_index = {}
    index_to_node = {}
    for i, node_id in enumerate(G.nodes()):
        node_index[node_id] = i
        index_to_node[i] = node_id

    version = time.strftime("%Y%m%d_%H%M%S")

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
    """Check if the graph provider is available."""
    return HAS_PYTHON_ROUTER
