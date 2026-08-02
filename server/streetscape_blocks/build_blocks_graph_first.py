#!/usr/bin/env python3
"""Graph-first block builder — grouping first, geometry from the groups.

Replaces the five-stage pipeline (build_blocks_generic → build_node_blocks →
build_edge_blocks → build_foot_blocks → merge_degenerate_blocks) with ONE pass
that inverts its core premise. The old pipeline generated polygons from drive
centerlines and then tried to map graph edges INTO them geometrically
(containment + nearest-within-30m), which left two systematic defects:
edges no polygon claimed (heatmap gaps: 2,450 on test-cp) and edges assigned
to polygons they don't even intersect (10,336 nearest-snapped on test-cp).

Here membership is decided FIRST, topologically, for every edge — then each
block's polygon is generated from its own member edges, so coverage and
edge∩polygon overlap hold by construction:

  1. JUNCTION CLUSTERS: nodes with unique-neighbour degree ≥ 3, union-found
     within LINK_M, oversized clusters bisected along their principal axis.
  2. EDGE GROUPING (total): an edge whose endpoints share a cluster belongs to
     that junction; a short (≤ NODE_CAPTURE_LEN_M) edge between two clusters
     is a crosswalk stub and joins the nearer one; every other edge joins a
     CORRIDOR — connected components linked through non-junction endpoints,
     i.e. one component per path segment between junctions.
  3. DEGENERACY + EQUIVALENCE FIXPOINT (topological): four rules iterate to
     convergence — A) corridors with the SAME two endpoint clusters merge IF
     they run alongside each other (both sidewalks + the roadway of one street
     segment = one block, but NOT two different routes between the same pair
     of intersections: each group is resolved anchor-first, and a corridor
     joins only while it never strays more than PARALLEL_MAX_SEP_M from the
     group's longest corridor — the roadway, where there is one); V1)
     driveway-class stubs (extent ≤ STUB_MAX_M, one junction) melt into their
     junction; V2) a junction touching ≤ 1 corridor isn't a junction and
     dissolves into that corridor; B) clusters with the SAME incident-corridor
     set (≥ 2 corridors) merge (an over-split intersection becomes one
     junction). A and B feed each other, hence the loop; every rule strictly
     shrinks a count, so it terminates.
  4. GEOMETRY FROM MEMBERSHIP: a junction cell is the CONVEX HULL of its
     member nodes (⊕ pad) unioned with its captured edges' tubes — a compact
     intersection footprint, not a bulbous disc union — then bisector-trimmed
     (Voronoi-style) against overlapping neighbour cells UNCONDITIONALLY, so
     abutting junctions get a straight shared boundary instead of stacked
     blobs; a cell left with < 1 m² on its own side of a bisector is
     geometrically one intersection with that neighbour and is ABSORBED into
     it. A corridor is the union of its member-edge tubes (per-class
     half-width) minus the junction cells, so corridor-vs-junction stays
     disjoint (junctions win). CORRIDOR-VS-CORRIDOR overlaps (parallel
     streets within buffer reach, grade-separated crossings) are resolved by
     the same geometry-wins rule: each overlap region is claimed by the
     corridor whose member edges carry more length inside it and cut from the
     other; a corridor cut to < 1 m² merges into the winner. The final
     polygon set is audited PAIRWISE ACROSS ALL CLASSES — nothing ships with
     an overlap ≥ 1 m². Membership follows the final geometry: a corridor
     edge left entirely inside junction cells is REASSIGNED to the cell
     containing its midpoint, an edge cut away from its corridor moves to the
     polygon holding its midpoint (clipped tube graft as last resort), and a
     captured edge a cut stranded outside its own cell is RE-HOMED to the
     cell or corridor that holds it now — so edge∩polygon overlap is total.
  5. CONTIGUITY: the shipped geometry has the last word on what one block is.
     Each block's polygon parts are bucketed into runs no more than
     PART_GAP_M apart and EVERY BUCKET BECOMES ITS OWN BLOCK, with members
     following their own geometry — so a block is always one place, and one
     click can never vote in two. Buckets are disjoint subsets of an
     already-audited polygon, so splitting cannot create an overlap.

Outputs (same contract the server + tippecanoe already consume):
  output/blocks_final_<city>.geojson       display polygons, dense block_id
  osm_data/<city>/edge_blocks_<net>.npy    edge_id → block_id (−1 only for
                                           self-edges, which have no extent)
  osm_data/<city>/edge_blocks_<net>.json   meta: n_blocks, topology_etag,
                                           blocks_sha256 + coverage/overlap audit

Run in the SERVER venv:
  CITY=test-cp NETWORK=streets ./env/bin/python streetscape_blocks/build_blocks_graph_first.py
"""
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.abspath(os.path.join(_HERE, ".."))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import shapely  # noqa: E402
from shapely import STRtree, LineString, union_all, buffer as shp_buffer  # noqa: E402
from shapely.geometry import Polygon, mapping  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph, STATION_NETWORKS, load_station_graph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")

# Junction clustering (same knobs/values as build_node_blocks.py).
LINK_M = float(os.environ.get("NODE_CLUSTER_LINK_M", "18"))
MAX_EXTENT_M = float(os.environ.get("NODE_CLUSTER_MAX_EXTENT_M", "40"))
# Disc radius around each junction-cluster member node (the cell is the union
# of these discs + the captured edges' tubes). 8 m keeps junction blobs in
# proportion to footway tubes (4 m half-width); 10 read as chunky gloops on
# the central-park test map.
NODE_R_M = float(os.environ.get("NODE_BLOCK_RADIUS_M", "8"))

# Edge grouping / degeneracy (same knobs as the old bake + merge).
NODE_CAPTURE_LEN_M = float(os.environ.get("NODE_CAPTURE_LEN_M", "30"))
STUB_MAX_M = float(os.environ.get("STUB_MAX_M", "25"))
MAX_ROUNDS = int(os.environ.get("MERGE_MAX_ROUNDS", "25"))
STUBBY_CLASSES = {"service", "footway", "path", "steps", "cycleway",
                  "pedestrian", "track", ""}

# Oversized-block split: a corridor whose bbox diagonal exceeds
# SPLIT_MAX_EXTENT_M is cut in two along its THINNEST crossing — the
# perpendicular to its principal axis, placed at whichever station in the
# middle band has the least polygon width — and each half re-checked, so a
# merged park loop or a kilometres-long greenway becomes a chain of
# street-scale blocks instead of one giant vote target. 400 m ≈ p99.5 of the
# nyc corridor-extent distribution (a long Manhattan block face is ~280 m),
# so ordinary street corridors are untouched.
SPLIT_MAX_EXTENT_M = float(os.environ.get("SPLIT_MAX_EXTENT_M", "400"))
SPLIT_BAND = (0.35, 0.65)      # candidate cut stations, fraction of the span
SPLIT_STATIONS = 13            # stations sampled inside the band
SPLIT_MAX_DEPTH = 8            # ≤ 2^8 pieces from one block (backstop)

# Contiguity. Rule A merges corridors that share both endpoint junctions —
# right for the two sides + roadway of ONE street segment, wrong for two
# DIFFERENT routes between the same pair of intersections (a park loop's two
# arms, a street and the service road that rejoins it, a path either side of a
# median strip). Those merges shipped blocks made of ribbons 70–230 m apart:
# one click voted in two places at once and lit two disconnected shapes.
# PARALLEL_MAX_SEP_M is how far a corridor may stray from its group's anchor
# and still count as the same street. Measured against the anchor — the
# group's LONGEST corridor, normally the roadway lying between the sidewalks —
# so the bound is half a right-of-way, not a full one: 30 m clears NYC's
# widest (Park Avenue, ~43 m building line to building line) with room to
# spare while rejecting the East River Park arms at 72 m.
PARALLEL_MAX_SEP_M = float(os.environ.get("PARALLEL_MAX_SEP_M", "30"))
HUG_SAMPLE_M = 5.0             # densify step for the stray-distance measure

