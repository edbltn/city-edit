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
from shapely import make_valid, union_all  # noqa: E402
from shapely.geometry import mapping, shape as shp_shape  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
STUB_MAX_M = float(os.environ.get("STUB_MAX_M", "25"))
MAX_ROUNDS = int(os.environ.get("MERGE_MAX_ROUNDS", "25"))
# V1 class affinity: a stub only merges into its junction when ALL its member
# edges are driveway-like — a named residential/secondary dead-end (or a
# footway pill that carries street-class pieces) keeps its own block. Field
# case: a ~40 m footway pill at Broadway/W 26th (plaza-class Broadway) merged
# through V1+V2 into the West 26th Street block and read as part of the wrong
# street.
STUBBY_CLASSES = {"service", "footway", "path", "steps", "cycleway",
                  "pedestrian", "track", ""}
# Edges-are-the-source-of-truth guard: a polygon whose bbox diagonal exceeds its
# member edges' bbox diagonal by more than this is a PHANTOM (its shape came
# from a drive centerline with no votable edges — e.g. a motorway ROW whose only
# members are a footway overpass); its geometry is regenerated as the buffered
# union of exactly its member edges. Features with ZERO member edges are dropped
# outright (nothing can ever light or select them).
TRIM_SLACK_M = float(os.environ.get("TRIM_SLACK_M", "60"))
# Tube radius per member-edge road class when regenerating a phantom's geometry
# (same half-widths as build_blocks_generic.py, small pad so the band reads).
HALF_WIDTH = {
    "motorway": 18.0, "motorway_link": 12.0, "trunk": 16.0, "trunk_link": 11.0,
    "primary": 14.0, "primary_link": 10.0, "secondary": 11.0, "secondary_link": 9.0,
    "tertiary": 9.0, "tertiary_link": 8.0, "residential": 8.0, "living_street": 7.0,
    "unclassified": 8.0, "service": 6.0, "pedestrian": 6.0, "footway": 4.0,
    "path": 4.0, "cycleway": 4.0,
}
DEFAULT_HALF_WIDTH = 8.0
TUBE_PAD_M = 2.0


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

    # Per-block V1 affinity: every member edge is driveway-like. Maintained
    # through unions (a merged group is stubby only if all parts are).
    edge_stubby = np.array(
        [str(e[3]) if len(e) > 3 else "" for e in g.edges]) \
        .astype(object)
    edge_stubby = np.array([c in STUBBY_CLASSES for c in edge_stubby])
    stubby = np.ones(n_blocks, dtype=bool)
    np.logical_and.at(stubby, ebid[valid], edge_stubby[valid])

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
        stubby[ra] = stubby[ra] and stubby[rb]
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

        # V1: edge groups adjacent to exactly one node group, extent-capped and
        # driveway-like (class affinity — see STUBBY_CLASSES).
        eg, counts = np.unique(gpairs[:, 0], return_counts=True)
        stub_groups = set(eg[counts == 1])
        ext_now = np.hypot(maxx[roots] - minx[roots], maxy[roots] - miny[roots])
        for a, b in gpairs:
            if a in stub_groups and ext_now[a] <= STUB_MAX_M and stubby[find(int(a))]:
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

    # ── Final edge membership per merged group (root-keyed) ─────────────────
    edge_root = np.where(ebid >= 0, roots[np.maximum(ebid, 0)], -1)
    e_order = np.argsort(edge_root, kind="stable")
    e_sorted = edge_root[e_order]
    first_valid = np.searchsorted(e_sorted, 0, side="left")
    r_uniq, r_starts = np.unique(e_sorted[first_valid:], return_index=True)
    r_starts = np.append(r_starts + first_valid, len(e_sorted))
    edges_of_root: dict[int, np.ndarray] = {
        int(r_uniq[k]): e_order[r_starts[k]:r_starts[k + 1]]
        for k in range(len(r_uniq))
    }
    # ── Adopt empty node cells into their dominant adjacent block ───────────
    # A simple fork (single-node cluster) has no internal edges, so its cell
    # would render nothing and hover-dead — dropping it left black gaps in
    # path networks. Instead the cell joins the adjacent block that owns most
    # of the fork's incident edges (pure union-find, before features build).
    empty_node_roots = {int(r) for r in uniq_roots
                        if kind_node[find(int(r))] and int(r) not in edges_of_root}
    adopted = 0
    if empty_node_roots:
        cluster_root_of_node = np.full(len(nodes), -1, dtype=np.int64)
        cluster_root_of_node[jn_idx] = roots[jn_block]
        votes: dict[int, dict[int, int]] = {}
        for col in (0, 1):
            nds = ends[:, col]
            cr = cluster_root_of_node[nds]
            ok = (cr >= 0) & (edge_root >= 0) & (cr != edge_root)
            for c, er in zip(cr[ok], edge_root[ok]):
                if int(c) in empty_node_roots:
                    d = votes.setdefault(int(c), {})
                    d[int(er)] = d.get(int(er), 0) + 1
        for c, d in votes.items():
            target = max(d, key=d.get)
            tr = find(target)
            union(tr, c, result_node_kind=bool(kind_node[tr]))
            adopted += 1
        if adopted:
            # Re-derive groups after adoption (edges keep their roots — the
            # adopted cells had none).
            roots = np.array([find(a) for a in range(n_blocks)])
            uniq_roots = np.unique(roots)
            edge_root = np.where(ebid >= 0, roots[np.maximum(ebid, 0)], -1)
            e_order = np.argsort(edge_root, kind="stable")
            e_sorted = edge_root[e_order]
            first_valid = np.searchsorted(e_sorted, 0, side="left")
            r_uniq, r_starts = np.unique(e_sorted[first_valid:], return_index=True)
            r_starts = np.append(r_starts + first_valid, len(e_sorted))
            edges_of_root = {
                int(r_uniq[k]): e_order[r_starts[k]:r_starts[k + 1]]
                for k in range(len(r_uniq))
            }
        print(f"[merge] adopted {adopted} empty node cells into their "
              f"dominant neighbour", flush=True)

    rc_of_edge = np.array([str(e[3]) if len(e) > 3 else "" for e in g.edges])
    ex = (nodes[:, 1] - w_) * mlon
    ey = (nodes[:, 0] - s_) * mlat

    def member_tube(eids: np.ndarray):
        """Union of the member edges' class-width buffers (metres frame) —
        the edges-as-source-of-truth geometry for a phantom block."""
        lines = np.empty(len(eids), dtype=object)
        radii = np.empty(len(eids))
        for k, eid in enumerate(eids):
            u, v = ends[eid]
            lines[k] = shapely.LineString([(ex[u], ey[u]), (ex[v], ey[v])])
            radii[k] = HALF_WIDTH.get(rc_of_edge[eid], DEFAULT_HALF_WIDTH) + TUBE_PAD_M
        return union_all(shapely.buffer(lines, radii, quad_segs=4))

    to_ll = lambda a: np.column_stack((a[:, 0] / mlon + w_, a[:, 1] / mlat + s_))

    # ── Rebuild features: union member geometries per merged group ──────────
    members_of_root: dict[int, list[int]] = {}
    for bid in range(n_blocks):
        members_of_root.setdefault(int(roots[bid]), []).append(bid)

    out_feats = []          # (root, props, geometry) — ids assigned after drops
    merged_geoms = 0
    dropped_empty = 0
    regenerated = 0
    for r in uniq_roots:
        members = members_of_root[int(r)]
        fs = [feat_by_id[m] for m in members if m in feat_by_id]
        if not fs:
            continue

        eids = edges_of_root.get(int(r))
        # Zero member edges → nothing can ever light, hover or select this
        # feature; drop it (phantom drive slivers, isolated cells — empty
        # node cells with graph neighbours were adopted above).
        if eids is None or len(eids) == 0:
            dropped_empty += 1
            continue

        if len(fs) == 1:
            geom = fs[0]["geometry"]
        else:
            # make_valid: punched street polygons can carry self-touching
            # slivers that make GEOS throw "side location conflict" on union.
            geom = mapping(union_all([make_valid(shp_shape(f["geometry"]))
                                      for f in fs]))
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
        props["n_merged"] = len(fs)
        props["area_m2"] = round(sum(f["properties"].get("area_m2") or 0 for f in fs), 1)

        # Phantom guard (non-node features): polygon must not sprawl far past
        # its member edges — a drive-only corridor (motorway ROW) can carry a
        # huge polygon whose only members are an overpass. Regenerate such
        # geometry from the edges themselves.
        if not r_kind_node:
            u_ids = ends[eids].ravel()
            e_diag = math.hypot(ex[u_ids].max() - ex[u_ids].min(),
                                ey[u_ids].max() - ey[u_ids].min())
            gsh = shp_shape(geom)
            bx = gsh.bounds
            p_diag = math.hypot((bx[2] - bx[0]) * mlon, (bx[3] - bx[1]) * mlat)
            if p_diag > e_diag + TRIM_SLACK_M:
                tube = member_tube(eids)
                geom = mapping(shapely.transform(tube, to_ll))
                props["area_m2"] = round(tube.area, 1)
                props["regen"] = "edges"
                regenerated += 1

        out_feats.append((int(r), props, geom))

    print(f"[merge] edges-as-truth pass: {dropped_empty} zero-member features "
          f"dropped, {regenerated} phantom geometries regenerated from edges",
          flush=True)

    # ── Contiguity pass: a block is ALWAYS one connected polygon ─────────────
    # Disjoint geometry (a band severed by a punched cell, a tube cut by a
    # street cell, satellite pieces from merges) EXPLODES into one block per
    # part; member edges are re-partitioned by midpoint (containment, else
    # nearest part); parts left with no members drop.
    mid_lon_deg = (nodes[ends[:, 0], 1] + nodes[ends[:, 1], 1]) / 2.0
    mid_lat_deg = (nodes[ends[:, 0], 0] + nodes[ends[:, 1], 0]) / 2.0
    final_entries = []   # (props, geom_mapping, member_edge_ids ndarray)
    split_blocks = 0
    split_parts_dropped = 0
    for r, props, geom in out_feats:
        eids = edges_of_root[int(r)]
        shp = make_valid(shp_shape(geom))
        parts = [p for p in shapely.get_parts(shp)
                 if p.geom_type == "Polygon" and p.area > 0]
        if len(parts) == 0:
            continue  # degenerate geometry, nothing real to draw
        if len(parts) == 1:
            # Normalize: even a "single-part" original can be a MultiPolygon
            # JSON with zero-area siblings — always emit the one real Polygon.
            final_entries.append((props, mapping(parts[0]), eids))
            continue
        split_blocks += 1
        pts = shapely.points(mid_lon_deg[eids], mid_lat_deg[eids])
        # containment first, nearest part for the rest — every edge stays owned
        part_of = np.full(len(eids), -1, dtype=np.int64)
        tree_p = shapely.STRtree(parts)
        got_i, got_p = tree_p.query(pts, predicate="within")
        part_of[got_i] = got_p
        rest = np.where(part_of < 0)[0]
        if len(rest):
            part_of[rest] = tree_p.nearest(pts[rest])
        for k, part in enumerate(parts):
            members = eids[part_of == k]
            if len(members) == 0:
                split_parts_dropped += 1
                continue
            p2 = dict(props)
            p2["n_split"] = len(parts)
            p2["area_m2"] = round(part.area * mlat * mlon, 1)
            final_entries.append((p2, mapping(part), members))
    if split_blocks:
        print(f"[merge] contiguity pass: {split_blocks} disjoint blocks "
              f"exploded into parts ({split_parts_dropped} empty parts "
              f"dropped)", flush=True)

    # ── Dense renumber; edge mapping comes straight from feature membership ──
    feats_json = []
    new_ebid = np.full(len(ebid), -1, dtype=np.int32)
    for i, (props, geom, eids) in enumerate(final_entries):
        props["block_id"] = i
        new_ebid[eids] = i
        feats_json.append({"type": "Feature", "properties": props, "geometry": geom})

    dst_path = os.path.join(blocks_dir, f"blocks_final_{CITY}.geojson")
    tmp = f"{dst_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats_json}, fh)
    os.replace(tmp, dst_path)
    print(f"[merge] wrote {os.path.basename(dst_path)} "
          f"({len(feats_json)} features, {merged_geoms} unions) "
          f"({time.time()-t0:.0f}s)", flush=True)

    np.save(npy_path, new_ebid)

    meta.update({
        "n_blocks": len(feats_json),
        "blocks_sha256": _sha256_file(dst_path),
        "blocks_file": os.path.basename(dst_path),
        "merged_from_blocks": int(n_blocks),
        "merge_stubs": int(v1_total),
        "merge_junction_dissolves": int(v2_total),
        "stub_max_m": STUB_MAX_M,
        "dropped_zero_member": int(dropped_empty),
        "regenerated_from_edges": int(regenerated),
        "trim_slack_m": TRIM_SLACK_M,
    })
    json.dump(meta, open(meta_path, "w"), indent=2)
    print(f"[merge] remapped {os.path.basename(npy_path)} + meta "
          f"(sha {meta['blocks_sha256']}) ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
