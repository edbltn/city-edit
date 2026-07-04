"""
Block-level vote display — a deduplicated projection of edge votes onto blocks.

Votes are stored per graph **edge** (see vote_store.py / database.edge_votes,
unchanged). For *display* we aggregate to **blocks** (one polygon per street
segment) and deduplicate so that **each (block, vote_type, direction, device)
counts at most once** — even when a single route laid a user's vote across many
edges inside one block.

Edge counts in Redis (`ev:<slug>`) have already discarded device identity, so we
cannot dedupe users from them. Instead we maintain a small, **incremental,
Redis-native** structure keyed by device, updated inside the same vote lock as
the edge write. This keeps serving block votes as fast as the edge heatmap, at
the cost of being derived state that must be rebuilt from Postgres on cold-start
or graph resnap (see `rebuild_from_db`). Full rationale + invariants:
docs/vote-system-design.md §2.

Redis keys (namespaced per map slug + mode):
  bd:<slug>:<mode>:<block>:<vt>:<dir>   HASH  field=device_id  value=edge-multiplicity
                                        → HLEN == distinct devices == deduped count
  bagg:<slug>:<mode>                    HASH  field=packed(block,vt,dir)  value=deduped count
                                        → one HGETALL serves the whole block layer

`dir` here is the 0/1 direction bit (0=up, 1=down), matching vote_store.dir_to_bit.
"""

import logging

logger = logging.getLogger(__name__)

# ── Key helpers ─────────────────────────────────────────────────────────────

def bd_key(slug: str, mode: int, block_id: int, vt_id: int, dbit: int) -> str:
    """Per-(block,vote_type,direction) device-multiplicity hash. HLEN = deduped count."""
    return f"bd:{slug}:{mode}:{block_id}:{vt_id}:{dbit}"


def bagg_key(slug: str, mode: int) -> str:
    """Per-(slug,mode) aggregate hash: packed(block,vt,dir) → deduped count."""
    return f"bagg:{slug}:{mode}"


# packed aggregate field: block_id [0..23] | vt_id [24..39] | dir [40]
def pack_block_field(block_id: int, vt_id: int, dbit: int) -> int:
    return (dbit << 40) | (vt_id << 24) | block_id


def unpack_block_field(key: int) -> tuple[int, int, int]:
    return (key & 0xFF_FFFF, (key >> 24) & 0xFFFF, (key >> 40) & 0x1)


# ── Write path (incremental, called inside the voter lock) ──────────────────

def apply_block_delta(
    redis_client, slug: str, mode: int, block_id: int, vt_id: int,
    new_dir: int, prev_dir: int, device_id: str,
) -> None:
    """Move one device across directions for one block, keeping the deduped
    aggregate exact. Called once per *changed* edge (the caller resolves the
    edge's block via `edge_block_id`); blocks aggregate naturally because the
    aggregate only moves on the device's 0↔1 presence boundary within the block.

    new_dir/prev_dir use +1 (up), -1 (down), 0 (none). A no-op when unchanged or
    when the edge has no block (caller passes block_id < 0 → skip there).
    """
    from vote_store import dir_to_bit, UP, DOWN
    if new_dir == prev_dir or block_id < 0:
        return
    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()

    if new_dir in (UP, DOWN):
        db = dir_to_bit(new_dir)
        # HINCRBY returns the new value; ==1 means this device just became present
        n = redis_client.hincrby(bd_key(slug, mode, block_id, vt_id, db), device_id, 1)
        if n == 1:
            pipe.hincrby(bagg, str(pack_block_field(block_id, vt_id, db)), 1)
    if prev_dir in (UP, DOWN):
        db = dir_to_bit(prev_dir)
        bdk = bd_key(slug, mode, block_id, vt_id, db)
        n = redis_client.hincrby(bdk, device_id, -1)
        if n <= 0:
            redis_client.hdel(bdk, device_id)
            if n == 0:  # device fully left this block for (vt,dir)
                pipe.hincrby(bagg, str(pack_block_field(block_id, vt_id, db)), -1)
    pipe.execute()


def apply_block_deltas(
    redis_client, slug: str, mode: int, edge_block_id, changed_edges: list[int],
    vt_id: int, new_dir: int, prev_dirs: dict[int, int], device_id: str,
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
                          new_dir, prev_dirs.get(eid, 0), device_id)


# ── Read path ───────────────────────────────────────────────────────────────

def build_block_arrays(redis_client, slug: str, mode: int, n_blocks: int) -> dict:
    """Project the aggregate hash into per-block arrays sized to the block set.

    Returns the same shape as vote_store.build_arrays' edge fields, keyed by
    block_id:
      block_votes[b]      = up − down  (net deduped, for the heat ramp)
      block_vote_types[b] = [[legendIdx, up, down], …]  sorted by net desc
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
        for vt_id, (u, dn) in sorted(vt_map.items(), key=lambda x: -(x[1][0] - x[1][1])):
            if vt_id not in li:
                li[vt_id] = len(legend)
                legend.append(resolve_vote_type(vt_id))
            enc.append([li[vt_id], u, dn])
        return enc

    block_votes = [up[i] - down[i] for i in range(n_blocks)]
    block_vote_types: list[list] = [[] for _ in range(n_blocks)]
    for b, vt_map in bvt.items():
        block_vote_types[b] = encode(vt_map)

    return {
        "block_votes": block_votes,
        "block_vote_types": block_vote_types,
        "block_vote_type_legend": legend,
        "n_blocks": n_blocks,
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

    `rows` is an iterable of (edge_id, vote_type_id, direction, device_id) for the
    map (direction is +1/-1). Idempotent: clears first, replays multiplicities,
    then derives the aggregate as HLEN of each device hash. Returns block count
    touched. Called on cold Redis (hydration) and after a graph resnap (edge_ids
    and thus edge_block_id changed). See docs/vote-system-design.md §2.6.
    """
    from vote_store import dir_to_bit, UP, DOWN
    if edge_block_id is None:
        return 0
    clear(redis_client, slug, mode)
    n_edges = len(edge_block_id)
    touched_keys: set[str] = set()
    # (block, vt, device) → dbit seen so far; 2 = both seen + already warned.
    # Guards the §2.5 invariant (a block never holds both directions from one
    # device+type) — canonical rows violating it are logged, not dropped.
    seen_dir: dict[tuple[int, int, str], int] = {}
    pipe = redis_client.pipeline()
    n = 0
    for edge_id, vt_id, direction, device_id in rows:
        if edge_id < 0 or edge_id >= n_edges:
            continue
        b = int(edge_block_id[edge_id])
        if b < 0:
            continue
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
        bdk = bd_key(slug, mode, b, vt_id, dbit)
        pipe.hincrby(bdk, device_id, 1)
        touched_keys.add(bdk)
        n += 1
        if n % 5000 == 0:
            pipe.execute(); pipe = redis_client.pipeline()
    pipe.execute()

    # Derive the aggregate: deduped count == HLEN of each device hash.
    bagg = bagg_key(slug, mode)
    pipe = redis_client.pipeline()
    for bdk in touched_keys:
        # bd:<slug>:<mode>:<block>:<vt>:<dir>
        _, _, _, block_s, vt_s, dir_s = bdk.split(":")
        hlen = redis_client.hlen(bdk)
        if hlen > 0:
            field = pack_block_field(int(block_s), int(vt_s), int(dir_s))
            pipe.hset(bagg, str(field), hlen)
    pipe.execute()
    logger.info(f"[BLOCKVOTES] rebuilt {slug}/{mode}: {n} edge-votes → "
                f"{len(touched_keys)} (block,vt,dir) groups")
    return len(touched_keys)