# Backstop: a block whose shipped polygon falls into pieces further apart than
# PART_GAP_M is split into one block per piece (§5). Catches fragmentation the
# geometry passes cause after grouping is settled — a corridor sliced through
# by a grade-separated crossing's tube, or by a junction cell mid-span. 20 m is
# wider than any gap a junction cell can open in a street corridor and far
# narrower than the separations that read as two places.
PART_GAP_M = float(os.environ.get("PART_GAP_M", "20"))

# Corridor tube half-widths per road class (from build_blocks_generic.py,
# NYC-calibrated; classes are universal). Scaled by WIDTH_SCALE for sweeps.
WIDTH_SCALE = float(os.environ.get("WIDTH_SCALE", "1.0"))
HALF_WIDTH = {
    "motorway": 18.0, "motorway_link": 12.0, "trunk": 16.0, "trunk_link": 11.0,
    "primary": 14.0, "primary_link": 10.0, "secondary": 11.0, "secondary_link": 9.0,
    "tertiary": 9.0, "tertiary_link": 8.0, "residential": 8.0, "living_street": 7.0,
    "unclassified": 8.0, "service": 6.0, "pedestrian": 6.0, "footway": 4.0,
    "path": 4.0, "cycleway": 4.0, "steps": 4.0,
}
DEFAULT_HALF_WIDTH = 8.0


