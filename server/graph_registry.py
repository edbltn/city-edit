"""
Per-city graph + OSRM registries.

Replaces the old single-global graph cache. Each city's walk graph
(osm_data/<city>/walk_graph.pkl) is loaded on demand into a CityGraph that holds
the topology, coordinate→edge lookups, node adjacency, and a cached topology JSON
blob (with ETag) for /api/graph-topology. An LRU bound keeps memory in check when
several cities are active. OSRM routers are likewise created per city, each
pointing at that city's OSRM container.
"""
import hashlib
import json
import logging
import os
import struct
import threading
from collections import OrderedDict

import numpy as np
from scipy.spatial import cKDTree

from cities import City
from python_router import PythonRouter
from osrm_router import OsrmRouter

logger = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# A "network" is what a map votes on. "streets" (default) is the city's full
# pedestrian walk graph. The others are fixed point sets loaded from a committed
# JSON of {name, lat, lon} and served as a *degenerate graph* (one node + one
# self-edge per point), so the entire topology/vote/render stack is reused
# unchanged. See build_ebike_stations.py for how the data is produced.
STATION_NETWORKS = {
    "ebikes": "ebike_stations.json",
}


def load_station_graph(network: str) -> dict:
    """Load a station network into the same shape PythonRouter.get_graph_for_bbox
    returns: each station i is a node and a zero-length self-edge [i, i, name, ...].
    A self-edge's midpoint is the node itself, so map markers land on the station.
    """
    path = os.path.join(_DATA_DIR, STATION_NETWORKS[network])
    with open(path) as f:
        stations = json.load(f)
    nodes = [[float(s["lat"]), float(s["lon"])] for s in stations]
    edges = [[i, i, s.get("name", ""), "station", 0.0] for i, s in enumerate(stations)]
    return {"nodes": nodes, "edges": edges, "osm_to_graph_idx": {}, "node_pair_to_edge": {}}


