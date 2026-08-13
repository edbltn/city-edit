# Referenced by: docs/algorithms/01-block-identification.md (block ids ->
#   deduped vote aggregates) and docs/algorithms/07-counts.md (why summing
#   these across a selection over-counts people).
"""
Block-level vote display — a deduplicated projection of edge votes onto blocks.

Votes are stored per graph **edge** (see vote_store.py / database.edge_votes,
unchanged). For *display* we aggregate to **blocks** (one polygon per street
segment) and deduplicate so that **each (block, vote_type, direction, voter)
counts at most once** — even when a single route laid a user's vote across many
edges inside one block.

"Voter" here is the COUNTING identity, not the row's owner: vote_identity.py
decides what counts as one person (the IP hash by default, so a browser whose
localStorage does not survive the visit cannot vote the same block twice), while
`device_id` stays the storage key that owns and can retract the row. Everything
below is keyed on the counting identity; callers pass it in already resolved.

Edge counts in Redis (`ev:<slug>`) have already discarded voter identity, so we
cannot dedupe users from them. Instead we maintain a small, **incremental,
Redis-native** structure keyed by identity, updated inside the same vote lock as
the edge write. This keeps serving block votes as fast as the edge heatmap, at
the cost of being derived state that must be rebuilt from Postgres on cold-start
or graph resnap (see `rebuild_from_db`). Full rationale + invariants:
docs/vote-system-design.md §2.

Redis keys (namespaced per map slug + mode):
  bd:<slug>:<mode>:<block>:<vt>:<dir>   HASH  field=identity  value=edge-multiplicity
                                        → HLEN == distinct voters == deduped count
  bagg:<slug>:<mode>                    HASH  field=packed(block,vt,dir)  value=deduped count
                                        → one HGETALL serves the whole block layer

`dir` here is the 0/1 direction bit (0=up, 1=down), matching vote_store.dir_to_bit.
"""

import logging

import vote_identity

logger = logging.getLogger(__name__)


def version_marker(blocks_version: str | None) -> str:
    """What `bver:<slug>:<mode>` stores, so the aggregate rebuilds when either
    of the two things that change a field's MEANING changes.

    Block ids renumber on every re-bake (blocks_version), and the counting
    identity decides what a field IS (vote_identity.SCHEME). Mixing two
    identity keyings inside one `bd:` hash would count the same person twice
    — the exact failure the identity change exists to fix — so the scheme is
    part of the marker and a flip forces a rebuild from Postgres.
    """
    return f"{blocks_version or ''}|{vote_identity.SCHEME}"


# ── Slots and keys ──────────────────────────────────────────────────────────
#
# A SLOT is the (block_id, vt_id, direction bit) triple both Redis structures
# are keyed by: `bd:` holds one identity-multiplicity hash per slot, `bagg:`
# holds one deduped count per slot. Every path below carries slots and derives
# whichever key it needs from them — so the two encodings are written once, and
# no code has to recover a slot by taking a key string apart again.

Slot = tuple[int, int, int]


def bd_key(slug: str, mode: int, block_id: int, vt_id: int, dbit: int) -> str:
    """Per-(block,vote_type,direction) identity-multiplicity hash. HLEN = deduped count."""
    return f"bd:{slug}:{mode}:{block_id}:{vt_id}:{dbit}"


def bagg_key(slug: str, mode: int) -> str:
    """Per-(slug,mode) aggregate hash: packed(block,vt,dir) → deduped count."""
    return f"bagg:{slug}:{mode}"


# packed aggregate field: block_id [0..23] | vt_id [24..47] | dir [48]
# (vt widened to 24 bits alongside vote_store's codec — the global vote_types
# SERIAL exceeds 16 bits; server-internal, never crosses the wire packed)
BLOCK_BITS = 24
BLOCK_VT_BITS = 24
BLOCK_DIR_BIT = 48


def pack_block_field(block_id: int, vt_id: int, dbit: int) -> int:
    """Pack a (block, vote type, direction) slot into one integer.

    Bounds-checked for the same reason vote_store.pack is. This layout has the
    identical failure mode one field over: at vt_id = 2**24 the overflow lands
    exactly on BLOCK_DIR_BIT, so the slot decodes as vote type 0 with the
    direction flipped — an upvote silently aggregated as a downvote on the block
    layer. That is the July bug (vt at 16 bits overflowing onto bit 44), just
    relocated by the widening. Until this raised, it was still live here.

    The real guard is at creation (database.get_or_create_vote_type_id refuses
    ids past vote_store.MAX_VOTE_TYPE_ID); this is the last line of defence, so
    a bad id reaches an exception rather than the heatmap."""
    if not 0 <= block_id < (1 << BLOCK_BITS):
        raise ValueError(f"block_id {block_id} exceeds {BLOCK_BITS}-bit field")
    if not 0 <= vt_id < (1 << BLOCK_VT_BITS):
        raise ValueError(f"vote_type_id {vt_id} exceeds {BLOCK_VT_BITS}-bit field")
    return (dbit << BLOCK_DIR_BIT) | (vt_id << BLOCK_BITS) | block_id