class UnionFind:
    def __init__(self, n: int):
        self.parent = np.arange(n)

    def find(self, a: int) -> int:
        p = self.parent
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def split_oversized(xy: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Bisect clusters whose bbox diagonal exceeds MAX_EXTENT_M along their
    principal axis (from build_node_blocks.py) so members fit the radius cap."""
    next_label = int(labels.max()) + 1 if len(labels) else 0
    queue = list(np.unique(labels))
    while queue:
        lb = queue.pop()
        idx = np.where(labels == lb)[0]
        if len(idx) < 2:
            continue
        pts = xy[idx]
        span = pts.max(axis=0) - pts.min(axis=0)
        if float(np.hypot(*span)) <= MAX_EXTENT_M:
            continue
        centered = pts - pts.mean(axis=0)
        cov = centered.T @ centered
        _, vecs = np.linalg.eigh(cov)
        proj = centered @ vecs[:, -1]
        half = idx[proj > np.median(proj)]
        if len(half) == 0 or len(half) == len(idx):
            half = idx[np.argsort(proj)[len(idx) // 2:]]
        labels[half] = next_label
        queue.append(lb)
        queue.append(next_label)
        next_label += 1
    return labels


def _polyparts(geom) -> list:
    """Polygonal parts of any geometry (lines/points dropped)."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return [geom]
    if geom.geom_type == "GeometryCollection":
        out = []
        for g in geom.geoms:
            out.extend(_polyparts(g))
        return out
    return []


def _clean(geom):
    """Valid, purely-polygonal geometry. Difference/intersection chains emit
    self-touching rings and mixed-dimension collections — make_valid itself
    throws on the latter ("Overlay input is mixed-dimension"), so strip to
    polygonal parts FIRST, validate each part (buffer(0) as fallback), then
    union."""
    if geom is None or geom.is_empty:
        return geom
    parts = _polyparts(geom)
    if not parts:
        return Polygon()
    fixed = []
    for p in parts:
        if not p.is_valid:
            try:
                p = shapely.make_valid(p)
            except shapely.errors.GEOSException:
                p = p.buffer(0)
            fixed.extend(_polyparts(p))
        else:
            fixed.append(p)
    if not fixed:
        return Polygon()
    try:
        out = union_all(fixed)
    except shapely.errors.GEOSException:
        out = union_all([p.buffer(0) for p in fixed])
    parts = _polyparts(out)
    if not parts:
        return Polygon()
    return parts[0] if len(parts) == 1 else union_all(parts)


def majority(values):
    """Most common non-empty value, or None."""
    counts = {}
    for v in values:
        if v in (None, "", "None"):
            continue
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main():
    t0 = time.time()
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    nodes = np.asarray(g.node_coords, dtype=np.float64)    # [N,2] = (lat, lon)
    n_nodes = len(nodes)
    ends = np.asarray(g.edge_ends, dtype=np.int64)         # [E,2] = (u, v)
    # Array-native CityGraph drops per-edge name/highway at load; pull them
    # from the same provider output the topology (and its etag) is built from,
    # so the edge order here IS the etag contract's order.
    if NETWORK in STATION_NETWORKS:
        data = load_station_graph(NETWORK)
    else:
        data = g.provider.get_graph_for_bbox(*city.bbox)
    if len(data["edges"]) != len(ends):
        raise RuntimeError(f"provider edges ({len(data['edges'])}) != loaded "
                           f"topology edges ({len(ends)})")
    rclass = np.array([str(e[3]) if len(e) > 3 else "" for e in data["edges"]])
    names = [e[2] if len(e) > 2 else None for e in data["edges"]]
    del data
    n_edges = len(ends)
    not_self = ends[:, 0] != ends[:, 1]
    print(f"[gf] {CITY}:{NETWORK} — {n_nodes} nodes, {n_edges} edges "
          f"({int((~not_self).sum())} self), etag={g.topology_etag}", flush=True)

    # Local equirectangular metres frame (shared convention with the old scripts).
    s, w, n, e = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s + n) / 2))
    node_xy = np.column_stack(((nodes[:, 1] - w) * mlon, (nodes[:, 0] - s) * mlat))
    evec = node_xy[ends[:, 1]] - node_xy[ends[:, 0]]
    elen = np.hypot(evec[:, 0], evec[:, 1])

    # ── 1. Junction clusters ────────────────────────────────────────────────
    und = np.unique(np.sort(ends[not_self], axis=1), axis=0)
    deg = np.bincount(und.ravel(), minlength=n_nodes)
    junctions = np.where(deg >= 3)[0]
    jxy = node_xy[junctions]
    uf_j = UnionFind(len(junctions))
    for a, b in cKDTree(jxy).query_pairs(r=LINK_M, output_type="ndarray"):
        uf_j.union(a, b)
    labels = np.array([uf_j.find(i) for i in range(len(junctions))])
    labels = split_oversized(jxy, labels)
    uniq, labels = np.unique(labels, return_inverse=True)
    n_clusters = len(uniq)

    node_cluster = np.full(n_nodes, -1, dtype=np.int64)
    node_cluster[junctions] = labels
    cl_members = [junctions[labels == k] for k in range(n_clusters)]
    centroids = np.array([node_xy[m].mean(axis=0) for m in cl_members])
    print(f"[gf] {len(junctions)} junction nodes → {n_clusters} clusters "
          f"({time.time()-t0:.0f}s)", flush=True)

    # ── 2. Edge grouping (total, topological) ───────────────────────────────
    # group encoding: cluster k → k ; corridor c → n_clusters + c ; self → −1
    cu = node_cluster[ends[:, 0]]
    cv = node_cluster[ends[:, 1]]
    edge_cluster = np.full(n_edges, -1, dtype=np.int64)

    same = not_self & (cu >= 0) & (cu == cv)
    edge_cluster[same] = cu[same]

    cross_short = not_self & (cu >= 0) & (cv >= 0) & (cu != cv) & (elen <= NODE_CAPTURE_LEN_M)
    if cross_short.any():
        mid = (node_xy[ends[cross_short, 0]] + node_xy[ends[cross_short, 1]]) / 2.0
        du = np.hypot(*(centroids[cu[cross_short]] - mid).T)
        dv = np.hypot(*(centroids[cv[cross_short]] - mid).T)
        edge_cluster[cross_short] = np.where(du <= dv, cu[cross_short], cv[cross_short])

    corridor_mask = not_self & (edge_cluster < 0)
    corr_edges = np.where(corridor_mask)[0]
    # Union-find over NODES, linking only through non-junction endpoints; an
    # edge with a junction endpoint hangs off its other end's component.
    uf_n = UnionFind(n_nodes)
    for eid in corr_edges:
        u, v = ends[eid]
        if node_cluster[u] < 0 and node_cluster[v] < 0:
            uf_n.union(u, v)
    comp_key = np.empty(len(corr_edges), dtype=np.int64)
    for i, eid in enumerate(corr_edges):
        u, v = ends[eid]
        if node_cluster[u] < 0:
            comp_key[i] = uf_n.find(u)
        elif node_cluster[v] < 0:
            comp_key[i] = uf_n.find(v)
        else:
            comp_key[i] = n_nodes + eid          # both-junction long edge: singleton
    comp_ids, comp_inv = np.unique(comp_key, return_inverse=True)
    n_corr = len(comp_ids)
    corridors: list[list[int]] = [[] for _ in range(n_corr)]
    for i, eid in enumerate(corr_edges):
        corridors[comp_inv[i]].append(int(eid))
    print(f"[gf] grouped: {int(same.sum())} same-cluster + "
          f"{int(cross_short.sum())} crosswalk stubs + {len(corr_edges)} corridor "
          f"edges in {n_corr} corridors ({time.time()-t0:.0f}s)", flush=True)

    # ── Corridor centreline geometry (shared by §3's hug test and §4's
    # overlap arbitration; invalidated whenever a corridor's members change) ──
    def edge_lines(eids):
        return [LineString([node_xy[ends[eid, 0]], node_xy[ends[eid, 1]]])
                for eid in eids]

    lines_cache: dict[int, tuple] = {}

    def corr_geom(c):
        """(centrelines, sample points) for corridor c, in the metric frame.
        The sample points densify the centrelines to HUG_SAMPLE_M so a single
        long straight edge cannot hide its own middle from a distance probe."""
        geom = lines_cache.get(c)
        if geom is None:
            lines = shapely.multilinestrings(
                [list(ln.coords) for ln in edge_lines(corridors[c])])
            pts = shapely.points(shapely.get_coordinates(
                shapely.segmentize(lines, HUG_SAMPLE_M)))
            lines_cache[c] = geom = (lines, pts)
        return geom

    def member_lines(c):
        return corr_geom(c)[0]

    def strays(c, anchor):
        """Farthest any point of corridor c gets from the anchor's centrelines
        (a directed Hausdorff distance) — small only when c runs alongside the
        anchor for its whole length."""
        pts = corr_geom(c)[1]
        if len(pts) == 0:
            return math.inf
        return float(shapely.distance(pts, member_lines(anchor)).max())

    # ── 3. Degeneracy + equivalence fixpoint ────────────────────────────────
    # Four rules iterate to convergence — each strictly shrinks the corridor
    # or cluster count, so termination is guaranteed (MAX_ROUNDS is a
    # backstop). Each round does ONE O(E) incidence sweep and groups by
    # frozenset signature (dict buckets, no pairwise comparisons):
    #   A   parallel corridors: corridors with the SAME two endpoint clusters
    #       merge — the two sidewalks + roadway of one street segment (or the
    #       two sides of a path loop) become ONE block;
    #   V1  stub: a driveway-class corridor ≤ STUB_MAX_M touching exactly one
    #       junction melts into it;
    #   V2  fake junction: a cluster touching ≤ 1 live corridor dissolves
    #       into that corridor;
    #   B   equivalent junctions: clusters with the SAME incident-corridor
    #       set (≥ 2 corridors) merge — an intersection the link/extent
    #       clustering split into several sub-clusters becomes one junction.
    # A and B feed each other (merging sidewalk pairs makes the two corner
    # clusters' corridor sets equal; merging those clusters keys more
    # corridors onto the same endpoint pair), hence the loop.
    corr_alive = [True] * n_corr
    cluster_alive = [len(m) > 0 for m in cl_members]

    stubs_merged = 0
    junctions_dissolved = 0
    corridors_merged = 0
    clusters_merged = 0
    rounds = 0
    # How far each rule-A candidate strayed from its anchor, in 10 m buckets,
    # sampled on the first round only (later rounds re-evaluate the same
    # rejected pairs and would inflate the tail). This is the evidence for
    # where PARALLEL_MAX_SEP_M belongs; it is stamped into the meta.
    GAP_BUCKETS = 12
    gap_hist = [0] * GAP_BUCKETS
    for _ in range(MAX_ROUNDS):
        rounds += 1
        changed = False

        # One incidence sweep: corridor → set of live clusters it touches.
        corr_cl: list[set[int]] = [set() for _ in range(n_corr)]
        for c in range(n_corr):
            if not corr_alive[c]:
                continue
            for eid in corridors[c]:
                for nd in ends[eid]:
                    k = int(node_cluster[nd])
                    if k >= 0 and cluster_alive[k]:
                        corr_cl[c].add(k)

        # A) Same endpoint-cluster pair → one corridor, IF they run together.
        # Sharing two junctions makes corridors candidates, not duplicates: the
        # two arms of a park loop share their endpoints too. So each group is
        # resolved anchor-first — its longest corridor claims every corridor
        # that never strays more than PARALLEL_MAX_SEP_M from it, and whatever
        # is left re-anchors and forms its own block. Anchoring on the longest
        # (the roadway, where there is one) measures each sidewalk against the
        # carriageway between them rather than across the whole right-of-way.
        # (Merging same-key corridors leaves the survivor's cluster set == the
        # key, so corr_cl stays valid for the passes below.)
        by_ends: dict[frozenset, list[int]] = {}
        for c in range(n_corr):
            if corr_alive[c] and len(corr_cl[c]) == 2:
                by_ends.setdefault(frozenset(corr_cl[c]), []).append(c)
        for cs in by_ends.values():
            if len(cs) < 2:
                continue
            pending = sorted(cs, key=lambda c: -member_lines(c).length)
            while len(pending) > 1:
                root, rest = pending[0], pending[1:]
                # min of the two directed distances: a sidewalk that covers
                # only part of the segment still hugs the roadway, even though
                # the roadway's far end strays from the short sidewalk.
                gaps = {c: min(strays(c, root), strays(root, c)) for c in rest}
                claimed = {c for c, gap in gaps.items()
                           if gap <= PARALLEL_MAX_SEP_M}
                if rounds == 1:          # later rounds re-see the same rejects
                    for gap in gaps.values():
                        gap_hist[min(int(gap // 10), GAP_BUCKETS - 1)] += 1
                for c in claimed:
                    corridors[root].extend(corridors[c])
                    corridors[c] = []
                    corr_alive[c] = False
                    lines_cache.pop(c, None)
                if claimed:
                    lines_cache.pop(root, None)
                    corridors_merged += len(claimed)
                    changed = True
                pending = [c for c in rest if c not in claimed]

        # V1) driveway-class stub corridors melt into their only junction.
        for c in range(n_corr):
            if not corr_alive[c] or len(corr_cl[c]) != 1:
                continue
            members = corridors[c]
            pts = node_xy[ends[members].ravel()]
            diag = float(np.hypot(*(pts.max(axis=0) - pts.min(axis=0))))
            if diag > STUB_MAX_M:
                continue
            if not all(rc in STUBBY_CLASSES for rc in rclass[members]):
                continue
            k = next(iter(corr_cl[c]))
            edge_cluster[members] = k
            corr_alive[c] = False
            corridors[c] = []
            lines_cache.pop(c, None)
            stubs_merged += 1
            changed = True

        # Cluster → set of live incident corridors (reflects A/V1 above).
        cl_corrs: dict[int, set[int]] = {}
        for c in range(n_corr):
            if not corr_alive[c]:
                continue
            for k in corr_cl[c]:
                cl_corrs.setdefault(k, set()).add(c)

        # V2) A cluster touching ≤ 1 live corridor isn't a junction. Dissolve
        # it into that corridor (its captured edges + member nodes go there);
        # an isolated cluster (0 corridors) keeps its members and is skipped.
        for k in range(n_clusters):
            if not cluster_alive[k]:
                continue
            cs = cl_corrs.get(k, set())
            if len(cs) != 1:
                continue
            c = next(iter(cs))
            captured = np.where(edge_cluster == k)[0]
            corridors[c].extend(int(x) for x in captured)
            lines_cache.pop(c, None)
            edge_cluster[captured] = -1
            node_cluster[cl_members[k]] = -1
            cluster_alive[k] = False
            cl_corrs.pop(k, None)
            junctions_dissolved += 1
            changed = True

        # B) Same incident-corridor set (≥ 2 corridors) → one cluster. The
        # remap is applied vectorized once per round.
        by_corrs: dict[frozenset, list[int]] = {}
        for k in range(n_clusters):
            if cluster_alive[k]:
                sig = frozenset(cl_corrs.get(k, set()))
                if len(sig) >= 2:
                    by_corrs.setdefault(sig, []).append(k)
        remap = None
        for ks in by_corrs.values():
            if len(ks) > 1:
                if remap is None:
                    remap = np.arange(n_clusters)
                root = ks[0]
                for k in ks[1:]:
                    remap[k] = root
                    cl_members[root] = np.concatenate([cl_members[root], cl_members[k]])
                    cl_members[k] = cl_members[k][:0]
                    cluster_alive[k] = False
                clusters_merged += len(ks) - 1
                changed = True
        if remap is not None:
            m = edge_cluster >= 0
            edge_cluster[m] = remap[edge_cluster[m]]
            m = node_cluster >= 0
            node_cluster[m] = remap[node_cluster[m]]

        if not changed:
            break
    print(f"[gf] fixpoint ({rounds} rounds): {corridors_merged} parallel "
          f"corridors merged, {clusters_merged} equivalent clusters merged, "
          f"{stubs_merged} stubs → junctions, {junctions_dissolved} fake "
          f"junctions → corridors ({time.time()-t0:.0f}s)", flush=True)
    print(f"[gf] rule-A stray distance from anchor (10 m buckets, cut at "
          f"{PARALLEL_MAX_SEP_M:.0f} m): {gap_hist}", flush=True)

    # ── 4. Geometry from membership ─────────────────────────────────────────
    widths = np.array([HALF_WIDTH.get(rc, DEFAULT_HALF_WIDTH) * WIDTH_SCALE
                       for rc in rclass])

    def tube_of(eids):
        return union_all(shp_buffer(np.array(edge_lines(eids), dtype=object),
                                    widths[list(eids)],
                                    cap_style="round", join_style="round"))

    # Junction cells: CONVEX HULL of the member nodes (⊕ NODE_R_M pad) ∪ the
    # captured edges' tubes — one compact shape that follows the intersection's
    # own footprint instead of the bulbous per-node disc unions (which drew as
    # 4-lobed clovers at street corners). Every member is still inside its own
    # cell by construction. A cluster that captured NO edges gets no cell at
    # all: an empty cell could never hold or display a vote (votes live on
    # edges), so any interaction inside it would resolve to a NEIGHBOURING
    # block's edge — the surrounding corridors' own tubes cover the fork
    # instead, and the junction area lights with whichever corridor actually
    # holds the votes.
    cap_count = np.bincount(edge_cluster[edge_cluster >= 0], minlength=n_clusters)
    empty_junctions = sum(1 for k in range(n_clusters)
                          if cluster_alive[k] and cap_count[k] == 0)
    live_cl = [k for k in range(n_clusters) if cluster_alive[k] and cap_count[k] > 0]
    cells = []
    cell_ctr = []
    for k in live_cl:
        pts = node_xy[cl_members[k]]
        hull = shapely.convex_hull(shapely.multipoints(pts)).buffer(
            NODE_R_M, quad_segs=4)
        captured = np.where(edge_cluster == k)[0]
        cells.append(union_all([hull, tube_of(captured)]) if len(captured)
                     else hull)
        cell_ctr.append(pts.mean(axis=0))

    # Voronoi trim: where two cells overlap, cut each at the perpendicular
    # bisector of the two clusters' member centroids — abutting junctions get
    # a straight shared boundary instead of stacked blobs. Cuts are
    # UNCONDITIONAL: a captured edge a cut strands outside its cell is
    # re-homed once corridor geometry exists (membership follows the final
    # geometry, same as the corridor-edge reassignment below). The old veto
    # ("skip a cut that would evict a member node / detach a captured edge")
    # shipped ~4.9k visibly stacked junction pairs on nyc — big multi-node
    # intersections always had some captured stub past the bisector. A cell
    # left with < 1 m² on its own side of a bisector IS its neighbour's
    # intersection and is absorbed into it; absorption grows the survivor,
    # so sweep to a fixpoint (2 sweeps in practice, last sweep measure-only).
    def halfplane(c, nb, span=200.0):
        """Rectangle covering c's side of the perpendicular bisector toward nb."""
        d = float(np.hypot(*(nb - c)))
        if d < 1e-9:
            return None
        u = (nb - c) / d
        v = np.array([-u[1], u[0]])
        m = (c + nb) / 2.0
        return Polygon([m + span * v, m - span * v,
                        m - span * v - 2 * span * u,
                        m + span * v - 2 * span * u])

    cells_trimmed = 0
    cells_absorbed = 0
    residual_pairs = 0
    cell_alive = [True] * len(cells)

    def absorb(i: int, j: int):
        """Fold cell i (members, captured edges, geometry) into cell j."""
        nonlocal cells_absorbed
        ki, kj = live_cl[i], live_cl[j]
        edge_cluster[edge_cluster == ki] = kj
        node_cluster[cl_members[ki]] = kj
        cl_members[kj] = np.concatenate([cl_members[kj], cl_members[ki]])
        cl_members[ki] = cl_members[ki][:0]
        cells[j] = union_all([cells[j], cells[i]])
        cell_ctr[j] = node_xy[cl_members[kj]].mean(axis=0)
        cell_alive[i] = False
        cells_absorbed += 1

    for sweep in range(5):
        idxs = [i for i in range(len(cells)) if cell_alive[i]]
        if len(idxs) < 2:
            break
        alive_geoms = np.array([cells[i] for i in idxs], dtype=object)
        tree = STRtree(list(alive_geoms))
        qi, qj = tree.query(alive_geoms, predicate="intersects")
        pairs = [(idxs[int(a)], idxs[int(b)]) for a, b in zip(qi, qj)
                 if a < b and cells[idxs[int(a)]].intersection(
                     cells[idxs[int(b)]]).area >= 1.0]
        residual_pairs = len(pairs)
        if not pairs or sweep == 4:
            break
        for a, b in pairs:
            for i, j in ((a, b), (b, a)):
                if not (cell_alive[i] and cell_alive[j]):
                    break
                hp = halfplane(cell_ctr[i], cell_ctr[j])
                if hp is None:                       # coincident centroids
                    absorb(i, j)
                    break
                cut = cells[i].intersection(hp)
                if cut.is_empty or cut.area < 1.0:   # i lies on j's side
                    absorb(i, j)
                    break
                if cut.area < cells[i].area - 1e-6:
                    cells[i] = cut
                    cells_trimmed += 1
    if cells_absorbed:
        keep = [i for i in range(len(cells)) if cell_alive[i]]
        cells = [cells[i] for i in keep]
        live_cl = [live_cl[i] for i in keep]
        cell_ctr = [cell_ctr[i] for i in keep]
    cell_tree = STRtree(cells) if cells else None

    # Corridor tubes minus junction cells (junctions win where they meet, so
    # corridor-vs-junction geometry stays disjoint).
    live_corrs = [c for c in range(n_corr) if corr_alive[c] and corridors[c]]
    corr_polys: dict[int, object] = {}
    for c in live_corrs:
        tube = tube_of(corridors[c])
        if cell_tree is not None and not tube.is_empty:
            hit = cell_tree.query(tube, predicate="intersects")
            if len(hit):
                tube = _clean(shapely.difference(tube, union_all([cells[i] for i in hit])))
        if not tube.is_empty and tube.area > 1.0:
            corr_polys[c] = tube

    # ── Corridor-vs-corridor disjointness ──────────────────────────────────
    # Two corridors' tubes overlap wherever DISTINCT streets run closer than
    # the sum of their half-widths: parallel service roads and paths,
    # grade-separated crossings (no shared node → no junction cell between
    # them), wide-class buffers spilling over a neighbouring footway. This was
    # the last unhandled overlap class (thousands of pairs per city) — an
    # overlapped block under another is impossible to click.
    # Resolution mirrors the junction trim's "geometry wins" rule: each
    # overlap region is CLAIMED by the corridor whose own member edges carry
    # more length inside it (its street actually runs there; tie → longer
    # member length overall, then lower id) and CUT from the other. A loser
    # left with < 1 m² is geometrically a duplicate of the winner and MERGES
    # into it (members + geometry) — cuts only shrink and merges only union,
    # so the sweep converges; ≤ 4 mutating sweeps + a measure-only one.
    corr_cuts = 0
    corr_merges = 0
    cc_residual = 0

    def merge_corridor(i: int, j: int):
        """Fold corridor i (members + geometry) into corridor j."""
        nonlocal corr_merges
        corridors[j].extend(corridors[i])
        corridors[i] = []
        corr_polys[j] = _clean(union_all([corr_polys[j], corr_polys[i]]))
        del corr_polys[i]
        lines_cache.pop(i, None)
        lines_cache.pop(j, None)
        corr_merges += 1

    for sweep in range(5):
        ids = [c for c in corr_polys if not corr_polys[c].is_empty]
        if len(ids) < 2:
            break
        geoms = np.array([corr_polys[c] for c in ids], dtype=object)
        tree = STRtree(list(geoms))
        qi, qj = tree.query(geoms, predicate="intersects")
        pairs = [(ids[int(a)], ids[int(b)]) for a, b in zip(qi, qj) if a < b]
        if sweep == 4:
            cc_residual = sum(
                1 for a, b in pairs
                if a in corr_polys and b in corr_polys
                and corr_polys[a].intersection(corr_polys[b]).area >= 1.0)
            break
        n_cut_before = corr_cuts + corr_merges
        for a, b in pairs:
            if a not in corr_polys or b not in corr_polys:
                continue
            pa, pb = corr_polys[a], corr_polys[b]
            region = _clean(pa.intersection(pb))
            if region.is_empty or region.area < 1.0:
                continue
            la = member_lines(a).intersection(region).length
            lb = member_lines(b).intersection(region).length
            if la > lb:
                keep, lose = a, b
            elif lb > la:
                keep, lose = b, a
            else:
                # Pure buffer spill on both sides — the longer street keeps.
                ta, tb = member_lines(a).length, member_lines(b).length
                keep, lose = (a, b) if (ta, -a) >= (tb, -b) else (b, a)
            cut = _clean(shapely.difference(corr_polys[lose], corr_polys[keep]))
            if cut.is_empty or cut.area < 1.0:
                merge_corridor(lose, keep)
            else:
                corr_polys[lose] = cut
                corr_cuts += 1
        if corr_cuts + corr_merges == n_cut_before:
            # Nothing mutated: remaining intersects are boundary touches.
            cc_residual = 0
            break
    print(f"[gf] corridor disjointness: {corr_cuts} cuts, {corr_merges} "
          f"duplicate corridors merged, {cc_residual} overlapping corridor "
          f"pairs left ({time.time()-t0:.0f}s)", flush=True)

    # Membership follows the final geometry: a corridor edge whose tube was
    # entirely eaten by junction cells (short link threading an intersection)
    # OR cut away by the corridor-disjointness pass moves to the polygon that
    # holds its midpoint now — a cell first, else the corridor whose (final)
    # geometry contains it; last resort, its clipped tube is grafted back onto
    # its own corridor, so every edge intersects its own block polygon.
    # Corridors left with no geometry disappear entirely.
    reassigned = 0
    corr_grafts = 0
    reamap_ids = [c for c in corr_polys if not corr_polys[c].is_empty]
    reamap_tree = STRtree([corr_polys[c] for c in reamap_ids]) if reamap_ids else None
    moved_in: dict[int, list[int]] = {}
    for c in list(live_corrs):
        poly = corr_polys.get(c)
        stay = []
        for eid in corridors[c]:
            line = LineString([node_xy[ends[eid, 0]], node_xy[ends[eid, 1]]])
            if poly is not None and line.distance(poly) <= 1e-6:
                stay.append(eid)
                continue
            mid = shapely.points((node_xy[ends[eid, 0]] + node_xy[ends[eid, 1]]) / 2.0)
            hit = cell_tree.query(mid, predicate="intersects") if cell_tree is not None else []
            if len(hit):
                edge_cluster[eid] = live_cl[int(hit[0])]
                reassigned += 1
                continue
            hit = reamap_tree.query(mid, predicate="intersects") if reamap_tree is not None else []
            hit = [h for h in hit if reamap_ids[int(h)] != c]
            if len(hit):
                moved_in.setdefault(reamap_ids[int(hit[0])], []).append(int(eid))
                reassigned += 1
                continue
            if poly is not None:
                # Graft the edge's own tube back, clipped against every other
                # polygon so the graft cannot re-create an overlap.
                patch = tube_of([eid])
                near = cell_tree.query(patch, predicate="intersects") if cell_tree is not None else []
                if len(near):
                    patch = shapely.difference(patch, union_all([cells[int(i)] for i in near]))
                nearc = reamap_tree.query(patch, predicate="intersects") if reamap_tree is not None else []
                others = [corr_polys[reamap_ids[int(i)]] for i in nearc if reamap_ids[int(i)] != c]
                if others:
                    patch = shapely.difference(patch, union_all(others))
                corr_polys[c] = _clean(union_all([corr_polys[c], patch]))
                poly = corr_polys[c]
                corr_grafts += 1
            stay.append(eid)
        corridors[c] = stay
        if not stay and c in corr_polys and c not in moved_in:
            del corr_polys[c]
    for c, eids in moved_in.items():
        corridors[c].extend(eids)

    # Junction membership follows the final geometry too: an unconditional
    # bisector cut can strand a captured edge outside its own (now smaller)
    # cell. Re-home each stranded edge to the polygon that actually holds it
    # now — the cell or corridor containing its midpoint, falling back to any
    # cell the line still touches. An edge NO polygon holds gets its tube
    # grafted back onto its own cell, clipped against neighbour cells (safe:
    # no cell touches the line, else it would have been re-homed) so the
    # graft cannot re-stack junction cells; counted (near-zero in practice).
    rehomed = 0
    grafts = 0
    corr_ids = list(corr_polys.keys())
    corr_tree = STRtree([corr_polys[c] for c in corr_ids]) if corr_ids else None
    for idx, k in enumerate(live_cl):
        for eid in np.where(edge_cluster == k)[0]:
            line = LineString([node_xy[ends[eid, 0]], node_xy[ends[eid, 1]]])
            if line.distance(cells[idx]) <= 1e-6:
                continue
            mid = shapely.points((node_xy[ends[eid, 0]] + node_xy[ends[eid, 1]]) / 2.0)
            hit = cell_tree.query(mid, predicate="intersects")
            if len(hit):
                edge_cluster[eid] = live_cl[int(hit[0])]
                rehomed += 1
                continue
            hit = corr_tree.query(mid, predicate="intersects") if corr_tree is not None else []
            if len(hit):
                edge_cluster[eid] = -1
                corridors[corr_ids[int(hit[0])]].append(int(eid))
                rehomed += 1
                continue
            hit = cell_tree.query(line, predicate="intersects")
            if len(hit):
                edge_cluster[eid] = live_cl[int(hit[0])]
                rehomed += 1
                continue
            patch = tube_of([eid])
            near = [int(n) for n in cell_tree.query(patch, predicate="intersects")
                    if int(n) != idx]
            if near:
                patch = shapely.difference(
                    patch, union_all([cells[n] for n in near]))
            nearc = corr_tree.query(patch, predicate="intersects") if corr_tree is not None else []
            if len(nearc):
                # Clip against corridors too — un-clipped grafts were the one
                # remaining source of junction-vs-corridor overlap.
                patch = shapely.difference(
                    patch, union_all([corr_polys[corr_ids[int(n)]] for n in nearc]))
            cells[idx] = _clean(union_all([cells[idx], patch]))
            grafts += 1
    if grafts:
        # Grafts changed geometry after the trim's residual measurement —
        # re-measure so the stamped meta reflects the shipped polygons.
        tree = STRtree(cells)
        qi, qj = tree.query(np.array(cells, dtype=object),
                            predicate="intersects")
        residual_pairs = sum(
            1 for a, b in zip(qi, qj)
            if a < b and cells[int(a)].intersection(cells[int(b)]).area >= 1.0)
    print(f"[gf] geometry: {len(corr_polys)} corridor tubes, {len(cells)} "
          f"junction cells ({cells_trimmed} bisector cuts, {cells_absorbed} "
          f"absorbed into a neighbour, {residual_pairs} overlapping pairs "
          f"left; {empty_junctions} edge-less clusters skipped — corridor "
          f"tubes cover those forks); {reassigned} intersection-interior "
          f"edges moved to their junction cell, {rehomed} stranded junction "
          f"edges re-homed, {grafts} tube grafts ({time.time()-t0:.0f}s)",
          flush=True)

    # ── 4b. Oversized-corridor split (thinnest cut) ─────────────────────────
    # The fixpoint's merge rules have no size cap: a park path loop whose two
    # arms share endpoint junctions (rule A), or one big connected path
    # component between few junctions, becomes a single corridor kilometres
    # across (Central Park reservoir loop: 4.3 km bbox, 264 edges — one
    # un-hoverable vote target). Cut every corridor whose bbox diagonal
    # exceeds SPLIT_MAX_EXTENT_M along its thinnest crossing, recursively:
    # sample stations across the middle of the principal axis, measure the
    # polygon's width at each (length of its intersection with the
    # perpendicular chord — a gap between parts measures 0 and splits free),
    # and cut at the narrowest. Member edges follow their midpoints; a piece
    # holding no members melts into the nearest member-holding piece. Pieces
    # meet only along the cut line (zero shared area), so the disjointness
    # audit below is unaffected.
    def _principal_frame(poly):
        pts = np.concatenate([np.asarray(p.exterior.coords)
                              for part in _polyparts(poly)
                              for p in (part.geoms if part.geom_type ==
                                        "MultiPolygon" else [part])])
        ctr = pts.mean(axis=0)
        centered = pts - ctr
        _, vecs = np.linalg.eigh(centered.T @ centered)
        u = vecs[:, -1]
        proj = centered @ u
        return ctr, u, float(proj.min()), float(proj.max())

    def _thinnest_cut(poly):
        """Split poly at the narrowest sampled crossing; (lo, hi) halves."""
        ctr, u, plo, phi = _principal_frame(poly)
        span = phi - plo
        if span < 1e-6:
            return None
        v = np.array([-u[1], u[0]])
        reach = max(span, 2 * SPLIT_MAX_EXTENT_M)      # covers the whole poly
        best_t, best_w = None, math.inf
        for f in np.linspace(SPLIT_BAND[0], SPLIT_BAND[1], SPLIT_STATIONS):
            t = plo + f * span
            m = ctr + t * u
            chord = LineString([m + reach * v, m - reach * v])
            try:
                w_here = poly.intersection(chord).length
            except shapely.errors.GEOSException:
                continue
            if w_here < best_w:
                best_t, best_w = t, w_here
        if best_t is None:
            return None
        m = ctr + best_t * u
        half = Polygon([m + reach * v, m - reach * v,
                        m - reach * v - 2 * reach * u,
                        m + reach * v - 2 * reach * u])
        lo = _clean(poly.intersection(half))
        hi = _clean(shapely.difference(poly, half))
        if lo.is_empty or hi.is_empty or min(lo.area, hi.area) < 1.0:
            return None
        return lo, hi

    def _split_to_size(poly, depth=0):
        b = poly.bounds
        if depth >= SPLIT_MAX_DEPTH or \
                math.hypot(b[2] - b[0], b[3] - b[1]) <= SPLIT_MAX_EXTENT_M:
            return [poly]
        cut = _thinnest_cut(poly)
        if cut is None:
            return [poly]
        return _split_to_size(cut[0], depth + 1) + _split_to_size(cut[1], depth + 1)

    blocks_split = 0
    split_pieces = 0
    split_gaveup = 0
    for c in [c for c in corr_polys]:
        poly = corr_polys[c]
        b = poly.bounds
        if math.hypot(b[2] - b[0], b[3] - b[1]) <= SPLIT_MAX_EXTENT_M:
            continue
        pieces = _split_to_size(poly)
        if len(pieces) == 1:
            split_gaveup += 1
            continue
        # Members follow their midpoints; an edge no piece contains (stranded
        # members metres off their polygon exist pre-split too) goes to the
        # piece nearest its line.
        piece_members: list[list[int]] = [[] for _ in pieces]
        for eid in corridors[c]:
            line = LineString([node_xy[ends[eid, 0]], node_xy[ends[eid, 1]]])
            mid = shapely.points(np.asarray(line.coords).mean(axis=0))
            target = next((i for i, p in enumerate(pieces) if p.covers(mid)),
                          None)
            if target is None:
                target = int(np.argmin([line.distance(p) for p in pieces]))
            piece_members[target].append(eid)
        # A member-less piece can't hold or display a vote — melt it into the
        # nearest piece that has members (same rationale as empty junctions).
        live = [i for i, ms in enumerate(piece_members) if ms]
        if not live:
            split_gaveup += 1
            continue
        for i, p in enumerate(pieces):
            if i in live or p.is_empty:
                continue
            j = min(live, key=lambda j: p.distance(pieces[j]))
            pieces[j] = _clean(union_all([pieces[j], p]))
        corridors[c] = piece_members[live[0]]
        corr_polys[c] = pieces[live[0]]
        for i in live[1:]:
            corridors.append(piece_members[i])
            corr_polys[len(corridors) - 1] = pieces[i]
        blocks_split += 1
        split_pieces += len(live)
    print(f"[gf] oversized split (> {SPLIT_MAX_EXTENT_M:.0f} m): "
          f"{blocks_split} corridors cut into {split_pieces} pieces, "
          f"{split_gaveup} could not be split ({time.time()-t0:.0f}s)",
          flush=True)

    # ── Finalize in the SHIPPED (lon/lat) frame: snap → validate → audit →
    # repair. Valid-in-metres polygons flip invalid after the lon/lat
    # transform (708 on test-mid): cut seams carry near-coincident collinear
    # vertices whose relative float error grows under the frame scaling, so
    # rings self-cross — and a consumer's make_valid can FILL a self-crossed
    # ring into a large false lobe. So the disjointness contract is enforced
    # in the frame that actually ships: transform FIRST, snap every
    # coordinate to a 1e-7° grid (≈1 cm; shared cut seams land on identical
    # grid points), make_valid, then measure ALL-CLASS pairwise overlap and
    # cut any residual pair (junction beats corridor, else larger area) —
    # nothing ships overlapping ≥ 1 m² or invalid.
    GRID_LL = 1e-7
    ll_area_m2 = mlon * mlat                      # deg² → m² at this latitude

    def to_ll(geom):
        return shapely.transform(
            geom, lambda a: np.column_stack((a[:, 0] / mlon + w, a[:, 1] / mlat + s)))

    invalid_fixed = 0

    def _finalize(geom):
        nonlocal invalid_fixed
        g = to_ll(geom)
        if not g.is_valid:
            invalid_fixed += 1
            g = _clean(g)
        try:
            g = shapely.set_precision(g, GRID_LL)
        except shapely.errors.GEOSException:
            # set_precision chokes on some repaired hole/shell arrangements —
            # re-validate and retry; give up on snapping only as a last resort
            # (the pairwise repair sweep below still enforces disjointness).
            g = _clean(g)
            try:
                g = shapely.set_precision(g, GRID_LL)
            except shapely.errors.GEOSException:
                pass
        return _clean(g)

    for c in list(corr_polys):
        corr_polys[c] = _finalize(corr_polys[c])
        if corr_polys[c].is_empty:
            del corr_polys[c]
    cells = [_finalize(cl) for cl in cells]

    audit_ids = list(corr_polys.keys())           # corridor ids, then cells
    repaired_geo: set[int] = set()
    repair_cuts = 0
    overlap_detail = {"CC": 0, "CJ": 0, "JJ": 0}
    residual_all = 0
    # Repair at ≥ 0.5 m² but promise ≥ 1 m² clean: the half-metre hysteresis
    # absorbs measuring-frame differences (an external scan's projection can
    # rate a threshold-grazing pair a little differently).
    for rnd in range(4):
        geoms = [corr_polys[c] for c in audit_ids] + list(cells)
        kinds = ["C"] * len(audit_ids) + ["J"] * len(cells)
        overlap_detail = {"CC": 0, "CJ": 0, "JJ": 0}
        hits = []
        nonempty = [i for i, g in enumerate(geoms) if not g.is_empty]
        if len(nonempty) >= 2:
            atree = STRtree([geoms[i] for i in nonempty])
            aq, at = atree.query(np.array([geoms[i] for i in nonempty],
                                          dtype=object),
                                 predicate="intersects")
            for a, b in zip(aq, at):
                a, b = nonempty[int(a)], nonempty[int(b)]
                if a >= b:
                    continue
                try:
                    inter = geoms[a].intersection(geoms[b]).area
                except shapely.errors.GEOSException:
                    inter = math.inf          # unmeasurable pair → force repair
                if inter * ll_area_m2 >= 0.5:
                    cls = "".join(sorted(kinds[a] + kinds[b]))
                    overlap_detail[cls] += 1
                    hits.append((a, b))
        residual_all = len(hits)
        if not hits or rnd == 3:
            break
        for a, b in hits:
            # Junction beats corridor; same kind → larger area keeps.
            if kinds[a] == kinds[b]:
                keep, lose = (a, b) if geoms[a].area >= geoms[b].area else (b, a)
            else:
                keep, lose = (a, b) if kinds[a] == "J" else (b, a)
            try:
                cut = shapely.difference(geoms[lose], geoms[keep])
            except shapely.errors.GEOSException:
                try:
                    cut = shapely.difference(_clean(geoms[lose]), _clean(geoms[keep]))
                except shapely.errors.GEOSException:
                    cut = Polygon()          # unrecoverable: drop, re-home members
            try:
                cut = _clean(shapely.set_precision(cut, GRID_LL))
            except shapely.errors.GEOSException:
                cut = _clean(cut)
            if lose < len(audit_ids):
                corr_polys[audit_ids[lose]] = cut
            else:
                cells[lose - len(audit_ids)] = cut
            repaired_geo.add(lose)
            repair_cuts += 1
    print(f"[gf] final ship-frame audit: {residual_all} overlapping pairs "
          f"≥1m² ({overlap_detail}); {invalid_fixed} transform-invalid "
          f"geometries fixed, {repair_cuts} residual repair cuts "
          f"({time.time()-t0:.0f}s)", flush=True)

    # GLOBAL ship-frame membership re-home: the metric-frame passes re-home
    # by exact touch, but corridor cuts + the frame snap leave a tail of
    # members metres from their polygon (244 on test-mid, up to 23 m). Final
    # rule, applied to EVERY member edge against the FINAL shipped geometry:
    # an edge whose line doesn't touch its polygon (within one snap step)
    # moves to the polygon holding its midpoint, else any polygon its line
    # crosses; a member nothing holds stays put and surfaces in §7.
    ll_nodes = np.column_stack((nodes[:, 1], nodes[:, 0]))    # (lon, lat)
    repair_rehomed = 0
    fin_corr_ids = [c for c in corr_polys if not corr_polys[c].is_empty]
    fin_geoms = [corr_polys[c] for c in fin_corr_ids] + \
        [cl for cl in cells if not cl.is_empty]
    fin_kind: list[tuple[str, int]] = [("C", c) for c in fin_corr_ids] + \
        [("J", i) for i, cl in enumerate(cells) if not cl.is_empty]
    fin_tree = STRtree(fin_geoms) if fin_geoms else None
    moved_edges: dict[int, tuple[str, int]] = {}

    def rehome_members(eids, own_poly):
        nonlocal repair_rehomed
        for eid in list(eids):
            line = LineString([ll_nodes[ends[eid, 0]], ll_nodes[ends[eid, 1]]])
            if own_poly is not None and not own_poly.is_empty \
                    and line.distance(own_poly) <= 2 * GRID_LL:
                continue
            if fin_tree is None:
                continue
            mid = shapely.points((ll_nodes[ends[eid, 0]] + ll_nodes[ends[eid, 1]]) / 2.0)
            hit = fin_tree.query(mid, predicate="intersects")
            if not len(hit):
                hit = fin_tree.query(line, predicate="intersects")
            if not len(hit):
                continue
            moved_edges[int(eid)] = fin_kind[int(hit[0])]
            repair_rehomed += 1

    for c in list(corr_polys):
        rehome_members(corridors[c], corr_polys.get(c))
    for ci, k in enumerate(live_cl):
        rehome_members(np.where(edge_cluster == k)[0], cells[ci])
    if moved_edges:
        for c in list(corr_polys):
            corridors[c] = [e for e in corridors[c] if e not in moved_edges]
        for eid, (kind, tgt) in moved_edges.items():
            if kind == "C":
                edge_cluster[eid] = -1
                if eid not in corridors[tgt]:
                    corridors[tgt].append(int(eid))
            else:
                edge_cluster[eid] = live_cl[tgt]
        print(f"[gf] ship-frame re-home: {repair_rehomed} members moved to "
              f"the polygon that holds them ({time.time()-t0:.0f}s)", flush=True)

    # ── 5. Contiguity: one shipped block per contiguous piece ──────────────
    # Everything above is settled — membership, geometry, disjointness. What
    # can still be wrong is a block that is one GROUP but two PLACES: rule A's
    # anchor test keeps corridors together only while they run alongside each
    # other, but a corridor can also come apart downstream — a grade-separated
    # crossing's tube slices it, a junction cell eats its middle, a ship-frame
    # repair cut takes a bite out of it.
    #
    # So the last word belongs to the shipped geometry: bucket each block's
    # polygon parts so that every part is within PART_GAP_M of another part in
    # its own bucket, and emit ONE BLOCK PER BUCKET. Members follow their own
    # geometry (the bucket holding the edge's midpoint, else the one its line
    # crosses, else the nearest). A bucket that ends up holding no member is
    # dropped: a shard with no edge in it can neither carry a vote nor light
    # up, and keeping it would only re-attach a detached shape to a block that
    # isn't there. Buckets are disjoint subsets of an already-audited polygon,
    # so no split can create an overlap — nothing below needs re-auditing.
    GAP_LL = PART_GAP_M * 2.0 / (mlon + mlat)     # metres → degrees (isotropic)

    def _parts(geom):
        """The individual Polygon components of any polygonal geometry."""
        return [g for p in _polyparts(geom)
                for g in (p.geoms if p.geom_type == "MultiPolygon" else [p])]

    def contiguous_buckets(poly):
        """Polygon parts grouped into runs no wider than PART_GAP_M apart."""
        parts = _parts(poly)
        if len(parts) < 2:
            return [poly] if parts else []
        uf = UnionFind(len(parts))
        tree = STRtree(parts)
        for i, part in enumerate(parts):
            for j in tree.query(shp_buffer(part, GAP_LL), predicate="intersects"):
                uf.union(i, int(j))
        runs: dict[int, list] = {}
        for i in range(len(parts)):
            runs.setdefault(uf.find(i), []).append(parts[i])
        return [union_all(r) if len(r) > 1 else r[0] for r in runs.values()]

    # (polygon, member edge ids, geojson properties beyond the shared ones)
    staged: list[tuple[object, list[int], dict]] = []
    for c, poly in corr_polys.items():
        if not poly.is_empty and corridors[c]:
            staged.append((poly, list(corridors[c]), {}))
    for i, k in enumerate(live_cl):
        captured = [int(e) for e in np.where(edge_cluster == k)[0]]
        if not cells[i].is_empty and captured:
            staged.append((cells[i], captured,
                           {"node_id": int(cl_members[k].min()),
                            "n_nodes": len(cl_members[k])}))

    contiguity_splits = 0
    contiguity_pieces = 0
    shards_dropped = 0
    split_staged: list[tuple[object, list[int], dict]] = []
    for poly, members, extra in staged:
        buckets = contiguous_buckets(poly)
        if len(buckets) < 2:
            split_staged.append((poly, members, extra))
            continue
        held: list[list[int]] = [[] for _ in buckets]
        btree = STRtree(buckets)
        for eid in members:
            line = LineString([ll_nodes[ends[eid, 0]], ll_nodes[ends[eid, 1]]])
            mid = shapely.points(np.asarray(line.coords).mean(axis=0))
            hit = btree.query(mid, predicate="intersects")
            if not len(hit):
                hit = btree.query(line, predicate="intersects")
            held[int(hit[0]) if len(hit) else
                 int(np.argmin([line.distance(b) for b in buckets]))].append(eid)
        live = [i for i, ms in enumerate(held) if ms]
        shards_dropped += len(buckets) - len(live)
        if len(live) > 1:
            contiguity_splits += 1
            contiguity_pieces += len(live)
        for i in live:
            split_staged.append((buckets[i], held[i], extra))
    staged = split_staged
    print(f"[gf] contiguity (> {PART_GAP_M:.0f} m apart): {contiguity_splits} "
          f"blocks split into {contiguity_pieces} pieces, {shards_dropped} "
          f"member-less shards dropped ({time.time()-t0:.0f}s)", flush=True)

    # ── 6. Emit: dense block ids, geojson + npy + meta ─────────────────────

    # block_id starts at 1: tippecanoe/MVT cannot represent a native feature
    # id of 0 (it drops it), which would detach feature-state (heat/selection)
    # from block 0. Server arrays are sized n_blocks = len(feats)+1, slot 0 unused.
    # Geometry is ALREADY in the shipped lon/lat frame (finalized above);
    # area_m2 converts via the equirectangular scale.
    feats = []
    edge_block = np.full(n_edges, -1, dtype=np.int32)
    poly_of_block: dict[int, object] = {}
    for poly, members, extra in staged:
        bid = len(feats) + 1
        edge_block[members] = bid
        poly_of_block[bid] = poly
        is_junction = "node_id" in extra
        feats.append({
            "type": "Feature",
            "properties": {
                "block_id": bid, "seg_id": -1,
                "road_class": "node" if is_junction else
                              (majority(rclass[members]) or "path"),
                "road_name": None if is_junction else
                             majority([names[i] for i in members]),
                "area_m2": round(poly.area * ll_area_m2, 1),
                "n_edges": len(members),
                **extra,
            },
            "geometry": mapping(poly),
        })

    # ── 7. Audit: coverage + overlap by construction, verified anyway ──────
    # Touch is measured in the SHIPPED frame (2·GRID_LL ≈ 2 cm tolerance —
    # the snap can move a boundary by at most one grid step).
    mapped = int((edge_block >= 0).sum())
    checked = 0
    overlapping = 0
    for i in range(n_edges):
        b = int(edge_block[i])
        if b < 0 or not not_self[i]:
            continue
        poly = poly_of_block.get(b)
        if poly is None:
            continue
        checked += 1
        if poly.distance(LineString([ll_nodes[ends[i, 0]],
                                     ll_nodes[ends[i, 1]]])) <= 2 * GRID_LL:
            overlapping += 1
    print(f"[gf] audit: {mapped}/{n_edges} mapped "
          f"({100*mapped/max(1, int(not_self.sum())):.2f}% of non-self); "
          f"{overlapping}/{checked} member edges touch their polygon "
          f"({100*overlapping/max(1, checked):.2f}%)", flush=True)

    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    os.makedirs(out_dir, exist_ok=True)
    final_path = os.path.join(out_dir, f"blocks_final_{CITY}.geojson")
    tmp = f"{final_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    os.replace(tmp, final_path)

    # BLOCKS_DATA_DIR redirects the baked mapping away from the city's live
    # osm_data/ — set it for parameter sweeps and A/B runs so an experiment
    # can't leave the local server serving a mapping nobody meant to ship.
    data_dir = os.environ.get("BLOCKS_DATA_DIR") or os.path.join(_SERVER, city.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    np.save(os.path.join(data_dir, f"edge_blocks_{NETWORK}.npy"), edge_block)
    meta = {
        "city": CITY, "network": NETWORK, "builder": "graph_first",
        "n_edges": n_edges, "n_blocks": len(feats) + 1,  # ids are 1-based
        "topology_etag": g.topology_etag,
        "blocks_sha256": _sha256_file(final_path),
        "blocks_file": os.path.basename(final_path),
        "mapped_edges": mapped,
        "unmapped": n_edges - mapped,
        "self_edges": int((~not_self).sum()),
        "corridors": len(corr_polys),
        "junction_cells": len(cells),
        "stubs_merged": stubs_merged,
        "junctions_dissolved": junctions_dissolved,
        "parallel_corridors_merged": corridors_merged,
        "equivalent_clusters_merged": clusters_merged,
        "cells_bisector_trimmed": cells_trimmed,
        "cells_absorbed": cells_absorbed,
        # ALL-CLASS residual (corridor-corridor + corridor-junction +
        # junction-junction), measured on the final shipped polygons. The
        # pre-2026-07-22 stamp counted junction pairs only.
        "residual_overlap_pairs": residual_all,
        "residual_overlap_detail": overlap_detail,
        "residual_junction_pairs_presweep": residual_pairs,
        "corridor_cuts": corr_cuts,
        "corridor_duplicates_merged": corr_merges,
        "corridor_tube_grafts": corr_grafts,
        "invalid_geoms_fixed": invalid_fixed,
        "edges_reassigned_to_cells": reassigned,
        "junction_edges_rehomed": rehomed,
        "junction_tube_grafts": grafts,
        "empty_junctions_skipped": empty_junctions,
        "oversized_blocks_split": blocks_split,
        "split_pieces": split_pieces,
        "split_gaveup": split_gaveup,
        "contiguity_splits": contiguity_splits,
        "contiguity_pieces": contiguity_pieces,
        "contiguity_shards_dropped": shards_dropped,
        # Rule-A candidates by how far they strayed from their group anchor,
        # in 10 m buckets — the evidence behind parallel_max_sep_m.
        "parallel_gap_hist_10m": gap_hist,
        "edges_overlap_ok": overlapping, "edges_overlap_checked": checked,
        "node_capture_len_m": NODE_CAPTURE_LEN_M, "stub_max_m": STUB_MAX_M,
        "split_max_extent_m": SPLIT_MAX_EXTENT_M,
        "parallel_max_sep_m": PARALLEL_MAX_SEP_M,
        "part_gap_m": PART_GAP_M,
        "width_scale": WIDTH_SCALE,
    }
    with open(os.path.join(data_dir, f"edge_blocks_{NETWORK}.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"[gf] wrote {os.path.basename(final_path)} ({len(feats)} blocks) + "
          f"edge_blocks_{NETWORK}.npy/.json ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
