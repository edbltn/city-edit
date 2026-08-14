# Referenced by: docs/algorithms/04-route-finding.md
#   (osm_nodes_to_edge_ids — how an OSRM route becomes votable edge ids).
"""
Redis vote cache using packed integer field keys.

This is purely the Redis cache representation — Postgres stores the canonical
votes as natural columns (see database.edge_votes: map_slug, edge_id,
vote_type_id, device_id, ip_hash, direction).

Redis field layout (53 bits, fits JS Number.MAX_SAFE_INTEGER):
  bits [23..0]   edge_id      (24 bits, up to 16M edges)
  bits [27..24]  mode         (4 bits — the map's mode; constant per map hash)
  bits [51..28]  vote_type_id (24 bits — the global SERIAL exceeds 16 bits)
  bit  [52]      direction    (0 = upvote, 1 = downvote)

Redis is namespaced per map: hash "ev:<slug>", channel "vote_deltas:<slug>",
revision "vote_rev:<slug>" — field = str(redis_field), value = count. Each map's
edge_id space is its own city graph, so isolating by slug avoids cross-map
collisions.
"""

import contextlib
import json
import logging
import os
import time

logger = logging.getLogger(__name__)


# ── Cross-instance voter lock ──────────────────────────────────────────────

@contextlib.contextmanager
def voter_lock(redis_client, slug: str, identity: str,
               timeout: float = 5.0, ttl: int = 10):
    """Serialize one voter's read-modify-write on a map ACROSS Flask instances.

    `identity` is whatever the caller counts as one voter — since 2026-08-13
    that is the COUNTING identity (vote_identity.py), not the device, because
    co-NAT devices now share a `bd:` field and must not interleave on it. The
    cost is that everyone behind one IP serializes here; the lock holds only a
    batched pipeline, and `VOTE_LOCK_SHED_SECONDS` bounds the wait.

    The app's per-process threading.Lock only orders votes within a single
    worker; run more than one worker/instance and two near-simultaneous votes
    from the same voter can each read the same prior direction and double-apply
    (inflating the count, or leaving a stuck toggle). A short Redis SET-NX lock
    keyed by (slug, identity) closes that race fleet-wide.

    Best effort: the lock auto-expires (ttl) so a crashed holder can't wedge a
    voter, and if Redis is unavailable we proceed anyway (the per-process lock
    still holds) — a vote must never hang on the limiter. Yields True when the
    cross-instance lock was actually held, False when we fell open."""
    key = f"votelock:{slug}:{identity}"
    token = os.urandom(8).hex()
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            if redis_client.set(key, token, nx=True, ex=ttl):
                acquired = True
                break
            if time.monotonic() >= deadline:
                break  # contended past the timeout — fall open, don't block
            time.sleep(0.02)  # gevent-patched: yields the hub
    except Exception as e:
        logger.warning(f"[VOTELOCK] acquire failed for {key}: {e}")
    try:
        yield acquired
    finally:
        if acquired:
            try:
                # Delete only if the value is still our token, so we never clear
                # a lock another instance acquired after ours expired.
                if redis_client.get(key) == token:
                    redis_client.delete(key)
            except Exception:
                pass


def hash_key(slug: str) -> str:
    return f"ev:{slug}"


def channel_key(slug: str) -> str:
    return f"vote_deltas:{slug}"


def revision_key(slug: str) -> str:
    return f"vote_rev:{slug}"


def delta_log_key(slug: str) -> str:
    return f"deltalog:{slug}"


# ── Recent-delta log (backs the WS sync packet) ────────────────────────────
# A backgrounded tab misses deltas. Before this, the only repair was a full
# /api/graph-votes refetch (~57 KB on nyc-bikes, whole-city, through the
# debounced snapshot cache). The log keeps the last few minutes of deltas so a
# foregrounding client can be repaired with only the cells that actually
# changed — usually well under a kilobyte.
#
# Shape: a Redis SORTED SET scored by revision, so a client that asks "what
# changed after rev N" costs one ZRANGEBYSCORE returning only the entries it
# needs, not a decode of the whole log. Members are base64 of the canonical
# binary frame (the app's Redis client runs decode_responses=True, so raw
# bytes can't ride in a value).
#
# Memory: the median cast on nyc-bikes is 89 edges ≈ 0.9 KB base64, so a full
# log is ~120 KB per map, ~2.5 MB across every map on the instance. Memorystore
# is 1 GB with allkeys-lru and evicting the vote hashes themselves is the
# failure that matters, so the log is bounded BOTH ways: SYNC_LOG_MAX entries
# and SYNC_LOG_TTL seconds. It is pure cache — losing it costs a client one
# full refetch, which is exactly today's behaviour.

