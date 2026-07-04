#!/usr/bin/env python3
"""Hybrid street-strip blocks: rectangle (flat-cap buffer) clipped to the segment's
Voronoi cell.

- The rectangle gives straight, node-aligned sides defined by the edge + its nodes.
- Clipping to the Voronoi cell resolves the intersection overlap, so blocks TILE
  (a point belongs to exactly one block) -> the edge->block mapping stays unambiguous.

Where the street body is narrower than the cell (the common case) the rectangle side
wins (straight boundary). Only the corners near an intersection are trimmed by the
bisector. Scored on the same Midtown window vs brook.
"""
import time
import numpy as np
import geopandas as gpd
from shapely.ops import unary_union
from shapely import voronoi_polygons, MultiPoint

from eval_generate import (load_segments_utm, window_box, area_metrics, iou_mean,
                           CLASS_W, DEFAULT_W, _class_of, BBOX, MIN_AREA, UTM, OUT,
                           SEED_SPACING)
from eval_rectangles import rect_blocks, overlap_stats


def voronoi_cells(seg, win, seed_spacing=SEED_SPACING):
    """seg_id -> Voronoi cell polygon (the tiling partition by nearest centerline)."""
    big = win.buffer(120.0)
    cand = seg[seg.geometry.intersects(big)]
    pts, ids = [], []
    for sid, g in zip(cand.seg_id, cand.geometry):
        gg = g.intersection(big)
        if gg.is_empty:
            continue
        for part in (gg.geoms if gg.geom_type.startswith("Multi") else [gg]):
            n = max(2, int(part.length // seed_spacing))
            for i in range(n + 1):
                pts.append(part.interpolate(i / n, normalized=True))
                ids.append(sid)
    cells = list(voronoi_polygons(MultiPoint(pts), extend_to=big.buffer(50)).geoms)
    cells = gpd.GeoDataFrame(geometry=cells, crs=UTM)
    ptg = gpd.GeoDataFrame({"seg_id": ids}, geometry=pts, crs=UTM)
    cells = gpd.sjoin(cells, ptg, how="inner", predicate="intersects").drop_duplicates("geometry")
    diss = cells.dissolve("seg_id").geometry
    return diss.to_dict()


def hybrid_blocks(seg, win, scale=1.0):
    cells = voronoi_cells(seg, win)
    cand = seg[seg.geometry.intersects(win.buffer(40))]
    geoms, ids = [], []
    for sid, g, hwy in zip(cand.seg_id, cand.geometry, cand["highway"]):
        cell = cells.get(sid)
        if cell is None:
            continue
        w = CLASS_W.get(_class_of(hwy), DEFAULT_W) * scale
        rect = g.buffer(w, cap_style="flat", join_style="mitre")
        block = rect.intersection(cell).intersection(win)
        if block.is_empty or block.area <= MIN_AREA:
            continue
        geoms.append(unary_union(block)); ids.append(sid)
    return gpd.GeoDataFrame({"seg_id": ids}, geometry=geoms, crs=UTM)


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
              f"overlap={ov['overlap_ratio']:.3f}", flush=True)
        return gen

    score("rectangle x1.0", rect_blocks(seg, win, scale=1.0, flat=True))
    for sc in (1.0, 1.2, 1.4):
        score(f"hybrid x{sc}", hybrid_blocks(seg, win, scale=sc))


if __name__ == "__main__":
    main()
