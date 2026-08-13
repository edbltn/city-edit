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

    # Equivalent canonical rows: (edge_id, vt_id, direction, device_id, ip_hash)
    rows = [
        (0, VT, UP, "d1", "d1"),
        (1, VT, UP, "d1", "d1"),
        (2, VT, DOWN, "d2", "d2"),
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
        (0, VT, UP, "d1", "ip1"),
        (1, VT, DOWN, "d1", "ip1"),
    ]
    with caplog.at_level(logging.WARNING, logger="block_votes"):
        bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    warnings = [r for r in caplog.records if "invariant violation" in r.message]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "block 10" in msg and f"vt {VT}" in msg and "d1" in msg


def test_rebuild_no_warning_when_consistent(redis_client, caplog):
    rows = [
        (0, VT, UP, "d1", "ip1"),
        (1, VT, UP, "d1", "ip1"),
        (2, VT, DOWN, "d1", "ip1"),  # different block — allowed
    ]
    with caplog.at_level(logging.WARNING, logger="block_votes"):
        bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert not [r for r in caplog.records if "invariant violation" in r.message]


# ── Counting identity (vote_identity) ──────────────────────────────────────
#
# The block layer counts on the COUNTING identity, not the device that owns the
# row. These pin the two halves of that: fragmented ids behind one IP collapse
# (the 2026-08-13 prod bug), and genuinely different people still accumulate.

def test_rebuild_collapses_fragmented_devices_behind_one_ip(redis_client):
    # One person, one IP, three device_ids — a phone whose localStorage did not
    # survive between scans. Under device identity this block read 3.
    rows = [
        (0, VT, UP, "dev-a", "ip1"),
        (0, VT, UP, "dev-b", "ip1"),
        (1, VT, UP, "dev-c", "ip1"),
    ]
    bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert total(redis_client, 10) == 1
    # …and the multiplicity remembers all three, so no single retraction
    # can drop the block below what is still genuinely there.
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "ip1") == "3"


def test_rebuild_keeps_distinct_ips_apart(redis_client):
    rows = [
        (0, VT, UP, "dev-a", "ip1"),
        (1, VT, UP, "dev-b", "ip2"),
    ]
    bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert total(redis_client, 10) == 2


def test_rebuild_falls_back_to_device_without_an_ip(redis_client):
    # Rows predating the ip_hash column: no IP to collapse on, so each device
    # counts — exactly the old behaviour, for those rows only.
    rows = [
        (0, VT, UP, "dev-a", None),
        (1, VT, UP, "dev-b", None),
    ]
    bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert total(redis_client, 10) == 2


def test_shared_identity_survives_one_voters_retraction(redis_client):
    # Two devices behind one IP vote the same block; one takes their vote back.
    # The block must still count 1 — the other person is still asking for it.
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "ip1")
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, UP, 0, "ip1")
    assert total(redis_client, 10) == 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "ip1")
    assert total(redis_client, 10) == 1
    bv.apply_block_delta(redis_client, SLUG, MODE, 10, VT, 0, UP, "ip1")
    assert total(redis_client, 10) == 0


def test_rebuild_does_not_warn_when_co_nat_voters_disagree(redis_client, caplog):
    # Two DEVICES behind one IP, opposite directions on one block. The
    # one-direction invariant is per device and neither breaks it, so this is a
    # disagreement, not corruption — it must not be logged as a violation.
    rows = [
        (0, VT, UP, "dev-a", "ip1"),
        (1, VT, DOWN, "dev-b", "ip1"),
    ]
    with caplog.at_level(logging.WARNING, logger="block_votes"):
        bv.rebuild_from_db(redis_client, SLUG, MODE, EDGE_BLOCK, rows)
    assert not [r for r in caplog.records if "invariant violation" in r.message]
    # Both sides are counted; the block nets out to zero, one up and one down.
    arr = bv.build_block_arrays(redis_client, SLUG, MODE, N_BLOCKS)
    assert arr["block_vote_types"][10] == [[0, 1, 1]]


def test_version_marker_changes_with_the_counting_identity():
    # The marker forces a rebuild when a field's MEANING changes, not just when
    # block ids renumber — otherwise two keyings would share one hash.
    import vote_identity
    assert vote_identity.SCHEME in bv.version_marker("blocks-v9")
    assert bv.version_marker("blocks-v9") != bv.version_marker("blocks-v10")


# ── Batched write path (apply_block_deltas_batch ≡ per-edge loop) ───────────

def _loop_apply(r, block_ops, device):
    for b, prev, new in block_ops:
        bv.apply_block_delta(r, SLUG, MODE, b, VT, new, prev, device)


def _state(r):
    """(bagg nonzero fields, all bd hashes) — the full observable block state."""
    bagg = {k: v for k, v in r.hgetall(bv.bagg_key(SLUG, MODE)).items() if int(v) != 0}
    bd = {}
    for key in r.scan_iter(match=f"bd:{SLUG}:{MODE}:*"):
        h = r.hgetall(key)
        if h:
            bd[key] = h
    return bagg, bd


def test_batch_matches_loop_route_vote(redis_client):
    """A route vote spanning both edges of block 10 + block 20, then a reversal."""
    import fakeredis
    loop_r = fakeredis.FakeStrictRedis(decode_responses=True)

    fresh = [(10, 0, UP), (10, 0, UP), (20, 0, UP)]
    reverse = [(10, UP, DOWN), (10, UP, DOWN), (20, UP, DOWN)]
    for ops in (fresh, reverse):
        bv.apply_block_deltas_batch(redis_client, SLUG, MODE, ops, VT, "d1")
        _loop_apply(loop_r, ops, "d1")
        assert _state(redis_client) == _state(loop_r)
    assert total(redis_client, 10) == 1
    assert total(redis_client, 20) == 1


def test_batch_clear_then_cast_same_block_keeps_field(redis_client):
    """A plan that clears edge A and casts edge B in the SAME block must leave
    the device present (multiplicity 1) — the deferred HDEL must not wipe the
    field the cast re-created."""
    import fakeredis
    loop_r = fakeredis.FakeStrictRedis(decode_responses=True)

    # prior: d1 upvoted edge 0 (block 10). New selection: edge 1 only (block 10)
    # → plan clears edge 0 (UP→0) and casts edge 1 (0→UP).
    seed = [(10, 0, UP)]
    plan = [(10, UP, 0), (10, 0, UP)]
    for r, apply in ((redis_client, lambda o: bv.apply_block_deltas_batch(
            r, SLUG, MODE, o, VT, "d1")), (loop_r, lambda o: _loop_apply(loop_r, o, "d1"))):
        apply(seed)
        apply(plan)

    assert _state(redis_client) == _state(loop_r)
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") == "1"
    assert total(redis_client, 10) == 1  # still exactly one deduped vote


def test_batch_full_clear_removes_field_and_aggregate(redis_client):
    seed = [(10, 0, UP), (10, 0, UP)]
    bv.apply_block_deltas_batch(redis_client, SLUG, MODE, seed, VT, "d1")
    bv.apply_block_deltas_batch(
        redis_client, SLUG, MODE, [(10, UP, 0), (10, UP, 0)], VT, "d1")
    assert redis_client.hget(bv.bd_key(SLUG, MODE, 10, VT, 0), "d1") is None
    assert total(redis_client, 10) == 0


def test_batch_skips_unmapped_and_noop(redis_client):
    bv.apply_block_deltas_batch(
        redis_client, SLUG, MODE, [(-1, 0, UP), (10, UP, UP)], VT, "d1")
    assert _state(redis_client) == ({}, {})