SYNC_LOG_MAX = 128     # entries retained per map
SYNC_LOG_TTL = 900     # seconds; a tab backgrounded longer just refetches


def append_delta_log(redis_client, slug: str, rev: int, frame_b64: str) -> None:
    """Record one delta's encoded frame under its revision. Best effort — the
    log is a cache, and a vote must never fail because the log did."""
    try:
        key = delta_log_key(slug)
        pipe = redis_client.pipeline()
        pipe.zadd(key, {frame_b64: rev})
        pipe.zremrangebyrank(key, 0, -(SYNC_LOG_MAX + 1))
        pipe.expire(key, SYNC_LOG_TTL)
        pipe.execute()
    except Exception as e:
        logger.warning(f"[SYNCLOG] append failed for {slug}: {e}")


def read_delta_log(redis_client, slug: str, since: int) -> tuple[list, int | None]:
    """Return (entries newer than `since`, oldest revision still logged).

    `oldest` is None when the log is empty — the caller uses it to decide
    whether the log can actually cover the client's gap."""
    key = delta_log_key(slug)
    entries = redis_client.zrangebyscore(key, f"({since}", "+inf")
    head = redis_client.zrange(key, 0, 0, withscores=True)
    oldest = int(head[0][1]) if head else None
    return entries, oldest


def build_sync_frame(redis_client, slug: str, since: int) -> bytes:
    """Encode the SYNC frame that carries a client from rev `since` to head.

    Coalesced: one group per (mode, vote type), counts merged last-write-wins.
    That is lossless because the counts are absolute post-write values — the
    same argument DeltaHub already makes for coalescing its flush window.

    Sets the TRUNCATED flag whenever the log cannot PROVE it covers
    (since, head]. Every delta claims exactly one revision (INCR then publish),
    so full coverage means exactly head - since logged entries in that range.
    Anything less means a revision moved without publishing a delta — a
    hydration, a resnap, a manual `INCR vote_rev:<slug>` — and those change
    counts the log can't describe. The client answers TRUNCATED with the full
    /api/graph-votes refetch it does today, so the sync packet is strictly an
    improvement: it repairs the common case cheaply and degrades to the old
    path rather than to a wrong heatmap."""
    import base64

    import wire_codec

    head = int(redis_client.get(revision_key(slug)) or 0)
    # A client ahead of the server (its own optimistic cast raced this read)
    # has nothing to catch up on. Don't report that as a gap.
    if since >= head:
        return wire_codec.encode_frame(
            wire_codec.Frame(wire_codec.KIND_SYNC, head, head, []))

    entries, oldest = read_delta_log(redis_client, slug, since)
    expected = head - since
    truncated = (
        len(entries) != expected                       # a rev with no delta
        or oldest is None                              # log empty / evicted
        or oldest > since + 1                          # log starts too late
    )
    groups = []
    if not truncated:
        try:
            for e in entries:
                frame = wire_codec.decode_frame(base64.b64decode(e))
                groups.extend(frame.groups)
            groups = wire_codec.coalesce(groups)
        except Exception as ex:
            logger.warning(f"[SYNCLOG] undecodable entry for {slug}: {ex}")
            truncated, groups = True, []
    base = since if not truncated else head
    return wire_codec.encode_frame(
        wire_codec.Frame(wire_codec.KIND_SYNC, head, base, groups,
                         truncated=truncated))

# ── Mode enum ──────────────────────────────────────────────────────────────

MODE_IDS: dict[str, int] = {
    "bikepaths": 0,
    "trees": 1,
    "walkways": 2,
    "walk": 3,
}
MODE_NAMES: dict[int, str] = {v: k for k, v in MODE_IDS.items()}


def mode_to_int(name: str) -> int:
    return MODE_IDS.get(name, MODE_IDS["walk"])


def int_to_mode(i: int) -> str:
    return MODE_NAMES.get(i, "walk")


# ── Vote type cache ────────────────────────────────────────────────────────
# In-memory label↔id cache. All SQL lives in database.py; this just caches it.

_vt_label: dict[str, int] = {}
_vt_id: dict[int, str] = {}
# Ids with no vote_types row, so a genuinely orphaned id is looked up once per
# process instead of on every snapshot build. Cleared by load_vote_types().
_vt_missing: set[int] = set()


