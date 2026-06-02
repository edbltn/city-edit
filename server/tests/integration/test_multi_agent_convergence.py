"""Multi-agent convergence — the in-process twin of loadtest/verify_loadtest.py.

This reproduces the server's /api/vote inner loop (read prior per-voter
direction → apply_directional → persist) against fakeredis + a dict "DB", then
marches 10 agents through the SAME convoluted routine the load test uses, with
their steps interleaved to mimic concurrency. It asserts the Redis aggregate
converges to the expected sum of each agent's final per-edge direction — the
correctness property the live load test checks end-to-end.
"""
import vote_store as v

FINALS = [1, -1, 0]
MODE = v.mode_to_int("walkways")
VT = 7
SLUG = "conv-map"
EDGES = list(range(9))
N_AGENTS = 10


def final_dir(agent_i, edge_j):
    return FINALS[(agent_i + edge_j) % len(FINALS)]


def server_cast(redis_client, db, device, edge_ids, direction):
    """Mirror of app.cast_vote's locked loop: only change edges that differ."""
    for eid in edge_ids:
        key = (SLUG, eid, VT, device)
        prev = db.get(key, 0)
        if prev != direction:
            v.apply_directional(redis_client, SLUG, eid, MODE, VT, direction, prev)
            if direction == 0:
                db.pop(key, None)
            else:
                db[key] = direction


def agent_steps(agent_i):
    """The convoluted routine as a list of (edge_ids, direction) operations."""
    device = f"agent-{agent_i}"
    by_dir = {1: [], -1: [], 0: []}
    for j in EDGES:
        by_dir[final_dir(agent_i, j)].append(j)
    ops = [
        (EDGES, 1),        # noise: cast for
        (EDGES, -1),       # noise: flip against
        (EDGES, 1),        # noise: flip back to for
        (by_dir[-1], -1),  # settle: reverse the ones whose final is against
        (by_dir[0], 0),    # settle: remove the ones whose final is none
    ]
    return device, ops


def test_ten_agents_converge_to_expected_aggregate(redis_client):
    db: dict = {}
    agents = [agent_steps(i) for i in range(N_AGENTS)]

    # Interleave agents round-robin (concurrency simulation): step 0 of every
    # agent, then step 1 of every agent, etc.
    max_steps = max(len(ops) for _, ops in agents)
    for step in range(max_steps):
        for device, ops in agents:
            if step < len(ops):
                edge_ids, direction = ops[step]
                server_cast(redis_client, db, device, edge_ids, direction)

    # Per-agent final state matches its target.
    for i in range(N_AGENTS):
        device = f"agent-{i}"
        for j in EDGES:
            assert db.get((SLUG, j, VT, device), 0) == final_dir(i, j)

    # Aggregate Redis net per edge == sum of agents' final directions.
    counts = v.read_edge_vt_counts(redis_client, SLUG, EDGES, MODE, VT)
    for j in EDGES:
        up, down = counts[j]
        expected = sum(final_dir(i, j) for i in range(N_AGENTS))
        assert up - down == expected, f"edge {j}: net {up - down} != {expected}"


def test_repeated_same_direction_is_idempotent_per_voter(redis_client):
    db: dict = {}
    for _ in range(5):
        server_cast(redis_client, db, "solo", EDGES, 1)
    counts = v.read_edge_vt_counts(redis_client, SLUG, EDGES, MODE, VT)
    for j in EDGES:
        assert counts[j] == [1, 0]  # one voter, one up — never accumulates
