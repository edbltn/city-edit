#!/usr/bin/env python3
"""Render verification PNGs from blocks_nyc.geojson:
  - overview_by_class.png : whole city, colored by road class (is everything covered?)
  - window_check.png      : hi-res zoom, one unique color per block (did segments split right?)

Output dir defaults to ./output next to this script; override with BLOCKS_OUT env var.
Optional zoom window via env: WIN="west,south,east,north" (default = lower Manhattan).
"""
import warnings; warnings.filterwarnings("ignore")
import os
from pathlib import Path
import geopandas as gpd, numpy as np, matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(os.environ.get("BLOCKS_OUT", Path(__file__).resolve().parent / "output"))
WIN = tuple(float(x) for x in os.environ.get("WIN", "-74.012,40.704,-74.000,40.712").split(","))

b = gpd.read_file(OUT/"blocks_nyc.geojson")
print(f"{len(b)} blocks loaded")

# 1) citywide overview by road class
cmap = {'residential':'#9ecae1','secondary':'#fd8d3c','tertiary':'#74c476','primary':'#e6550d',
        'motorway':'#cb181d','motorway_link':'#fb6a4a','trunk':'#a50f15','unclassified':'#bdbdbd'}
fig, ax = plt.subplots(figsize=(14,14))
b.plot(ax=ax, color=b.road_class.map(cmap).fillna("#dddddd"), linewidth=0)
ax.set_title(f"NYC streetscape blocks — {len(b):,} polygons (color = road class)"); ax.set_axis_off()
fig.savefig(OUT/"overview_by_class.png", dpi=130, bbox_inches="tight"); plt.close(fig)

# 2) hi-res window, unique color per block
W,S,E,N = WIN
win = b.cx[W:E, S:N].to_crs(32618).reset_index(drop=True)
rng = np.random.default_rng(3)
cols = plt.cm.hsv(np.linspace(0,1,len(win)))[rng.permutation(len(win))]
fig, ax = plt.subplots(figsize=(13,13))
win.plot(ax=ax, color=cols, edgecolor="black", linewidth=0.9)
ax.set_title(f"Window {WIN} — {len(win)} blocks, unique color each"); ax.set_axis_off()
fig.savefig(OUT/"window_check.png", dpi=200, bbox_inches="tight"); plt.close(fig)
print("wrote overview_by_class.png, window_check.png")
