#!/usr/bin/env python3
"""City-agnostic streetscape-block generator (OSM-only, no municipal open data).

Produces the SAME thing as build_nyc_blocks.py — one merged polygon per street
segment between intersections — but the right-of-way (ROW) surface is approximated
by **buffering the OSM centerlines per road class** instead of unioning
planimetric roadbed + sidewalk polygons (which only NYC publishes). Everything
downstream (segment-Voronoi partition, per-segment dissolve, schema) mirrors
build_nyc_blocks.py so the two are directly comparable.

To "respect Brook's graph as closely as possible" this reuses the *identical*
centerline graph (osmnx drive network, consolidated intersections) — so `seg_id`
is shared 1:1 with the planimetric blocks and the only difference is polygon
geometry. For a brand-new city with no cached graph it builds that graph from OSM
on the fly (works anywhere OSM has a drive network).

Output: blocks_generic_<city>.geojson (EPSG:4326), schema identical to
build_nyc_blocks.py: block_id, seg_id, road_class, road_name, area_m2.

Env:
  BLOCKS_OUT   output dir (default ./output)
  CITY         city id for filenames + graph cache (default "nyc")
  BBOX         "W,S,E,N" to build the graph if no cache (default = CityEdit nyc bbox)
  WIDTH_SCALE  global multiplier on the per-class ROW half-widths (tuning knob)
"""
import warnings; warnings.filterwarnings("ignore")
import os, pickle, time
from pathlib import Path
import numpy as np, geopandas as gpd, osmnx as ox
from shapely.ops import unary_union
from shapely import voronoi_polygons, MultiPoint, box as shp_box

OUT = Path(os.environ.get("BLOCKS_OUT", Path(__file__).resolve().parent / "output"))
CITY = os.environ.get("CITY", "nyc")
WIDTH_SCALE = float(os.environ.get("WIDTH_SCALE", "1.0"))

# Same tiling/seeding constants as build_nyc_blocks.py (metres, UTM 18N) so the
# Voronoi partition behaves identically — only the ROW surface differs.
TILE, MARGIN, SEED_SPACING, MIN_AREA = 1500.0, 400.0, 6.0, 60.0

# Per-class ROW *half-width* in metres (buffer radius around the centerline).
# Tuned against Brook's NYC planimetric blocks (compare_blocks.py): these match
# Brook's ROW *area* (area-ratio median 1.00, IoU median 0.84 over ~82k shared
# segments). NOTE: a pure area/(2·length) calibration UNDER-shoots (area ratio
# 0.90, IoU 0.82) because the Voronoi assigns intersection flare to segments, so
# the effective mid-block width must run a touch wider than area/length implies.
# Road classes are universal, so these NYC-tuned widths are reused for every city.
# Scaled by WIDTH_SCALE for sweeps; unknown classes fall back to DEFAULT.
HALF_WIDTH = {
    "motorway": 18.0, "motorway_link": 12.0, "trunk": 16.0, "trunk_link": 11.0,
    "primary": 14.0, "primary_link": 10.0, "secondary": 11.0, "secondary_link": 9.0,
    "tertiary": 9.0, "tertiary_link": 8.0, "residential": 8.0, "living_street": 7.0,
    "unclassified": 8.0, "service": 6.0, "pedestrian": 6.0, "footway": 4.0,
    "path": 4.0, "cycleway": 4.0,
}
DEFAULT_HALF_WIDTH = 8.0


def _consolidated_graph():
    """Load the cached consolidated drive graph (shared with build_nyc_blocks.py)
    or build it from OSM for this city's bbox — identical recipe to pull_nyc.py."""
    gp = OUT / f"{CITY}_drive_consolidated.pkl"
    if not gp.exists() and CITY == "nyc":
        gp = OUT / "nyc_drive_consolidated.pkl"   # name used by pull_nyc.py
    if gp.exists():
        print(f"[graph] reusing cached {gp.name}", flush=True)
        return pickle.load(open(gp, "rb"))
    # Build from OSM (city-agnostic path). BBOX is "W,S,E,N".
    W, S, E, N = (float(x) for x in
                  os.environ.get("BBOX", "-74.2591,40.4774,-73.7004,40.9176").split(","))
    print(f"[graph] building drive network for {CITY} from OSM ({W},{S},{E},{N})…", flush=True)
    G = ox.graph_from_bbox(bbox=(W, S, E, N), network_type="drive",
                           simplify=True, truncate_by_edge=True)
    Gc = ox.consolidate_intersections(ox.project_graph(G, to_crs=32618), tolerance=12,
                                      rebuild_graph=True, dead_ends=False)
    tmp = (OUT / f"{CITY}_drive_consolidated.pkl"); tmp_t = tmp.with_suffix(".tmp")
    pickle.dump(Gc, open(tmp_t, "wb")); os.replace(tmp_t, tmp)
    return Gc


