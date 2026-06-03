#!/usr/bin/env python3
"""
Empirically verify the votable topology graph is a superset of OSRM's foot network.

Routes many coordinate pairs through OSRM and checks that the OSM node IDs it returns
(annotations=nodes) resolve to topology edges via the exact path app.py uses
(osm_to_graph_idx + node_pair_to_edge + vote_store.osm_nodes_to_edge_ids). Reports
node/edge coverage and how often the coordinate-snapping fallback would trigger.

Usage (against the local compose OSRM, or any OSRM serving the city):
    OSRM_URL=http://localhost:5000 python tests/validate_osrm_topology.py --city sf -n 500

Run it once BEFORE the build change for a baseline, and after to confirm ~100%.
"""
import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cities import get_city
from python_router import PythonRouter
from osrm_router import OsrmRouter
from vote_store import osm_nodes_to_edge_ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="sf")
    ap.add_argument("-n", "--num", type=int, default=500, help="route samples")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--osrm-url", default=os.environ.get("OSRM_URL"))
    ap.add_argument("--osrm-host", default=os.environ.get("OSRM_HOST", "localhost"))
    ap.add_argument("--osrm-port", type=int, default=int(os.environ.get("OSRM_PORT", "5000")))
    args = ap.parse_args()

    city = get_city(args.city)
    if city is None:
        print(f"unknown city {args.city}")
        sys.exit(1)

    print(f"[validate] loading topology graph for {city.id}")
    router = PythonRouter(city.data_dir)
    data = router.get_graph_for_bbox(*city.bbox)
    osm_to_graph_idx = data["osm_to_graph_idx"]
    node_pair_to_edge = data["node_pair_to_edge"]
    nodes = data["nodes"]  # [[lat, lon], ...] — endpoints to route between
    print(f"[validate] {len(nodes)} nodes, {len(node_pair_to_edge)} node-pairs in bbox")

    if args.osrm_url:
        osrm = OsrmRouter(base_url=args.osrm_url, use_id_token=args.osrm_url.startswith("https://"))
    else:
        osrm = OsrmRouter(host=args.osrm_host, port=args.osrm_port)

    rng = random.Random(args.seed)

    routes_ok = routes_failed = 0
    total_nodes = nodes_hit = 0
    total_pairs = pairs_hit = 0
    fallback_routes = 0          # routes where osm_nodes_to_edge_ids would return []
    worst = []                   # (miss_frac, start, end, sample missing ids)

    for _ in range(args.num):
        a = nodes[rng.randrange(len(nodes))]
        b = nodes[rng.randrange(len(nodes))]
        res = osrm.calculate_route((a[0], a[1]), (b[0], b[1]), "walk")
        if "error" in res:
            routes_failed += 1
            continue
        routes_ok += 1
        ids = res.get("osm_node_ids", [])
        if not ids:
            continue

        miss_ids = [i for i in ids if i not in osm_to_graph_idx]
        total_nodes += len(ids)
        nodes_hit += len(ids) - len(miss_ids)

        # consecutive-pair edge coverage (mirrors osm_nodes_to_edge_ids logic)
        prev = None
        for osm_id in ids:
            gi = osm_to_graph_idx.get(osm_id)
            if gi is not None and prev is not None and gi != prev:
                total_pairs += 1
                if (prev, gi) in node_pair_to_edge:
                    pairs_hit += 1
            if gi is not None:
                prev = gi

        edge_ids = osm_nodes_to_edge_ids(ids, osm_to_graph_idx, node_pair_to_edge)
        if not edge_ids:
            fallback_routes += 1
        if miss_ids:
            worst.append((len(miss_ids) / len(ids), a, b, miss_ids[:5]))

    print("\n========== RESULTS ==========")
    print(f"routes ok / failed:      {routes_ok} / {routes_failed}")
    if total_nodes:
        print(f"node coverage:           {nodes_hit}/{total_nodes} = {100*nodes_hit/total_nodes:.3f}%")
    if total_pairs:
        print(f"edge (pair) coverage:    {pairs_hit}/{total_pairs} = {100*pairs_hit/total_pairs:.3f}%")
    print(f"routes hitting fallback: {fallback_routes}/{routes_ok} "
          f"({100*fallback_routes/max(routes_ok,1):.2f}%)")

    worst.sort(reverse=True)
    if worst:
        print("\nworst node-miss routes (miss%, start, end, sample missing osm ids):")
        for frac, a, b, ids in worst[:5]:
            print(f"  {100*frac:.1f}%  {a} -> {b}  {ids}")
    else:
        print("\nno node misses 🎉")


if __name__ == "__main__":
    main()
