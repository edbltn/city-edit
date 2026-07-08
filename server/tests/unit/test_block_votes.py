"""Unit tests for block_votes — the deduplicated block projection of edge votes.

These pin the invariants from docs/vote-system-design.md §2.7: a user's vote_type
counts once per block no matter how many edges in that block carry it; removals
are exact; reversals move the device; and a rebuild from Postgres rows yields the
same aggregate as the incremental path.
"""
import logging

import block_votes as bv
from vote_store import UP, DOWN

SLUG = "test-map"
MODE = 3  # "walk"
VT = 5

# edges 0,1 → block 10 ; edge 2 → block 20 ; edge 3 unmapped
EDGE_BLOCK = [10, 10, 20, -1]
N_BLOCKS = 30


def total(redis_client, block_id):
    """block_votes is TOTAL deduped activity (up + down) — the heat value."""
    arr = bv.build_block_arrays(redis_client, SLUG, MODE, N_BLOCKS)
    return arr["block_votes"][block_id]


def test_same_user_multiple_edges_counts_once(redis_client):
    # One device votes UP on both edges of block 10.
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert total(redis_client, 10) == 1  # deduped, not 2
    # multiplicity is tracked even though the deduped count is 1
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") == "2"
    assert redis_client.hlen(bv.bd_key(SLUG, MODE, 10, VT, 0)) == 1


def test_distinct_users_accumulate(redis_client):
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d2")
    assert total(redis_client, 10) == 2


def test_removal_is_exact(redis_client):
    # d1 on both edges of block 10 → count 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert total(redis_client, 10) == 1
    # remove one edge → still present (multiplicity 2→1)
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "d1")
    assert total(redis_client, 10) == 1
    # remove the other edge → gone
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "d1")
    assert total(redis_client, 10) == 0
    # the device hash field is cleaned up
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") is None


def test_reversal_moves_device(redis_client):
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "d1")
    assert total(redis_client, 10) == 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, DOWN, UP, "d1")
    assert total(redis_client, 10) == 1  # up 0, down 1 → total 1 (downvotes read hot)
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
    assert total(redis_client, 10) == 1   # two edges, one user → 1
    assert total(redis_client, 20) == 1


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
    assert total(redis_client, 10) == 1
    assert total(redis_client, 20) == 1  # one DOWN vote → total 1


def test_rebuild_warns_on_both_directions_in_a_block(redis_client, caplog):
    # Canonical rows violating the §2.5 invariant: one device holds UP on edge 0
    # and DOWN on edge 1, both in block 10. Rebuild logs a warning, no crash.
    rows = [
        (0, VT, UP, "d1"),
        (1, VT, DOWN, "d1"),
    ]
    with caplog.at_level(logging.WARNING, logger="block_votes"):
        bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    warnings = [r for r in caplog.records if "invariant violation" in r.message]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "block 10" in msg and f"vt {VT}" in msg and "d1" in msg


def test_rebuild_no_warning_when_consistent(redis_client, caplog):
    rows = [
        (0, VT, UP, "d1"),
        (1, VT, UP, "d1"),
        (2, VT, DOWN, "d1"),  # different block — allowed
    ]
    with caplog.at_level(logging.WARNING, logger="block_votes"):
        bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert not [r for r in caplog.records if "invariant violation" in r.message]
