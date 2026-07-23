"""
Per-city graph + OSRM registries.

Each city's walk graph (osm_data/<city>/walk_graph_arrays.npz) is loaded on
demand into a CityGraph that holds array-native topology, the OSM-annotation
lookup maps, and the cached GTB2 binary blob for /api/graph-topology. An LRU
bound keeps memory in check when several cities are active. OSRM routers are
likewise created per city, each pointing at that city's OSRM container.

Memory model: everything resident is a numpy array or a numpy-backed sorted
lookup (IntMap/IntPairMap). The old dict/list-of-lists representation cost
~2.5GB for NYC alone; the arrays cost ~150MB. The list form still exists
transiently during load because topology_etag is the sha256 of the exact JSON
the legacy path produced — baked blocks stamp that etag, so it must never
change encoding.
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
from graph_arrays import build_csr_adjacency
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


class IntMap:
    """Immutable int→int map over sorted numpy arrays (dict-compatible .get()).

    Replaces the per-city osm_to_graph_idx dict (~60MB of boxed ints for NYC)
    with two flat arrays (~8MB) + binary search.
    """

    __slots__ = ("_keys", "_values")

    def __init__(self, keys: np.ndarray, values: np.ndarray):
        order = np.argsort(keys, kind="stable")
        self._keys = keys[order]
        self._values = values[order]

    def get(self, key, default=None):
        i = np.searchsorted(self._keys, key)
        if i < len(self._keys) and self._keys[i] == key:
            return int(self._values[i])
        return default

    def __len__(self):
        return len(self._keys)


class IntPairMap:
    """Immutable (int, int)→int map, keys packed into uint64 (a<<32 | b).

    Replaces node_pair_to_edge (~450MB of tuple-keyed dict for NYC) with flat
    arrays (~50MB). Duplicate pairs keep the LAST inserted value — matching the
    legacy dict-assignment semantics, so parallel edges resolve identically.
    """

    __slots__ = ("_keys", "_values")

    def __init__(self, a: np.ndarray, b: np.ndarray, values: np.ndarray):
        packed = (a.astype(np.uint64) << np.uint64(32)) | b.astype(np.uint64)
        # stable sort + keep the last entry of each duplicate-key run
        order = np.argsort(packed, kind="stable")
        keys = packed[order]
        vals = values[order]
        if len(keys):
            last_of_run = np.r_[keys[1:] != keys[:-1], True]
            keys = keys[last_of_run]
            vals = vals[last_of_run]
        self._keys = keys
        self._values = vals

    def get(self, pair, default=None):
        key = (np.uint64(pair[0]) << np.uint64(32)) | np.uint64(pair[1])
        i = np.searchsorted(self._keys, key)
        if i < len(self._keys) and self._keys[i] == key:
            return int(self._values[i])
        return default

    def __len__(self):
        return len(self._keys)


class NodeAdjacency:
    """CSR node→incident-edge-ids view with the old list-of-lists interface.

    adj[node] returns a numpy slice of edge ids (use len(), not truthiness).
    Self-edges appear once (matching the legacy build), so station-network
    snapping keeps returning each station's own edge.
    """

    __slots__ = ("_indptr", "_edges")

    def __init__(self, indptr: np.ndarray, edges: np.ndarray):
        self._indptr = indptr
        self._edges = edges

    def __getitem__(self, node: int) -> np.ndarray:
        return self._edges[self._indptr[node]:self._indptr[node + 1]]

    def __len__(self):
        return len(self._indptr) - 1


def _build_node_adjacency(edge_ends: np.ndarray, n_nodes: int) -> NodeAdjacency:
    """CSR adjacency over edge endpoint POSITIONS, self-edges recorded once."""
    u = edge_ends[:, 0]
    v = edge_ends[:, 1]
    self_edge = u == v
    endpoints = np.concatenate([u, v[~self_edge]]).astype(np.int64)
    edge_pos = np.concatenate([
        np.arange(len(u), dtype=np.int32),
        np.arange(len(u), dtype=np.int32)[~self_edge],
    ])
    # lexsort: group by node, ascending edge id within each node — the same
    # order the legacy per-edge append loop produced (snap_point_to_edge
    # returns the FIRST incident edge, so ordering is behavior).
    order = np.lexsort((edge_pos, endpoints))
    counts = np.bincount(endpoints, minlength=n_nodes)
    indptr = np.zeros(n_nodes + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    return NodeAdjacency(indptr, edge_pos[order])


def encode_topology_bin(node_coords: np.ndarray, edge_ends: np.ndarray,
                        edge_block_id=None, n_blocks: int = 0) -> bytes:
    """Assemble the GTB2 binary topology blob (pure; see topology_binary).

    [b'GTB2', u32 nNodes, u32 nEdges, u32 nBlocks] + int32 coords (deg×1e7,
    lat/lon pairs) + uint32 edge ends (from/to pairs) + — when edge_block_id is
    given — a trailing int32[nEdges] block-id section (−1 = unmapped).
    """
    coords = np.rint(node_coords.reshape(-1) * 1e7).astype("<i4")
    ends = edge_ends.astype("<u4").reshape(-1)
    has_blocks = edge_block_id is not None
    header = struct.pack(
        "<4sIII", b"GTB2", len(node_coords), len(edge_ends),
        n_blocks if has_blocks else 0
    )
    parts = [header, coords.tobytes(), ends.tobytes()]
    if has_blocks:
        parts.append(np.asarray(edge_block_id, dtype="<i4").tobytes())
    return b"".join(parts)


class CityGraph:
    """Lazily-loaded array-native topology + derived lookups for one city+network."""

    def __init__(self, city: City, redis_client=None, network: str = "streets"):
        self.city = city
        self.network = network
        self.provider = PythonRouter(data_dir=city.data_dir, redis_client=redis_client)
        self._loaded = False
        # Serializes concurrent first-loads of this city so two requests for the
        # same unloaded city don't both build it. threading.Lock is
        # gevent-patched at runtime.
        self._load_lock = threading.Lock()

        self.n_nodes: int = 0
        self.n_edges: int = 0
        self.node_coords: np.ndarray | None = None   # [n, 2] float64 (lat, lon)
        self.edge_ends: np.ndarray | None = None     # [e, 2] int32 (from_pos, to_pos)
        self.node_adj: NodeAdjacency | None = None
        self.osm_to_graph_idx: IntMap | None = None
        self.node_pair_to_edge: IntPairMap | None = None
        # JSON topology is served only for station networks (tiny, names matter).
        # Street networks serve the GTB2 binary; their JSON exists only long
        # enough at load time to compute the etag baked blocks are stamped with.
        self.topology_json: str | None = None
        self.topology_etag: str | None = None
        self.topology_bin: bytes | None = None
        self.topology_bin_gz: bytes | None = None
        self.topology_bin_br: bytes | None = None
        # Lazily-built kdtrees: node tree for point-vote snapping, edge-midpoint
        # tree for the vote migration (kept off the hot load path).
        self._node_tree: cKDTree | None = None
        self._edge_mid_tree: cKDTree | None = None

        # Optional block layer: edge_block_id[edge_id] → block_id (−1 unmapped),
        # loaded from osm_data/<city>/edge_blocks_<network>.npy when present and
        # built against THIS topology. None ⇒ the map falls back to the edge
        # heatmap. blocks_version stamps the response for the client stale-guard.
        self.edge_block_id = None
        self.n_blocks: int = 0
        self.blocks_version: str | None = None

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

        # topology_etag is the sha256 of the exact legacy JSON encoding of
        # (nodes, [from, to, name] edges). Baked block artifacts are stamped
        # with it, so the encoding is a compatibility contract. The string is
        # retained only for station networks (tiny; served as JSON); street
        # networks hash it and let it go.
        edges_slim = [[e[0], e[1], e[2]] for e in edges]
        topology_json = json.dumps({"nodes": nodes, "edges": edges_slim})
        topology_etag = '"' + hashlib.sha256(topology_json.encode()).hexdigest()[:16] + '"'
        keep_json = self.network in STATION_NETWORKS

        # Array-native residents; the list form dies with this frame.
        node_coords = np.asarray(nodes, dtype=np.float64).reshape(-1, 2)
        edge_ends = (
            np.asarray([(e[0], e[1]) for e in edges], dtype=np.int32).reshape(-1, 2)
        )

        # osm id → node position, as sorted flat arrays
        osm_map = data.get("osm_to_graph_idx", {})
        osm_keys = np.fromiter(osm_map.keys(), dtype=np.int64, count=len(osm_map))
        osm_vals = np.fromiter(osm_map.values(), dtype=np.int32, count=len(osm_map))

        self.n_nodes = len(node_coords)
        self.n_edges = len(edge_ends)
        self.node_coords = node_coords
        self.edge_ends = edge_ends
        self.node_adj = _build_node_adjacency(edge_ends, self.n_nodes)
        self.osm_to_graph_idx = IntMap(osm_keys, osm_vals)
        # Legacy insertion order was per-edge interleaved — (a,b) then (b,a) for
        # edge 0, then edge 1, … — with dict assignment (last wins). Interleave
        # the arrays the same way so duplicate pairs resolve identically.
        pair_a = np.column_stack([edge_ends[:, 0], edge_ends[:, 1]]).reshape(-1)
        pair_b = np.column_stack([edge_ends[:, 1], edge_ends[:, 0]]).reshape(-1)
        pair_val = np.repeat(np.arange(self.n_edges, dtype=np.int32), 2)
        self.node_pair_to_edge = IntPairMap(pair_a, pair_b, pair_val)
        self.topology_json = topology_json if keep_json else None
        self.topology_etag = topology_etag
        self._node_tree = None
        self._edge_mid_tree = None  # rebuilt lazily for the new edge set
        self._loaded = True
        self._load_edge_blocks()

        logger.info(
            f"[GRAPH] '{self.city.id}:{self.network}' loaded: {self.n_nodes} nodes, "
            f"{self.n_edges} edges"
            + (f", {self.n_blocks} blocks" if self.has_blocks else "")
        )

    def _load_edge_blocks(self):
        """Load the edge→block mapping for this network if present + current.

        Validates the mapping's stamped topology_etag against the just-loaded
        graph so a mapping left over from a previous graph build is ignored (the
        map then serves the edge heatmap) rather than mis-coloring blocks against
        the wrong edge ids. Built by streetscape_blocks/build_blocks_graph_first.py."""
        self.edge_block_id = None
        self.n_blocks = 0
        self.blocks_version = None
        base = os.path.join(os.path.dirname(__file__), self.city.data_dir)
        npy = os.path.join(base, f"edge_blocks_{self.network}.npy")
        meta_path = os.path.join(base, f"edge_blocks_{self.network}.json")
        if not (os.path.exists(npy) and os.path.exists(meta_path)):
            return
        try:
            meta = json.load(open(meta_path))
            if meta.get("topology_etag") != self.topology_etag:
                logger.warning(
                    f"[GRAPH] edge_blocks for '{self.city.id}:{self.network}' stamped "
                    f"{meta.get('topology_etag')} != live {self.topology_etag}; "
                    "ignoring (serving edge heatmap)")
                return
            arr = np.load(npy)
            if len(arr) != self.n_edges:
                logger.warning(f"[GRAPH] edge_blocks length {len(arr)} != "
                               f"{self.n_edges} edges; ignoring")
                return
            self.edge_block_id = arr
            self.n_blocks = int(meta.get("n_blocks", int(arr.max()) + 1 if len(arr) else 0))
            self.blocks_version = meta.get("blocks_sha256")
        except Exception as e:
            logger.warning(f"[GRAPH] failed to load edge_blocks "
                           f"'{self.city.id}:{self.network}': {e}")

    @property
    def has_blocks(self) -> bool:
        return self.edge_block_id is not None

    def blocks_stamp(self) -> int | None:
        """Cheap block-set version usable WITHOUT loading the graph: the baked
        mapping's mtime (a stat). Block ids renumber on every re-bake under the
        SAME topology etag and revision, so anything HTTP-cached that carries
        block arrays must fold this into its validator — else a re-bake 304s
        clients onto a body whose block ids color the wrong polygons."""
        npy = os.path.join(os.path.dirname(__file__), self.city.data_dir,
                           f"edge_blocks_{self.network}.npy")
        try:
            return int(os.stat(npy).st_mtime)
        except OSError:
            return None

    def topology_binary(self) -> bytes:
        """Compact little-endian binary topology — the mobile-safe wire format.

        Layout (GTB2): 16-byte header [magic 'GTB2', uint32 nNodes, uint32
        nEdges, uint32 nBlocks], then nNodes×2 int32 (lat, lon as degrees×1e7,
        ~1cm precision), then nEdges×2 uint32 (from_idx, to_idx), then — only
        when nBlocks > 0 — a trailing int32[nEdges] edge_block_id section
        (−1 = unmapped edge). Edge names are omitted (the client reverse-
        geocodes street tooltips instead). This exists so a phone decodes an
        ArrayBuffer rather than JSON.parse-ing the ~150MB NYC topology string,
        which OOM-crashes mobile Safari. Built once per load and cached; ~37MB raw
        → ~16MB gzipped by nginx, so it clears Cloud Run's 32MB response cap. The
        edges array index is the canonical edge id (matches /api/graph-votes)."""
        self.ensure_loaded()
        if self.topology_bin is None:
            self.topology_bin = encode_topology_bin(
                self.node_coords, self.edge_ends,
                self.edge_block_id if self.has_blocks else None,
                n_blocks=self.n_blocks if self.has_blocks else 0,
            )
        return self.topology_bin

    def topology_binary_gz(self) -> bytes:
        """Pre-gzipped GTB2 blob, built once per load and served verbatim.

        nginx used to gzip the ~37MB NYC blob PER REQUEST (~0.4 core-seconds
        each) — a 30-tenant join wave burned ~12 core-seconds and starved every
        cheap endpoint on the instance. Level 6 once (~16MB) beats level 1
        every time. zlib releases the GIL, so the one-time build doesn't stall
        the gevent hub. Serving Content-Encoding: gzip directly also stops
        nginx weakening the ETag (W/...), so conditional requests 304 again."""
        if self.topology_bin_gz is None:
            import gzip as _gzip
            self.topology_bin_gz = _gzip.compress(self.topology_binary(), 6)
        return self.topology_bin_gz

    def topology_binary_br(self) -> bytes | None:
        """Pre-brotli'd GTB2 blob — ~17% smaller than the gzip twin AND faster
        to build (NYC: 15.6MB br q5 in 0.6s vs 18.8MB gzip-6 in 1.4s, measured
        on the 50MB blob). Every current browser sends Accept-Encoding: br, so
        this is what first-time visitors actually download; gzip stays as the
        fallback. Returns None when the brotli package is unavailable so the
        caller degrades to gzip instead of erroring."""
        if self.topology_bin_br is None:
            try:
                import brotli
            except ImportError:
                return None
            self.topology_bin_br = brotli.compress(self.topology_binary(), quality=5)
        return self.topology_bin_br

    def _ensure_node_tree(self) -> cKDTree | None:
        self.ensure_loaded()
        if self._node_tree is None and self.n_nodes:
            self._node_tree = cKDTree(self.node_coords)
        return self._node_tree

    def snap_point_to_edge(self, lat: float, lon: float) -> list[int]:
        """Snap a lat/lon point to the nearest graph edge via closest node."""
        tree = self._ensure_node_tree()
        if tree is None:
            return []
        _, best_idx = tree.query([lat, lon])
        incident = self.node_adj[int(best_idx)]
        if len(incident):
            return [int(incident[0])]
        return []

    def edge_midpoint(self, edge_id: int) -> tuple[float, float] | None:
        """(lat, lon) midpoint of an edge — the migration anchor stored per vote."""
        self.ensure_loaded()
        if edge_id < 0 or edge_id >= self.n_edges:
            return None
        a, b = self.edge_ends[edge_id]
        mid = (self.node_coords[a] + self.node_coords[b]) / 2.0
        return (float(mid[0]), float(mid[1]))

    def edge_midpoints(self, edge_ids) -> dict[int, tuple[float, float]]:
        """{edge_id: (lat, lon)} midpoints for many edges (skips out-of-range ids)."""
        self.ensure_loaded()
        out: dict[int, tuple[float, float]] = {}
        for eid in edge_ids:
            if 0 <= eid < self.n_edges:
                a, b = self.edge_ends[eid]
                mid = (self.node_coords[a] + self.node_coords[b]) / 2.0
                out[eid] = (float(mid[0]), float(mid[1]))
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
            if not self.n_edges:
                return None
            mids = (self.node_coords[self.edge_ends[:, 0]] +
                    self.node_coords[self.edge_ends[:, 1]]) / 2.0  # [E, 2]
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
        self.n_nodes = 0
        self.n_edges = 0
        self.node_coords = None
        self.edge_ends = None
        self.node_adj = None
        self.osm_to_graph_idx = None
        self.node_pair_to_edge = None
        self.topology_json = None
        self.topology_etag = None
        self.topology_bin = None
        self.topology_bin_gz = None
        self.topology_bin_br = None
        self._node_tree = None
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

    A single merged OSRM serves every city (set OSRM_URL) → one shared router
    for all cities; without OSRM_URL (local dev) a per-city host from cities.py.
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
