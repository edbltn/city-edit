#!/usr/bin/env python3
"""Merge degenerate blocks until the block graph is well-formed.

Operates on the BLOCK ADJACENCY GRAPH derived purely from the walk graph and
the baked edge→block mapping (no geometry heuristics): an edge-block and a
node-block are adjacent iff some member edge of the edge-block has an endpoint
in the node block's junction cluster.

Two violations are merged away, iterating to a fixpoint:

  V1  STUB: an edge-block adjacent to exactly ONE node-block, with spatial
      extent ≤ STUB_MAX_M (bbox diagonal of its member-edge endpoints) —
      garage entrances, driveways, alley stubs — merges INTO that node block.
      (Long dead-ends — cul-de-sacs — exceed the extent cap and stay.)

  V2  FAKE JUNCTION: a node-block left adjacent to EXACTLY ONE distinct
      edge-block is not a real junction — it dissolves into that block. (A
      mid-street garage junction ends up back inside its street's block, stub
      included — the W 31st case: the driveway isn't in the drive graph, so
      the street is ONE segment through the junction.) A node between TWO
      distinct blocks stays: dissolving it would weld two different streets
      end-to-end into one corridor (seen live where a pedestrian cross street
      met an avenue — the avenue isn't drive-split there, and the whole cross
      street fused into the avenue's block).

Result: every edge-block touches ≥ 2 node-blocks (or is a big dead-end /
isolated component); every node-block touches ≥ 2 distinct edge-blocks.

Inputs (after the FINAL build_edge_blocks.py pass):
  output/blocks_generic_<city>.geojson   street/node/foot features (pristine)
  osm_data/<city>/edge_blocks_<net>.npy  baked mapping
  osm_data/<city>/node_clusters_<net>.npz  junction-cluster membership

Outputs (the generic file + sidecars stay pristine pipeline intermediates):
  output/blocks_final_<city>.geojson     merged + densely renumbered features
  osm_data/<city>/edge_blocks_<net>.npy  remapped to final block ids
  osm_data/<city>/edge_blocks_<net>.json meta stamped with the FINAL file sha

Run in the SERVER venv (see build_city_blocks.sh, which orders the pipeline):

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/merge_degenerate_blocks.py
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
from shapely import union_all  # noqa: E402
from shapely.geometry import mapping, shape as shp_shape  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
STUB_MAX_M = float(os.environ.get("STUB_MAX_M", "40"))
MAX_ROUNDS = int(os.environ.get("MERGE_MAX_ROUNDS", "25"))


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
    nodes = np.asarray(g.nodes, dtype=np.float64)
    ends = np.array([(e[0], e[1]) for e in g.edges], dtype=np.int64)

    out_dir = os.path.join(_SERVER, "osm_data", CITY)
    npy_path = os.path.join(out_dir, f"edge_blocks_{NETWORK}.npy")
    meta_path = os.path.join(out_dir, f"edge_blocks_{NETWORK}.json")
    meta = json.load(open(meta_path))

    # Merge is destructive on the npy; keep the bake's raw mapping beside it
    # so the merge can be re-run with different knobs without re-baking. The
    # premerge copy is refreshed whenever the bake ran after the last merge
    # (no "merged_from_blocks" stamp in meta yet).
    premerge_path = os.path.join(out_dir, f"edge_blocks_{NETWORK}.premerge.npy")
    if "merged_from_blocks" in meta:
        if not os.path.exists(premerge_path):
            raise SystemExit("[merge] npy is already merged and no premerge copy "
                             "exists — re-run build_edge_blocks.py first")
        print("[merge] npy already merged — re-merging from the premerge copy")
        ebid = np.load(premerge_path).astype(np.int64)
    else:
        ebid = np.load(npy_path).astype(np.int64)
        np.save(premerge_path, ebid.astype(np.int32))

    blocks_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    src_path = os.path.join(blocks_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(src_path))
    feats = fc["features"]
    n_blocks = max(f["properties"]["block_id"] for f in feats) + 1
    feat_by_id: dict[int, dict] = {f["properties"]["block_id"]: f for f in feats}

    # Which block ids are node blocks (junction clusters).
    nc = np.load(os.path.join(out_dir, f"node_clusters_{NETWORK}.npz"))
    jn_idx, jn_block = nc["node_idx"], nc["block_id"]
    is_node_block = np.zeros(n_blocks, dtype=bool)
    is_node_block[np.unique(jn_block)] = True
    node_cluster = np.full(len(nodes), -1, dtype=np.int64)
    node_cluster[jn_idx] = jn_block
    print(f"[merge] {n_blocks} blocks ({int(is_node_block.sum())} node blocks), "
          f"{len(ebid)} edges; stub cap {STUB_MAX_M:.0f}m", flush=True)

    # ── Primitive incidences (fixed; group-level adjacency derives per round) ─
    # (edge-block, node-block) pairs: a member edge endpoint sits in a cluster.
    valid = (ebid >= 0) & (ends[:, 0] != ends[:, 1])
    eb = np.concatenate([ebid[valid], ebid[valid]])
    cl = np.concatenate([node_cluster[ends[valid, 0]], node_cluster[ends[valid, 1]]])
    keep = (cl >= 0) & (eb != cl) & ~is_node_block[eb]
    pairs = np.unique(np.column_stack((eb[keep], cl[keep])), axis=0)
    print(f"[merge] {len(pairs)} primitive edge-block↔node-block incidences",
          flush=True)

    # Per-block bbox extent from member edges (endpoints, degrees → metres).
    s_, w_, n_, e_ = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s_ + n_) / 2))
    minx = np.full(n_blocks, np.inf); maxx = np.full(n_blocks, -np.inf)
    miny = np.full(n_blocks, np.inf); maxy = np.full(n_blocks, -np.inf)
    for col in (0, 1):
        b = ebid[valid]
        x = (nodes[ends[valid, col], 1] - w_) * mlon
        y = (nodes[ends[valid, col], 0] - s_) * mlat
        np.minimum.at(minx, b, x); np.maximum.at(maxx, b, x)
        np.minimum.at(miny, b, y); np.maximum.at(maxy, b, y)
    has_edges = np.isfinite(minx)
    extent = np.where(has_edges,
                      np.hypot(maxx - minx, maxy - miny), 0.0)

    # ── Union-find over block ids ────────────────────────────────────────────
    parent = np.arange(n_blocks)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return int(a)

    # Group kind: True = node-kind (an intersection block). V1 keeps node kind;
    # V2 produces edge kind (corridor).
    kind_node = is_node_block.copy()

    def union(a, b, result_node_kind):
        ra, rb = find(a), find(b)
        if ra == rb:
            return ra
        parent[rb] = ra
        kind_node[ra] = result_node_kind
        # merged extent: combined bbox
        minx[ra] = min(minx[ra], minx[rb]); maxx[ra] = max(maxx[ra], maxx[rb])
        miny[ra] = min(miny[ra], miny[rb]); maxy[ra] = max(maxy[ra], maxy[rb])
        return ra

    v1_total = v2_total = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        roots = np.array([find(a) for a in range(n_blocks)])
        ge = roots[pairs[:, 0]]     # edge-side group
        gn = roots[pairs[:, 1]]     # node-side group
        # After V2 an ex-node group is edge-kind: its primitive pairs now
        # relate two edge-kind groups — drop them. Ditto V1 stubs (node-kind).
        live = kind_node[gn] & ~kind_node[ge] & (ge != gn)
        gpairs = np.unique(np.column_stack((ge[live], gn[live])), axis=0)

        changed = 0

        # V1: edge groups adjacent to exactly one node group, extent-capped.
        eg, counts = np.unique(gpairs[:, 0], return_counts=True)
        stub_groups = set(eg[counts == 1])
        ext_now = np.hypot(maxx[roots] - minx[roots], maxy[roots] - miny[roots])
        for a, b in gpairs:
            if a in stub_groups and ext_now[a] <= STUB_MAX_M:
                union(b, a, result_node_kind=True)
                changed += 1
                v1_total += 1

        # Recompute groups after V1 so V2 sees the absorbed stubs.
        roots = np.array([find(a) for a in range(n_blocks)])
        ge = roots[pairs[:, 0]]
        gn = roots[pairs[:, 1]]
        live = kind_node[gn] & ~kind_node[ge] & (ge != gn)
        gpairs = np.unique(np.column_stack((ge[live], gn[live])), axis=0)

        # V2: a node group adjacent to exactly ONE distinct edge group
        # dissolves into it. Two or more distinct neighbours = a real junction
        # between different streets — dissolving would weld them end-to-end.
        # (gpairs sorted by node column → contiguous slices per node group.)
        by_node = gpairs[np.argsort(gpairs[:, 1], kind="stable")]
        ng, starts = np.unique(by_node[:, 1], return_index=True)
        starts = np.append(starts, len(by_node))
        for k in range(len(ng)):
            adj = by_node[starts[k]:starts[k + 1], 0]
            if len(adj) != 1:
                continue
            union(find(int(adj[0])), int(ng[k]), result_node_kind=False)
            changed += 1
            v2_total += 1

        print(f"[merge] round {rounds}: {changed} merges "
              f"({time.time()-t0:.0f}s)", flush=True)
        if changed == 0:
            break

    roots = np.array([find(a) for a in range(n_blocks)])
    uniq_roots = np.unique(roots)
    print(f"[merge] fixpoint: {n_blocks} → {len(uniq_roots)} blocks "
          f"({v1_total} stub merges, {v2_total} junction dissolves)", flush=True)

    # ── Rebuild features: union member geometries per merged group ──────────
    new_id_of_root = {int(r): i for i, r in enumerate(uniq_roots)}
    members_of_root: dict[int, list[int]] = {}
    for bid in range(n_blocks):
        members_of_root.setdefault(int(roots[bid]), []).append(bid)

    out_feats = []
    merged_geoms = 0
    for r in uniq_roots:
        members = members_of_root[int(r)]
        fs = [feat_by_id[m] for m in members if m in feat_by_id]
        if not fs:
            continue
        if len(fs) == 1:
            geom = fs[0]["geometry"]
        else:
            geom = mapping(union_all([shp_shape(f["geometry"]) for f in fs]))
            merged_geoms += 1
        # Representative properties: node-kind keeps junction identity; a
        # corridor takes the largest street member's naming.
        r_kind_node = bool(kind_node[find(int(r))])
        if r_kind_node:
            node_fs = [f for f in fs if f["properties"].get("road_class") == "node"]
            rep = node_fs[0] if node_fs else fs[0]
        else:
            rep = max(fs, key=lambda f: (f["properties"].get("road_class") != "node",
                                         f["properties"].get("area_m2") or 0))
        props = dict(rep["properties"])
        props["block_id"] = new_id_of_root[int(r)]
        props["n_merged"] = len(fs)
        props["area_m2"] = round(sum(f["properties"].get("area_m2") or 0 for f in fs), 1)
        out_feats.append({"type": "Feature", "properties": props, "geometry": geom})

    dst_path = os.path.join(blocks_dir, f"blocks_final_{CITY}.geojson")
    tmp = f"{dst_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": out_feats}, fh)
    os.replace(tmp, dst_path)
    print(f"[merge] wrote {os.path.basename(dst_path)} "
          f"({len(out_feats)} features, {merged_geoms} unions) "
          f"({time.time()-t0:.0f}s)", flush=True)

    # ── Remap the bake to final ids ──────────────────────────────────────────
    remap = np.full(n_blocks, -1, dtype=np.int32)
    remap[uniq_roots] = [new_id_of_root[int(r)] for r in uniq_roots]
    new_ebid = np.where(ebid >= 0, remap[roots[np.maximum(ebid, 0)]], -1).astype(np.int32)
    np.save(npy_path, new_ebid)

    meta.update({
        "n_blocks": len(out_feats),
        "blocks_sha256": _sha256_file(dst_path),
        "blocks_file": os.path.basename(dst_path),
        "merged_from_blocks": int(n_blocks),
        "merge_stubs": int(v1_total),
        "merge_junction_dissolves": int(v2_total),
        "stub_max_m": STUB_MAX_M,
    })
    json.dump(meta, open(meta_path, "w"), indent=2)
    print(f"[merge] remapped {os.path.basename(npy_path)} + meta "
          f"(sha {meta['blocks_sha256']}) ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
