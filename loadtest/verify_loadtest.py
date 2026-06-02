#!/usr/bin/env python3
"""Stateful load test: drive N agents to a KNOWN final vote state, then verify.

Unlike the throughput-only Locust run, this asserts correctness under
concurrency. Each agent is given a deterministic expected final direction for
every edge of a shared route and a *convoluted* path to get there (cast for,
flip against, flip back, then settle — including removals). All agents march
concurrently through the real /api/vote endpoint, then we verify two ways:

  1. Per-agent: GET /api/my-votes returns exactly each agent's final direction.
  2. Aggregate: the GET /api/graph-votes net per edge moved by exactly the sum
     of agents' final directions (measured as an after−before delta, so
     pre-existing votes on those edges don't matter).

Exit code is non-zero if either check fails.

  python verify_loadtest.py --host http://localhost:8080 --users 10
"""
import argparse
import math
import random
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

# Final-direction cycle spread across (agent, edge): +1 for, -1 against, 0 none.
FINALS = [1, -1, 0]

CITY_SEEDS = {
    "sf": [(37.7944, -122.3977), (37.7785, -122.4056), (37.7609, -122.4350)],
    "nyc": [(40.7580, -73.9855), (40.7061, -74.0087), (40.7440, -73.9489)],
    "chicago": [(41.8781, -87.6298), (41.9214, -87.6513), (41.9092, -87.6776)],
}
EARTH_KM_PER_DEG_LAT = 111.0


def nearby_point(lat, lon, max_km):
    r = max_km * math.sqrt(random.random())
    theta = random.uniform(0, 2 * math.pi)
    dlat = (r * math.cos(theta)) / EARTH_KM_PER_DEG_LAT
    dlon = (r * math.sin(theta)) / (EARTH_KM_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lat + dlat, lon + dlon)


def final_dir(agent_i, edge_j):
    return FINALS[(agent_i + edge_j) % len(FINALS)]


def fetch_map_meta(host, slug):
    r = requests.get(f"{host}/api/maps/{slug}", timeout=30)
    r.raise_for_status()
    data = r.json()
    mode = data.get("mode") or "walk"
    route_types = [vt["label"] for vt in (data.get("voteTypes") or [])
                   if vt.get("pointType") == "route"]
    label = route_types[0] if route_types else "Improve bike lane"
    city = data.get("city") or {}
    city_id = data.get("cityId") or city.get("id") or "sf"
    seeds = CITY_SEEDS.get(city_id) or CITY_SEEDS["sf"]
    return mode, label, seeds


def find_shared_route(host, slug, seeds, max_edges):
    """Route between nearby seeded points until we get a non-empty edge set."""
    for seed in seeds:
        for _ in range(4):
            start = nearby_point(seed[0], seed[1], 0.3)
            end = nearby_point(seed[0], seed[1], 1.2)
            r = requests.post(f"{host}/api/routes", timeout=60, json={
                "map": slug, "start": list(start), "end": list(end), "waypoints": [],
            })
            if r.ok:
                edges = r.json().get("edge_ids") or []
                if edges:
                    return list(dict.fromkeys(edges))[:max_edges]
    return []


def post_vote(session, host, slug, mode, label, voter_id, edge_ids, direction):
    if not edge_ids:
        return
    session.post(f"{host}/api/vote", timeout=60, json={
        "map": slug, "mode": mode, "vote_type": label, "voter_id": voter_id,
        "edge_ids": edge_ids, "direction": direction,
    }).raise_for_status()


def run_agent(host, slug, mode, label, agent_i, edges):
    """Convoluted routine that lands each edge on its expected final direction."""
    session = requests.Session()
    voter_id = f"verify-{uuid.uuid4()}"

    # Noise: cast for → flip against → flip back to for (all edges).
    post_vote(session, host, slug, mode, label, voter_id, edges, 1)
    post_vote(session, host, slug, mode, label, voter_id, edges, -1)
    post_vote(session, host, slug, mode, label, voter_id, edges, 1)

    # Settle: group edges by their target final direction and apply it once.
    by_dir = {1: [], -1: [], 0: []}
    for j, eid in enumerate(edges):
        by_dir[final_dir(agent_i, j)].append(eid)
    # -1 reverses the +1 from noise; 0 removes it; +1 is already set (no-op).
    post_vote(session, host, slug, mode, label, voter_id, by_dir[-1], -1)
    post_vote(session, host, slug, mode, label, voter_id, by_dir[0], 0)

    return voter_id


def get_net(host, slug, mode, edges):
    r = requests.get(f"{host}/api/graph-votes",
                     params={"map": slug, "mode": mode}, timeout=60)
    r.raise_for_status()
    ev = r.json().get("edge_votes") or []
    return {eid: (ev[eid] if eid < len(ev) else 0) for eid in edges}


def verify_my_votes(host, slug, mode, label, voter_id, agent_i, edges):
    r = requests.get(f"{host}/api/my-votes", params={
        "map": slug, "mode": mode, "voter_id": voter_id,
        "edge_ids": ",".join(str(e) for e in edges),
    }, timeout=60)
    r.raise_for_status()
    votes = r.json().get("votes") or {}
    bad = []
    for j, eid in enumerate(edges):
        want = final_dir(agent_i, j)
        got = (votes.get(str(eid)) or {}).get(label, 0)
        if got != want:
            bad.append((eid, want, got))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://localhost:8080")
    ap.add_argument("--map", default="sf-bike-lanes")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--edges", type=int, default=9, help="edges of the shared route to use")
    args = ap.parse_args()
    host = args.host.rstrip("/")

    print(f"[verify] map={args.map} users={args.users} host={host}")
    mode, label, seeds = fetch_map_meta(host, args.map)
    print(f"[verify] mode={mode} vote_type={label!r}")

    edges = find_shared_route(host, args.map, seeds, args.edges)
    if not edges:
        print("[verify] FAIL: could not find a routable shared edge set")
        return 1
    print(f"[verify] shared edges ({len(edges)}): {edges}")

    # Expected aggregate net delta per edge = sum of agents' final directions.
    expected_delta = {
        eid: sum(final_dir(i, j) for i in range(args.users))
        for j, eid in enumerate(edges)
    }

    before = get_net(host, args.map, mode, edges)

    with ThreadPoolExecutor(max_workers=args.users) as pool:
        futures = [pool.submit(run_agent, host, args.map, mode, label, i, edges)
                   for i in range(args.users)]
        voter_ids = [f.result() for f in futures]
    print(f"[verify] {args.users} agents finished their routines")

    ok = True

    # 1. Per-agent authoritative state.
    for i, vid in enumerate(voter_ids):
        bad = verify_my_votes(host, args.map, mode, label, vid, i, edges)
        if bad:
            ok = False
            print(f"[verify] agent {i}: WRONG my-votes {bad}")
    if ok:
        print(f"[verify] PASS: all {args.users} agents' per-edge final state correct")

    # 2. Aggregate net delta.
    after = get_net(host, args.map, mode, edges)
    agg_ok = True
    for eid in edges:
        delta = after[eid] - before[eid]
        if delta != expected_delta[eid]:
            agg_ok = False
            print(f"[verify] edge {eid}: net delta {delta} != expected {expected_delta[eid]}")
    if agg_ok:
        print("[verify] PASS: aggregate net per edge matches the expected sum")
    ok = ok and agg_ok

    print(f"[verify] {'PASS ✅' if ok else 'FAIL ❌'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
