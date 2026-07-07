#!/usr/bin/env python3
"""Give every junction node its own block — a small disc punched out of the
street/foot blocks (docs/three-layer-model.md §2).

Why: block polygons extend across intersections (the Voronoi assigns the
intersection flare to ONE of the crossing segments), so the short graph edges
that cross an intersection get baked into a PERPENDICULAR street's block. A
route down an avenue then selects — and block-scoped casting votes on — every
cross street it passes ("ladder" selections). Discs at junction nodes catch
those edges' midpoints instead, so a path only ever touches blocks that share
its orientation, plus the intersections themselves.

What it does, given blocks_generic_<city>.geojson:
  1. drops any road_class in {"node","foot"} features (idempotent re-runs; foot
     blocks are rebuilt afterwards by build_foot_blocks.py);
  2. finds junction nodes of the city walk graph — unique-neighbour degree >= 3
     (degree-2 geometry nodes and dead ends stay part of their street block);
  3. buffers each into a disc of NODE_BLOCK_RADIUS_M (default 12 m ~ the client's
     3 px node hover/snap affordance at z≈15, and > build_foot_blocks' 6 m tube
     radius so foot components sever at junctions);
  4. subtracts the discs from every street block they intersect (blocks and
     discs never overlap — containment in the bake stays unambiguous);
  5. appends the discs as features: road_class="node", seg_id=-1, node_id=<idx>,
     block_id continuing after the street blocks.

Run in the SERVER venv, then re-run build_edge_blocks.py (see
build_city_blocks.sh, which orders the whole pipeline):

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/build_node_blocks.py
"""
import json
import math
import os
import sys
import time

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.abspath(os.path.join(_HERE, ".."))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

import shapely  # noqa: E402
from shapely import STRtree, buffer as shp_buffer, points as shp_points, union_all  # noqa: E402
from shapely.geometry import shape, mapping  # noqa: E402

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
RADIUS_M = float(os.environ.get("NODE_BLOCK_RADIUS_M", "12"))
# Discs are 16-gons: plenty round at 12 m, keeps the geojson/tiles small.
QUAD_SEGS = int(os.environ.get("NODE_BLOCK_QUAD_SEGS", "4"))


def junction_nodes(g) -> np.ndarray:
    """Indices of nodes with unique-neighbour degree >= 3 (true junctions)."""
    # edge rows are [u, v, name, road_class, length_m] — take the endpoints only
    E = np.array([(e[0], e[1]) for e in g.edges], dtype=np.int64)
    E = E[E[:, 0] != E[:, 1]]                      # self-edges don't add degree
    und = np.unique(np.sort(E, axis=1), axis=0)    # unique undirected pairs
    deg = np.bincount(und.ravel(), minlength=len(g.nodes))
    return np.where(deg >= 3)[0]


def main():
    t0 = time.time()
    city = CITIES[CITY]
    g = CityGraph(city, redis_client=None, network=NETWORK)
    g.ensure_loaded()
    nodes = np.asarray(g.nodes, dtype=np.float64)  # [N,2] = (lat, lon)

    junctions = junction_nodes(g)
    print(f"[node_blocks] {len(junctions)} junction nodes "
          f"(deg>=3) of {len(nodes)} — r={RADIUS_M}m", flush=True)

    # Same local equirectangular frame as build_foot_blocks.py so both scripts
    # agree exactly on where a disc's boundary falls.
    s, w, n, e = city.bbox
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((s + n) / 2))

    xs = (nodes[junctions, 1] - w) * mlon
    ys = (nodes[junctions, 0] - s) * mlat
    discs_m = shp_buffer(shp_points(xs, ys), RADIUS_M, quad_segs=QUAD_SEGS)
    to_ll = lambda a: np.column_stack((a[:, 0] / mlon + w, a[:, 1] / mlat + s))
    discs = shapely.transform(discs_m, to_ll)
    print(f"[node_blocks] built {len(discs)} discs in {time.time()-t0:.0f}s", flush=True)

    out_dir = os.environ.get("BLOCKS_OUT", os.path.join(_HERE, "output"))
    blocks_path = os.path.join(out_dir, f"blocks_generic_{CITY}.geojson")
    fc = json.load(open(blocks_path))
    feats = [f for f in fc["features"]
             if f["properties"].get("road_class") not in ("node", "foot")]
    dropped = len(fc["features"]) - len(feats)
    if dropped:
        print(f"[node_blocks] dropped {dropped} stale node/foot features "
              f"(rebuilt downstream)", flush=True)

    # Subtract every disc from every street block it intersects.
    geoms = shapely.from_geojson(
        json.dumps({"type": "GeometryCollection",
                    "geometries": [f["geometry"] for f in feats]}))
    polys = np.asarray(shapely.get_parts(geoms) if geoms.geom_type == "GeometryCollection"
                       else [geoms], dtype=object)
    tree = STRtree(discs)
    block_idx, disc_idx = tree.query(polys, predicate="intersects")
    print(f"[node_blocks] clipping {len(np.unique(block_idx))} street blocks "
          f"touched by discs…", flush=True)
    cut = 0
    for bi in np.unique(block_idx):
        d_union = union_all(discs[disc_idx[block_idx == bi]])
        # area_m2 is left as-is: informational only, and the removed disc area
        # (~450 m² per touched corner) is small against a block's ROW.
        feats[bi]["geometry"] = mapping(shapely.difference(polys[bi], d_union))
        cut += 1

    next_id = max((f["properties"]["block_id"] for f in feats), default=-1) + 1
    disc_area = math.pi * RADIUS_M * RADIUS_M
    for k, poly in enumerate(discs):
        feats.append({
            "type": "Feature",
            "properties": {
                "block_id": next_id, "seg_id": -1, "road_class": "node",
                "road_name": None, "area_m2": round(disc_area, 1),
                "node_id": int(junctions[k]),
            },
            "geometry": mapping(poly),
        })
        next_id += 1

    fc["features"] = feats
    tmp = blocks_path + ".tmp"
    json.dump(fc, open(tmp, "w"))
    os.replace(tmp, blocks_path)
    print(f"[node_blocks] clipped {cut} blocks, appended {len(discs)} node discs "
          f"→ {os.path.basename(blocks_path)} ({len(feats)} features, "
          f"{time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
