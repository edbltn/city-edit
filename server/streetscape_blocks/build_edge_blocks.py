#!/usr/bin/env python3
"""Build the edge → block mapping that powers block-level vote display.

Loads the SAME city graph the server votes on (via graph_registry, so edge_id
ordering is identical to the vote store), and assigns each edge a block in
three passes:

  0. JUNCTION CAPTURE (nodes' own mapping logic, separate from the polygons):
     an edge whose midpoint lies within NODE_CAPTURE_M (default 12 m) of a
     junction maps to that junction's node block (node_clusters_<network>.npz,
     written by build_node_blocks.py). The drawn node blobs are deliberately
     smaller (9 m) — capture by graph distance is what keeps intersection-
     crossing stubs out of PERPENDICULAR street blocks (the "ladder").
  1. containment: the block polygon containing the midpoint;
  2. nearest block within BLOCK_SNAP_M (30 m) — sidewalks sit a few metres off
     the centerline.

Writes a dense int32 array `edge_block_id[edge_id] = block_id` (−1 if
unmapped) plus a meta file stamping the topology etag + blocks hash so the server
(and the deploy cache) can tell when it must be rebuilt.

Run in the SERVER venv (it has graph_registry's deps) with shapely installed:
  CITY=nyc NETWORK=streets \
  BLOCKS_FILE=streetscape_blocks/output/blocks_generic_nyc.geojson \
  ./env/bin/python streetscape_blocks/build_edge_blocks.py

Output (next to the graph): osm_data/<city>/edge_blocks_<network>.npy + .json
"""
import hashlib
import json
import math
import os
import sys

import numpy as np