class CityGraph:
    """Lazily-loaded graph + derived lookups for a single city + network."""

    def __init__(self, city: City, redis_client=None, network: str = "streets"):
        self.city = city
        self.network = network
        self.provider = PythonRouter(data_dir=city.data_dir, redis_client=redis_client)
        self._loaded = False
        # Serializes concurrent first-loads of this city. Under the gevent worker
        # pickle.load yields on file I/O, so without this two requests for the
        # same unloaded city would each load the full graph (e.g. 3x the NYC
        # graph at once → OOM). threading.Lock is gevent-patched at runtime.
        self._load_lock = threading.Lock()

        self.nodes: list = []
        self.edges: list = []
        self.node_adj: list[list[int]] = []
        self.coord_to_edge_idx: dict = {}
        self.coord_to_node_idx: dict = {}
        self.osm_to_graph_idx: dict = {}
        self.node_pair_to_edge: dict = {}
        self.topology_json: str | None = None
        self.topology_etag: str | None = None
        # Compact binary topology (built lazily; see topology_binary).
        self.topology_bin: bytes | None = None
        # Lazily-built cKDTree over edge midpoints, used by the vote migration to
        # re-snap many anchors at once (kept off the hot load path / common case).
        self._edge_mid_tree: cKDTree | None = None

    def ensure_loaded(self):
        if self._loaded:
            return
        with self._load_lock:
            # Re-check: another greenlet/thread may have loaded while we waited.
            if self._loaded:
                return
            self._load_locked()

    def _load_locked(self):
        logger.info(f"[GRAPH] Loading graph for '{self.city.id}:{self.network}'...")
        if self.network in STATION_NETWORKS:
            # Fixed point network — never touches the heavy city walk graph.
            data = load_station_graph(self.network)
        else:
            south, west, north, east = self.city.bbox
            data = self.provider.get_graph_for_bbox(south, west, north, east)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        # coord → edge index reverse map (both directions for undirected lookup)
        coord_to_edge_idx: dict[tuple[str, str], list[int]] = {}
        for i, edge in enumerate(edges):
            from_idx, to_idx = edge[0], edge[1]
            from_lat, from_lon = nodes[from_idx]
            to_lat, to_lon = nodes[to_idx]
            c1 = f"{round(from_lon, 5)},{round(from_lat, 5)}"
            c2 = f"{round(to_lon, 5)},{round(to_lat, 5)}"
            coord_to_edge_idx.setdefault((c1, c2), []).append(i)
            if c1 != c2:
                coord_to_edge_idx.setdefault((c2, c1), []).append(i)

        coord_to_node_idx: dict[str, int] = {}
        for i, node in enumerate(nodes):
            lat, lon = node[0], node[1]
            coord_to_node_idx[f"{round(lon, 5)},{round(lat, 5)}"] = i

        # Node adjacency: node_id → [edge_ids]. A self-edge (station network) is
        # added once, not twice, so snap_point_to_edge returns it cleanly.
        adj: list[list[int]] = [[] for _ in range(len(nodes))]
        for i, edge in enumerate(edges):
            adj[edge[0]].append(i)
            if edge[1] != edge[0]:
                adj[edge[1]].append(i)

        edges_slim = [[e[0], e[1], e[2]] for e in edges]
        topology_json = json.dumps({"nodes": nodes, "edges": edges_slim})
        topology_etag = '"' + hashlib.sha256(topology_json.encode()).hexdigest()[:16] + '"'

        self.nodes = nodes
        self.edges = edges
        self.node_adj = adj
        self.coord_to_edge_idx = coord_to_edge_idx
        self.coord_to_node_idx = coord_to_node_idx
        self.osm_to_graph_idx = data.get("osm_to_graph_idx", {})
        self.node_pair_to_edge = data.get("node_pair_to_edge", {})
        self.topology_json = topology_json
        self.topology_etag = topology_etag
        self._edge_mid_tree = None  # rebuilt lazily for the new edge set
        self._loaded = True

        logger.info(
            f"[GRAPH] '{self.city.id}:{self.network}' loaded: {len(nodes)} nodes, "
            f"{len(edges)} edges, topology {len(topology_json) / (1024 * 1024):.1f} MB"
        )

    def topology_binary(self) -> bytes:
        """Compact little-endian binary topology — the mobile-safe wire format.

        Layout: 12-byte header [magic 'GTB1', uint32 nNodes, uint32 nEdges], then
        nNodes×2 int32 (lat, lon as degrees×1e7, ~1cm precision), then nEdges×2
        uint32 (from_idx, to_idx). Edge names are omitted (the client reverse-
        geocodes street tooltips instead). This exists so a phone decodes an
        ArrayBuffer rather than JSON.parse-ing the ~150MB NYC topology string,
        which OOM-crashes mobile Safari. Built once per load and cached; ~37MB raw
        → ~16MB gzipped by nginx, so it clears Cloud Run's 32MB response cap. The
        edges array index is the canonical edge id (matches /api/graph-votes)."""
        self.ensure_loaded()
        if self.topology_bin is None:
            nodes, edges = self.nodes, self.edges
            coords = np.fromiter(
                (round(v * 1e7) for nd in nodes for v in (nd[0], nd[1])),
                dtype="<i4", count=2 * len(nodes),
            )
            ends = np.fromiter(
                (v for e in edges for v in (e[0], e[1])),
                dtype="<u4", count=2 * len(edges),
            )
            header = struct.pack("<4sII", b"GTB1", len(nodes), len(edges))
            self.topology_bin = header + coords.tobytes() + ends.tobytes()
        return self.topology_bin

    def snap_point_to_edge(self, lat: float, lon: float) -> list[int]:
        """Snap a lat/lon point to the nearest graph edge via closest node."""
        self.ensure_loaded()
        if not self.nodes:
            return []
        best_idx = 0
        best_dist = float("inf")
        for i, node in enumerate(self.nodes):
            d = (node[0] - lat) ** 2 + (node[1] - lon) ** 2
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx < len(self.node_adj) and self.node_adj[best_idx]:
            return [self.node_adj[best_idx][0]]
        return []

    def edge_midpoint(self, edge_id: int) -> tuple[float, float] | None:
        """(lat, lon) midpoint of an edge — the migration anchor stored per vote."""
        self.ensure_loaded()
        if edge_id < 0 or edge_id >= len(self.edges):
            return None
        e = self.edges[edge_id]
        a = self.nodes[e[0]]
        b = self.nodes[e[1]]
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

    def edge_midpoints(self, edge_ids) -> dict[int, tuple[float, float]]:
        """{edge_id: (lat, lon)} midpoints for many edges (skips out-of-range ids)."""
        self.ensure_loaded()
        out: dict[int, tuple[float, float]] = {}
        n = len(self.edges)
        for eid in edge_ids:
            if 0 <= eid < n:
                e = self.edges[eid]
                a = self.nodes[e[0]]
                b = self.nodes[e[1]]
                out[eid] = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        return out

    def _ensure_edge_mid_tree(self) -> cKDTree | None:
        """Build (once) a cKDTree over edge midpoints in (lat, lon) space.

        DUPLICITY (intentional, documented): this answers the same question as the
        client's nearest-edge snap (GraphLayer hitTest / projectOntoEdge) — "which
        edge does this coordinate belong to" — so a vote re-snapped here after a
        graph rebuild lands on the same edge a fresh click would. Built lazily and
        only for the migration, so the kdtree memory never burdens the hot path.
        """
        self.ensure_loaded()
        if self._edge_mid_tree is None:
            if not self.edges:
                return None
            nodes = np.asarray(self.nodes, dtype=np.float64)  # [N, 2] = (lat, lon)
            e = np.asarray([(edge[0], edge[1]) for edge in self.edges], dtype=np.int64)
            mids = (nodes[e[:, 0]] + nodes[e[:, 1]]) / 2.0  # [E, 2]
            self._edge_mid_tree = cKDTree(mids)
        return self._edge_mid_tree

    def nearest_edges(self, points) -> list[int]:
        """Vectorized: nearest edge id (by midpoint) for each (lat, lon) point.

        O((P + E) log E) via a kdtree, vs the old O(P·E) scan — the difference
        between feasible and not when re-snapping hundreds of thousands of votes.
        """
        tree = self._ensure_edge_mid_tree()
        if tree is None or not len(points):
            return []
        _, idx = tree.query(np.asarray(points, dtype=np.float64))
        return [int(i) for i in np.atleast_1d(idx)]

    def nearest_edge_by_midpoint(self, lat: float, lon: float) -> int | None:
        """Edge whose midpoint is closest to (lat, lon). Single-point convenience."""
        out = self.nearest_edges([(lat, lon)])
        return out[0] if out else None

    def unload(self):
        """Drop derived caches and the underlying graph to free memory on eviction."""
        redis_client = self.provider.redis
        self.provider = PythonRouter(data_dir=self.city.data_dir, redis_client=redis_client)
        self._loaded = False
        self.nodes = []
        self.edges = []
        self.node_adj = []
        self.coord_to_edge_idx = {}
        self.coord_to_node_idx = {}
        self.osm_to_graph_idx = {}
        self.node_pair_to_edge = {}
        self.topology_json = None
        self.topology_etag = None
        self._edge_mid_tree = None


