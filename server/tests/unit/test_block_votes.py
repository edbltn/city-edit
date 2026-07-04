"""Unit tests for block_votes — the deduplicated block projection of edge votes.

These pin the invariants from docs/vote-system-design.md §2.7: a user's vote_type
counts once per block no matter how many edges in that block carry it; removals
are exact; reversals move the device; and a rebuild from Postgres rows yields the
same aggregate as the incremental path.
"""
import block_votes as bv
from vote_store import UP, DOWN

SLUG = "test-map"
MODE = 3  # "walk"
VT = 5

# edges 0,1 → block 10 ; edge 2 → block 20 ; edge 3 unmapped
EDGE_BLOCK = [10, 10, 20, -1]
N_BLOCKS = 30


def net(redis_client, block_id):
    arr = bv.build_block_arrays(redis_client, SLUG, MODE, N_BLOCKS)
    return arr["block_votes"][block_id]


def test_same_user_multiple_edges_counts_once(redis_client):
    # One device votes UP on both edges of block 10.
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert net(redis_client, 10) == 1  # deduped, not 2
    # multiplicity is tracked even though the deduped count is 1
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") == "2"
    assert redis_client.hlen(bv.bd_key(SLUG, MODE, 10, VT, 0)) == 1


def test_distinct_users_accumulate(redis_client):
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d2")
    assert net(redis_client, 10) == 2


def test_removal_is_exact(redis_client):
    # d1 on both edges of block 10 → count 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert net(redis_client, 10) == 1
    # remove one edge → still present (multiplicity 2→1)
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "d1")
    assert net(redis_client, 10) == 1
    # remove the other edge → gone
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "d1")
    assert net(redis_client, 10) == 0
    # the device hash field is cleaned up
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") is None


def test_reversal_moves_device(redis_client):
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert net(redis_client, 10) == 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, DOWN, UP, "d1")
    assert net(redis_client, 10) == -1  # up 0, down 1
    arr = bv.build_block_arrays(redis_client, SLUG, MODE, N_BLOCKS)
    # vote-type breakdown reflects the down vote: [legendIdx, up, down]
    assert arr["block_vote_types"][10] == [[0, 0, 1]]


def test_unmapped_edge_ignored(redis_client):
    prev = {3: 0}
    bv.apply_block_deltas(redis_client, SLUG, MODE, EDGE_BLOCK, [3], VT, UP, prev, "d1")
    # edge 3 maps to block -1 → nothing recorded anywhere
    assert bv.build_block_arrays(redis_client, SLUG, MODE, N_BLOCKS)["block_votes"] == [0] * N_BLOCKS


def test_apply_deltas_dedups_across_block_edges(redis_client):
    # A route votes both edges of block 10 + the single edge of block 20.
    prev = {0: 0, 1: 0, 2: 0}
    bv.apply_block_deltas(redis_client, SLUG, MODE, EDGE_BLOCK, [0, 1, 2], VT, UP, prev, "d1")
    assert net(redis_client, 10) == 1   # two edges, one user → 1
    assert net(redis_client, 20) == 1


def test_rebuild_matches_incremental(redis_client):
    # Build state incrementally: d1 votes both edges of block 10 UP; d2 votes
    # edge 2 (block 20) DOWN.
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 20, VT, DOWN, 0, "d2")
    incremental = redis_client.hgetall(bv.bagg_key(SLUG, MODE))

    # Equivalent canonical rows: (edge_id, vt_id, direction, device_id)
    rows = [
        (0, VT, UP, "d1"),
        (1, VT, UP, "d1"),
        (2, VT, DOWN, "d2"),
    ]
    bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    rebuilt = redis_client.hgetall(bv.bagg_key(SLUG, MODE))

    # Aggregate is identical; only non-zero fields matter.
    inc = {k: v for k, v in incremental.items() if int(v) != 0}
    reb = {k: v for k, v in rebuilt.items() if int(v) != 0}
    assert inc == reb
    assert net(redis_client, 10) == 1
    assert net(redis_client, 20) == -1
