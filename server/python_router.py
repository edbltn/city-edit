"""
Walk-graph provider: topology, nearest-node snapping, and reverse geocoding.

Loads a city's prebaked compact arrays (walk_graph_arrays.npz — see
graph_arrays.py) into numpy arrays + a kdtree and serves:
  - get_graph_for_bbox: nodes/edges plus the OSM-node→edge maps that let OSRM
    route annotations resolve to votable edges (osm_to_graph_idx / node_pair_to_edge)
  - nearest_node_coords / reverse_geocode: point snapping and intersection naming

Routing itself is OSRM's job (see osrm_router.py). The runtime never touches
networkx or the walk_graph.pkl pickle — that decode transiently inflated to
several GB per city and OOM-looped prod. The pickle remains a build-time
artifact only (graph builds, block bakes, array conversion).
"""
import logging
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

import graph_arrays

logger = logging.getLogger(__name__)


class PythonRouter:
    """Walk-graph provider: compact-array topology, snapping, and geocoding.

    Despite the name (kept for import stability), this is not a router — OSRM
    serves every route. It loads the prebaked arrays once and answers
    topology/snap/geocode queries from them.
    """

    def __init__(self, data_dir: str = "osm_data", redis_client=None):
        """
        Args:
            data_dir: Directory containing walk_graph_arrays.npz
            redis_client: Redis client (held for callers that re-pass it on reload)
        """
        self.data_dir = Path(data_dir)
        self.redis = redis_client
        self._loaded = False
        self._version: str | None = None
        self._kdtree: cKDTree | None = None
        self._coords: np.ndarray | None = None       # [n, 2] -> (lat, lon)
        self._node_osmid: np.ndarray | None = None   # [n] int64 graph index -> osm id
        self._edge_u: np.ndarray | None = None       # [e] int32 from-index
        self._edge_v: np.ndarray | None = None       # [e] int32 to-index
        self._edge_len: np.ndarray | None = None     # [e] float32 length (m)
        self._edge_name: list[str] = []              # [e] shared street-name strings
        self._edge_highway: list[str] = []           # [e] shared highway-tag strings
        # CSR adjacency: node index -> incident edge positions.
        self._adj_indptr: np.ndarray | None = None
        self._adj_edges: np.ndarray | None = None

    def _ensure_loaded(self):
        """Load the prebaked arrays + spatial index (~1s; no pickle, no networkx)."""
        if self._loaded:
            return

        arrays = graph_arrays.load(self.data_dir)
        self._coords = arrays["coords"]
        self._node_osmid = arrays["node_osmid"]
        self._edge_u = arrays["edge_u"]
        self._edge_v = arrays["edge_v"]
        self._edge_len = arrays["edge_len"]
        self._version = arrays["version"]

        # Expand name/highway codes to shared Python strings (interned so the
        # per-edge lists are references onto the small unique sets).
        names = [sys.intern(s) for s in arrays["names"]]
        highways = [sys.intern(s) for s in arrays["highways"]]
        self._edge_name = [names[c] for c in arrays["edge_name"]]
        self._edge_highway = [highways[c] for c in arrays["edge_highway"]]

        n = len(self._coords)
        self._adj_indptr, self._adj_edges = graph_arrays.build_csr_adjacency(
            self._edge_u, self._edge_v, n)
        self._kdtree = cKDTree(self._coords)
        self._loaded = True

        logger.info(
            f"[ROUTER] Walk graph loaded from arrays: {n} nodes, "
            f"{len(self._edge_u)} edges, version: {self._version}"
        )

    def _incident_edges(self, node: int) -> np.ndarray:
        """Edge positions incident to a node (CSR slice)."""
        return self._adj_edges[self._adj_indptr[node]:self._adj_indptr[node + 1]]

    def get_graph_for_bbox(self, south: float, west: float, north: float, east: float) -> dict:
        """Return nodes and edges of the walk graph within a lat/lon bounding box.

        The node/edge construction (ordering, rounding, field layout) is the
        etag-critical contract: baked blocks stamp a topology_etag computed from
        this exact output, so any change here invalidates every block artifact.
        """
        self._ensure_loaded()

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
            int(self._node_osmid[int(g)]): pos for pos, g in enumerate(included)
        }

        # (graph_node_a, graph_node_b) → edge index (both directions for undirected)
        node_pair_to_edge: dict[tuple[int, int], int] = {}
        for i, edge in enumerate(edge_list):
            node_pair_to_edge[(edge[0], edge[1])] = i
            node_pair_to_edge[(edge[1], edge[0])] = i

        return {
            "nodes": node_list,
            "edges": edge_list,
            "osm_to_graph_idx": osm_to_graph_idx,
            "node_pair_to_edge": node_pair_to_edge,
        }

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
        # walking the CSR adjacency (node index → incident edge positions).
        visited: set = {start_node}
        frontier = [start_node]
        for _ in range(5):
            if len(names) >= 2:
                break
            next_frontier = []
            for node in frontier:
                for ep in self._incident_edges(node):
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
        logger.info("[ROUTER] Reloading walk graph...")
        self._loaded = False
        self._version = None
        self._kdtree = None
        self._coords = None
        self._node_osmid = None
        self._edge_u = None
        self._edge_v = None
        self._edge_len = None
        self._edge_name = []
        self._edge_highway = []
        self._adj_indptr = None
        self._adj_edges = None
        self._ensure_loaded()

    def stats(self) -> dict:
        """Get graph statistics."""
        self._ensure_loaded()
        return {
            "nodes": int(len(self._coords)),
            "edges": int(len(self._edge_u)) if self._edge_u is not None else 0,
            "version": self._version
        }


def build_graph(bbox: tuple, output_dir: str, pbf_url: str | None = None) -> dict:
    """
    Build the walk graph from the same OSM PBF that feeds OSRM.

    Reads the city's `pbf_url` extract and keeps every way OSRM's foot profile would
    route (see osm_graph_builder / foot_profile), so the topology graph is a superset
    of OSRM's foot network and route votes map cleanly by OSM node id. Emits BOTH
    artifacts: walk_graph.pkl (build-time canonical source, consumed by the block
    bake) and walk_graph_arrays.npz (the only thing the runtime loads).

    Args:
        bbox: (south, west, north, east) bounding box
        output_dir: Directory to save the graph artifacts (and cache the source PBF)
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

    import os
    import pickle
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

    # The runtime loads only the compact arrays — convert immediately so a
    # fresh build is servable without a separate step.
    graph_arrays.convert(output_path)

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
