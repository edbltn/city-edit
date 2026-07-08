#!/usr/bin/env python3
"""Give every junction its own block — one merged blob per junction CLUSTER,
punched out of the street/foot blocks (docs/three-layer-model.md §2).

Why: block polygons extend across intersections (the Voronoi assigns the
intersection flare to ONE of the crossing segments), so the short graph edges
that cross an intersection get baked into a PERPENDICULAR street's block. A
route down an avenue then selects — and block-scoped casting votes on — every
cross street it passes ("ladder" selections). Junction blocks catch those
edges' midpoints instead, so a path only ever touches blocks that share its
orientation, plus the intersections themselves.

Nodes and edges get SEPARATE block-forming logic: edges partition the ROW by
segment-Voronoi (build_blocks_generic.py); junction nodes each contribute a
disc of NODE_BLOCK_RADIUS_M, and discs that OVERLAP merge into one multi-node
block (a physical intersection is several OSM junction nodes — centerline
node, crossing ends, sidewalk corners — one per ~5-15 m; per-node discs drew
as stacked circles). Union-find over center pairs closer than 2R, then
union_all per cluster.

What it does, given blocks_generic_<city>.geojson:
  1. drops any road_class in {"node","foot"} features (idempotent re-runs; foot
     blocks are rebuilt afterwards by build_foot_blocks.py);
  2. finds junction nodes of the city walk graph — unique-neighbour degree >= 3
     (degree-2 geometry nodes and dead ends stay part of their street block);
  3. buffers each into a disc of NODE_BLOCK_RADIUS_M (default 9 m — small
     enough that same-intersection blobs stay intersection-sized, still > the
     6 m foot tube so build_foot_blocks' mesh severs at junctions);
  4. merges overlapping discs into one polygon per connected cluster;
  5. subtracts the cluster blobs from every street block they intersect
     (blocks and node blobs never overlap — bake containment stays
     unambiguous);
  6. appends the blobs as features: road_class="node", seg_id=-1,
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
from shapely import STRtree, buffer as shp_buffer, points as shp_points, union_all  # noqa: E402
from shapely.geometry import mapping  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
RADIUS_M = float(os.environ.get("NODE_BLOCK_RADIUS_M", "9"))
# Discs are 16-gons: plenty round at 9 m, keeps the geojson/tiles small.
QUAD_SEGS = int(os.environ.get("NODE_BLOCK_QUAD_SEGS", "4"))


def junction_nodes(g) -> np.ndarray:
    """Indices of nodes with unique-neighbour degree >= 3 (true junctions)."""
    # edge rows are [u, v, name, road_class, length_m] — take the endpoints only
    E = np.array([(e[0], e[1]) for e in g.edges], dtype=np.int64)
    E = E[E[:, 0] != E[:, 1]]                      # self-edges don't add degree
    und = np.unique(np.sort(E, axis=1), axis=0)    # unique undirected pairs
    deg = np.bincount(und.ravel(), minlength=len(g.nodes))
    return np.where(deg >= 3)[0]


def cluster_junctions(xy: np.ndarray, link_dist: float) -> np.ndarray:
    """Union-find label per junction: centers closer than link_dist (= 2R,
    i.e. overlapping discs) share a cluster. Returns int labels, one per row."""
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
    return np.array([find(i) for i in range(len(xy))])


def main():
    t0 = time.time()
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    nodes = np.asarray(g.nodes, dtype=np.float64)  # [N,2] = (lat, lon)

    junctions = junction_nodes(g)
    print(f"[node_blocks] {len(junctions)} junction nodes "
          f"(deg>=3) of {len(nodes)} — r={RADIUS_M}m", flush=True)

    # Same local equirectangular frame as build_foot_blocks.py so both scripts
    # agree exactly on where a blob's boundary falls.
    s, w, n, e = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s + n) / 2))

    xs = (nodes[junctions, 1] - w) * mlon
    ys = (nodes[junctions, 0] - s) * mlat
    xy = np.column_stack((xs, ys))
    labels = cluster_junctions(xy, link_dist=2 * RADIUS_M)
    n_clusters = len(np.unique(labels))
    print(f"[node_blocks] {n_clusters} clusters from {len(junctions)} junctions "
          f"({time.time()-t0:.0f}s)", flush=True)

    discs_m = shp_buffer(shp_points(xs, ys), RADIUS_M, quad_segs=QUAD_SEGS)
    to_ll = lambda a: np.column_stack((a[:, 0] / mlon + w, a[:, 1] / mlat + s))

    # One blob per cluster: union of the member discs, converted to lon/lat.
    order = np.argsort(labels, kind="stable")
    blobs = []          # (blob_lonlat, node_id, n_nodes)
    blob_members = []   # graph node indices per blob, aligned with `blobs`
    i = 0
    sorted_labels = labels[order]
    while i < len(order):
        j = i
        while j < len(order) and sorted_labels[j] == sorted_labels[i]:
            j += 1
        members = order[i:j]
        geom = discs_m[members[0]] if len(members) == 1 else union_all(discs_m[members])
        blobs.append((
            shapely.transform(geom, to_ll),
            int(junctions[members.min()]),
            len(members),
        ))
        blob_members.append(junctions[members])
        i = j
    max_n = max(b[2] for b in blobs)
    print(f"[node_blocks] built {len(blobs)} blobs (largest cluster: {max_n} "
          f"nodes) in {time.time()-t0:.0f}s", flush=True)

    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    blocks_path = os.path.join(out_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(blocks_path))
    feats = [f for f in fc["features"]
             if f["properties"].get("road_class") not in ("node", "foot")]
    dropped = len(fc["features"]) - len(feats)
    if dropped:
        print(f"[node_blocks] dropped {dropped} stale node/foot features "
              f"(rebuilt downstream)", flush=True)

    # Subtract every blob from every street block it intersects.
    geoms = shapely.from_geojson(
        json.dumps({"type": "GeometryCollection",
                    "geometries": [f["geometry"] for f in feats]}))
    polys = np.asarray(shapely.get_parts(geoms) if geoms.geom_type == "GeometryCollection"
                       else [geoms], dtype=object)
    blob_geoms = np.array([b[0] for b in blobs], dtype=object)
    tree = STRtree(blob_geoms)
    block_idx, blob_idx = tree.query(polys, predicate="intersects")
    print(f"[node_blocks] clipping {len(np.unique(block_idx))} street blocks "
          f"touched by node blobs…", flush=True)
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

    # Sidecar for the bake's junction-capture pass (SEPARATE node mapping logic:
    # the drawn blobs are 9 m, but any edge whose midpoint is within
    # NODE_CAPTURE_M of a junction maps to that junction's block by GRAPH
    # distance — stub midpoints run to ~12 m, past the drawn rim).
    sidecar = os.path.join(_SERVER, city.data_dir, f"node_clusters_{NETWORK}.npz")
    np.savez(
        sidecar,
        node_idx=np.concatenate(blob_members).astype(np.int64),
        block_id=np.concatenate([
            np.full(len(m), first_blob_id + k, dtype=np.int32)
            for k, m in enumerate(blob_members)
        ]),
    )
    print(f"[node_blocks] clipped {cut} blocks, appended {len(blobs)} node blobs "
          f"→ {os.path.basename(blocks_path)} ({len(feats)} features); "
          f"sidecar {os.path.basename(sidecar)} ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