def load_vote_types() -> None:
    """Populate the cache from the database (called once at startup)."""
    global _vt_label, _vt_id
    import database
    rows = database.fetch_all_vote_types()
    _vt_label = {label: vid for vid, label in rows}
    _vt_id = {vid: label for vid, label in rows}
    _vt_missing.clear()
    logger.info(f"[VOTES] Loaded {len(rows)} vote types into cache")


def get_vote_type_id(label: str, point_type: str | None = None) -> int:
    """Return the id for a label, creating + caching it on first use.

    `point_type` ('route' | 'point') records the kind when the label is NEW —
    the creating cast is the only witness of whether a free-text suggestion was
    made on a route or a point. Cached/existing labels keep their stored kind
    (get_or_create only fills NULL)."""
    if not label:
        return 0
    cached = _vt_label.get(label)
    if cached is not None:
        return cached
    import database
    vid = database.get_or_create_vote_type_id(label, point_type)
    if vid:
        _vt_label[label] = vid
        _vt_id[vid] = label
    return vid


def resolve_vote_type(vid: int) -> str:
    """id → label for the heatmap legend. "#<id>" only when the id has no row.

    The cache is loaded at startup, so an id this process has never seen is
    normally NOT an orphan: another instance minted the label on a free-text
    cast, or an admin retyped votes onto a label created out-of-band
    (retype_votes.py). Rendering those as "#43675" until the next restart is a
    display bug with a one-row fix, so miss → read through to the database and
    remember it. Bounded: callers resolve each DISTINCT id once per snapshot
    build, and a real orphan is recorded in _vt_missing so it is asked about
    once."""
    if vid == 0:
        return ""
    label = _vt_id.get(vid)
    if label is not None:
        return label
    if vid not in _vt_missing:
        import database
        label = database.fetch_vote_type_label(vid)
        if label:
            _vt_id[vid] = label
            _vt_label.setdefault(label, vid)
            return label
        _vt_missing.add(vid)
    return f"#{vid}"


def all_vote_types() -> dict[int, str]:
    return dict(_vt_id)


# ── Bit packing ────────────────────────────────────────────────────────────
# Layout (53 bits — the full Number.MAX_SAFE_INTEGER budget; the client codec
# in client-react/src/utils/voteKey.ts mirrors it bit-for-bit):
#
#   bit  [52]      direction    (0 = up, 1 = down) — only on Redis count fields
#   bits [51..28]  vote_type_id (24 bits, <= ~16.7M)
#   bits [27..24]  mode         (4 bits)
#   bits [23..0]   edge_id      (24 bits)
#
# vt was 16 bits (direction at bit 44) until 2026-07: vote_types is a global
# SERIAL that bulk imports pushed past 65535, and the overflowing id bit landed
# exactly on the direction bit — silently recording every such vote as a
# downvote. Widening moves the down fields, so ev:/bagg: hashes must be flushed
# and rehydrated from Postgres when this ships (up fields and client
# localStorage keys with vt < 65536 are byte-identical, no client migration).

DIR_BIT = 52  # direction bit position in the Redis field key

EDGE_BITS = 24
VT_BITS = 24

# The NARROWEST real ceiling on a vote_type_id anywhere in the system, and the
# number the creation guard enforces (database.get_or_create_vote_type_id).
#
# Audited 2026-08-13, every path a vt id travels:
#   Redis vote field   pack()                  vt at bits 51..28  -> 24 bits  ← narrowest
#   Block aggregate    block_votes.pack_block_field  vt at 47..24 -> 24 bits  ← narrowest
#   Client store key   client-react/src/utils/voteKey.ts VT_BITS  -> 24 bits  ← narrowest
#   WS binary frame    wire_codec vtId varint                     -> 2^53-1
#   Sparse HTTP body   {str(id): label} JSON                      -> unbounded
#   Postgres           vote_types.id is int4 SERIAL               -> 2147483647
#
# So Postgres can mint ids 128x larger than the three packed layouts can carry,
# which is exactly the shape of the July bug: vt was 16 bits, a bulk import
# pushed the SERIAL past 65535, and the overflow bit landed on the direction bit
# at 52 — recording those votes as DOWNVOTES, silently. Widening to 24 moved the
# cliff; it did not remove it. At vt = 2^24 the Redis field's overflow lands on
# bit 52 again, and the block field's on ITS direction bit at 48.
#
# Hence: refuse the id at CREATION, where a human is present and can be told,
# rather than at pack time on a vote that has already been accepted. The packers
# keep their own ValueError as the last line of defence.
MAX_VOTE_TYPE_ID = (1 << VT_BITS) - 1  # 16,777,215


