"""
Block-scoped vote planning — the pure write-semantics core of /api/vote.

Implements docs/three-layer-model.md §4 ("clear-then-cast"): a cast on a
selection S first clears the device's same-type votes across every *touched
block* (the blocks covering S), then casts on exactly the edges of S. This
module has no Flask/Redis/Postgres dependencies so the semantics are testable
in isolation; app.py applies the returned plan under the voter lock.
"""

from dataclasses import dataclass, field


@dataclass
class VotePlan:
    """The edge-level transitions realizing one block-scoped press.

    clear: (edge_id, prev_dir) rows to delete (prev_dir ∈ {+1, −1}).
    cast:  (edge_id, prev_dir) edges to set to the pressed direction
           (prev_dir 0 = fresh, opposite = in-place reversal).
    touched_blocks: block keys covering the selection (for logging/tests).
    """
    clear: list[tuple[int, int]] = field(default_factory=list)
    cast: list[tuple[int, int]] = field(default_factory=list)
    touched_blocks: set = field(default_factory=set)


def block_of(edge_block_id, edge_id: int):
    """The block key an edge aggregates under.

    A real block id when the map ships a mapping and the edge is mapped (≥ 0);
    otherwise a per-edge singleton key, so every rule below degrades to plain
    per-edge semantics when blocks are absent or the edge is unmapped (−1).
    """
    if edge_block_id is not None and 0 <= edge_id < len(edge_block_id):
        b = int(edge_block_id[edge_id])
        if b >= 0:
            return b
    return ("e", edge_id)


def plan_block_vote(
    edge_ids: list[int],
    direction: int,
    prior: dict[int, int],
    edge_block_id,
) -> VotePlan:
    """Plan one press: direction ∈ {+1, −1} casts, 0 unvotes-all.

    `prior` is the device's existing {edge_id: direction} rows for this
    (map, vote_type). `edge_block_id` maps edge → block (−1/None ⇒ singleton).

    Post-condition (§2.5 invariant): after applying the plan, the device's
    votes of this type inside every touched block are exactly
    {e ∈ S: direction} — one direction per block. Blocks outside the touched
    set are untouched. Selection edges already at `direction` appear in
    neither list (no-ops).
    """
    selection = list(dict.fromkeys(edge_ids))  # dedupe, keep order
    sel_set = set(selection)
    touched = {block_of(edge_block_id, e) for e in selection}

    clear: list[tuple[int, int]] = []
    for e in sorted(prior):
        prev = prior[e]
        if prev == 0 or block_of(edge_block_id, e) not in touched:
            continue
        if direction != 0 and e in sel_set:
            continue  # handled by cast as an in-place transition
        clear.append((e, prev))

    cast: list[tuple[int, int]] = []
    if direction != 0:
        cast = [(e, prior.get(e, 0)) for e in selection
                if prior.get(e, 0) != direction]

    return VotePlan(clear=clear, cast=cast, touched_blocks=touched)
