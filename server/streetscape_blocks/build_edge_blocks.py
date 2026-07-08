#!/usr/bin/env python3
"""Build the edge → block mapping that powers block-level vote display.

Loads the SAME city graph the server votes on (via graph_registry, so edge_id
ordering is identical to the vote store), and assigns each edge a block in
four passes:

  0. JUNCTION CAPTURE (topological — nodes' own mapping logic, separate from
     the polygons): an edge is captured into a junction's node block iff BOTH
     its endpoints are junction-cluster members (node_clusters_<network>.npz,
     written by build_node_blocks.py) AND they share a cluster OR the edge is
     shorter than NODE_CAPTURE_LEN_M (default 30 m — crosswalks between the
     corner clusters flanking a wide avenue). Same-cluster edges go to that
     cluster; short cross-cluster edges go to the cluster whose centroid is
     nearer the edge midpoint. This is what keeps intersection-crossing stubs
     out of PERPENDICULAR street blocks (the "ladder") without eating long
     park paths whose midpoints merely pass near a junction (the old
     midpoint-distance rule captured 63% of all NYC edges).
  1. FOOT SIDECAR: edges assigned by construction by build_foot_blocks.py
     (foot_clusters_<network>.npz) — their block membership is the graph
     component the polygon was generated from, no geometry involved.
  2. containment: the block polygon containing the midpoint;
  3. nearest block within BLOCK_SNAP_M (30 m) — sidewalks sit a few metres off
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
# Pass-0 cross-cluster capture length: an edge between members of two DIFFERENT
# clusters is captured only when this short (crosswalks over a wide avenue run
# ~15-30 m corner to corner; anything longer is a real segment, not a stub).
NODE_CAPTURE_LEN_M = float(os.environ.get("NODE_CAPTURE_LEN_M", "30"))


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

    ends = np.array([(e[0], e[1]) for e in edges], dtype=np.int64)
    nodes_arr = np.asarray(nodes, dtype=np.float64)

    # Edge midpoints as (lon, lat) for shapely.
    lat = (nodes_arr[ends[:, 0], 0] + nodes_arr[ends[:, 1], 0]) / 2.0
    lon = (nodes_arr[ends[:, 0], 1] + nodes_arr[ends[:, 1], 1]) / 2.0
    pts = shp_points(lon, lat)

    feats = json.load(open(BLOCKS_FILE))["features"]
    # Node cells are EXCLUDED from the geometric passes: a junction block's
    # membership comes only from capture (pass 0) and the merge pass's stub
    # rule — letting containment/nearest dump whatever edges pass through a
    # cell turned park forks into confetti (every nearby path fragment glommed
    # into the fork bubble instead of staying with its path).
    geo_feats = [f for f in feats
                 if f["properties"].get("road_class") != "node"]
    polys = [shape(f["geometry"]) for f in geo_feats]
    block_ids = np.array([int(f["properties"]["block_id"]) for f in geo_feats], dtype=np.int64)
    tree = STRtree(polys)
    print(f"[edge_blocks] {len(polys)} street/foot polygons "
          f"(of {len(feats)} features) from {os.path.basename(BLOCKS_FILE)}")

    edge_block = np.full(n_edges, -1, dtype=np.int32)
    seen = np.zeros(n_edges, dtype=bool)

    # Same local-metres frame as the generators.
    s_, w_, n_, e_ = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s_ + n_) / 2))

    # 0) Junction capture — topological (see module docstring).
    captured = 0
    sidecar = os.path.join(
        _SERVER, city.data_dir, f"node_clusters_{NETWORK}.npz")
    if os.path.exists(sidecar):
        nc = np.load(sidecar)
        jn_idx, jn_block = nc["node_idx"], nc["block_id"]
        # node index → cluster block id (−1 = not a junction-cluster member)
        node_cluster = np.full(len(nodes), -1, dtype=np.int64)
        node_cluster[jn_idx] = jn_block
        # Cluster street flag: capture only applies at STREET junctions. A
        # pure-foot fork (park paths, greenways) has no perpendicular street
        # block to protect from the "ladder" — capturing there just shreds the
        # path network into node-cell confetti. Older sidecars lack the flag;
        # treat every cluster as street (the pre-scoping behaviour).
        cluster_is_street = np.ones(len(nodes), dtype=bool)
        if "street" in nc:
            cluster_is_street = np.zeros(len(nodes), dtype=bool)
            cluster_is_street[jn_idx] = nc["street"]

        cu = node_cluster[ends[:, 0]]
        cv = node_cluster[ends[:, 1]]
        su = cluster_is_street[ends[:, 0]]
        sv = cluster_is_street[ends[:, 1]]
        ex = (nodes_arr[ends[:, 0], 1] - nodes_arr[ends[:, 1], 1]) * mlon
        ey = (nodes_arr[ends[:, 0], 0] - nodes_arr[ends[:, 1], 0]) * mlat
        elen = np.hypot(ex, ey)
        not_self = ends[:, 0] != ends[:, 1]
        both = (cu >= 0) & (cv >= 0) & not_self & su & sv

        same = both & (cu == cv)
        edge_block[same] = cu[same]

        # Short cross-cluster stubs → the cluster whose centroid is nearer the
        # edge midpoint (crosswalks between an avenue's two corner clusters).
        cross = both & (cu != cv) & (elen <= NODE_CAPTURE_LEN_M)
        if cross.any():
            # cluster centroids from member node coords, in metres
            jx = (nodes_arr[jn_idx, 1] - w_) * mlon
            jy = (nodes_arr[jn_idx, 0] - s_) * mlat
            n_cl = int(jn_block.max()) + 1
            sums_x = np.bincount(jn_block, weights=jx, minlength=n_cl)
            sums_y = np.bincount(jn_block, weights=jy, minlength=n_cl)
            cnt = np.bincount(jn_block, minlength=n_cl).astype(np.float64)
            cnt[cnt == 0] = 1.0
            cxs, cys = sums_x / cnt, sums_y / cnt
            mx = (lon[cross] - w_) * mlon
            my = (lat[cross] - s_) * mlat
            du = np.hypot(cxs[cu[cross]] - mx, cys[cu[cross]] - my)
            dv = np.hypot(cxs[cv[cross]] - mx, cys[cv[cross]] - my)
            edge_block[cross] = np.where(du <= dv, cu[cross], cv[cross])

        seen |= same | cross
        captured = int((same | cross).sum())
        print(f"[edge_blocks] junction capture: {captured} edges "
              f"({int(same.sum())} same-cluster + {int(cross.sum())} "
              f"cross-cluster ≤{NODE_CAPTURE_LEN_M:.0f}m)")
    else:
        print(f"[edge_blocks] no node-cluster sidecar ({os.path.basename(sidecar)}) "
              "— skipping junction capture")

    # 1) Foot sidecar — membership by construction (build_foot_blocks.py).
    foot_assigned = 0
    foot_sidecar = os.path.join(
        _SERVER, city.data_dir, f"foot_clusters_{NETWORK}.npz")
    if os.path.exists(foot_sidecar):
        fcarr = np.load(foot_sidecar)
        f_idx, f_block = fcarr["edge_idx"], fcarr["block_id"]
        fresh = ~seen[f_idx]
        edge_block[f_idx[fresh]] = f_block[fresh]
        seen[f_idx[fresh]] = True
        foot_assigned = int(fresh.sum())
        print(f"[edge_blocks] foot sidecar: {foot_assigned} edges by construction")

    # 2) Containment: polygon that contains the midpoint (vectorized). shapely's
    # STRtree applies the predicate as input.predicate(tree_geom), so a point is
    # "within" the polygon (NOT polygon "contains" point, which would be false).
    pre_contain = int(seen.sum())
    in_idx, tree_idx = tree.query(pts[~seen], predicate="within")
    # If a midpoint is in multiple blocks (shared boundary), first wins.
    uncap_ids = np.where(~seen)[0]
    for pi, ti in zip(in_idx, tree_idx):
        ei = uncap_ids[pi]
        if not seen[ei]:
            edge_block[ei] = block_ids[ti]
            seen[ei] = True
    contained = int(seen.sum()) - pre_contain

    # 3) Cluster fallback BEFORE nearest-snap: an edge nothing else claimed
    # whose endpoints are junction-cluster members belongs to the junction —
    # fork-internal fragments at NON-street clusters (park path forks) land
    # here (street clusters already took theirs in pass 0). Without this the
    # fragments nearest-snap into a NEIGHBOURING path's tube and the fork cell
    # dies empty (a black gap in the path network).
    cluster_fallback = 0
    if os.path.exists(sidecar):
        cu2 = node_cluster[ends[:, 0]]
        cv2 = node_cluster[ends[:, 1]]
        rest = ~seen & (cu2 >= 0) & (cv2 >= 0) & (ends[:, 0] != ends[:, 1])
        same2 = rest & (cu2 == cv2)
        edge_block[same2] = cu2[same2]
        seen |= same2
        cluster_fallback = int(same2.sum())
        if cluster_fallback:
            print(f"[edge_blocks] cluster fallback: {cluster_fallback} "
                  "leftover intra-junction edges")

    # 4) Nearest-within-threshold for the rest.
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
    nearest_n = snapped - contained - captured - foot_assigned - cluster_fallback
    meta = {
        "city": CITY, "network": NETWORK,
        "n_edges": n_edges, "n_blocks": len(feats),
        "topology_etag": g.topology_etag,
        "blocks_sha256": _sha256_file(BLOCKS_FILE),
        "blocks_file": os.path.basename(BLOCKS_FILE),
        "mapped_edges": snapped,
        "captured_junction": captured,
        "foot_by_construction": foot_assigned,
        "contained": contained,
        "cluster_fallback": cluster_fallback,
        "snapped_nearest": nearest_n,
        "unmapped": n_edges - snapped,
        "block_snap_m": NEAREST_THRESHOLD_M,
        "node_capture_len_m": NODE_CAPTURE_LEN_M,
    }
    json.dump(meta, open(os.path.join(out_dir, f"edge_blocks_{NETWORK}.json"), "w"), indent=2)

    print(f"[edge_blocks] mapped {snapped}/{n_edges} edges "
          f"({100*snapped/n_edges:.1f}%): {captured} junction-captured + "
          f"{foot_assigned} foot-by-construction + {contained} contained + "
          f"{nearest_n} snapped(<{NEAREST_THRESHOLD_M:.0f}m); "
          f"{n_edges-snapped} unmapped")
    print(f"[edge_blocks] wrote {npy}")


if __name__ == "__main__":
    main()
