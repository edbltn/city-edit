"""
Redis vote cache using packed integer field keys.

This is purely the Redis cache representation — Postgres stores the canonical
votes as natural columns (see database.edge_votes: map_slug, edge_id,
vote_type_id, device_id, ip_hash, direction).

Redis field layout (45 bits, fits JS Number.MAX_SAFE_INTEGER):
  bits [23..0]   edge_id      (24 bits, up to 16M edges)
  bits [27..24]  mode         (4 bits — the map's mode; constant per map hash)
  bits [43..28]  vote_type_id (16 bits, up to 65K vote types)
  bit  [44]      direction    (0 = upvote, 1 = downvote)

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
def voter_lock(redis_client, slug: str, device_id: str,
               timeout: float = 5.0, ttl: int = 10):
    """Serialize one voter's read-modify-write on a map ACROSS Flask instances.

    The app's per-process threading.Lock only orders votes within a single
    worker; run more than one worker/instance and two near-simultaneous votes
    from the same voter can each read the same prior direction and double-apply
    (inflating the count, or leaving a stuck toggle). A short Redis SET-NX lock
    keyed by (slug, device_id) closes that race fleet-wide.

    Best effort: the lock auto-expires (ttl) so a crashed holder can't wedge a
    voter, and if Redis is unavailable we proceed anyway (the per-process lock
    still holds) — a vote must never hang on the limiter. Yields True when the
    cross-instance lock was actually held, False when we fell open."""
    key = f"votelock:{slug}:{device_id}"
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


def load_vote_types() -> None:
    """Populate the cache from the database (called once at startup)."""
    global _vt_label, _vt_id
    import database
    rows = database.fetch_all_vote_types()
    _vt_label = {label: vid for vid, label in rows}
    _vt_id = {vid: label for vid, label in rows}
    logger.info(f"[VOTES] Loaded {len(rows)} vote types into cache")


def get_vote_type_id(label: str) -> int:
    """Return the id for a label, creating + caching it on first use."""
    if not label:
        return 0
    cached = _vt_label.get(label)
    if cached is not None:
        return cached
    import database
    vid = database.get_or_create_vote_type_id(label)
    if vid:
        _vt_label[label] = vid
        _vt_id[vid] = label
    return vid


def resolve_vote_type(vid: int) -> str:
    if vid == 0:
        return ""
    return _vt_id.get(vid, f"#{vid}")


def all_vote_types() -> dict[int, str]:
    return dict(_vt_id)


# ── Bit packing ────────────────────────────────────────────────────────────

DIR_BIT = 44  # direction bit position in the Redis field key

# Vote directions (API/app layer): +1 = up, -1 = down, 0 = no vote
UP = 1
DOWN = -1


def pack(edge_id: int, mode: int, vt_id: int) -> int:
    """Direction-less proposal key — the base of the Redis field (no direction bit)."""
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
        (key >> 28) & 0xFFFF,
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


def publish_delta(
    redis_client, slug: str, edge_ids: list[int], mode: int, vt_id: int,
    direction: int = UP, reversed_vote: bool = False,
    vt_counts: dict[int, list[int]] | None = None,
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
    redis_client.publish(channel_key(slug), json.dumps(delta))
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


def build_arrays(
    votes: dict[int, int],
    edge_count: int,
    node_count: int,
    node_adj: list[list[int]],
    mode_filter: int | None = None,
) -> dict:
    """Unpack votes into per-edge and derived per-node arrays.

    `edge_votes`/`node_votes` are NET counts (up − down) — the heatmap value
    (clamped to ≥ 0 client-side). Per-type breakdowns are encoded as
    [legendIdx, up, down] so modals can show "net (−down, +up)".
    """
    edge_up = [0] * edge_count
    edge_down = [0] * edge_count
    # edge_id → {vt_id: [up, down]}
    edge_vt: dict[int, dict[int, list[int]]] = {}

    for packed, count in votes.items():
        eid, m, vtid, dbit = unpack(packed)
        if eid >= edge_count:
            continue
        if mode_filter is not None and m != mode_filter:
            continue
        if dbit:
            edge_down[eid] += count
        else:
            edge_up[eid] += count
        if vtid:
            evd = edge_vt.get(eid)
            if evd is None:
                evd = {}
                edge_vt[eid] = evd
            pair = evd.get(vtid)
            if pair is None:
                pair = [0, 0]
                evd[vtid] = pair
            pair[1 if dbit else 0] += count

    edge_totals = [edge_up[i] - edge_down[i] for i in range(edge_count)]

    # Build legend + per-edge vote types — sorted by net descending
    legend: list[str] = []
    li: dict[int, int] = {}

    def encode(vt_map: dict[int, list[int]]) -> list:
        pairs = sorted(vt_map.items(), key=lambda x: -(x[1][0] - x[1][1]))
        enc = []
        for vtid, (up, down) in pairs:
            if vtid not in li:
                li[vtid] = len(legend)
                legend.append(resolve_vote_type(vtid))
            enc.append([li[vtid], up, down])
        return enc

    edge_vote_types: list[list] = [[] for _ in range(edge_count)]
    for eid, vt_map in edge_vt.items():
        edge_vote_types[eid] = encode(vt_map)

    # Derive node votes from edges (max net of adjacent edges; per-type pair
    # taken from the adjacent edge with the larger net for that type).
    node_totals = [0] * node_count
    node_vt_merged: dict[int, dict[int, list[int]]] = {}
    for nid in range(node_count):
        adj = node_adj[nid]
        if not adj:
            continue
        best = 0
        merged: dict[int, list[int]] | None = None
        for eid in adj:
            v = edge_totals[eid]
            if v > best:
                best = v
            evd = edge_vt.get(eid)
            if evd:
                if merged is None:
                    merged = {}
                for vtid, pair in evd.items():
                    cur = merged.get(vtid)
                    if cur is None or (pair[0] - pair[1]) > (cur[0] - cur[1]):
                        merged[vtid] = [pair[0], pair[1]]
        node_totals[nid] = best
        if merged:
            node_vt_merged[nid] = merged

    node_vote_types: list[list] = [[] for _ in range(node_count)]
    for nid, vt_map in node_vt_merged.items():
        node_vote_types[nid] = encode(vt_map)

    return {
        "edge_votes": edge_totals,
        "node_votes": node_totals,
        "vote_type_legend": legend,
        "edge_vote_types": edge_vote_types,
        "node_vote_types": node_vote_types,
    }


# ── Coordinate → edge ID mapping ──────────────────────────────────────────

def coords_to_edge_ids(segments: list, coord_to_edge: dict) -> list[int]:
    """Map route segments (coordinate pairs) to unique edge IDs."""
    seen: set[int] = set()
    result: list[int] = []
    for seg in segments:
        if len(seg) < 2:
            continue
        c1 = f"{round(seg[0][0], 5)},{round(seg[0][1], 5)}"
        c2 = f"{round(seg[1][0], 5)},{round(seg[1][1], 5)}"
        for eid in coord_to_edge.get((c1, c2), ()):
            if eid not in seen:
                seen.add(eid)
                result.append(eid)
    return result


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
