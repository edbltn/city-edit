"""Extract a small real slice of the NYC walk graph + its block membership.

Output: scene.json with street polylines and block polygons already projected
into SVG pixel space, so the panel generator is pure drawing.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import LineString
from shapely.ops import unary_union

DATA = Path(__file__).resolve().parent.parent.parent / "server/osm_data/nyc"
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="scene.json")
ap.add_argument("--lat", type=float, default=40.7415)
ap.add_argument("--lon", type=float, default=-73.9895)
ap.add_argument("--width", type=float, default=1180.0)
ap.add_argument("--foot", action="store_true", help="include sidewalks/crossings/paths")
args = ap.parse_args()

OUT = Path(__file__).with_name(args.out)

# Flatiron / Madison Square: the grid plus Broadway's diagonal.
CENTER_LAT, CENTER_LON = args.lat, args.lon
WIDTH_M, HEIGHT_M = args.width, args.width * 9 / 16   # 16:9
CANVAS_W, CANVAS_H = 1600.0, 900.0

ROAD = {
    "primary", "secondary", "tertiary", "residential", "unclassified",
    "living_street", "trunk", "primary_link", "secondary_link",
    "tertiary_link", "motorway_link",
}
MINOR = {"service", "pedestrian"}
FOOT = {"footway", "path", "steps", "cycleway"}

meta = json.loads(np.load(DATA / "walk_graph_arrays.npz")["meta"].tobytes().decode())
highways = meta["highways"]
names = meta["names"]

z = np.load(DATA / "walk_graph_arrays.npz")
coords = z["coords"]
eu, ev = z["edge_u"], z["edge_v"]
ehw = z["edge_highway"]
ename = z["edge_name"]
eblock = np.load(DATA / "edge_blocks_streets.npy")

M_PER_DEG_LAT = 111_320.0
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT))

half_lat = (HEIGHT_M / 2) / M_PER_DEG_LAT
half_lon = (WIDTH_M / 2) / m_per_deg_lon
lat0, lat1 = CENTER_LAT - half_lat, CENTER_LAT + half_lat
lon0, lon1 = CENTER_LON - half_lon, CENTER_LON + half_lon

lat, lon = coords[:, 0], coords[:, 1]
in_box = (lat >= lat0) & (lat <= lat1) & (lon >= lon0) & (lon <= lon1)

road_ids = {i for i, h in enumerate(highways) if h in ROAD}
minor_ids = {i for i, h in enumerate(highways) if h in (MINOR | FOOT if args.foot else MINOR)}
keep_hw = np.isin(ehw, list(road_ids | minor_ids))
sel = keep_hw & in_box[eu] & in_box[ev]
idx = np.nonzero(sel)[0]
print(f"edges in scene: {len(idx)}")


def to_px(node):
    la, lo = coords[node]
    x = (lo - lon0) / (lon1 - lon0) * CANVAS_W
    y = (1 - (la - lat0) / (lat1 - lat0)) * CANVAS_H
    return round(float(x), 2), round(float(y), 2)


def to_m(node):
    la, lo = coords[node]
    return ((lo - CENTER_LON) * m_per_deg_lon, (la - CENTER_LAT) * M_PER_DEG_LAT)


streets = []          # px polylines for the basemap
nodes = {}            # node id -> px, for routing inside the scene
by_block = defaultdict(list)   # block_id -> [(m-space line)]
block_px_edges = defaultdict(list)

for i in idx:
    u, v = int(eu[i]), int(ev[i])
    a, b = to_px(u), to_px(v)
    if a == b:
        continue
    cls = "road" if int(ehw[i]) in road_ids else "minor"
    nm = names[int(ename[i])] if int(ename[i]) < len(names) else ""
    bid = int(eblock[i]) if i < len(eblock) else -1
    nodes[u], nodes[v] = a, b
    streets.append({"a": a, "b": b, "u": u, "v": v, "c": cls, "n": nm, "bid": bid})
    if bid > 0 and (cls == "road" or args.foot):
        by_block[bid].append(LineString([to_m(u), to_m(v)]))
        block_px_edges[bid].append([a, b])

# Real block polygons: buffered union of each block's own member edges — the
# same "generate geometry from membership" rule the block builder uses.
BUF = 7.0 if args.foot else 11.0
scale_x = CANVAS_W / WIDTH_M
scale_y = CANVAS_H / HEIGHT_M
blocks = []
for bid, lines in by_block.items():
    if not lines:
        continue
    poly = unary_union([ln.buffer(BUF, cap_style=2, join_style=1) for ln in lines])
    poly = poly.buffer(2.0).buffer(-2.0).simplify(1.0)
    geoms = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)
    rings = []
    for g in geoms:
        if g.area < (30 if args.foot else 120):
            continue
        ring = [
            [round((x * scale_x) + CANVAS_W / 2, 1), round(CANVAS_H / 2 - (y * scale_y), 1)]
            for x, y in g.exterior.coords
        ]
        rings.append(ring)
    if not rings:
        continue
    cx = sum(p[0] for r in rings for p in r) / sum(len(r) for r in rings)
    cy = sum(p[1] for r in rings for p in r) / sum(len(r) for r in rings)
    nm = ""
    for e in streets:
        if e["bid"] == bid and e["n"]:
            nm = e["n"]
            break
    blocks.append({
        "id": bid,
        "rings": rings,
        "c": [round(cx, 1), round(cy, 1)],
        "n": nm,
        "len": round(sum(ln.length for ln in lines), 1),
    })

print(f"blocks: {len(blocks)}")
scene = {
    "canvas": [CANVAS_W, CANVAS_H],
    "center": [CENTER_LAT, CENTER_LON],
    "extent_m": [WIDTH_M, HEIGHT_M],
    "streets": streets,
    "nodes": {str(k): v for k, v in nodes.items()},
    "blocks": blocks,
}
OUT.write_text(json.dumps(scene))
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")

# Handy: the named corridors available, for choosing the story's street.
cnt = defaultdict(float)
for e in streets:
    if e["n"]:
        cnt[e["n"]] += math.dist(e["a"], e["b"])
for n, l in sorted(cnt.items(), key=lambda kv: -kv[1])[:18]:
    print(f"  {n:<28} {l:8.0f}px")
