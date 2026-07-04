"""Unit tests for vote_semantics.plan_block_vote — the block-scoped
clear-then-cast press matrix from docs/three-layer-model.md §4.2, plus the
§2.5 invariant as a randomized property.
"""
import random

from vote_semantics import VotePlan, block_of, plan_block_vote

# edges 0,1,2 → block 10 ; edges 3,4 → block 20 ; edge 5 → block 30 ;
# edges 6,7 unmapped (singletons)
EBID = [10, 10, 10, 20, 20, 30, -1, -1]


def apply_plan(prior: dict[int, int], plan: VotePlan, direction: int) -> dict[int, int]:
    """Apply a plan to a prior state, asserting each transition's prev matches."""
    state = dict(prior)
    for e, prev in plan.clear:
        assert state.get(e, 0) == prev, f"clear prev mismatch on edge {e}"
        state.pop(e, None)
    for e, prev in plan.cast:
        assert state.get(e, 0) == prev, f"cast prev mismatch on edge {e}"
        state[e] = direction
    return {e: d for e, d in state.items() if d != 0}


def votes_by_block(state: dict[int, int], ebid) -> dict:
    out: dict = {}
    for e, d in state.items():
        out.setdefault(block_of(ebid, e), {})[e] = d
    return out


def assert_postcondition(state, selection, direction, touched, ebid):
    """§2.5: inside every touched block, votes == {e in S: direction}."""
    by_block = votes_by_block(state, ebid)
    sel = set(selection)
    for b in touched:
        expected = ({e: direction for e in sel if block_of(ebid, e) == b}
                    if direction != 0 else {})
        assert by_block.get(b, {}) == expected


# ── §4.2 press matrix ────────────────────────────────────────────────────────

def test_fresh_cast_lands_only_on_selection():
    plan = plan_block_vote([0, 3], 1, {}, EBID)
    assert plan.clear == []
    assert plan.cast == [(0, 0), (3, 0)]
    assert plan.touched_blocks == {10, 20}
    state = apply_plan({}, plan, 1)
    assert state == {0: 1, 3: 1}


def test_unvote_all_clears_everything_in_touched_blocks():
    # Device holds + on edges 0,1 (block 10) and 3 (block 20); presses off.
    prior = {0: 1, 1: 1, 3: 1}
    plan = plan_block_vote([0, 3], 0, prior, EBID)
    assert plan.cast == []
    assert sorted(plan.clear) == [(0, 1), (1, 1), (3, 1)]
    state = apply_plan(prior, plan, 0)
    assert state == {}


def test_flip_clears_plus_everywhere_and_casts_minus_on_selection():
    # + held on every edge of blocks 10 and 20; cast − on a narrower selection.
    prior = {0: 1, 1: 1, 2: 1, 3: 1, 4: 1}
    plan = plan_block_vote([0, 3], -1, prior, EBID)
    # Non-selection + rows in touched blocks are cleared…
    assert sorted(plan.clear) == [(1, 1), (2, 1), (4, 1)]
    # …selection edges transition in place (reversal, prev = +1).
    assert plan.cast == [(0, 1), (3, 1)]
    state = apply_plan(prior, plan, -1)
    assert state == {0: -1, 3: -1}
    assert_postcondition(state, [0, 3], -1, plan.touched_blocks, EBID)


def test_partial_coverage_clears_then_casts():
    # Scattered prior + only in block 10 (edge 1, not selected).
    prior = {1: 1}
    plan = plan_block_vote([0, 3], 1, prior, EBID)
    assert plan.clear == [(1, 1)]
    assert plan.cast == [(0, 0), (3, 0)]
    state = apply_plan(prior, plan, 1)
    assert state == {0: 1, 3: 1}


def test_blocks_outside_touched_are_untouched():
    prior = {5: 1, 6: -1}  # block 30 + an unmapped singleton
    plan = plan_block_vote([0], 1, prior, EBID)
    assert plan.clear == []
    assert plan.cast == [(0, 0)]
    state = apply_plan(prior, plan, 1)
    assert state == {0: 1, 5: 1, 6: -1}


def test_selection_edges_already_at_direction_are_noops():
    prior = {0: 1, 1: 1}
    plan = plan_block_vote([0, 1], 1, prior, EBID)
    assert plan.clear == []
    assert plan.cast == []


def test_singleton_fallback_without_blocks():
    # edge_block_id=None → per-edge semantics: a cast on edge 0 never touches
    # the device's row on edge 1, and a same-direction re-cast is a no-op.
    prior = {0: 1, 1: 1}
    plan = plan_block_vote([0], 1, prior, None)
    assert plan.clear == []
    assert plan.cast == []
    plan = plan_block_vote([0], -1, prior, None)
    assert plan.clear == []
    assert plan.cast == [(0, 1)]
    plan = plan_block_vote([0], 0, prior, None)
    assert plan.clear == [(0, 1)]
    assert plan.cast == []


def test_unmapped_edges_are_singletons():
    # Edges 6 and 7 both map to -1 but must NOT share a block.
    prior = {7: 1}
    plan = plan_block_vote([6], 1, prior, EBID)
    assert plan.clear == []
    assert plan.cast == [(6, 0)]
    assert block_of(EBID, 6) != block_of(EBID, 7)
    # An unmapped edge still unvotes/flips itself.
    plan = plan_block_vote([7], -1, prior, EBID)
    assert plan.clear == []
    assert plan.cast == [(7, 1)]


def test_direction_zero_clears_selection_rows_too():
    prior = {6: 1}
    plan = plan_block_vote([6], 0, prior, EBID)
    assert plan.clear == [(6, 1)]
    assert plan.cast == []


def test_duplicate_selection_edges_deduped():
    plan = plan_block_vote([0, 0, 3, 3], 1, {}, EBID)
    assert plan.cast == [(0, 0), (3, 0)]


# ── Randomized property: no sequence of plans breaks the §2.5 invariant ─────

def assert_one_direction_per_block(state, ebid):
    dirs_by_block: dict = {}
    for e, d in state.items():
        dirs_by_block.setdefault(block_of(ebid, e), set()).add(d)
    for b, dirs in dirs_by_block.items():
        assert len(dirs) == 1, f"block {b} holds both directions: {dirs}"


def test_random_plan_sequences_preserve_invariant():
    rng = random.Random(20260704)
    n_edges = 24
    for trial in range(50):
        # Random mapping: some blocks of 2-4 edges, some unmapped edges.
        ebid = [rng.choice([0, 1, 2, 3, 4, -1]) for _ in range(n_edges)]
        if trial % 5 == 0:
            ebid = None  # exercise the singleton fallback too
        # Invariant-consistent random prior: one direction per block.
        block_dir = {}
        prior = {}
        for e in rng.sample(range(n_edges), rng.randint(0, n_edges)):
            b = block_of(ebid, e)
            prior[e] = block_dir.setdefault(b, rng.choice([1, -1]))
        state = dict(prior)

        for _ in range(20):
            selection = rng.sample(range(n_edges), rng.randint(1, 6))
            direction = rng.choice([1, -1, 0])
            plan = plan_block_vote(selection, direction, state, ebid)
            state = apply_plan(state, plan, direction)
            assert_one_direction_per_block(state, ebid)
            assert_postcondition(state, selection, direction,
                                 plan.touched_blocks, ebid)