class VoteTypeLimitError(Exception):
    """A new vote type cannot be created: the id space the packed vote-key
    layouts can address is exhausted. Carries a user-facing message."""

# Vote directions (API/app layer): +1 = up, -1 = down, 0 = no vote
UP = 1
DOWN = -1


def pack(edge_id: int, mode: int, vt_id: int) -> int:
    """Direction-less proposal key — the base of the Redis field (no direction bit)."""
    if not 0 <= edge_id < (1 << EDGE_BITS):
        raise ValueError(f"edge_id {edge_id} exceeds {EDGE_BITS}-bit field")
    if not 0 <= vt_id < (1 << VT_BITS):
        raise ValueError(f"vote_type_id {vt_id} exceeds {VT_BITS}-bit field")
    return (vt_id << 28) | (mode << 24) | edge_id


def dir_to_bit(direction: int) -> int:
    """Map a +1/-1 direction to the Redis field's direction bit (0=up, 1=down)."""
    return 0 if direction >= 0 else 1


def redis_field(edge_id: int, mode: int, vt_id: int, direction: int) -> int:
    """Redis hash field key — proposal key plus the direction bit."""
    base = pack(edge_id, mode, vt_id)
    return base | (1 << DIR_BIT) if dir_to_bit(direction) else base


def unpack(key: int) -> tuple[int, int, int, int]:
    """Return (edge_id, mode, vt_id, dir_bit) for a Redis field key."""
    return (
        key & 0xFF_FFFF,
        (key >> 24) & 0xF,
        (key >> 28) & 0xFF_FFFF,
        (key >> DIR_BIT) & 0x1,
    )


# ── Write path ─────────────────────────────────────────────────────────────

def apply_directional(
    redis_client, slug: str, edge_id: int, mode: int, vt_id: int,
    new_dir: int, prev_dir: int,
) -> None:
    """Apply a single proposal vote, moving this voter across directions.

    new_dir / prev_dir use +1 (up), -1 (down), 0 (none/removed). Increments the
    new direction's field (only when it's an actual vote, not removal) and
    decrements the prior direction's field when there was one. Handles every
    transition: fresh (0→±1), reversal (±1→∓1), and removal (±1→0). A no-op when
    the direction is unchanged.
    """
    if new_dir == prev_dir:
        return
    h = hash_key(slug)
    pipe = redis_client.pipeline()
    if new_dir in (UP, DOWN):
        pipe.hincrby(h, str(redis_field(edge_id, mode, vt_id, new_dir)), 1)
    if prev_dir in (UP, DOWN):
        pipe.hincrby(h, str(redis_field(edge_id, mode, vt_id, prev_dir)), -1)
    pipe.execute()


def apply_directional_batch(
    redis_client, slug: str, ops: list[tuple[int, int, int]], mode: int, vt_id: int,
) -> None:
    """Apply a whole vote plan's edge-counter transitions in ONE round trip.

    `ops` is [(edge_id, prev_dir, new_dir), …] in plan order (clears then
    casts). Semantically identical to calling apply_directional per edge — the
    same HINCRBYs in the same order — but a 145-edge route vote costs one
    pipeline execute instead of ~145 (the measured bulk of the vote critical
    section; changelog/2026-07-08-agent-load-test.html finding #2).
    """
    h = hash_key(slug)
    pipe = redis_client.pipeline()
    queued = False
    for edge_id, prev_dir, new_dir in ops:
        if new_dir == prev_dir:
            continue
        if new_dir in (UP, DOWN):
            pipe.hincrby(h, str(redis_field(edge_id, mode, vt_id, new_dir)), 1)
            queued = True
        if prev_dir in (UP, DOWN):
            pipe.hincrby(h, str(redis_field(edge_id, mode, vt_id, prev_dir)), -1)
            queued = True
    if queued:
        pipe.execute()


