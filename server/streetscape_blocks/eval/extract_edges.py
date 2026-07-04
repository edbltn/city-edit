#!/usr/bin/env python3
"""Extract a study set of graph edges/nodes for a bbox and cache to .npz.

We pull every undirected edge of the walk graph whose midpoint lands inside the
bbox, plus the unique endpoint nodes. Everything downstream (reference mapping,
heuristics) reads this cache, so the heavy 297 MB pickle is loaded exactly once.

Edge representative point = midpoint of its two endpoints (lon, lat). That is what
the production edge->block bake snaps, so it is the natural unit to evaluate.
"""
import sys, time, pickle
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
GRAPH = HERE.parent.parent / "osm_data" / "nyc" / "walk_graph.pkl"

# Manhattan-ish bbox (lon_min, lat_min, lon_max, lat_max). Spills slightly into
# adjacent boroughs / the rivers on purpose: those edges are the coverage-gap
# cases (no containing block) we care about.
BBOX = (-74.02, 40.70, -73.93, 40.82)


def main(out_name="manhattan_edges.npz", bbox=BBOX):
    t = time.time()
    print(f"loading {GRAPH.name} ...", flush=True)
    d = pickle.load(open(GRAPH, "rb"))
    G = d["graph"]
    lon0, lat0, lon1, lat1 = bbox

    # node coords keyed by osm id
    xs = {n: (data["x"], data["y"]) for n, data in G.nodes(data=True)}

    seen = set()
    e_u, e_v, mids = [], [], []
    for u, v in G.edges():
        key = (u, v) if u < v else (v, u)
        if key in seen:
            continue
        seen.add(key)
        (xu, yu), (xv, yv) = xs[u], xs[v]
        mx, my = (xu + xv) / 2.0, (yu + yv) / 2.0
        if not (lon0 <= mx <= lon1 and lat0 <= my <= lat1):
            continue
        e_u.append(u); e_v.append(v); mids.append((mx, my))

    mids = np.asarray(mids, dtype=np.float64)
    # unique nodes that touch a kept edge, with coords
    node_ids = sorted(set(e_u) | set(e_v))
    node_xy = np.asarray([xs[n] for n in node_ids], dtype=np.float64)

    out = HERE / out_name
    np.savez(
        out,
        edge_u=np.asarray(e_u, dtype=np.int64),
        edge_v=np.asarray(e_v, dtype=np.int64),
        edge_mid=mids,                       # (n_edges, 2) lon/lat
        node_id=np.asarray(node_ids, dtype=np.int64),
        node_xy=node_xy,                     # (n_nodes, 2) lon/lat
        bbox=np.asarray(bbox),
    )
    print(f"  edges={len(mids)}  nodes={len(node_ids)}  -> {out.name}  {time.time()-t:.1f}s",
          flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
