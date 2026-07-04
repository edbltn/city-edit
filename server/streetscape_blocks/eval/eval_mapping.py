#!/usr/bin/env python3
"""Task 1 - edge -> block mapping precision.

Given a study set of edges (eval/manhattan_edges.npz), a set of block polygons,
and a REFERENCE mapping, measure how *precise* cheap heuristics are.

Reference mapping (the gold the production bake also uses):
  - exact point-in-polygon containment of the edge midpoint, else
  - nearest block within SNAP_M metres, else unmapped (-1).

Heuristics under test (each forbidden from using the exact contains test, except
H_exact which is the "do it properly but still indexed" upper bound):
  H_centroid : nearest block CENTROID via KD-tree            (cheapest)
  H_poly     : nearest block POLYGON via STRtree.nearest     (near-exact)
  H_knn_poly : 8 nearest centroids -> contains-test -> nearest of those polys
  H_exact    : containment via STRtree, nearest-poly fallback (== reference build)

precision = (# edges where heuristic block == reference block) / (# reference-labelled edges)
We also report precision on the strictly-contained subset (unambiguous truth) and
the wall-clock cost of each heuristic.
"""
import sys, time, json
from pathlib import Path
import pickle
import numpy as np
import geopandas as gpd
import osmnx as ox
from shapely import STRtree
from shapely.geometry import Point
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
SNAP_M = 30.0            # matches production block_snap_m
UTM = 32618              # UTM 18N (metres) for NYC


def load_blocks(path, bbox):
    g = gpd.read_file(path, bbox=tuple(bbox)).to_crs(UTM)
    g = g.reset_index(drop=True)
    return g


def project_points(lonlat):
    """Project (lon,lat) -> UTM 18N metres, vectorised, no per-point GeoSeries."""
    g = gpd.GeoSeries(gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]), crs=4326).to_crs(UTM)
    return np.column_stack([g.x.values, g.y.values])


def build_reference(pts, polys, tree, centroids, ctree):
    """Containment, then nearest-within-SNAP_M. Returns (ref, contained_mask)."""
    n = len(pts)
    ref = np.full(n, -1, dtype=np.int64)
    contained = np.zeros(n, dtype=bool)
    shp_pts = [Point(x, y) for x, y in pts]

    # vectorised contains: for each point, which polygons contain it
    qi, qj = tree.query(shp_pts, predicate="within")  # qi=point idx, qj=poly idx
    # first poly wins (points on an edge between two blocks are rare)
    for pi, pj in zip(qi, qj):
        if ref[pi] == -1:
            ref[pi] = pj
            contained[pi] = True

    # nearest-within-SNAP_M fallback for the rest
    todo = np.where(ref == -1)[0]
    if len(todo):
        nidx = tree.nearest(np.asarray(shp_pts, dtype=object)[todo])
        for k, pi in enumerate(todo):
            pj = int(nidx[k])
            if polys[pj].distance(shp_pts[pi]) <= SNAP_M:
                ref[pi] = pj
    return ref, contained


def h_centroid(pts, centroids, ctree, **_):
    _, idx = ctree.query(pts, k=1)
    return idx.astype(np.int64)


def h_poly(pts, polys, tree, **_):
    shp_pts = [Point(x, y) for x, y in pts]
    return tree.nearest(shp_pts).astype(np.int64)


def h_knn_poly(pts, polys, tree, centroids, ctree, **_):
    """8 nearest centroids, prefer one that contains the point, else nearest poly."""
    k = min(8, len(centroids))
    _, idx = ctree.query(pts, k=k)
    out = np.empty(len(pts), dtype=np.int64)
    for i, (x, y) in enumerate(pts):
        cand = idx[i]
        p = Point(x, y)
        best, bestd = -1, 1e18
        hit = -1
        for j in cand:
            d = polys[j].distance(p)
            if d == 0.0:
                hit = j
                break
            if d < bestd:
                bestd, best = d, j
        out[i] = hit if hit >= 0 else best
    return out


