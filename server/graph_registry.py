"""
Per-city graph + OSRM registries.

Replaces the old single-global graph cache. Each city's walk graph
(osm_data/<city>/walk_graph.pkl) is loaded on demand into a CityGraph that holds
the topology, coordinate→edge lookups, node adjacency, and a cached topology JSON
blob (with ETag) for /api/graph-topology. An LRU bound keeps memory in check when
several cities are active. OSRM routers are likewise created per city, each
pointing at that city's OSRM container.
"""
import gzip
import hashlib
import json
import logging
import threading
from collections import OrderedDict

from cities import City
from python_router import PythonRouter
from osrm_router import OsrmRouter

logger = logging.getLogger(__name__)


class CityGraph:
    """Lazily-loaded graph + derived lookups for a single city."""

    def __init__(self, city: City, redis_client=None):
        self.city = city
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
        self.topology_gzip: bytes | None = None
        self.topology_etag: str | None = None

    def ensure_loaded(self):
        if self._loaded:
            return
        with self._load_lock:
            # Re-check: another greenlet/thread may have loaded while we waited.
            if self._loaded:
                return
            self._load_locked()

    def _load_locked(self):
        logger.info(f"[GRAPH] Loading graph for city '{self.city.id}'...")
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

        # Node adjacency: node_id → [edge_ids]
        adj: list[list[int]] = [[] for _ in range(len(nodes))]
        for i, edge in enumerate(edges):
            adj[edge[0]].append(i)
            adj[edge[1]].append(i)

        edges_slim = [[e[0], e[1], e[2]] for e in edges]
        topology_json = json.dumps({"nodes": nodes, "edges": edges_slim})
        topology_etag = '"' + hashlib.sha256(topology_json.encode()).hexdigest()[:16] + '"'
        # Pre-compressed variant served to gzip-accepting clients (~4-5x
        # smaller; coordinate JSON compresses well). Built once per load so
        # neither Flask nor nginx re-compresses ~24MB per cold visitor.
        topology_gzip = gzip.compress(topology_json.encode(), compresslevel=6)

        self.nodes = nodes
        self.edges = edges
        self.node_adj = adj
        self.coord_to_edge_idx = coord_to_edge_idx
        self.coord_to_node_idx = coord_to_node_idx
        self.osm_to_graph_idx = data.get("osm_to_graph_idx", {})
        self.node_pair_to_edge = data.get("node_pair_to_edge", {})
        self.topology_json = topology_json
        self.topology_gzip = topology_gzip
        self.topology_etag = topology_etag
        self._loaded = True

        logger.info(
            f"[GRAPH] '{self.city.id}' loaded: {len(nodes)} nodes, {len(edges)} edges, "
            f"topology {len(topology_json) / (1024 * 1024):.1f} MB"
        )

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
        self.topology_gzip = None
        self.topology_etag = None


class GraphRegistry:
    """LRU-bounded set of loaded CityGraphs, keyed by city id."""

    def __init__(self, redis_client=None, max_loaded: int = 3):
        self.redis = redis_client
        self.max_loaded = max_loaded
        self._graphs: "OrderedDict[str, CityGraph]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, city: City) -> CityGraph:
        # Hold the registry lock only for the fast bookkeeping; the (slow) graph
        # load happens outside it under the per-graph lock, so different cities
        # can load concurrently and repeated hits to one city don't double-load.
        with self._lock:
            cg = self._graphs.get(city.id)
            if cg is None:
                cg = CityGraph(city, self.redis)
                self._graphs[city.id] = cg
                while len(self._graphs) > self.max_loaded:
                    old_id, old = self._graphs.popitem(last=False)
                    logger.info(f"[GRAPH] Evicting '{old_id}' (LRU)")
                    old.unload()
            else:
                self._graphs.move_to_end(city.id)
        cg.ensure_loaded()
        return cg

    def loaded_ids(self) -> list[str]:
        return list(self._graphs.keys())


class OsrmRegistry:
    """One OsrmRouter per city, pointing at that city's OSRM container."""

    def __init__(self):
        self._routers: dict[str, OsrmRouter] = {}

    def get(self, city: City) -> OsrmRouter:
        r = self._routers.get(city.id)
        if r is None:
            r = OsrmRouter(host=city.osrm_host, port=city.osrm_port)
            self._routers[city.id] = r
        return r