# Make the server package importable when run from server/ or its subdir.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.abspath(os.path.join(_HERE, ".."))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from shapely import STRtree, points as shp_points  # noqa: E402
from shapely.geometry import shape  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
BLOCKS_FILE = os.environ.get(
    "BLOCKS_FILE",
    os.path.join(_HERE, "output", f"blocks_generic_{CITY}.geojson"),
)
# Assign an edge to the nearest block when none contains its midpoint, but only
# within this many metres (a sidewalk edge sits ~a few m off the centerline; a
# foot edge far from any street block legitimately gets no block).
NEAREST_THRESHOLD_M = float(os.environ.get("BLOCK_SNAP_M", "30"))
# Pass-0 junction capture radius: midpoints this close to a junction map to the
# junction's node block regardless of which polygon contains them. Crossing
# stubs' midpoints run to ~12 m from the junction (measured, NYC midtown), past
# the 9 m drawn blob rim — capture must reach them or the ladder comes back.
NODE_CAPTURE_M = float(os.environ.get("NODE_CAPTURE_M", "12"))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main():
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    nodes, edges = g.nodes, g.edges
    n_edges = len(edges)
    print(f"[edge_blocks] {CITY}:{NETWORK} — {n_edges} edges, etag={g.topology_etag}")

    # Edge midpoints as (lon, lat) for shapely.
    lon = np.empty(n_edges); lat = np.empty(n_edges)
    for i, e in enumerate(edges):
        a, b = nodes[e[0]], nodes[e[1]]
        lat[i] = (a[0] + b[0]) / 2.0
        lon[i] = (a[1] + b[1]) / 2.0
    pts = shp_points(lon, lat)

    feats = json.load(open(BLOCKS_FILE))["features"]
    polys = [shape(f["geometry"]) for f in feats]
    block_ids = np.array([int(f["properties"]["block_id"]) for f in feats], dtype=np.int64)
    tree = STRtree(polys)
    print(f"[edge_blocks] {len(polys)} block polygons from {os.path.basename(BLOCKS_FILE)}")

    edge_block = np.full(n_edges, -1, dtype=np.int32)
    seen = np.zeros(n_edges, dtype=bool)

    # 0) Junction capture — nodes' own mapping rule (see module docstring).
    captured = 0
    sidecar = os.path.join(
        _SERVER, city.data_dir, f"node_clusters_{NETWORK}.npz")
    if os.path.exists(sidecar):
        from scipy.spatial import cKDTree
        nc = np.load(sidecar)
        jn_idx, jn_block = nc["node_idx"], nc["block_id"]
        # Same local-metres frame as the generators.
        s_, w_, n_, e_ = city.bbox
        mlat = 111_320.0
        mlon = 111_320.0 * math.cos(math.radians((s_ + n_) / 2))
        nodes_arr = np.asarray(nodes, dtype=np.float64)
        jxy = np.column_stack(((nodes_arr[jn_idx, 1] - w_) * mlon,
                               (nodes_arr[jn_idx, 0] - s_) * mlat))
        mxy = np.column_stack(((lon - w_) * mlon, (lat - s_) * mlat))
        dist, nearest_j = cKDTree(jxy).query(mxy, workers=-1)
        cap = dist <= NODE_CAPTURE_M
        edge_block[cap] = jn_block[nearest_j[cap]]
        seen |= cap
        captured = int(cap.sum())
        print(f"[edge_blocks] junction capture: {captured} edges "
              f"(<= {NODE_CAPTURE_M:.0f}m of a junction)")
    else:
        print(f"[edge_blocks] no node-cluster sidecar ({os.path.basename(sidecar)}) "
              "— skipping junction capture")

    # 1) Containment: polygon that contains the midpoint (vectorized). shapely's
    # STRtree applies the predicate as input.predicate(tree_geom), so a point is
    # "within" the polygon (NOT polygon "contains" point, which would be false).
    in_idx, tree_idx = tree.query(pts[~seen], predicate="within")
    # If a midpoint is in multiple blocks (shared boundary), first wins.
    uncap_ids = np.where(~seen)[0]
    for pi, ti in zip(in_idx, tree_idx):
        ei = uncap_ids[pi]
        if not seen[ei]:
            edge_block[ei] = block_ids[ti]
            seen[ei] = True
    contained = int(seen.sum()) - captured

    # 2) Nearest-within-threshold for the rest.
    missing = np.where(~seen)[0]
    if len(missing):
        nearest = tree.nearest(pts[missing])
        for k, pi in enumerate(missing):
            ti = nearest[k]
            poly = polys[ti]
            d_deg = poly.distance(pts[pi])  # degrees
            # convert to metres at this latitude
            m_per_deg = 111_320 * max(0.1, math.cos(math.radians(lat[pi])))
            if d_deg * m_per_deg <= NEAREST_THRESHOLD_M:
                edge_block[pi] = block_ids[ti]
    snapped = int((edge_block >= 0).sum())

    out_dir = os.path.join(_SERVER, "osm_data", CITY)
    os.makedirs(out_dir, exist_ok=True)
    npy = os.path.join(out_dir, f"edge_blocks_{NETWORK}.npy")
    np.save(npy, edge_block)
    meta = {
        "city": CITY, "network": NETWORK,
        "n_edges": n_edges, "n_blocks": len(polys),
        "topology_etag": g.topology_etag,
        "blocks_sha256": _sha256_file(BLOCKS_FILE),
        "blocks_file": os.path.basename(BLOCKS_FILE),
        "mapped_edges": snapped,
        "captured_junction": captured,
        "contained": contained,
        "snapped_nearest": snapped - contained - captured,
        "unmapped": n_edges - snapped,
        "block_snap_m": NEAREST_THRESHOLD_M,
        "node_capture_m": NODE_CAPTURE_M,
    }
    json.dump(meta, open(os.path.join(out_dir, f"edge_blocks_{NETWORK}.json"), "w"), indent=2)

    print(f"[edge_blocks] mapped {snapped}/{n_edges} edges "
          f"({100*snapped/n_edges:.1f}%): {captured} junction-captured + "
          f"{contained} contained + {snapped-contained-captured} "
          f"snapped(<{NEAREST_THRESHOLD_M:.0f}m); {n_edges-snapped} unmapped")
    print(f"[edge_blocks] wrote {npy}")


if __name__ == "__main__":
    main()
