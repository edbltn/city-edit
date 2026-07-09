"""Redis vote-count logic (fresh / reverse / remove) on fakeredis.

These exercise the live write path the unified /api/vote endpoint uses —
apply_directional + read_edge_vt_counts + build_arrays — without Postgres.
"""
import json

import vote_store as v

SLUG = "test-map"
MODE = v.mode_to_int("walkways")
VT = 10  # arbitrary vote-type id (label resolution not needed for count math)


def counts(redis_client, edges):
    return v.read_edge_vt_counts(redis_client, SLUG, edges, MODE, VT)


def test_fresh_up_increments_up(redis_client):
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    assert counts(redis_client, [0]) == {0: [1, 0]}


def test_reversal_moves_vote_across_sides(redis_client):
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)      # fresh up
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.DOWN, v.UP)  # reverse
    assert counts(redis_client, [0]) == {0: [0, 1]}


def test_removal_decrements_without_adding(redis_client):
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.DOWN, 0)     # fresh down
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, 0, v.DOWN)     # remove
    assert counts(redis_client, [0]) == {0: [0, 0]}


def test_no_op_when_direction_unchanged(redis_client):
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, v.UP)  # no change
    assert counts(redis_client, [0]) == {0: [1, 0]}


def test_independent_voters_accumulate(redis_client):
    # Two distinct voters both upvote the same edge (each is a fresh 0→up).
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    assert counts(redis_client, [0]) == {0: [2, 0]}


def test_build_arrays_reflects_net_and_breakdown(redis_client, tiny_graph):
    # edge0: 3 up, 1 down → net 2; edge1: 1 down → net -1
    for _ in range(3):
        v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.DOWN, 0)
    v.apply_directional(redis_client, SLUG, 1, MODE, VT, v.DOWN, 0)

    votes = v.read_all(redis_client, SLUG)
    arrays = v.build_arrays(
        votes, tiny_graph["edge_count"], tiny_graph["node_count"],
        tiny_graph["node_adj"], mode_filter=MODE,
    )
    assert arrays["edge_votes"] == [2, -1]
    # node 1 borders both edges; node total is the max adjacent net (2).
    assert arrays["node_votes"][1] == 2


def test_publish_delta_broadcasts_authoritative_counts(redis_client):
    pubsub = redis_client.pubsub()
    pubsub.subscribe(v.channel_key(SLUG))
    pubsub.get_message()  # consume the subscribe confirmation

    v.apply_directional(redis_client, SLUG, 0, MODE, VT, v.UP, 0)
    vt_counts = v.read_edge_vt_counts(redis_client, SLUG, [0], MODE, VT)
    rev = v.publish_delta(
        redis_client, SLUG, [0], MODE, VT, direction=v.UP, vt_counts=vt_counts
    )

    assert rev == 1
    msg = pubsub.get_message(timeout=1)
    payload = json.loads(msg["data"])
    assert payload["type"] == "delta"
    assert payload["vtCounts"] == {"0": [1, 0]}
    assert payload["rev"] == 1


# ── Batched write path (apply_directional_batch ≡ per-edge loop) ────────────

def test_batch_matches_loop_for_mixed_plan(redis_client):
    """One pipelined batch produces byte-identical Redis state to the loop."""
    import fakeredis
    loop_r = fakeredis.FakeStrictRedis(decode_responses=True)

    # clears (→0), fresh casts (0→dir), reversals (∓→±) in one plan
    ops = [(0, v.UP, 0), (1, v.DOWN, 0),          # clears
           (2, 0, v.UP), (3, v.DOWN, v.UP),       # fresh + reversal
           (4, v.UP, v.UP)]                       # no-op (guarded)
    # seed prior state on both sides so decrements land on real counts
    for r in (redis_client, loop_r):
        v.apply_directional(r, SLUG, 0, MODE, VT, v.UP, 0)
        v.apply_directional(r, SLUG, 1, MODE, VT, v.DOWN, 0)
        v.apply_directional(r, SLUG, 3, MODE, VT, v.DOWN, 0)
        v.apply_directional(r, SLUG, 4, MODE, VT, v.UP, 0)

    v.apply_directional_batch(redis_client, SLUG, ops, MODE, VT)
    for eid, prev, new in ops:
        v.apply_directional(loop_r, SLUG, eid, MODE, VT, new, prev)

    assert redis_client.hgetall(v.hash_key(SLUG)) == loop_r.hgetall(v.hash_key(SLUG))
    assert counts(redis_client, [0, 1, 2, 3, 4]) == {
        0: [0, 0], 1: [0, 0], 2: [1, 0], 3: [1, 0], 4: [1, 0]}


def test_batch_empty_plan_is_noop(redis_client):
    v.apply_directional_batch(redis_client, SLUG, [], MODE, VT)
    v.apply_directional_batch(redis_client, SLUG, [(0, v.UP, v.UP)], MODE, VT)
    assert redis_client.hgetall(v.hash_key(SLUG)) == {}
