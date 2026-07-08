#!/usr/bin/env python3
"""Give every junction cluster its own block — a Voronoi cell at the cluster
centroid, clipped to a max radius — punched out of the street/foot blocks
(docs/three-layer-model.md §2).

Why: block polygons extend across intersections (the Voronoi assigns the
intersection flare to ONE of the crossing segments), so the short graph edges
that cross an intersection get baked into a PERPENDICULAR street's block. A
route down an avenue then selects — and block-scoped casting votes on — every
cross street it passes ("ladder" selections). Junction blocks catch those
edges instead, so a path only ever touches blocks that share its orientation,
plus the intersections themselves.

Nodes and edges get SEPARATE block-forming logic: edges partition the ROW by
segment-Voronoi (build_blocks_generic.py); junction nodes cluster by proximity
(union-find over centers closer than NODE_CLUSTER_LINK_M — a physical
intersection is several OSM junction nodes: centerline node, crossing ends,
sidewalk corners, one per ~5-15 m). Each cluster's polygon is:

    voronoi_cell(centroid vs neighbouring cluster centroids) ∩ disc(centroid, R)

with R = clamp(max member distance + PAD, MIN_RADIUS, MAX_RADIUS). The Voronoi
bisectors keep adjacent intersections from overlapping; the radius cap keeps a
sprawling cluster from ballooning into a gloop (the old union-of-discs blobs
read as stacked circles). The cell is computed locally: start from a disc,
clip by the perpendicular bisector toward every neighbouring centroid closer
than 2R — exactly the Voronoi cell restricted to that disc.

What it does, given blocks_generic_<city>.geojson:
  1. drops any road_class in {"node","foot"} features (idempotent re-runs; foot
     blocks are rebuilt afterwards by build_foot_blocks.py);
  2. finds junction nodes of the city walk graph — unique-neighbour degree >= 3
     (degree-2 geometry nodes and dead ends stay part of their street block);
  3. clusters them (union-find, link distance NODE_CLUSTER_LINK_M);
  4. builds each cluster's clipped-Voronoi cell as above;
  5. subtracts the cells from every street block they intersect (blocks and
     node cells never overlap — bake containment stays unambiguous);
  6. appends the cells as features: road_class="node", seg_id=-1,
     node_id=<lowest member node idx>, n_nodes=<cluster size>, block_id
     continuing after the street blocks.

Run in the SERVER venv, then re-run build_edge_blocks.py (see
build_city_blocks.sh, which orders the whole pipeline):

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/build_node_blocks.py
"""
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
from shapely import STRtree, union_all  # noqa: E402
from shapely.geometry import Polygon, mapping  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
# Junction centers closer than this share a cluster (one physical intersection).
LINK_M = float(os.environ.get("NODE_CLUSTER_LINK_M", "18"))
# Single-linkage percolates in dense areas (FiDi chains 1600+ junctions over
# ~500 m); clusters wider than this are recursively bisected along their
# principal axis so every cluster stays intersection-sized and its members fit
# inside the max-radius cell.
MAX_EXTENT_M = float(os.environ.get("NODE_CLUSTER_MAX_EXTENT_M", "40"))
# Cell radius: cluster extent + pad, clamped to [MIN, MAX].
PAD_M = float(os.environ.get("NODE_BLOCK_PAD_M", "6"))
MIN_RADIUS_M = float(os.environ.get("NODE_BLOCK_MIN_RADIUS_M", "10"))
MAX_RADIUS_M = float(os.environ.get("NODE_BLOCK_MAX_RADIUS_M", "28"))
# Disc base is a 24-gon: plenty round at these radii, keeps the tiles small.
DISC_SEGS = int(os.environ.get("NODE_BLOCK_DISC_SEGS", "24"))

_DISC_ANGLES = np.linspace(0.0, 2.0 * np.pi, DISC_SEGS, endpoint=False)


def junction_nodes(g) -> np.ndarray:
    """Indices of nodes with unique-neighbour degree >= 3 (true junctions)."""
    # edge rows are [u, v, name, road_class, length_m] — take the endpoints only
    E = np.array([(e[0], e[1]) for e in g.edges], dtype=np.int64)
    E = E[E[:, 0] != E[:, 1]]                      # self-edges don't add degree
    und = np.unique(np.sort(E, axis=1), axis=0)    # unique undirected pairs
    deg = np.bincount(und.ravel(), minlength=len(g.nodes))
    return np.where(deg >= 3)[0]


