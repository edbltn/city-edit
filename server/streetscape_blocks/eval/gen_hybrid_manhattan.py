#!/usr/bin/env python3
"""Generate hybrid (rectangle ∩ Voronoi-cell) street-strip blocks over the whole
Manhattan bbox by tiling, then dissolve per seg_id. Writes a geojson that
eval_mapping.py can score, so we can test the generated blocks end-to-end against
the edge->block map.
"""
import sys, time
from pathlib import Path
import numpy as np
import geopandas as gpd
from shapely import box as shp_box
from shapely.ops import unary_union

from eval_generate import load_segments_utm, BBOX, UTM, MIN_AREA
from eval_hybrid import hybrid_blocks

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "output"
TILE = 1500.0


def main(scale=1.2, out_name="blocks_hybrid_manhattan.geojson"):
    scale = float(scale)
    t = time.time()
    seg = load_segments_utm()
    # bbox -> UTM extent
    corners = gpd.GeoSeries(gpd.points_from_xy([BBOX[0], BBOX[2]], [BBOX[1], BBOX[3]]),
                            crs=4326).to_crs(UTM)
    x0, x1 = sorted([corners.iloc[0].x, corners.iloc[1].x])
    y0, y1 = sorted([corners.iloc[0].y, corners.iloc[1].y])
    xs = np.arange(x0, x1 + TILE, TILE)
    ys = np.arange(y0, y1 + TILE, TILE)
    ntiles = len(xs) * len(ys)
    print(f"{len(seg)} segments; {ntiles} tiles; scale={scale}", flush=True)

    pieces = []
    done = 0
    for ax in xs:
        for ay in ys:
            core = shp_box(ax, ay, ax + TILE, ay + TILE)
            if not seg.geometry.intersects(core.buffer(40)).any():
                done += 1
                continue
            gb = hybrid_blocks(seg, core, scale=scale)
            if len(gb):
                pieces.append(gb)
            done += 1
            if done % 20 == 0:
                npiece = sum(len(p) for p in pieces)
                print(f"  tile {done}/{ntiles} pieces={npiece} {time.time()-t:.0f}s", flush=True)

    allp = gpd.GeoDataFrame(__import__("pandas").concat(pieces, ignore_index=True), crs=UTM)
    blocks = allp.dissolve("seg_id").reset_index()
    blocks = blocks[blocks.geometry.area > MIN_AREA].reset_index(drop=True)
    blocks["block_id"] = range(len(blocks))
    blocks["area_m2"] = blocks.geometry.area.round(1)
    blocks = blocks.to_crs(4326)
    out = OUT / out_name
    tmp = OUT / (out_name + ".tmp")
    blocks[["block_id", "seg_id", "area_m2", "geometry"]].to_file(tmp, driver="GeoJSON")
    Path(tmp).replace(out)
    print(f"WROTE {out.name}: {len(blocks)} blocks in {time.time()-t:.0f}s", flush=True)


if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
