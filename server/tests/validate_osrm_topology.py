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
    ap.add_argument("--reverse-edges", type=int, default=0,
                    help="also run the REVERSE check: sample this many topology edges and "
                         "confirm OSRM will actually traverse each (catches edges the "
                         "topology carries but OSRM refuses to route — e.g. ferries).")
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

    if args.reverse_edges:
        run_reverse_check(data, osrm, rng, args.reverse_edges)


def _edge_len_m(a, b):
    """Great-circle length of a topology edge between [lat, lon] endpoints."""
    import math
    (lat1, lon1), (lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(h))


def run_reverse_check(data, osrm, rng, num):
    """REVERSE coverage: confirm OSRM will traverse the edges the topology carries.

    The forward check proves topology ⊇ OSRM (every routed node maps to a topology
    edge). This proves the other direction is sane: a topology edge between two
    adjacent OSM nodes should be routable by OSRM at roughly its own length. An edge
    OSRM refuses (NoRoute) or only reaches via a huge detour is one the topology has
    but OSRM won't route — exactly the ferry class of bug (a route=ferry edge over
    open water). Reported as 'orphan' edges; a clean build should have ~0.
    """
    nodes = data["nodes"]
    pairs = list(data["node_pair_to_edge"].keys())
    if not pairs:
        print("\n[reverse] no edges to sample")
        return

    print(f"\n========== REVERSE CHECK ({num} edges) ==========")
    orphans = []
    checked = 0
    for _ in range(num):
        gi_from, gi_to = pairs[rng.randrange(len(pairs))]
        a, b = nodes[gi_from], nodes[gi_to]
        straight = _edge_len_m(a, b)
        if straight < 1.0:
            continue
        checked += 1
        res = osrm.calculate_route((a[0], a[1]), (b[0], b[1]), "walk")
        if "error" in res:
            orphans.append((straight, None, a, b))
            continue
        dist = res.get("distance")
        # Adjacent nodes: OSRM should reach b within a small multiple of the edge's
        # own length. A ferry edge is long + OSRM now refuses it, so it lands here.
        if dist is None or dist > max(4 * straight, straight + 300):
            orphans.append((straight, dist, a, b))

    print(f"edges checked:           {checked}")
    print(f"orphan edges (OSRM won't traverse): {len(orphans)}/{checked} "
          f"({100*len(orphans)/max(checked,1):.2f}%)")
    if orphans:
        orphans.sort(reverse=True, key=lambda o: o[0])
        print("\nlongest orphan edges (edge_len_m, osrm_dist_m, start, end):")
        for straight, dist, a, b in orphans[:8]:
            print(f"  {straight:8.0f}m  osrm={('NoRoute' if dist is None else f'{dist:.0f}m'):>10}  {a} -> {b}")
    else:
        print("no orphan edges 🎉  (topology and OSRM agree both directions)")


if __name__ == "__main__":
    main()
