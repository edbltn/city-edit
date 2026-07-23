#!/usr/bin/env python3
"""Scan a blocks_final_<city>.geojson for overlapping polygon pairs >= 1 m^2,
classified junction-junction / junction-corridor / corridor-corridor.

Usage: python scan_overlaps.py <geojson> [<geojson> ...]
"""
import json
import math
import sys

import numpy as np
from shapely.geometry import shape
from shapely.strtree import STRtree
import shapely


def scan(path):
    with open(path) as f:
        fc = json.load(f)
    feats = fc["features"]
    n = len(feats)
    polys = []
    kinds = []
    bids = []
    lat0 = None
    for ft in feats:
        g = shape(ft["geometry"])
        if lat0 is None:
            c = g.centroid
            lat0 = c.y
        polys.append(g)
        kinds.append("J" if ft["properties"].get("road_class") == "node" else "C")
        bids.append(ft["properties"].get("block_id"))
    mlat = 110574.0
    mlon = 111320.0 * math.cos(math.radians(lat0))

    def to_m(geom):
        return shapely.transform(
            geom, lambda a: np.column_stack((a[:, 0] * mlon, a[:, 1] * mlat)))

    polys_m = []
    invalid = 0
    for p in polys:
        m = to_m(p)
        if not m.is_valid:
            invalid += 1
            m = shapely.make_valid(m)
        polys_m.append(m)
    tree = STRtree(polys_m)
    pairs = tree.query(polys_m, predicate="intersects")
    seen = set()
    by_class = {}
    worst = []
    for qi, ti in zip(pairs[0], pairs[1]):
        if qi >= ti:
            continue
        key = (qi, ti)
        if key in seen:
            continue
        seen.add(key)
        inter = polys_m[qi].intersection(polys_m[ti])
        a = inter.area
        if a < 1.0:
            continue
        cls = "".join(sorted(kinds[qi] + kinds[ti]))
        by_class.setdefault(cls, []).append(a)
        if len(worst) < 400:
            c = inter.centroid
            worst.append((a, cls, bids[qi], bids[ti],
                          round(c.y / mlat, 6), round(c.x / mlon, 6)))
    name = path.split("blocks_final_")[-1].replace(".geojson", "")
    print(f"\n=== {name}: {n} features, {invalid} invalid geometries ===")
    if not by_class:
        print("  0 overlapping pairs >= 1 m^2")
        return
    total = 0
    for cls, areas in sorted(by_class.items()):
        areas.sort()
        total += len(areas)
        med = areas[len(areas) // 2]
        print(f"  {cls}: {len(areas)} pairs  median {med:.0f} m^2  max {areas[-1]:.0f} m^2")
    print(f"  TOTAL {total} pairs")
    worst.sort(reverse=True)
    for a, cls, b1, b2, lat, lon in worst[:6]:
        print(f"    worst {cls} {a:.0f} m^2  blocks {b1}/{b2}  at {lat},{lon}")


for p in sys.argv[1:]:
    scan(p)