def publish_delta(
    redis_client, slug: str, edge_ids: list[int], mode: int, vt_id: int,
    direction: int = UP, reversed_vote: bool = False,
    vt_counts: dict[int, list[int]] | None = None,
    block_counts: dict[int, list[int]] | None = None,
) -> int:
    rev = redis_client.incr(revision_key(slug))
    delta: dict = {
        "type": "delta",
        "rev": rev,
        "edges": edge_ids,
        "m": int_to_mode(mode),
        "vt": vt_id,
        "dir": 1 if direction >= 0 else -1,
        "reversed": reversed_vote,
    }
    if vt_id > 0:
        delta["vtLabel"] = resolve_vote_type(vt_id)
    # Authoritative post-write [up, down] for this vote type on each changed
    # edge. When present, clients SET these counts (idempotent) instead of
    # incrementing — so a caster's optimistic guess can't drift or double-count.
    if vt_counts is not None:
        delta["vtCounts"] = {str(eid): counts for eid, counts in vt_counts.items()}
    # Authoritative post-write deduped [up, down] per affected BLOCK (same SET
    # semantics as vtCounts) so open modals/heat track block counts without a
    # full /api/graph-votes refetch.
    if block_counts is not None:
        delta["blockCounts"] = {str(b): counts for b, counts in block_counts.items()}
    redis_client.publish(channel_key(slug), json.dumps(delta))
    # Record the same delta in the sync log, in the SAME call that publishes
    # it, so the log can never drift from the stream it is meant to replay.
    # Best effort: encoding or Redis trouble must not fail a vote.
    if vt_counts:
        try:
            import base64
            import wire_codec
            group = wire_codec.group_from_delta(delta, mode)
            frame = wire_codec.Frame(wire_codec.KIND_DELTA, rev, rev - 1, [group])
            append_delta_log(
                redis_client, slug,
                rev, base64.b64encode(wire_codec.encode_frame(frame)).decode("ascii"))
        except Exception as e:
            logger.warning(f"[SYNCLOG] could not log delta rev {rev} for {slug}: {e}")
    return rev


# ── Read path ──────────────────────────────────────────────────────────────

def read_edge_vt_counts(
    redis_client, slug: str, edge_ids: list[int], mode: int, vt_id: int,
) -> dict[int, list[int]]:
    """Return {edge_id: [up, down]} for one vote type on the given edges,
    read straight from Redis after a write (the authoritative aggregate)."""
    h = hash_key(slug)
    pipe = redis_client.pipeline()
    for eid in edge_ids:
        pipe.hget(h, str(redis_field(eid, mode, vt_id, UP)))
        pipe.hget(h, str(redis_field(eid, mode, vt_id, DOWN)))
    vals = pipe.execute()
    out: dict[int, list[int]] = {}
    for i, eid in enumerate(edge_ids):
        out[eid] = [int(vals[2 * i] or 0), int(vals[2 * i + 1] or 0)]
    return out

def read_all(redis_client, slug: str) -> dict[int, int]:
    raw = redis_client.hgetall(hash_key(slug))
    return {int(k): int(v) for k, v in raw.items()}


_EMPTY: list = []  # shared per-edge/node placeholder — never mutated, only replaced


