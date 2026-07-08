#!/usr/bin/env python3
"""Add foot-path blocks for areas the drive-centerline street blocks don't reach.

build_blocks_generic.py covers the street grid; foot edges >30 m from any drive
centerline — park interior paths, pedestrian plazas, boardwalks — stay unmapped
after the pass-1 bake (edge_blocks_<net>.npy == −1). This groups those edges
GRAPH-FIRST and generates geometry from the grouping (not the other way around):

  1. connected components of the unmapped edges, SPLIT AT JUNCTION CLUSTERS —
     traversal never crosses a node that belongs to a junction cluster
     (node_clusters_<network>.npz), so each component is one path segment
     between junctions, the same grain as street blocks;
  2. each component's polygon is the union of ITS OWN buffered edge polylines
     (purely procedural from the composing edges), minus the junction-node
     cells so blocks never overlap;
  3. edge → block membership is recorded BY CONSTRUCTION in a sidecar
     (foot_clusters_<network>.npz) consumed by build_edge_blocks.py's final
     pass — no midpoint-in-polygon geometry round-trip for foot blocks.

Results append to blocks_generic_<city>.geojson (road_class="foot", seg_id=−1,
block_ids continuing from the existing features). Components whose polygon is
swallowed by node cells (or below MIN_AREA) are not emitted; their edges fall
through to the final bake's containment/nearest passes.

After running this, re-run build_edge_blocks.py and rebuild blocks.pmtiles.

Runs in the SERVER venv (graph_registry + shapely). No projection deps: a local
equirectangular frame (metres) is fine for ~6 m buffers at city scale.

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/build_foot_blocks.py
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
from shapely import STRtree, LineString, union_all, buffer as shp_buffer  # noqa: E402
from shapely.geometry import mapping, shape as shp_shape  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
HALF_WIDTH_M = float(os.environ.get("FOOT_HALF_WIDTH_M", "6"))   # buffer radius
MIN_AREA_M2 = float(os.environ.get("FOOT_MIN_AREA_M2", "120"))   # drop slivers


def main():
    t0 = time.time()
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    if g.edge_block_id is None:
        raise SystemExit("no edge_blocks mapping — run build_edge_blocks.py first")

    nodes = np.asarray(g.nodes, dtype=np.float64)   # [N,2] = (lat, lon)
    ends = np.array([(e[0], e[1]) for e in g.edges], dtype=np.int64)
    ebid = np.asarray(g.edge_block_id)
    unmapped = np.where((ebid < 0) & (ends[:, 0] != ends[:, 1]))[0]
    print(f"[foot] {len(unmapped)} unmapped edges of {len(ebid)} "
          f"({100*len(unmapped)/len(ebid):.1f}%)")

    # Junction-cluster members sever components (same grain as street blocks).
    sidecar = os.path.join(_SERVER, city.data_dir, f"node_clusters_{NETWORK}.npz")
    is_junction = np.zeros(len(nodes), dtype=bool)
    if os.path.exists(sidecar):
        is_junction[np.load(sidecar)["node_idx"]] = True
    else:
        print(f"[foot] WARNING: no node-cluster sidecar — components won't "
              "sever at junctions")

    # Union-find over nodes, but never THROUGH a junction-cluster member: an
    # edge with a junction endpoint hangs off the component of its other end.
    parent = np.arange(len(nodes))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for u, v in ends[unmapped]:
        if is_junction[u] or is_junction[v]:
            continue
        ru, rv = find(u), find(v)
        if ru != rv:
            parent[rv] = ru

    # Component key per edge: root of a non-junction endpoint; an edge whose
    # endpoints are BOTH junction members (uncaptured cross-cluster link) is
    # its own singleton component, keyed negatively by edge id.
    comp_of_edge = np.empty(len(unmapped), dtype=np.int64)
    for k, eid in enumerate(unmapped):
        u, v = ends[eid]
        if not is_junction[u]:
            comp_of_edge[k] = find(u)
        elif not is_junction[v]:
            comp_of_edge[k] = find(v)
        else:
            comp_of_edge[k] = -1 - eid
    n_comp = len(np.unique(comp_of_edge))
    print(f"[foot] {n_comp} components (junction-severed) "
          f"({time.time()-t0:.0f}s)", flush=True)

    # Local equirectangular projection (metres) centred on the city bbox.
    s, w, n, e = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s + n) / 2))

    def to_ll_xy(x, y):  # metres -> (lon, lat)
        return x / mlon + w, y / mlat + s

    # Buffer every unmapped edge (vectorized), then union per component.
    lines = np.empty(len(unmapped), dtype=object)
    for k, eid in enumerate(unmapped):
        p, q = nodes[ends[eid, 0]], nodes[ends[eid, 1]]
        lines[k] = LineString([((p[1] - w) * mlon, (p[0] - s) * mlat),
                               ((q[1] - w) * mlon, (q[0] - s) * mlat)])
    print(f"[foot] buffering {len(lines)} segments (r={HALF_WIDTH_M}m)…", flush=True)
    bufs = shp_buffer(lines, HALF_WIDTH_M, cap_style="round", join_style="round")

    order = np.argsort(comp_of_edge, kind="stable")
    sorted_comp = comp_of_edge[order]
    comp_polys = []      # metres-frame polygon per component
    comp_edges = []      # member edge ids per component, aligned
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and sorted_comp[j] == sorted_comp[i]:
            j += 1
        members = order[i:j]
        geom = bufs[members[0]] if len(members) == 1 else union_all(bufs[members])
        comp_polys.append(geom)
        comp_edges.append(unmapped[members])
        i = j
    print(f"[foot] unioned {len(comp_polys)} component tubes "
          f"({time.time()-t0:.0f}s)", flush=True)

    # Load the existing blocks: junction-node cells trim the tubes (blocks
    # must never overlap), and block ids continue from the existing features.
    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    blocks_path = os.path.join(out_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(blocks_path))
    feats = [f for f in fc["features"]
             if f["properties"].get("road_class") != "foot"]  # idempotent re-runs
    dropped = len(fc["features"]) - len(feats)
    if dropped:
        print(f"[foot] dropped {dropped} stale foot features")

    cell_ll = [shp_shape(f["geometry"]) for f in feats
               if f["properties"].get("road_class") == "node"]
    if cell_ll:
        def to_m_geom(a):  # (lon,lat) ndarray -> metres in the same local frame
            return np.column_stack(((a[:, 0] - w) * mlon, (a[:, 1] - s) * mlat))
        cells_m = shapely.transform(np.array(cell_ll, dtype=object), to_m_geom)
        tree = STRtree(cells_m)
        polys_arr = np.array(comp_polys, dtype=object)
        pi, ci = tree.query(polys_arr, predicate="intersects")
        for k in np.unique(pi):
            cutter = union_all(cells_m[ci[pi == k]])
            comp_polys[k] = shapely.difference(comp_polys[k], cutter)
        print(f"[foot] trimmed {len(np.unique(pi))} tubes at junction cells")

    next_id = max((f["properties"]["block_id"] for f in feats), default=-1) + 1
    emitted = 0
    skipped = 0
    sidecar_edges = []
    sidecar_blocks = []
    for poly, members in zip(comp_polys, comp_edges):
        if poly.is_empty or poly.area < MIN_AREA_M2:
            skipped += 1     # edges fall through to containment/nearest
            continue
        def ring_ll(coords):
            return [list(to_ll_xy(x, y)) for x, y in coords]
        parts = shapely.get_parts(poly) if poly.geom_type.startswith("Multi") else [poly]
        coords = [[ring_ll(p.exterior.coords)] + [ring_ll(r.coords) for r in p.interiors]
                  for p in parts]
        geometry = ({"type": "Polygon", "coordinates": coords[0]}
                    if len(coords) == 1 else
                    {"type": "MultiPolygon", "coordinates": coords})
        feats.append({
            "type": "Feature",
            "properties": {
                "block_id": next_id, "seg_id": -1, "road_class": "foot",
                "road_name": None, "area_m2": round(poly.area, 1),
                "n_edges": int(len(members)),
            },
            "geometry": geometry,
        })
        sidecar_edges.append(members)
        sidecar_blocks.append(np.full(len(members), next_id, dtype=np.int32))
        next_id += 1
        emitted += 1

    fc["features"] = feats
    # pid-unique tmp: a stale sibling process fighting over one shared ".tmp"
    # interleaves writes and corrupts the output on os.replace.
    tmp = f"{blocks_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(fc, fh)
    os.replace(tmp, blocks_path)

    foot_sidecar = os.path.join(_SERVER, city.data_dir,
                                f"foot_clusters_{NETWORK}.npz")
    np.savez(
        foot_sidecar,
        edge_idx=(np.concatenate(sidecar_edges).astype(np.int64)
                  if sidecar_edges else np.empty(0, dtype=np.int64)),
        block_id=(np.concatenate(sidecar_blocks)
                  if sidecar_blocks else np.empty(0, dtype=np.int32)),
    )
    print(f"[foot] appended {emitted} foot blocks ({skipped} skipped as "
          f"empty/<{MIN_AREA_M2:.0f}m²) → {os.path.basename(blocks_path)} "
          f"(now {len(feats)} total); sidecar {os.path.basename(foot_sidecar)} "
          f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