def cluster_junctions(xy: np.ndarray, link_dist: float) -> np.ndarray:
    """Union-find label per junction: centers closer than link_dist share a
    cluster. Returns int labels, one per row."""
    parent = np.arange(len(xy))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    pairs = cKDTree(xy).query_pairs(r=link_dist, output_type="ndarray")
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    return split_oversized(xy, np.array([find(i) for i in range(len(xy))]))


def split_oversized(xy: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Recursively bisect clusters whose bbox diagonal exceeds MAX_EXTENT_M:
    project members on the principal axis, cut at the median. Keeps every
    cluster intersection-sized so its members fit the max-radius cell."""
    next_label = int(labels.max()) + 1
    queue = list(np.unique(labels))
    splits = 0
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
        # principal axis of the member cloud (2×2 eigenproblem)
        cov = centered.T @ centered
        _, vecs = np.linalg.eigh(cov)
        proj = centered @ vecs[:, -1]
        half = idx[proj > np.median(proj)]
        if len(half) == 0 or len(half) == len(idx):
            half = idx[np.argsort(proj)[len(idx) // 2:]]  # degenerate: split by rank
        labels[half] = next_label
        queue.append(lb)
        queue.append(next_label)
        next_label += 1
        splits += 1
    if splits:
        print(f"[node_blocks] split {splits} oversized clusters "
              f"(> {MAX_EXTENT_M:.0f}m extent)", flush=True)
    return labels


def clipped_voronoi_cell(c: np.ndarray, radius: float,
                         neighbours: np.ndarray) -> Polygon:
    """Voronoi cell of centroid `c` against `neighbours`, restricted to
    disc(c, radius): start from the disc, clip by the perpendicular bisector
    toward every neighbour closer than 2·radius (farther bisectors can't
    intersect the disc)."""
    cell = Polygon(np.column_stack((
        c[0] + radius * np.cos(_DISC_ANGLES),
        c[1] + radius * np.sin(_DISC_ANGLES),
    )))
    for nb in neighbours:
        d = float(np.hypot(nb[0] - c[0], nb[1] - c[1]))
        if d >= 2.0 * radius or d == 0.0:
            continue
        u = (nb - c) / d                      # unit vector toward the neighbour
        v = np.array([-u[1], u[0]])           # bisector direction
        m = (c + nb) / 2.0                    # bisector midpoint
        span = 4.0 * radius
        # Rectangle covering our side of the bisector; intersect keeps the
        # half-plane closer to c.
        keep = Polygon([m + span * v, m - span * v,
                        m - span * v - (span + radius) * u,
                        m + span * v - (span + radius) * u])
        cell = cell.intersection(keep)
        if cell.is_empty:
            break
    return cell


def main():
    t0 = time.time()
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    nodes = np.asarray(g.nodes, dtype=np.float64)  # [N,2] = (lat, lon)

    junctions = junction_nodes(g)
    print(f"[node_blocks] {len(junctions)} junction nodes "
          f"(deg>=3) of {len(nodes)} — link={LINK_M}m "
          f"R∈[{MIN_RADIUS_M},{MAX_RADIUS_M}]m", flush=True)

    # Same local equirectangular frame as build_foot_blocks.py so both scripts
    # agree exactly on where a cell's boundary falls.
    s, w, n, e = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s + n) / 2))

    xy = np.column_stack(((nodes[junctions, 1] - w) * mlon,
                          (nodes[junctions, 0] - s) * mlat))
    labels = cluster_junctions(xy, link_dist=LINK_M)
    uniq = np.unique(labels)
    print(f"[node_blocks] {len(uniq)} clusters from {len(junctions)} junctions "
          f"({time.time()-t0:.0f}s)", flush=True)

    # Per-cluster centroid + radius (extent + pad, clamped).
    order = np.argsort(labels, kind="stable")
    sorted_labels = labels[order]
    starts = np.searchsorted(sorted_labels, uniq, side="left")
    ends = np.searchsorted(sorted_labels, uniq, side="right")

    centroids = np.empty((len(uniq), 2))
    radii = np.empty(len(uniq))
    members_per = []
    for k in range(len(uniq)):
        members = order[starts[k]:ends[k]]
        pts = xy[members]
        c = pts.mean(axis=0)
        extent = float(np.hypot(*(pts - c).T).max()) if len(pts) > 1 else 0.0
        centroids[k] = c
        radii[k] = min(MAX_RADIUS_M, max(MIN_RADIUS_M, extent + PAD_M))
        members_per.append(junctions[members])

    # Voronoi neighbours: centroid pairs close enough that a bisector can cut
    # either cell (dist < 2 · max radius of the pair).
    ctree = cKDTree(centroids)
    pairs = ctree.query_pairs(r=2.0 * MAX_RADIUS_M, output_type="ndarray")
    nb_lists: list[list[int]] = [[] for _ in range(len(uniq))]
    for a, b in pairs:
        nb_lists[a].append(b)
        nb_lists[b].append(a)

    to_ll = lambda a: np.column_stack((a[:, 0] / mlon + w, a[:, 1] / mlat + s))
    blobs = []          # (cell_lonlat, node_id, n_nodes)
    blob_members = []   # graph node indices per cell, aligned with `blobs`
    empty_cells = 0
    for k in range(len(uniq)):
        cell = clipped_voronoi_cell(centroids[k], radii[k],
                                    centroids[nb_lists[k]])
        if cell.is_empty or cell.area <= 0:
            empty_cells += 1
            continue
        blobs.append((
            shapely.transform(cell, to_ll),
            int(members_per[k].min()),
            len(members_per[k]),
        ))
        blob_members.append(members_per[k])
    max_n = max(b[2] for b in blobs)
    print(f"[node_blocks] built {len(blobs)} Voronoi cells "
          f"(largest cluster: {max_n} nodes, {empty_cells} empty) "
          f"in {time.time()-t0:.0f}s", flush=True)

    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    blocks_path = os.path.join(out_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(blocks_path))
    feats = [f for f in fc["features"]
             if f["properties"].get("road_class") not in ("node", "foot")]
    dropped = len(fc["features"]) - len(feats)
    if dropped:
        print(f"[node_blocks] dropped {dropped} stale node/foot features "
              f"(rebuilt downstream)", flush=True)

    # Subtract every cell from every street block it intersects.
    geoms = shapely.from_geojson(
        json.dumps({"type": "GeometryCollection",
                    "geometries": [f["geometry"] for f in feats]}))
    polys = np.asarray(shapely.get_parts(geoms) if geoms.geom_type == "GeometryCollection"
                       else [geoms], dtype=object)
    blob_geoms = np.array([b[0] for b in blobs], dtype=object)
    tree = STRtree(blob_geoms)
    block_idx, blob_idx = tree.query(polys, predicate="intersects")
    print(f"[node_blocks] clipping {len(np.unique(block_idx))} street blocks "
          f"touched by node cells…", flush=True)
    cut = 0
    for bi in np.unique(block_idx):
        cutter = union_all(blob_geoms[blob_idx[block_idx == bi]])
        # area_m2 is left as-is: informational only, and the removed area is
        # small against a block's ROW.
        feats[bi]["geometry"] = mapping(shapely.difference(polys[bi], cutter))
        cut += 1

    next_id = max((f["properties"]["block_id"] for f in feats), default=-1) + 1
    first_blob_id = next_id
    for geom_ll, node_id, n_nodes in blobs:
        feats.append({
            "type": "Feature",
            "properties": {
                "block_id": next_id, "seg_id": -1, "road_class": "node",
                "road_name": None,
                "area_m2": round(shapely.area(geom_ll) * mlat * mlon, 1),
                "node_id": node_id, "n_nodes": n_nodes,
            },
            "geometry": mapping(geom_ll),
        })
        next_id += 1

    fc["features"] = feats
    # pid-unique tmp: a stale sibling process fighting over one shared ".tmp"
    # interleaves writes and corrupts the output on os.replace.
    tmp = f"{blocks_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(fc, fh)
    os.replace(tmp, blocks_path)

    # Sidecar for the bake's junction-capture pass (SEPARATE node mapping
    # logic — capture is topological, by cluster membership; see
    # build_edge_blocks.py).
    sidecar = os.path.join(_SERVER, city.data_dir, f"node_clusters_{NETWORK}.npz")
    np.savez(
        sidecar,
        node_idx=np.concatenate(blob_members).astype(np.int64),
        block_id=np.concatenate([
            np.full(len(m), first_blob_id + k, dtype=np.int32)
            for k, m in enumerate(blob_members)
        ]),
    )
    print(f"[node_blocks] clipped {cut} blocks, appended {len(blobs)} node cells "
          f"→ {os.path.basename(blocks_path)} ({len(feats)} features); "
          f"sidecar {os.path.basename(sidecar)} ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