class GraphRegistry:
    """LRU-bounded set of loaded CityGraphs, keyed by '<city>:<network>'."""

    def __init__(self, redis_client=None, max_loaded: int = 3):
        self.redis = redis_client
        self.max_loaded = max_loaded
        self._graphs: "OrderedDict[str, CityGraph]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, city: City, network: str = "streets") -> CityGraph:
        # Hold the registry lock only for the fast bookkeeping; the (slow) graph
        # load happens outside it under the per-graph lock, so different graphs
        # can load concurrently and repeated hits to one don't double-load.
        key = f"{city.id}:{network}"
        with self._lock:
            cg = self._graphs.get(key)
            if cg is None:
                cg = CityGraph(city, self.redis, network=network)
                self._graphs[key] = cg
                while len(self._graphs) > self.max_loaded:
                    old_id, old = self._graphs.popitem(last=False)
                    logger.info(f"[GRAPH] Evicting '{old_id}' (LRU)")
                    old.unload()
            else:
                self._graphs.move_to_end(key)
        cg.ensure_loaded()
        return cg

    def loaded_ids(self) -> list[str]:
        return list(self._graphs.keys())

    def reload_city(self, city_id: str) -> list[CityGraph]:
        """Force-reload every loaded graph for a city from disk (after a rebuild).

        Returns the reloaded CityGraphs so callers can re-snap votes against them.
        """
        with self._lock:
            affected = [cg for k, cg in self._graphs.items() if cg.city.id == city_id]
        for cg in affected:
            cg.unload()
            cg.ensure_loaded()
            logger.info(f"[GRAPH] Reloaded '{cg.city.id}:{cg.network}' from disk")
        return affected


class OsrmRegistry:
    """Routers to OSRM.

    Preferred: a single merged OSRM serves every city (set OSRM_URL) → one shared
    router for all cities. Falls back to per-city hosts (OSRM_HOST_<CITY> / the
    service name in cities.py) for legacy setups that still run one OSRM per city.
    """

    def __init__(self):
        self._shared: OsrmRouter | None = None
        self._routers: dict[str, OsrmRouter] = {}

    def get(self, city: City) -> OsrmRouter:
        url = os.environ.get("OSRM_URL")
        if url:
            if self._shared is None:
                # HTTPS target ⇒ private Cloud Run service ⇒ needs ID-token auth.
                self._shared = OsrmRouter(
                    base_url=url, use_id_token=url.startswith("https://")
                )
            return self._shared
        r = self._routers.get(city.id)
        if r is None:
            r = OsrmRouter(host=city.osrm_host, port=city.osrm_port)
            self._routers[city.id] = r
        return r
