#!/usr/bin/env python3
"""Tiled segment-Voronoi over all NYC -> one merged streetscape-block polygon per
street segment. Reads the cached pull artifacts, writes blocks_nyc.geojson (EPSG:4326).

Method: union roadbed + sidewalk into the full right-of-way surface, then partition
it by assigning every point to its nearest centerline segment (segment-Voronoi). This
is width-agnostic (no intersection-cutter radius to tune) and cuts cleanly at junctions.

Output dir defaults to ./output next to this script; override with BLOCKS_OUT env var.
"""
import warnings; warnings.filterwarnings("ignore")
import os, pickle, time
from pathlib import Path
import numpy as np, geopandas as gpd, osmnx as ox
from shapely.ops import unary_union
from shapely import voronoi_polygons, MultiPoint, box as shp_box

OUT = Path(os.environ.get("BLOCKS_OUT", Path(__file__).resolve().parent / "output"))
TILE, MARGIN, SEED_SPACING, MIN_AREA = 1500.0, 400.0, 6.0, 60.0   # metres (UTM 18N)

def load():
    rb = gpd.read_file(OUT/"nyc_roadbed_full.geojson").set_crs(4326, allow_override=True).to_crs(32618)
    sw = gpd.read_file(OUT/"nyc_sidewalk_full.geojson").set_crs(4326, allow_override=True).to_crs(32618)
    Gc = pickle.load(open(OUT/"nyc_drive_consolidated.pkl","rb"))
    seg = ox.graph_to_gdfs(ox.convert.to_undirected(Gc), nodes=False).reset_index(drop=True)
    seg["seg_id"] = range(len(seg))
    seg["road_class"] = seg["highway"].apply(lambda v: v[0] if isinstance(v,list) else v)
    nm = seg["name"] if "name" in seg.columns else gpd.pd.Series([None]*len(seg))
    seg["road_name"] = nm.apply(lambda v: v[0] if isinstance(v,list) else v)
    return rb, sw, seg

def tile_blocks(row_mesh, seg, sindex, x0, y0):
    core = shp_box(x0, y0, x0+TILE, y0+TILE)
    big  = shp_box(x0-MARGIN, y0-MARGIN, x0+TILE+MARGIN, y0+TILE+MARGIN)
    row_core = row_mesh.intersection(core)
    if row_core.is_empty: return []
    cand = seg.iloc[list(sindex.query(big, predicate="intersects"))]
    cand = cand[cand.geometry.intersects(big)]
    if cand.empty: return []
    pts, ids = [], []
    for sid, g in zip(cand.seg_id, cand.geometry):
        gg = g.intersection(big)
        if gg.is_empty: continue
        for part in (gg.geoms if gg.geom_type.startswith("Multi") else [gg]):
            n = max(2, int(part.length//SEED_SPACING))
            for i in range(n+1):
                pts.append(part.interpolate(i/n, normalized=True)); ids.append(sid)
    if len(pts) < 3: return []
    cells = gpd.GeoDataFrame(geometry=list(voronoi_polygons(MultiPoint(pts), extend_to=big.buffer(50)).geoms), crs=32618)
    ptg = gpd.GeoDataFrame({"seg_id": ids}, geometry=pts, crs=32618)
    cells = gpd.sjoin(cells, ptg, how="inner", predicate="intersects").drop_duplicates("geometry")
    clipped = gpd.clip(cells.dissolve("seg_id"), gpd.GeoDataFrame(geometry=[row_core], crs=32618)).reset_index()
    out = []
    for sid, geom in zip(clipped.seg_id, clipped.geometry):
        if geom.is_empty: continue
        for part in (geom.geoms if geom.geom_type.startswith("Multi") else [geom]):
            if part.area > MIN_AREA: out.append((sid, part))
    return out

def main():
    t = time.time(); print("loading...", flush=True)
    rb, sw, seg = load()
    row_mesh = unary_union(list(rb.geometry)+list(sw.geometry)); sindex = seg.sindex
    minx, miny, maxx, maxy = seg.total_bounds
    xs = np.arange(minx, maxx+TILE, TILE); ys = np.arange(miny, maxy+TILE, TILE)
    print(f"{len(seg)} segments; {len(xs)*len(ys)} tiles", flush=True)
    pieces, done = [], 0
    for x0 in xs:
        for y0 in ys:
            pieces += tile_blocks(row_mesh, seg, sindex, x0, y0); done += 1
            if done % 50 == 0: print(f"  tile {done}/{len(xs)*len(ys)} pieces={len(pieces)} {time.time()-t:.0f}s", flush=True)
    g = gpd.GeoDataFrame({"seg_id":[p[0] for p in pieces]}, geometry=[p[1] for p in pieces], crs=32618)
    blocks = g.dissolve("seg_id").reset_index().merge(seg[["seg_id","road_class","road_name"]], on="seg_id", how="left")
    blocks = blocks[blocks.geometry.area > MIN_AREA].reset_index(drop=True)
    blocks["block_id"] = range(len(blocks)); blocks["area_m2"] = blocks.geometry.area.round(1)
    blocks = blocks.to_crs(4326)
    cols = ["block_id","seg_id","road_class","road_name","area_m2","geometry"]
    tmp = OUT/"blocks_nyc.geojson.tmp"
    blocks[cols].to_file(tmp, driver="GeoJSON"); os.replace(tmp, OUT/"blocks_nyc.geojson")
    print(f"WROTE blocks_nyc.geojson: {len(blocks)} blocks in {time.time()-t:.0f}s", flush=True)

if __name__ == "__main__":
    main()
