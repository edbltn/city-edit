"""Postgres → Redis hydration math (the lazy reconcile in app._hydrate_map_redis).

The heatmap serves only from Redis; Postgres is the durable copy. When a map's
Redis hash is empty, app._hydrate_map_redis replays the aggregated edge_votes
(edge_id, vote_type_id, direction, count) back into Redis keyed by the MAP'S MODE,
so the fields land in the same namespace the client reads under. These tests pin
that contract without needing Flask/Postgres — they reproduce the field-build and
assert it round-trips through read_all + build_arrays, and that reading under the
wrong mode (the original bug) yields nothing.
"""
import vote_store as v

SLUG = "test-map"
EDGES = 4
NODE_ADJ = [[0], [0, 1], [1, 2], [2, 3]]  # 5 nodes, 4 edges
NODES = len(NODE_ADJ)


def _hydrate(redis_client, rows, mode_int):
    """Mirror app._hydrate_map_redis's field-build + HSET (no Flask/Postgres)."""
    h = v.hash_key(SLUG)
    for edge_id, vt_id, direction, cnt in rows:
        field = v.redis_field(edge_id, mode_int, vt_id, direction)
        redis_client.hset(h, str(field), cnt)


def test_replayed_votes_surface_under_the_maps_mode(redis_client):
    mode = v.mode_to_int("bikepaths")
    # edge 0: 3 up · edge 2: 5 up, 1 down  (vote-type 7)
    _hydrate(redis_client, [(0, 7, v.UP, 3), (2, 7, v.UP, 5), (2, 7, v.DOWN, 1)], mode)

    votes = v.read_all(redis_client, SLUG)
    arrays = v.build_arrays(votes, EDGES, NODES, NODE_ADJ, mode_filter=mode)

    assert arrays["edge_votes"] == [3, 0, 4, 0]  # net (up − down)


def test_reading_under_the_wrong_mode_drops_everything(redis_client):
    # The exact regression: votes replayed under the map's mode are invisible when
    # the heatmap is read under a different mode (here votes land in "bikepaths"
    # but the read filters "walk"), so build_arrays returns an all-zero heatmap.
    stored = v.mode_to_int("bikepaths")
    _hydrate(redis_client, [(0, 7, v.UP, 3), (2, 7, v.UP, 5)], stored)

    votes = v.read_all(redis_client, SLUG)
    wrong = v.build_arrays(votes, EDGES, NODES, NODE_ADJ,
                           mode_filter=v.mode_to_int("walk"))

    assert wrong["edge_votes"] == [0, 0, 0, 0]


def test_hydrate_is_idempotent(redis_client):
    """HSET writes absolute counts, so re-running the replay re-asserts the same
    state rather than doubling it (re-hydration after a missed startup replay)."""
    mode = v.mode_to_int("bikepaths")
    rows = [(1, 7, v.UP, 4), (3, 7, v.UP, 2)]
    _hydrate(redis_client, rows, mode)
    _hydrate(redis_client, rows, mode)  # replay again

    votes = v.read_all(redis_client, SLUG)
    arrays = v.build_arrays(votes, EDGES, NODES, NODE_ADJ, mode_filter=mode)

    assert arrays["edge_votes"] == [0, 4, 0, 2]