def build_arrays(
    votes: dict[int, int],
    edge_count: int,
    node_count: int,
    edge_ends,  # np.ndarray [e, 2] int32 — edge id -> (node_u, node_v)
    mode_filter: int | None = None,
) -> dict:
    """Unpack votes into per-edge and derived per-node arrays.

    `edge_votes`/`node_votes` are NET counts (up − down) — the heatmap value
    (clamped to ≥ 0 client-side). Per-type breakdowns are encoded as
    [legendIdx, up, down] so modals can show "net (−down, +up)".

    Runs on the request path (debounced snapshot rebuild), so it must be
    sparse: work scales with the number of VOTED fields, never with graph
    size. The old pure-Python version looped every edge and every node
    (~4.6M iterations for NYC) per rebuild, stalling the gevent loop for
    seconds and head-of-line-blocking every other request under load.
    """
    import numpy as np

    edge_up = np.zeros(edge_count, dtype=np.int64)
    edge_down = np.zeros(edge_count, dtype=np.int64)
    # edge_id → {vt_id: [up, down]} — sparse, voted edges only
    edge_vt: dict[int, dict[int, list[int]]] = {}

    if votes:
        packed = np.fromiter(votes.keys(), dtype=np.int64, count=len(votes))
        counts = np.fromiter(votes.values(), dtype=np.int64, count=len(votes))
        eid = packed & 0xFFFFFF
        mode = (packed >> 24) & 0xF
        vtid = (packed >> 28) & 0xFFFFFF
        dbit = (packed >> 52) & 1
        keep = eid < edge_count
        if mode_filter is not None:
            keep &= mode == mode_filter
        eid, vtid, dbit, counts = eid[keep], vtid[keep], dbit[keep], counts[keep]

        up_rows = dbit == 0
        np.add.at(edge_up, eid[up_rows], counts[up_rows])
        np.add.at(edge_down, eid[~up_rows], counts[~up_rows])

        # typed breakdown: only rows carrying a vote type (small)
        for e, vt, d, c in zip(eid[vtid != 0].tolist(), vtid[vtid != 0].tolist(),
                               dbit[vtid != 0].tolist(), counts[vtid != 0].tolist()):
            pair = edge_vt.setdefault(e, {}).setdefault(vt, [0, 0])
            pair[1 if d else 0] += c

    edge_totals = edge_up - edge_down

    # Build legend + per-edge vote types — sorted by net descending
    legend: list[str] = []
    li: dict[int, int] = {}

    def encode(vt_map: dict[int, list[int]]) -> list:
        pairs = sorted(vt_map.items(), key=lambda x: -(x[1][0] - x[1][1]))
        enc = []
        for vt, (up, down) in pairs:
            if vt not in li:
                li[vt] = len(legend)
                legend.append(resolve_vote_type(vt))
            enc.append([li[vt], int(up), int(down)])
        return enc

    edge_vote_types: list[list] = [_EMPTY] * edge_count
    for e in sorted(edge_vt):
        edge_vote_types[e] = encode(edge_vt[e])

    # Derive node votes from edges: a node shows the max positive net among its
    # incident edges. Sparse form: only edges with a positive net can move a
    # node above 0, so scatter-max from those edges' endpoints.
    node_totals = np.zeros(node_count, dtype=np.int64)
    pos = np.nonzero(edge_totals > 0)[0]
    if len(pos) and edge_ends is not None and len(edge_ends):
        np.maximum.at(node_totals, edge_ends[pos, 0], edge_totals[pos])
        np.maximum.at(node_totals, edge_ends[pos, 1], edge_totals[pos])

    # Per-node type merge: for each type, the pair from the incident voted edge
    # with the larger net (ties → lowest edge id, deterministic).
    node_vt_merged: dict[int, dict[int, list[int]]] = {}
    if edge_ends is not None and len(edge_ends):
        for e in sorted(edge_vt):
            vt_map = edge_vt[e]
            for nid in (int(edge_ends[e, 0]), int(edge_ends[e, 1])):
                merged = node_vt_merged.setdefault(nid, {})
                for vt, pair in vt_map.items():
                    cur = merged.get(vt)
                    if cur is None or (pair[0] - pair[1]) > (cur[0] - cur[1]):
                        merged[vt] = [pair[0], pair[1]]

    node_vote_types: list[list] = [_EMPTY] * node_count
    for nid, vt_map in node_vt_merged.items():
        node_vote_types[nid] = encode(vt_map)

    return {
        "edge_votes": edge_totals,
        "node_votes": node_totals,
        "vote_type_legend": legend,
        "edge_vote_types": edge_vote_types,
        "node_vote_types": node_vote_types,
        # Sparse twins for the format=sparse body (keys are private — popped
        # before the dense body serializes). Built here, from the voted-only
        # dicts we already hold, so deriving them stays O(voted), never a
        # 4.6M-slot scan of the dense lists on the request path. A dense list
        # can hold a non-empty breakdown where the NET is 0 (counter-votes
        # cancel), so the keys come from the vt dicts, not from nonzero totals.
        "_evt_sparse": {str(e): edge_vote_types[e] for e in edge_vt},
        "_nvt_sparse": {str(n): node_vote_types[n] for n in node_vt_merged},
    }


# ── OSM node → edge ID mapping ────────────────────────────────────────────

def osm_nodes_to_edge_ids(
    osm_node_ids: list[int],
    osm_to_graph_idx: dict[int, int],
    node_pair_to_edge: dict[tuple[int, int], int],
) -> list[int]:
    """Map a sequence of OSM node IDs (from OSRM annotations) to graph edge IDs."""
    seen: set[int] = set()
    result: list[int] = []
    prev_graph_idx: int | None = None
    for osm_id in osm_node_ids:
        graph_idx = osm_to_graph_idx.get(osm_id)
        if graph_idx is not None and prev_graph_idx is not None and graph_idx != prev_graph_idx:
            eid = node_pair_to_edge.get((prev_graph_idx, graph_idx))
            if eid is not None and eid not in seen:
                seen.add(eid)
                result.append(eid)
        if graph_idx is not None:
            prev_graph_idx = graph_idx
    return result
