#!/usr/bin/env python3
"""Add foot-path blocks for areas the drive-centerline street blocks don't reach.

build_blocks_generic.py covers ~82% of foot-graph edges; the rest are foot edges
>30 m from any drive centerline — park interior paths, pedestrian plazas,
boardwalks. This takes those unmapped edges (edge_blocks_<net>.npy == −1), buffers
them, MERGES the overlapping buffers, and SUBTRACTS the junction-node discs
(road_class="node" features from build_node_blocks.py) before splitting into
connected components. The discs (12 m) are wider than the tube radius (6 m), so
the merged mesh severs at every junction and each polygon is one path segment
between junctions — the same grain as street blocks. (Without the subtraction a
whole park's path network union-finds into a handful of giant blocks — the old
Central Park behaviour.) Results append to blocks_generic_<city>.geojson
(road_class="foot", seg_id=−1, block_ids continuing from the existing features).

After running this, re-run build_edge_blocks.py and rebuild blocks.pmtiles.

Runs in the SERVER venv (graph_registry + shapely). No projection deps: a local
equirectangular frame (metres) is fine for ~6 m buffers at city scale.

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/build_foot_blocks.py
"""
import json
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.abspath(os.path.join(_HERE, ".."))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import shapely  # noqa: E402
from shapely import LineString, union_all, get_parts, buffer as shp_buffer  # noqa: E402
from shapely.geometry import shape as shp_shape  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
HALF_WIDTH_M = float(os.environ.get("FOOT_HALF_WIDTH_M", "6"))   # buffer radius
MIN_AREA_M2 = float(os.environ.get("FOOT_MIN_AREA_M2", "120"))   # drop slivers


def main():
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    if g.edge_block_id is None:
        raise SystemExit("no edge_blocks mapping — run build_edge_blocks.py first")

    nodes = np.asarray(g.nodes, dtype=np.float64)   # [N,2] = (lat, lon)
    ebid = np.asarray(g.edge_block_id)
    unmapped = np.where(ebid < 0)[0]
    print(f"[foot] {len(unmapped)} unmapped edges of {len(ebid)} "
          f"({100*len(unmapped)/len(ebid):.1f}%)")

    # Local equirectangular projection (metres) centred on the city bbox.
    s, w, n, e = city.bbox
    lat0 = math.radians((s + n) / 2)
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(lat0)

    def to_m(latlon):  # [...,2] (lat,lon) -> (x,y) metres
        x = (latlon[..., 1] - w) * mlon
        y = (latlon[..., 0] - s) * mlat
        return x, y

    def to_ll_xy(x, y):  # metres -> (lon, lat)
        return x / mlon + w, y / mlat + s

    # Build LineStrings (in metres) for unmapped edges, vectorized.
    lines = []
    for eid in unmapped:
        a = g.edges[eid]
        p, q = nodes[a[0]], nodes[a[1]]
        if a[0] == a[1]:
            continue
        x1, y1 = (p[1] - w) * mlon, (p[0] - s) * mlat
        x2, y2 = (q[1] - w) * mlon, (q[0] - s) * mlat
        lines.append(LineString([(x1, y1), (x2, y2)]))
    print(f"[foot] buffering + merging {len(lines)} segments (r={HALF_WIDTH_M}m)…")

    bufs = shp_buffer(np.array(lines, dtype=object), HALF_WIDTH_M,
                      cap_style="round", join_style="round")
    merged = union_all(bufs)

    # Load the existing blocks now: the junction-node discs in them cut the
    # merged mesh into per-segment components (and keep foot blocks from
    # overlapping the discs — blocks must never overlap).
    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    blocks_path = os.path.join(out_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(blocks_path))
    feats = fc["features"]

    disc_ll = [shp_shape(f["geometry"]) for f in feats
               if f["properties"].get("road_class") == "node"]
    if disc_ll:
        def to_m_geom(a):  # (lon,lat) ndarray -> metres in the same local frame
            return np.column_stack(((a[:, 0] - w) * mlon, (a[:, 1] - s) * mlat))
        discs_m = shapely.transform(np.array(disc_ll, dtype=object), to_m_geom)
        merged = shapely.difference(merged, union_all(discs_m))
        print(f"[foot] subtracted {len(disc_ll)} junction discs")

    parts = [p for p in get_parts(merged) if p.area >= MIN_AREA_M2]
    print(f"[foot] merged into {len(parts)} foot blocks (≥{MIN_AREA_M2:.0f} m²)")
    next_id = max((f["properties"]["block_id"] for f in feats), default=-1) + 1

    for poly in parts:
        # ring coords (metres) -> (lon, lat); polygon may have holes.
        def ring_ll(coords):
            return [list(to_ll_xy(x, y)) for x, y in coords]
        ext = ring_ll(poly.exterior.coords)
        holes = [ring_ll(r.coords) for r in poly.interiors]
        feats.append({
            "type": "Feature",
            "properties": {
                "block_id": next_id, "seg_id": -1, "road_class": "foot",
                "road_name": None, "area_m2": round(poly.area, 1),
            },
            "geometry": {"type": "Polygon", "coordinates": [ext] + holes},
        })
        next_id += 1

    # pid-unique tmp: a stale sibling process fighting over one shared ".tmp"
    # interleaves writes and corrupts the output on os.replace.
    tmp = f"{blocks_path}.tmp{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(fc, fh)
    os.replace(tmp, blocks_path)
    print(f"[foot] appended {len(parts)} foot blocks → {os.path.basename(blocks_path)} "
          f"(now {len(feats)} total)")


if __name__ == "__main__":
    main()
