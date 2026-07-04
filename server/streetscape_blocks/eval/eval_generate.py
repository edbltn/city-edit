#!/usr/bin/env python3
"""Task 2 - auto-generate blocks (no planimetric data) and score by polygon error.

Ground truth = `blocks_nyc.geojson` ("brook"), built from real NYC roadbed+sidewalk.
When a city has no such planimetric layer we must SYNTHESISE blocks from the routing
graph alone. The heuristic family here: segment-Voronoi (partition the plane by
nearest drive centerline) clipped to a synthetic right-of-way = buffer(W) of the
network. W is the only knob (half street width, metres).

Two evaluations:
  (A) score the existing `blocks_generic_nyc.geojson` against brook (area + IoU),
      matched by seg_id, over the Manhattan bbox.
  (B) generate blocks on a window for a sweep of W and score each, to pick W and
      show the area-error / IoU trade-off.

Polygon error metrics (per matched seg_id, area in m^2):
  area_rmse        : sqrt(mean( (A_gen - A_truth)^2 ))           -- raw, m^2
  area_rmse_norm   : sqrt(mean( ((A_gen-A_truth)/A_truth)^2 ))   -- scale-free
  med_abs_relerr   : median( |A_gen/A_truth - 1| )               -- robust, scale-free
  iou_mean         : mean intersection-over-union (placement, not just size)
"""
import sys, time, json
from pathlib import Path
import numpy as np
import geopandas as gpd
import osmnx as ox
import pickle
from shapely.ops import unary_union
from shapely import voronoi_polygons, MultiPoint, box as shp_box

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "output"
UTM = 32618
BBOX = (-74.02, 40.70, -73.93, 40.82)
SEED_SPACING, MIN_AREA = 6.0, 60.0


def area_metrics(a_gen, a_truth):
    a_gen, a_truth = np.asarray(a_gen, float), np.asarray(a_truth, float)
    diff = a_gen - a_truth
    rel = a_gen / a_truth
    return dict(
        n=int(len(a_gen)),
        area_rmse=round(float(np.sqrt(np.mean(diff ** 2))), 1),
        area_rmse_norm=round(float(np.sqrt(np.mean((diff / a_truth) ** 2))), 4),
        med_abs_relerr=round(float(np.median(np.abs(rel - 1.0))), 4),
        mean_area_ratio=round(float(np.mean(rel)), 4),
    )


def iou_mean(gen_gdf, truth_gdf):
    """IoU per matched seg_id (both indexed by seg_id, same UTM)."""
    j = gen_gdf.set_index("seg_id").geometry.to_dict()
    ious = []
    for sid, tg in zip(truth_gdf.seg_id, truth_gdf.geometry):
        gg = j.get(sid)
        if gg is None:
            continue
        inter = gg.intersection(tg).area
        uni = gg.union(tg).area
        if uni > 0:
            ious.append(inter / uni)
    return round(float(np.mean(ious)), 4) if ious else None


# ---------- (A) score existing generic blocks ----------

def eval_existing():
    t = time.time()
    brook = gpd.read_file(OUT / "blocks_nyc.geojson", bbox=BBOX).to_crs(UTM)
    gen = gpd.read_file(OUT / "blocks_generic_nyc.geojson", bbox=BBOX).to_crs(UTM)
    # one polygon per seg_id (geojson is already dissolved, but be safe)
    bt = brook.groupby("seg_id").geometry.apply(lambda s: unary_union(s.values))
    gt = gen.groupby("seg_id").geometry.apply(lambda s: unary_union(s.values))
    common = sorted(set(bt.index) & set(gt.index))
    a_truth = np.array([bt[s].area for s in common])
    a_gen = np.array([gt[s].area for s in common])
    m = area_metrics(a_gen, a_truth)
    m["matched"] = len(common)
    m["only_truth"] = int(len(set(bt.index) - set(gt.index)))
    m["only_gen"] = int(len(set(gt.index) - set(bt.index)))
    # IoU on a sample (full is slow); 2000 random matched segs
    rng = np.random.default_rng(0)
    samp = rng.choice(common, size=min(2000, len(common)), replace=False)
    g_idx = {s: gt[s] for s in samp}
    ious = [g_idx[s].intersection(bt[s]).area / g_idx[s].union(bt[s]).area
            for s in samp if g_idx[s].union(bt[s]).area > 0]
    m["iou_mean_sample"] = round(float(np.mean(ious)), 4)
    print(f"(A) existing generic vs brook [{time.time()-t:.1f}s]: {m}", flush=True)
    return m


# ---------- (B) generate on a window, sweep W ----------

def load_segments_utm():
    G = pickle.load(open(OUT / "nyc_drive_consolidated.pkl", "rb"))
    seg = ox.graph_to_gdfs(ox.convert.to_undirected(G), nodes=False).reset_index(drop=True)
    seg["seg_id"] = range(len(seg))
    if seg.crs is None or seg.crs.to_epsg() != UTM:
        seg = seg.set_crs(UTM) if seg.crs is None else seg.to_crs(UTM)
    return seg


