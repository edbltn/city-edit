#!/usr/bin/env python3
"""Ladder metric: do intersection-crossing stubs map to perpendicular blocks?

Samples named-avenue edges in midtown that (a) touch a junction node and
(b) are shorter than 24 m — the crossing stubs whose midpoints used to bake
into a PERPENDICULAR street's block (the "ladder" bug). Reports where they map
now. Target: 0 perpendicular.

  CITY=nyc NETWORK=streets ./env/bin/python streetscape_blocks/eval/eval_ladder.py
"""
import json
import math
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, _SERVER)
sys.path.insert(0, os.path.join(_SERVER, "streetscape_blocks"))

from cities import CITIES  # noqa: E402
from graph_registry import CityGraph  # noqa: E402
from build_node_blocks import junction_nodes  # noqa: E402

CITY = os.environ.get("CITY", "nyc")
NETWORK = os.environ.get("NETWORK", "streets")
BLOCKS_FILE = os.environ.get(
    "BLOCKS_FILE",
    os.path.join(_SERVER, "streetscape_blocks", "output", f"blocks_generic_{CITY}.geojson"),
)


def main():
    g = CityGraph(CITIES[CITY], redis_client=None, network=NETWORK)
    g.ensure_loaded()
    ebid = np.asarray(g.edge_block_id)
    fc = json.load(open(BLOCKS_FILE))
    cls, name = {}, {}
    for f in fc["features"]:
        p = f["properties"]
        cls[p["block_id"]] = p["road_class"]
        name[p["block_id"]] = p["road_name"]

    nodes = np.asarray(g.nodes)
    deg_junction = np.zeros(len(nodes), dtype=bool)
    deg_junction[junction_nodes(g)] = True

    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians(40.7))
    bad = ok_node = ok_same = other = 0
    samples = []
    for i, e in enumerate(g.edges):
        nm = e[2]
        if not nm or "Avenue" not in nm:
            continue
        a, b = e[0], e[1]
        la1, lo1 = nodes[a]
        la2, lo2 = nodes[b]
        if not (40.73 < la1 < 40.78 and -74.01 < lo1 < -73.96):
            continue
        if not (deg_junction[a] or deg_junction[b]):
            continue
        if math.hypot((la1 - la2) * mlat, (lo1 - lo2) * mlon) >= 24:
            continue
        bl = int(ebid[i])
        if bl < 0:
            other += 1
            continue
        c = cls.get(bl)
        if c == "node":
            ok_node += 1
        elif name.get(bl) == nm:
            ok_same += 1
        elif c == "foot":
            other += 1
        else:
            bad += 1
            if len(samples) < 5:
                samples.append((i, nm, "->", name.get(bl), c))
    tot = bad + ok_node + ok_same + other
    print(f"crossing-stub edges on midtown avenues: {tot}")
    print(f"  -> node block: {ok_node} ({100*ok_node/tot:.1f}%)")
    print(f"  -> same-street block: {ok_same} ({100*ok_same/tot:.1f}%)")
    print(f"  -> PERPENDICULAR street block: {bad} ({100*bad/tot:.1f}%)")
    print(f"  -> other (foot/unmapped): {other}")
    if samples:
        print("sample offenders:", samples)


if __name__ == "__main__":
    main()
