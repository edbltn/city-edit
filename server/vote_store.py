"""
Vote storage using packed integer keys.

Composite key layout (44 bits, fits JS Number.MAX_SAFE_INTEGER):
  bits [23..0]   edge_id      (24 bits, up to 16M edges)
  bits [27..24]  mode         (4 bits, up to 16 modes)
  bits [43..28]  vote_type_id (16 bits, up to 65K vote types)

Redis: single hash "ev" — field = str(packed_key), value = count.
"""

import json
import logging

logger = logging.getLogger(__name__)

REDIS_HASH = "ev"
REDIS_CHANNEL = "vote_deltas"
REVISION_KEY = "vote_rev"

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

_vt_label: dict[str, int] = {}
_vt_id: dict[int, str] = {}


def load_vote_types(cursor) -> None:
    global _vt_label, _vt_id
    cursor.execute("SELECT id, label FROM vote_types ORDER BY id")
    rows = cursor.fetchall()
    _vt_label = {r[1]: r[0] for r in rows}
    _vt_id = {r[0]: r[1] for r in rows}
    logger.info(f"[VOTES] Loaded {len(rows)} vote types into cache")


def get_vote_type_id(label: str, cursor) -> int:
    if not label:
        return 0
    cached = _vt_label.get(label)
    if cached is not None:
        return cached
    cursor.execute(
        "INSERT INTO vote_types (label) VALUES (%s) "
        "ON CONFLICT (label) DO UPDATE SET label = EXCLUDED.label "
        "RETURNING id",
        (label,),
    )
    vid = cursor.fetchone()[0]
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

def pack(edge_id: int, mode: int, vt_id: int) -> int:
    return (vt_id << 28) | (mode << 24) | edge_id


def unpack(key: int) -> tuple[int, int, int]:
    return key & 0xFF_FFFF, (key >> 24) & 0xF, key >> 28


# ── Write path ─────────────────────────────────────────────────────────────

def cast(redis_client, edge_ids: list[int], mode: int, vt_id: int) -> int:
    if not edge_ids:
        return 0
    pipe = redis_client.pipeline()
    for eid in edge_ids:
        pipe.hincrby(REDIS_HASH, str(pack(eid, mode, vt_id)), 1)
    pipe.execute()
    return len(edge_ids)


def publish_delta(redis_client, edge_ids: list[int], mode: int, vt_id: int) -> int:
    rev = redis_client.incr(REVISION_KEY)
    delta: dict = {
        "type": "delta",
        "rev": rev,
        "edges": edge_ids,
        "m": int_to_mode(mode),
        "vt": vt_id,
    }
    if vt_id > 0:
        delta["vtLabel"] = resolve_vote_type(vt_id)
    redis_client.publish(REDIS_CHANNEL, json.dumps(delta))
    return rev


# ── Read path ──────────────────────────────────────────────────────────────

def read_all(redis_client) -> dict[int, int]:
    raw = redis_client.hgetall(REDIS_HASH)
    return {int(k): int(v) for k, v in raw.items()}


def build_arrays(
    votes: dict[int, int],
    edge_count: int,
    node_count: int,
    node_adj: list[list[int]],
    mode_filter: int | None = None,
) -> dict:
    """Unpack votes into per-edge and derived per-node arrays."""
    edge_totals = [0] * edge_count
    # edge_id → {vt_id: count}
    edge_vt: dict[int, dict[int, int]] = {}

    for packed, count in votes.items():
        eid, m, vtid = unpack(packed)
        if eid >= edge_count:
            continue
        if mode_filter is not None and m != mode_filter:
            continue
        edge_totals[eid] += count
        if vtid:
            evd = edge_vt.get(eid)
            if evd is None:
                evd = {}
                edge_vt[eid] = evd
            evd[vtid] = evd.get(vtid, 0) + count

    # Build legend + per-edge vote types
    legend: list[str] = []
    li: dict[int, int] = {}
    edge_vote_types: list[list] = [[] for _ in range(edge_count)]
    for eid, vt_map in edge_vt.items():
        pairs = sorted(vt_map.items(), key=lambda x: -x[1])
        enc = []
        for vtid, cnt in pairs:
            if vtid not in li:
                li[vtid] = len(legend)
                legend.append(resolve_vote_type(vtid))
            enc.append([li[vtid], cnt])
        edge_vote_types[eid] = enc

    # Derive node votes from edges (max of adjacent edge totals)
    node_totals = [0] * node_count
    node_vt_merged: dict[int, dict[int, int]] = {}
    for nid in range(node_count):
        adj = node_adj[nid]
        if not adj:
            continue
        best = 0
        merged: dict[int, int] | None = None
        for eid in adj:
            v = edge_totals[eid]
            if v > best:
                best = v
            evd = edge_vt.get(eid)
            if evd:
                if merged is None:
                    merged = {}
                for vtid, cnt in evd.items():
                    merged[vtid] = max(merged.get(vtid, 0), cnt)
        node_totals[nid] = best
        if merged:
            node_vt_merged[nid] = merged

    node_vote_types: list[list] = [[] for _ in range(node_count)]
    for nid, vt_map in node_vt_merged.items():
        pairs = sorted(vt_map.items(), key=lambda x: -x[1])
        enc = []
        for vtid, cnt in pairs:
            if vtid not in li:
                li[vtid] = len(legend)
                legend.append(resolve_vote_type(vtid))
            enc.append([li[vtid], cnt])
        node_vote_types[nid] = enc

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