def unpack_block_field(key: int) -> tuple[int, int, int]:
    return (key & 0xFF_FFFF, (key >> 24) & 0xFF_FFFF, (key >> 48) & 0x1)


def _slot_bd_key(slug: str, mode: int, slot: Slot) -> str:
    return bd_key(slug, mode, *slot)


def _slot_bagg_field(slot: Slot) -> str:
    return str(pack_block_field(*slot))


def _transitions(block_id: int, vt_id: int,
                 prev_dir: int, new_dir: int) -> list[tuple[Slot, int]]:
    """The identity-multiplicity moves one edge's direction change implies:
    +1 on the slot the voter enters, −1 on the slot they leave (enter first,
    so a same-block clear-then-recast can never transiently read as absent).

    This is the whole of "which counters move" — both write paths below share
    it, and differ only in HOW they talk to Redis. Empty when nothing changed
    or the edge has no block.
    """
    from vote_store import dir_to_bit, UP, DOWN
    if new_dir == prev_dir or block_id < 0:
        return []
    moves: list[tuple[Slot, int]] = []
    if new_dir in (UP, DOWN):
        moves.append(((block_id, vt_id, dir_to_bit(new_dir)), 1))
    if prev_dir in (UP, DOWN):
        moves.append(((block_id, vt_id, dir_to_bit(prev_dir)), -1))
    return moves


# ── Write path (incremental, called inside the voter lock) ──────────────────

def apply_block_delta(
    redis_client, slug: str, mode: int, block_id: int, vt_id: int,
    new_dir: int, prev_dir: int, identity: str,
) -> None:
    """Move one voter across directions for one block, keeping the deduped
    aggregate exact. `identity` is the COUNTING identity (vote_identity.py), not
    the device that owns the row. Called once per *changed* edge (the caller
    resolves the edge's block via `edge_block_id`); blocks aggregate naturally
    because the aggregate only moves on the voter's 0↔1 presence boundary
    within the block.

    Multiplicity makes a coarse identity safe: when two devices behind one IP
    both vote a block, the shared field counts up to 2 and the block still reads
    1, and either of them retracting leaves the other's vote standing.

    new_dir/prev_dir use +1 (up), -1 (down), 0 (none). A no-op when unchanged or
    when the edge has no block (caller passes block_id < 0 → skip there).

    One move at a time, each HINCRBY awaited before the next decision — the
    straightforward reading of the rules, and the oracle the pipelined
    apply_block_deltas_batch is tested against (tests/unit/test_block_votes.py).
    Prefer the batch form for anything bigger than a single edge.
    """
    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    for slot, delta in _transitions(block_id, vt_id, prev_dir, new_dir):
        bdk = _slot_bd_key(slug, mode, slot)
        # HINCRBY returns the new value: ==1 on the way up means this voter
        # just became present, ==0 on the way down means they fully left.
        n = redis_client.hincrby(bdk, identity, delta)
        if delta > 0:
            if n == 1:
                pipe.hincrby(bagg, _slot_bagg_field(slot), 1)
        elif n <= 0:
            redis_client.hdel(bdk, identity)
            if n == 0:
                pipe.hincrby(bagg, _slot_bagg_field(slot), -1)
    pipe.execute()


def apply_block_deltas(
    redis_client, slug: str, mode: int, edge_block_id, changed_edges: list[int],
    vt_id: int, new_dir: int, prev_dirs: dict[int, int], identity: str,
) -> None:
    """Vectorized convenience: apply the block delta for every changed edge.

    `edge_block_id[edge]` → block_id (or <0 if unmapped). `prev_dirs[edge]` is the
    voter's prior direction on that edge. Safe to call with an empty/None mapping
    (no-op), so block state simply lies dormant for maps without block artifacts.
    """
    if edge_block_id is None:
        return
    n_edges = len(edge_block_id)
    for eid in changed_edges:
        if eid < 0 or eid >= n_edges:
            continue
        b = int(edge_block_id[eid])
        if b < 0:
            continue
        apply_block_delta(redis_client, slug, mode, b, vt_id,
                          new_dir, prev_dirs.get(eid, 0), identity)


