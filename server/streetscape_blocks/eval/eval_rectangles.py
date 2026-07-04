#!/usr/bin/env python3
"""Rectangle (flat-cap buffer) blocks vs capsule (Voronoi) blocks.

Rectangles: each segment -> segment.buffer(W, cap_style='flat'), so the polygon ends
exactly at the two nodes and its long sides run parallel to the edge. The edge/nodes
define the boundary. Downside: rectangles OVERLAP at intersections (no tiling), so we
also measure that overlap.

Compared on the same Midtown window and metrics as eval_generate.py, against brook.
"""
import time
import numpy as np
import geopandas as gpd
from shapely.ops import unary_union
from shapely import box as shp_box

from eval_generate import (load_segments_utm, window_box, area_metrics, iou_mean,
                           CLASS_W, DEFAULT_W, _class_of, BBOX, MIN_AREA, UTM, OUT)


def rect_blocks(seg, win, scale=1.0, flat=True):
    """One flat-cap (rectangle) or round-cap buffer per segment, clipped to window."""
    cand = seg[seg.geometry.intersects(win.buffer(40))].copy()
    cap = "flat" if flat else "round"
    geoms, ids = [], []
    for sid, g, hwy in zip(cand.seg_id, cand.geometry, cand["highway"]):
        w = CLASS_W.get(_class_of(hwy), DEFAULT_W) * scale
        r = g.buffer(w, cap_style=cap, join_style="mitre").intersection(win)
        if r.is_empty or r.area <= MIN_AREA:
            continue
        geoms.append(r); ids.append(sid)
    return gpd.GeoDataFrame({"seg_id": ids}, geometry=geoms, crs=UTM)


def overlap_stats(gdf, win):
    """How badly do the blocks overlap? sum(area)/area(union) - 1, and the
    fraction of the covered area that is multiply-covered."""
    areas = gdf.geometry.area.values
    total = float(areas.sum())
    union = unary_union(gdf.geometry.values)
    uarea = float(union.area)
    overlap_ratio = total / uarea - 1.0           # 0 == perfect tiling
    return dict(sum_area=round(total), union_area=round(uarea),
                overlap_ratio=round(overlap_ratio, 4),
                multicovered_frac=round((total - uarea) / total, 4))


def main():
    seg = load_segments_utm()
    brook = gpd.read_file(OUT / "blocks_nyc.geojson", bbox=BBOX).to_crs(UTM)
    win = window_box((-73.985, 40.758), half_m=750.0)
    bw = brook[brook.geometry.intersects(win)].copy()
    bw["geometry"] = bw.geometry.intersection(win)
    bw = bw[bw.geometry.area > MIN_AREA]
    bt = bw.groupby("seg_id").geometry.apply(lambda s: unary_union(s.values))
    print(f"window: {len(bt)} brook segs\n", flush=True)

    def score(label, gen):
        gt = gen.dissolve("seg_id").geometry
        common = sorted(set(bt.index) & set(gt.index))
        a_truth = np.array([bt[s].area for s in common])
        a_gen = np.array([gt[s].area for s in common])
        m = area_metrics(a_gen, a_truth)
        ov = overlap_stats(gen, win)
        print(f"  {label:22s} matched={len(common):4d}  area_rmse={m['area_rmse']:7.0f}  "
              f"med|relerr|={m['med_abs_relerr']:.3f}  ratio={m['mean_area_ratio']:.3f}  "
              f"IoU={iou_mean(gen.dissolve('seg_id').reset_index(), bw):.3f}  "
              f"overlap={ov['overlap_ratio']:.3f}  multicov={ov['multicovered_frac']:.3f}",
              flush=True)

    # rectangles at a few class-width scales
    for sc in (1.0, 1.1, 1.2):
        score(f"rectangle x{sc}", rect_blocks(seg, win, scale=sc, flat=True))
    # round-cap (capsule) buffer for contrast — same overlap problem, rounded ends
    score("roundcap x1.1", rect_blocks(seg, win, scale=1.1, flat=False))


if __name__ == "__main__":
    main()
