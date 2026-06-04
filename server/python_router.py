"""
Walk-graph provider: topology, nearest-node snapping, and reverse geocoding.

Loads a city's pickled walk graph into compact numpy arrays + a kdtree and serves:
  - get_graph_for_bbox: nodes/edges plus the OSM-node→edge maps that let OSRM
    route annotations resolve to votable edges (osm_to_graph_idx / node_pair_to_edge)
  - nearest_node_coords / reverse_geocode: point snapping and intersection naming

Routing itself is OSRM's job (see osrm_router.py); this module no longer computes
routes, so it carries no networkx/rustworkx routing graph at runtime — the heavy
networkx graph is freed right after the compact arrays are extracted.
"""
import gc
import logging
import os
import pickle
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

logger = logging.getLogger(__name__)


class PythonRouter:
    """Walk-graph provider: compact-array topology, snapping, and geocoding.

    Despite the name (kept for import stability), this is not a router — OSRM
    serves every route. It loads the walk graph once into numpy arrays and a
    kdtree, frees the source networkx graph, and answers topology/snap/geocode
    queries from the arrays.
    """

    def __init__(self, data_dir: str = "osm_data", redis_client=None):
        """
        Args:
            data_dir: Directory containing graph pickle files
            redis_client: Redis client (held for callers that re-pass it on reload)
        """
        self.data_dir = Path(data_dir)
        self.redis = redis_client
        self._loaded = False
        self._node_index = {}    # osm id -> graph index
        self._index_to_node = {}  # graph index -> osm id
        self._version = None
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
        let gc reclaim it. Nothing routing-related is kept (OSRM does routing).
        """
        if self._loaded:
            return

        graph_path = self.data_dir / "walk_graph.pkl"
        if not graph_path.exists():
            raise RuntimeError(
                f"Graph file not found: {graph_path}. "
                "Run: python refresh_osm.py --region fidi --force"
            )

        logger.info(f"Loading walk graph from {graph_path}")
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
            f"Walk graph loaded: {n} nodes, {len(edge_u)} edges, "
            f"version: {self._version} (networkx freed)"
        )

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
        logger.info("Reloading walk graph...")
        self._loaded = False
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
    # build_graph is the only path that needs osmium; loading does not.
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