def gen_window(seg, win, buffer_w, seed_spacing=SEED_SPACING):
    """Segment-Voronoi clipped to buffer(W) of the network, within window box."""
    margin = buffer_w + 60.0
    big = win.buffer(margin)
    cand = seg[seg.geometry.intersects(big)]
    if cand.empty:
        return None
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
    if len(pts) < 3:
        return None
    cells = list(voronoi_polygons(MultiPoint(pts), extend_to=big.buffer(50)).geoms)
    cells = gpd.GeoDataFrame(geometry=cells, crs=UTM)
    ptg = gpd.GeoDataFrame({"seg_id": ids}, geometry=pts, crs=UTM)
    cells = gpd.sjoin(cells, ptg, how="inner", predicate="intersects").drop_duplicates("geometry")
    diss = cells.dissolve("seg_id")
    # synthetic right-of-way = network buffer, clipped to the window core
    row = unary_union(cand.geometry.values).buffer(buffer_w).intersection(win)
    clipped = gpd.clip(diss, gpd.GeoDataFrame(geometry=[row], crs=UTM)).reset_index()
    out = []
    for sid, geom in zip(clipped.seg_id, clipped.geometry):
        if geom.is_empty or geom.area <= MIN_AREA:
            continue
        out.append((sid, unary_union(geom)))
    if not out:
        return None
    return gpd.GeoDataFrame({"seg_id": [o[0] for o in out]},
                            geometry=[o[1] for o in out], crs=UTM)


# per-class half-width (metres). Wider roads carry wider right-of-way.
CLASS_W = {
    "motorway": 16, "motorway_link": 12, "trunk": 14, "trunk_link": 11,
    "primary": 12, "primary_link": 10, "secondary": 11, "tertiary": 10,
    "residential": 9, "living_street": 8, "unclassified": 9, "service": 7,
}
DEFAULT_W = 10


def _class_of(hwy):
    return hwy[0] if isinstance(hwy, list) else hwy


def gen_window_classaware(seg, win, scale=1.0, seed_spacing=SEED_SPACING):
    """Like gen_window but the synthetic right-of-way buffers each segment by a
    class-dependent half-width (scaled by `scale`) instead of one global W."""
    maxw = max(CLASS_W.values()) * scale
    margin = maxw + 60.0
    big = win.buffer(margin)
    cand = seg[seg.geometry.intersects(big)].copy()
    if cand.empty:
        return None
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
    if len(pts) < 3:
        return None
    cells = list(voronoi_polygons(MultiPoint(pts), extend_to=big.buffer(50)).geoms)
    cells = gpd.GeoDataFrame(geometry=cells, crs=UTM)
    ptg = gpd.GeoDataFrame({"seg_id": ids}, geometry=pts, crs=UTM)
    cells = gpd.sjoin(cells, ptg, how="inner", predicate="intersects").drop_duplicates("geometry")
    diss = cells.dissolve("seg_id")
    cand["w"] = cand["highway"].apply(lambda h: CLASS_W.get(_class_of(h), DEFAULT_W) * scale)
    row = unary_union([g.buffer(w) for g, w in zip(cand.geometry, cand.w)]).intersection(win)
    clipped = gpd.clip(diss, gpd.GeoDataFrame(geometry=[row], crs=UTM)).reset_index()
    out = []
    for sid, geom in zip(clipped.seg_id, clipped.geometry):
        if geom.is_empty or geom.area <= MIN_AREA:
            continue
        out.append((sid, unary_union(geom)))
    if not out:
        return None
    return gpd.GeoDataFrame({"seg_id": [o[0] for o in out]},
                            geometry=[o[1] for o in out], crs=UTM)


def window_box(center_lonlat, half_m=750.0):
    c = gpd.GeoSeries(gpd.points_from_xy([center_lonlat[0]], [center_lonlat[1]]),
                      crs=4326).to_crs(UTM).iloc[0]
    return shp_box(c.x - half_m, c.y - half_m, c.x + half_m, c.y + half_m)


def eval_generated(widths=(9, 10, 11, 12)):
    seg = load_segments_utm()
    brook = gpd.read_file(OUT / "blocks_nyc.geojson", bbox=BBOX).to_crs(UTM)
    # Midtown window (Times Square-ish) - dense, regular grid
    win = window_box((-73.985, 40.758), half_m=750.0)
    bw = brook[brook.geometry.intersects(win)].copy()
    bw["geometry"] = bw.geometry.intersection(win)
    bw = bw[bw.geometry.area > MIN_AREA]
    bt = bw.groupby("seg_id").geometry.apply(lambda s: unary_union(s.values))
    print(f"(B) window: {len(bt)} brook segs", flush=True)
    def score(label, gen, t):
        gt = gen.set_index("seg_id").geometry
        common = sorted(set(bt.index) & set(gt.index))
        a_truth = np.array([bt[s].area for s in common])
        a_gen = np.array([gt[s].area for s in common])
        m = area_metrics(a_gen, a_truth)
        m["variant"] = label
        m["matched"] = len(common)
        m["iou_mean"] = iou_mean(gen, bw)
        m["sec"] = round(time.time() - t, 1)
        print(f"  {label:14s} matched={m['matched']:4d}  area_rmse={m['area_rmse']:8.0f}  "
              f"norm={m['area_rmse_norm']:.3f}  med|relerr|={m['med_abs_relerr']:.3f}  "
              f"ratio={m['mean_area_ratio']:.3f}  IoU={m['iou_mean']:.3f}  {m['sec']}s",
              flush=True)
        return m

    rows = []
    for w in widths:
        t = time.time()
        gen = gen_window(seg, win, float(w))
        if gen is not None:
            rows.append(score(f"fixed W={w}", gen, t))
    # class-aware variants (scale tunes the whole width table)
    for sc in (0.9, 1.0, 1.1):
        t = time.time()
        gen = gen_window_classaware(seg, win, scale=sc)
        if gen is not None:
            rows.append(score(f"classaware x{sc}", gen, t))
    return rows


def main(mode="all"):
    res = {}
    if mode in ("all", "existing"):
        res["existing_generic"] = eval_existing()
    if mode in ("all", "generate"):
        res["generated_sweep"] = eval_generated()
    (HERE / "results_generate.json").write_text(json.dumps(res, indent=2))
    print("wrote results_generate.json", flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