def build_seg_index(bbox, seg_to_block):
    """Centerline STRtree over the drive graph + map each kept seg_id -> block idx."""
    G = pickle.load(open(HERE.parent / "output" / "nyc_drive_consolidated.pkl", "rb"))
    seg = ox.graph_to_gdfs(ox.convert.to_undirected(G), nodes=False).reset_index(drop=True)
    seg["seg_id"] = range(len(seg))
    # the consolidated drive graph is already projected to UTM 18N (EPSG:32618);
    # filter by the bbox corners projected into the same CRS.
    if seg.crs is None:
        seg = seg.set_crs(UTM)
    elif seg.crs.to_epsg() != UTM:
        seg = seg.to_crs(UTM)
    lon0, lat0, lon1, lat1 = bbox
    (x0, y0), (x1, y1) = project_points(np.array([[lon0, lat0], [lon1, lat1]]))
    seg = seg.cx[x0:x1, y0:y1].reset_index(drop=True)
    lines = list(seg.geometry.values)
    seg_ids = seg["seg_id"].values
    tree = STRtree(lines)
    # block index that each line's seg_id resolves to (-1 if no block for that seg)
    line_block = np.asarray([seg_to_block.get(int(s), -1) for s in seg_ids], dtype=np.int64)
    return tree, line_block


def make_h_seg(bbox, seg_to_block):
    tree, line_block = build_seg_index(bbox, seg_to_block)

    def h_seg(pts, **_):
        shp_pts = [Point(x, y) for x, y in pts]
        nidx = tree.nearest(shp_pts)
        return line_block[nidx]

    return h_seg


HEURISTICS = {
    "H_centroid": h_centroid,
    "H_poly": h_poly,
    "H_knn_poly": h_knn_poly,
}


def main(blocks_path="output/blocks_generic_nyc.geojson", edges="manhattan_edges.npz"):
    blocks_path = (HERE.parent / blocks_path) if not Path(blocks_path).is_absolute() else Path(blocks_path)
    z = np.load(HERE / edges)
    bbox = z["bbox"]
    mids = z["edge_mid"]
    t = time.time()
    blocks = load_blocks(blocks_path, bbox)
    polys = list(blocks.geometry.values)
    tree = STRtree(polys)
    centroids = np.column_stack([blocks.geometry.centroid.x.values,
                                 blocks.geometry.centroid.y.values])
    ctree = cKDTree(centroids)
    pts = project_points(mids)
    print(f"blocks={len(blocks)} edges={len(pts)} loaded in {time.time()-t:.1f}s", flush=True)

    t = time.time()
    ref, contained = build_reference(pts, polys, tree, centroids, ctree)
    labelled = ref >= 0
    print(f"reference: contained={contained.sum()} "
          f"snapped={(labelled & ~contained).sum()} unmapped={(~labelled).sum()} "
          f"({time.time()-t:.1f}s)", flush=True)
    print(f"  COVERAGE GAP: {(~labelled).mean()*100:.1f}% of edges have no block "
          f"(contained gap {(~contained).mean()*100:.1f}%)", flush=True)

    # segment-anchored heuristic needs a seg_id -> block-row map
    seg_to_block = {int(s): i for i, s in enumerate(blocks["seg_id"].values)}
    t = time.time()
    heuristics = dict(HEURISTICS)
    heuristics["H_seg"] = make_h_seg(bbox, seg_to_block)
    print(f"built segment index in {time.time()-t:.1f}s", flush=True)

    kw = dict(polys=polys, tree=tree, centroids=centroids, ctree=ctree)
    results = {}
    for name, fn in heuristics.items():
        t = time.time()
        pred = fn(pts, **kw)
        dt = time.time() - t
        match_all = (pred[labelled] == ref[labelled]).mean()
        match_cont = (pred[contained] == ref[contained]).mean()
        results[name] = dict(precision_labelled=round(float(match_all), 4),
                             precision_contained=round(float(match_cont), 4),
                             sec=round(dt, 2))
        print(f"  {name:12s} precision(labelled)={match_all*100:5.1f}%  "
              f"precision(contained)={match_cont*100:5.1f}%  {dt:5.2f}s", flush=True)

    summary = dict(blocks=str(blocks_path.name), n_blocks=len(blocks), n_edges=len(pts),
                   contained=int(contained.sum()), snapped=int((labelled & ~contained).sum()),
                   unmapped=int((~labelled).sum()), coverage_gap=round(float((~labelled).mean()), 4),
                   heuristics=results)
    (HERE / "results_mapping.json").write_text(json.dumps(summary, indent=2))
    print("wrote results_mapping.json", flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