def apply_block_deltas_batch(
    redis_client, slug: str, mode: int,
    block_ops: list[tuple[int, int, int]], vt_id: int, identity: str,
) -> None:
    """Apply a whole vote plan's block deltas in TWO round trips.

    `block_ops` is [(block_id, prev_dir, new_dir), …] in plan order — one entry
    per changed edge (multiplicity per edge is the point; do NOT dedupe blocks).
    Equivalent to apply_block_delta per edge, but pipelined: the loop version
    cost 1–3 unpipelined commands per edge (~150–400 round trips on a route
    vote — changelog/2026-07-08-agent-load-test.html finding #2).

    Phase A pipelines every bd: multiplicity HINCRBY in op order. Phase B
    derives from the returned multiplicities:
      · bagg moves on every presence-boundary crossing (+1 result == 1,
        −1 result == 0), same as the loop — crossings are real regardless of
        interleaving, and opposite crossings on one slot cancel via summing.
      · an identity field is deleted only when the LAST op touching it left
        it ≤ 0 — deferring HDEL per-op could wipe a field a later op in the
        same plan re-created (clear on edge A + cast on edge B, same block).

    Phase B's HDEL is NOT safe against a concurrent writer on the same field,
    and a coarse counting identity creates such writers where a per-device key
    had none: two devices behind one IP share a field, so an interleaved
    "A drives it to 0 / B raises it to 1 / A's deferred HDEL lands" loses B's
    vote from the hash while `bagg` keeps its +1 — and B's eventual unvote then
    decrements to −1, which is not the ==0 boundary, so the aggregate never
    comes back. The fix is upstream and total: app.py takes the vote lock on the
    COUNTING identity, so everyone who can touch a field is serialized on it.
    """
    # Phase A: every transition the plan implies, pipelined in op order.
    moves = [move for block_id, prev_dir, new_dir in block_ops
             for move in _transitions(block_id, vt_id, prev_dir, new_dir)]
    if not moves:
        return
    pipe = redis_client.pipeline()
    for slot, delta in moves:
        pipe.hincrby(_slot_bd_key(slug, mode, slot), identity, delta)
    results = pipe.execute()

    # Phase B: aggregate boundary crossings + final-state cleanup.
    bagg_moves: dict[Slot, int] = {}   # slot → net bagg move
    final_mult: dict[Slot, int] = {}   # slot → multiplicity after its last op
    for (slot, delta), n in zip(moves, results):
        if delta > 0 and n == 1:
            bagg_moves[slot] = bagg_moves.get(slot, 0) + 1
        elif delta < 0 and n == 0:
            bagg_moves[slot] = bagg_moves.get(slot, 0) - 1
        final_mult[slot] = n

    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    queued = False
    for slot, n in final_mult.items():
        if n <= 0:
            pipe.hdel(_slot_bd_key(slug, mode, slot), identity)
            queued = True
    for slot, move in bagg_moves.items():
        if move:
            pipe.hincrby(bagg, _slot_bagg_field(slot), move)
            queued = True
    if queued:
        pipe.execute()


# ── Read path ───────────────────────────────────────────────────────────────

def build_block_arrays(redis_client, slug: str, mode: int, n_blocks: int) -> dict:
    """Project the aggregate hash into per-block arrays sized to the block set.

    Returns the same shape as vote_store.build_arrays' edge fields, keyed by
    block_id:
      block_votes[b]      = up + down  (total deduped activity, for the heat
                            ramp — downvotes are engagement, so they read hot)
      block_vote_types[b] = [[legendIdx, up, down], …]  sorted by total desc
    """
    from vote_store import resolve_vote_type
    raw = redis_client.hgetall(bagg_key(slug, mode))

    up = [0] * n_blocks
    down = [0] * n_blocks
    # block_id → {vt_id: [up, down]}
    bvt: dict[int, dict[int, list[int]]] = {}
    for k, v in raw.items():
        count = int(v)
        if count == 0:
            continue
        block_id, vt_id, dbit = unpack_block_field(int(k))
        if block_id >= n_blocks:
            continue
        if dbit:
            down[block_id] += count
        else:
            up[block_id] += count
        if vt_id:
            d = bvt.setdefault(block_id, {})
            pair = d.setdefault(vt_id, [0, 0])
            pair[1 if dbit else 0] += count

    legend: list[str] = []
    li: dict[int, int] = {}

    def encode(vt_map):
        enc = []
        for vt_id, (u, dn) in sorted(vt_map.items(), key=lambda x: -(x[1][0] + x[1][1])):
            if vt_id not in li:
                li[vt_id] = len(legend)
                legend.append(resolve_vote_type(vt_id))
            enc.append([li[vt_id], u, dn])
        return enc

    block_votes = [up[i] + down[i] for i in range(n_blocks)]
    block_vote_types: list[list] = [[] for _ in range(n_blocks)]
    for b, vt_map in bvt.items():
        block_vote_types[b] = encode(vt_map)

    return {
        "block_votes": block_votes,
        "block_vote_types": block_vote_types,
        "block_vote_type_legend": legend,
        "n_blocks": n_blocks,
        # Sparse twin for the format=sparse body (private key, popped before
        # the dense body serializes) — voted blocks only, O(voted).
        "_bvt_sparse": {str(b): block_vote_types[b] for b in bvt},
    }