def load_segments():
    """Centerline segments as a GeoDataFrame in UTM 18N — identical seg_id space
    to build_nyc_blocks.py (same consolidated graph, same ordering)."""
    Gc = _consolidated_graph()
    seg = ox.graph_to_gdfs(ox.convert.to_undirected(Gc), nodes=False).reset_index(drop=True)
    seg["seg_id"] = range(len(seg))
    seg["road_class"] = seg["highway"].apply(lambda v: v[0] if isinstance(v, list) else v)
    nm = seg["name"] if "name" in seg.columns else gpd.pd.Series([None] * len(seg))
    seg["road_name"] = nm.apply(lambda v: v[0] if isinstance(v, list) else v)
    if seg.crs is None:
        seg = seg.set_crs(32618)
    elif seg.crs.to_epsg() != 32618:
        seg = seg.to_crs(32618)
    return seg


def buffered_row(seg):
    """Approximate the right-of-way surface: each centerline buffered by its
    class half-width, unioned. This replaces build_nyc_blocks.py's
    unary_union(roadbed + sidewalk)."""
    widths = seg["road_class"].map(lambda c: HALF_WIDTH.get(c, DEFAULT_HALF_WIDTH) * WIDTH_SCALE)
    # flat caps so a segment's ROW doesn't bleed past its endpoints into the
    # neighbour's territory before the Voronoi cut; round joins keep bends smooth.
    bufs = [g.buffer(w, cap_style="flat", join_style="round")
            for g, w in zip(seg.geometry, widths)]
    return unary_union(bufs)


def tile_blocks(row_mesh, seg, sindex, x0, y0):
    """Segment-Voronoi over one tile — copied verbatim in spirit from
    build_nyc_blocks.py so the partition logic is identical."""
    core = shp_box(x0, y0, x0 + TILE, y0 + TILE)
    big = shp_box(x0 - MARGIN, y0 - MARGIN, x0 + TILE + MARGIN, y0 + TILE + MARGIN)
    row_core = row_mesh.intersection(core)
    if row_core.is_empty:
        return []
    cand = seg.iloc[list(sindex.query(big, predicate="intersects"))]
    cand = cand[cand.geometry.intersects(big)]
    if cand.empty:
        return []
    pts, ids = [], []
    for sid, g in zip(cand.seg_id, cand.geometry):
        gg = g.intersection(big)
        if gg.is_empty:
            continue
        for part in (gg.geoms if gg.geom_type.startswith("Multi") else [gg]):
            n = max(2, int(part.length // SEED_SPACING))
            for i in range(n + 1):
                pts.append(part.interpolate(i / n, normalized=True)); ids.append(sid)
    if len(pts) < 3:
        return []
    cells = gpd.GeoDataFrame(
        geometry=list(voronoi_polygons(MultiPoint(pts), extend_to=big.buffer(50)).geoms),
        crs=32618)
    ptg = gpd.GeoDataFrame({"seg_id": ids}, geometry=pts, crs=32618)
    cells = gpd.sjoin(cells, ptg, how="inner", predicate="intersects").drop_duplicates("geometry")
    clipped = gpd.clip(cells.dissolve("seg_id"),
                       gpd.GeoDataFrame(geometry=[row_core], crs=32618)).reset_index()
    out = []
    for sid, geom in zip(clipped.seg_id, clipped.geometry):
        if geom.is_empty:
            continue
        for part in (geom.geoms if geom.geom_type.startswith("Multi") else [geom]):
            if part.area > MIN_AREA:
                out.append((sid, part))
    return out


def main():
    t = time.time(); print(f"[generic] city={CITY} width_scale={WIDTH_SCALE}", flush=True)
    seg = load_segments()
    print(f"loading ROW (buffered centerlines)… {len(seg)} segments", flush=True)
    row_mesh = buffered_row(seg); sindex = seg.sindex
    minx, miny, maxx, maxy = seg.total_bounds
    xs = np.arange(minx, maxx + TILE, TILE); ys = np.arange(miny, maxy + TILE, TILE)
    print(f"{len(seg)} segments; {len(xs) * len(ys)} tiles", flush=True)
    pieces, done = [], 0
    for x0 in xs:
        for y0 in ys:
            pieces += tile_blocks(row_mesh, seg, sindex, x0, y0); done += 1
            if done % 50 == 0:
                print(f"  tile {done}/{len(xs)*len(ys)} pieces={len(pieces)} "
                      f"{time.time()-t:.0f}s", flush=True)
    g = gpd.GeoDataFrame({"seg_id": [p[0] for p in pieces]},
                         geometry=[p[1] for p in pieces], crs=32618)
    blocks = (g.dissolve("seg_id").reset_index()
              .merge(seg[["seg_id", "road_class", "road_name"]], on="seg_id", how="left"))
    blocks = blocks[blocks.geometry.area > MIN_AREA].reset_index(drop=True)
    blocks["block_id"] = range(len(blocks)); blocks["area_m2"] = blocks.geometry.area.round(1)
    blocks = blocks.to_crs(4326)
    cols = ["block_id", "seg_id", "road_class", "road_name", "area_m2", "geometry"]
    dst = OUT / f"blocks_generic_{CITY}.geojson"
    tmp = dst.with_suffix(".geojson.tmp")
    blocks[cols].to_file(tmp, driver="GeoJSON"); os.replace(tmp, dst)
    print(f"WROTE {dst.name}: {len(blocks)} blocks in {time.time()-t:.0f}s", flush=True)


if __name__ == "__main__":
    main()
