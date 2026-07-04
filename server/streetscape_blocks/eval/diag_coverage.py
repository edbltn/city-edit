#!/usr/bin/env python3
"""Why do thin rectangle blocks leave ~18% of edges unmapped? Diagnose the gap:
for each hybrid-unmapped edge, how far is the nearest hybrid block, and is it
covered by the looser Voronoi-buffer 'generic' blocks?
"""
import numpy as np, geopandas as gpd
from shapely import STRtree
from shapely.geometry import Point
from eval_mapping import load_blocks, project_points, build_reference, SNAP_M
from pathlib import Path

HERE = Path(__file__).resolve().parent
z = np.load(HERE / "manhattan_edges.npz")
bbox, mids = z["bbox"], z["edge_mid"]
pts = project_points(mids)
shp = [Point(x, y) for x, y in pts]

hy = load_blocks(HERE.parent / "output" / "blocks_hybrid_manhattan.geojson", bbox)
gen = load_blocks(HERE.parent / "output" / "blocks_generic_nyc.geojson", bbox)
htree, gtree = STRtree(list(hy.geometry)), STRtree(list(gen.geometry))

# hybrid reference (containment + 30m snap)
from scipy.spatial import cKDTree
hc = np.column_stack([hy.geometry.centroid.x, hy.geometry.centroid.y])
ref, contained = build_reference(pts, list(hy.geometry), htree, hc, cKDTree(hc))
unmapped = np.where(ref < 0)[0]
print(f"edges={len(pts)} hybrid-unmapped={len(unmapped)} ({len(unmapped)/len(pts)*100:.1f}%)")

# distance from each unmapped edge to nearest hybrid block and nearest generic block
hp = list(hy.geometry); gp = list(gen.geometry)
hn = htree.nearest([shp[i] for i in unmapped])
gn = gtree.nearest([shp[i] for i in unmapped])
dh = np.array([hp[j].distance(shp[unmapped[k]]) for k, j in enumerate(hn)])
dg = np.array([gp[j].distance(shp[unmapped[k]]) for k, j in enumerate(gn)])

for name, d in [("nearest HYBRID block", dh), ("nearest GENERIC block", dg)]:
    print(f"\n{name}, distance (m) for the {len(unmapped)} hybrid-unmapped edges:")
    print(f"  median={np.median(d):.1f}  p90={np.percentile(d,90):.1f}  max={d.max():.0f}")
    for thr in (15, 30, 60, 120):
        print(f"  within {thr:3d}m: {(d<=thr).mean()*100:5.1f}%")