def read_block_vt_counts(
    redis_client, slug: str, mode: int, block_ids, vt_id: int,
) -> dict[int, list[int]]:
    """{block_id: [up, down]} for one vote type, read from the aggregate after a
    write — the authoritative deduped counts the delta broadcast SETs on clients
    (the block twin of vote_store.read_edge_vt_counts)."""
    block_ids = list(block_ids)
    if not block_ids:
        return {}
    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    for b in block_ids:
        pipe.hget(bagg, _slot_bagg_field((b, vt_id, 0)))
        pipe.hget(bagg, _slot_bagg_field((b, vt_id, 1)))
    vals = pipe.execute()
    return {
        b: [int(vals[2 * i] or 0), int(vals[2 * i + 1] or 0)]
        for i, b in enumerate(block_ids)
    }


# ── Rebuild (derived state recovery) ────────────────────────────────────────

def clear(redis_client, slug: str, mode: int) -> None:
    """Drop all block state for a map+mode (before a rebuild)."""
    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    pipe.delete(bagg)
    # bd:<slug>:<mode>:* — scan to avoid blocking on KEYS in prod
    pattern = f"bd:{slug}:{mode}:*"
    for key in redis_client.scan_iter(match=pattern, count=500):
        pipe.delete(key)
    pipe.execute()


def rebuild_from_db(
    redis_client, slug: str, mode: int, edge_block_id, rows,
) -> int:
    """Rebuild bd:/bagg: for a map+mode from canonical edge_votes rows.

    `rows` is an iterable of (edge_id, vote_type_id, direction, device_id,
    ip_hash) for the map (direction is +1/-1). BOTH identities are needed and
    they are not interchangeable: the multiplicity is keyed on the counting
    identity (what a block counts), while the §2.5 one-direction invariant is
    checked per DEVICE (what casting actually enforces — two people behind one
    NAT legitimately disagree, and that must not be logged as corruption).

    Idempotent: clears first, replays multiplicities, then derives the aggregate
    as HLEN of each identity hash. Returns block count touched. Called on cold
    Redis (hydration), after a graph resnap (edge_ids and thus edge_block_id
    changed), and after a counting-identity change (version_marker). See
    docs/vote-system-design.md §2.6.
    """
    from vote_store import dir_to_bit, UP, DOWN
    if edge_block_id is None:
        return 0
    clear(redis_client, slug, mode)
    n_edges = len(edge_block_id)
    touched: set[Slot] = set()
    # (block, vt, device) → dbit seen so far; 2 = both seen + already warned.
    # Guards the §2.5 invariant (a block never holds both directions from one
    # device+type) — canonical rows violating it are logged, not dropped.
    seen_dir: dict[tuple[int, int, str], int] = {}
    pipe = redis_client.pipeline()
    n = 0
    for edge_id, vt_id, direction, device_id, ip_hash in rows:
        if edge_id < 0 or edge_id >= n_edges:
            continue
        b = int(edge_block_id[edge_id])
        if b < 0:
            continue
        identity = vote_identity.counting_identity(device_id, ip_hash)
        d = direction if direction in (UP, DOWN) else UP
        dbit = dir_to_bit(d)
        key = (b, vt_id, device_id)
        prev_bit = seen_dir.get(key)
        if prev_bit is None:
            seen_dir[key] = dbit
        elif prev_bit != dbit and prev_bit != 2:
            logger.warning(
                f"[BLOCKVOTES] invariant violation in {slug}/{mode}: block {b} "
                f"vt {vt_id} device {device_id} holds BOTH directions")
            seen_dir[key] = 2
        slot = (b, vt_id, dbit)
        pipe.hincrby(_slot_bd_key(slug, mode, slot), identity, 1)
        touched.add(slot)
        n += 1
        if n % 5000 == 0:
            pipe.execute(); pipe = redis_client.pipeline()
    pipe.execute()

    # Derive the aggregate: deduped count == HLEN of each identity hash. Both
    # halves are pipelined — a rebuild touches every voted slot in the map, so
    # reading the lengths one at a time was one full round trip per slot.
    slots = list(touched)
    pipe = redis_client.pipeline()
    for slot in slots:
        pipe.hlen(_slot_bd_key(slug, mode, slot))
    hlens = pipe.execute()

    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    for slot, hlen in zip(slots, hlens):
        if hlen > 0:
            pipe.hset(bagg, _slot_bagg_field(slot), hlen)
    pipe.execute()
    logger.info(f"[BLOCKVOTES] rebuilt {slug}/{mode}: {n} edge-votes → "
                f"{len(slots)} (block,vt,dir) groups")
    return len(slots)
