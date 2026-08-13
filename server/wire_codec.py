"""Binary wire codec for the WebSocket vote stream (frame version 1).

Why this exists
---------------
The vote stream used to be JSON. A single 89-edge route cast published

    {"type":"delta","rev":41,"edges":[...89 ints...],"m":"bikepaths","vt":15,
     "dir":1,"reversed":false,"vtLabel":"Protected bike lane",
     "vtCounts":{"2079":[3,0], ...89 entries...},
     "blockCounts":{"1841":[3,0], ...~60 entries...}}

which is ~4 KB for ~150 numbers that carry maybe 300 bytes of information. The
edge ids are transmitted TWICE (once in `edges`, once as the keys of
`vtCounts`), every id is spelled out in full even though a route's edge ids run
consecutively, every key is a quoted decimal string, and `vt` is repeated once
per frame while `vtLabel` is repeated in full.

This codec keeps exactly the same semantics — authoritative post-write
[up, down] per (edge|block, vote type), applied as an idempotent SET — and
spends bytes only on what changed.

Two frame kinds share one layout:

  DELTA  the live per-vote packet. One group per source delta, so the client
         sees the same per-revision stream (and the same gap detection) it
         always did.
  SYNC   the catch-up packet requested on foreground / reconnect. Coalesced:
         one group per vote type, counts already merged last-write-wins, which
         is lossless because counts are absolute SETs. Repairs a backgrounded
         tab for ~1 KB instead of a ~57 KB /api/graph-votes refetch.

Layout (all multi-byte integers are LEB128 unsigned varints, little-endian
groups of 7 bits, high bit = continue):

    FRAME
      byte 0   magic    u8   0xCE                  (constant)
      byte 1   version  u8   0x01                  (this layout)
      byte 2   kind     u8   1 = DELTA, 2 = SYNC
      byte 3   flags    u8   bit0 = TRUNCATED (sync could not cover `since`)
      ...      rev      var  head revision this frame describes
      ...      baseRev  var  revisions (baseRev, rev] are covered
      ...      nGroups  var  number of groups that follow

    GROUP  (repeated nGroups times)
      ...      rev      var  this group's revision
      byte     mode     u8   MODE_IDS value (0..15 today)
      ...      vtId     var  GLOBAL vote_type_id, full width
      ...      labelLen var  UTF-8 byte length of the vote-type label
      ...      label    labelLen bytes
      ...      nEdge    var  edge cells that follow
      ...      cells    nEdge x CELL   (edge ids, strictly ascending)
      ...      nBlock   var  block cells that follow
      ...      cells    nBlock x CELL  (block ids, strictly ascending)

    CELL
      ...      idDelta  var  id - previousId; previousId starts at 0
      ...      up       var  authoritative up count
      ...      down     var  authoritative down count

Bit budget, and what happens at each ceiling
--------------------------------------------
The predecessor of this codec is the 53-bit Redis field layout in
vote_store.py, where `vote_type_id` was given 16 bits, a bulk import pushed
vote_types past 65535, and the overflow landed on the direction bit at 52 —
silently recording upvotes as downvotes. The structural lesson, applied here:

  * A field that can grow is a VARINT, not a fixed width. Varints are
    self-delimiting, so an oversized value can never bleed into the next
    field the way a fixed-width overflow does. `vtId` is a varint (its real
    max on this deployment is already 75107, i.e. past the 16-bit ceiling
    that caused the original bug).
  * A field that can grow appears ONCE PER GROUP at full width, never once
    per cell at a guessed width. The per-cell fields are the ones that must
    stay small, so the per-cell fields are the ones that carry no global ids.
  * Every varint is checked against MAX_VARINT (2^53 - 1, JS
    Number.MAX_SAFE_INTEGER — the same ceiling the existing vote codec
    respects). Encoding a larger value raises; decoding one raises. There is
    no silent wrap anywhere in this file.

Per field:

  magic      u8, constant. A byte that isn't 0xCE raises WireFormatError; the
             client treats that as "not our frame" and ignores it.
  version    u8, 1..255. A version the reader doesn't implement raises, and
             the client falls back to a full /api/graph-votes refetch — the
             pre-codec behaviour, so a layout change degrades instead of
             corrupting. Bump this on ANY layout change.
  kind       u8. Unknown kinds raise (same fallback).
  flags      u8, 8 flags available, 1 used. Unknown bits are ignored by
             readers, so adding a flag is backward compatible.
  mode       u8, 0..255. MODE_IDS uses 0..3 and the Redis field gives it 4
             bits, so 255 is 16x the addressable space; encoding a mode
             outside 0..255 raises rather than truncating.
  rev,
  baseRev,
  group rev  varint, ceiling 2^53-1. `vote_rev:<slug>` is a monotonic INCR
             that is never reset; at the observed rate (~15k casts total on
             the busiest map) 2^53 is unreachable. Above the ceiling the
             encoder raises, and the client's existing gap detection would
             force a refetch rather than mis-apply.
  vtId       varint, ceiling 2^53-1. vote_types.id is a Postgres SERIAL
             (int4, max 2147483647), so the wire field is ~4M times the
             widest value the database can produce.
  labelLen   varint, ceiling 2^53-1 in the format but bounded at encode by
             MAX_LABEL_BYTES (1024); a longer label raises. The decoder
             refuses a length that runs past the end of the buffer.
  nGroups,
  nEdge,
  nBlock     varint. The decoder refuses counts that exceed the remaining
             buffer length, so a corrupt length can't drive a huge allocation.
  idDelta    varint. Ids are strictly ascending within a cell run, so deltas
             are >= 1 after the first (route edge ids are near-consecutive:
             the median gap on a real NYC cast is 6, and 65% of gaps fit in
             one byte). The ABSOLUTE id is what has a real ceiling —
             n_edges (3.3M on NYC) and n_blocks (287k) — and the id space is
             the graph's, not this codec's. Bounds-checking an id against the
             live topology is the DECODER's job at apply time, exactly as
             votesMatchTopology already does for the snapshot; a stale
             topology paired with fresh ids is the mobile-Safari crash this
             repo already has defenses for.
  up, down   varint, ceiling 2^53-1. Redis HINCRBY counts; the observed max
             on prod-shaped local data is 127. A count can go transiently
             NEGATIVE if a decrement races ahead of its increment, and an
             unsigned varint cannot represent that — so the encoder CLAMPS
             negatives to 0 (which is what the client's voteApply already
             does with Math.max(0, ...)) rather than raising on the vote
             path. Clamping is recorded in the module logger.
"""

import logging

logger = logging.getLogger(__name__)

MAGIC = 0xCE
VERSION = 1

KIND_DELTA = 1
KIND_SYNC = 2

FLAG_TRUNCATED = 0x01

# JS Number.MAX_SAFE_INTEGER — the ceiling the 53-bit vote codec already works
# to, so nothing this codec emits can lose precision on the client.
MAX_VARINT = 2 ** 53 - 1
MAX_LABEL_BYTES = 1024


class WireFormatError(ValueError):
    """A frame that cannot be trusted: bad magic, unknown version, or a length
    that runs past the end of the buffer."""


# ── varint ────────────────────────────────────────────────────────────────

def write_varint(out: bytearray, value: int) -> None:
    if value < 0 or value > MAX_VARINT:
        raise WireFormatError(f"varint out of range: {value}")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Return (value, next_pos).

    CANONICAL only. Plain LEB128 lets the same number be spelled more than one
    way (0x00 and 0x80 0x00 both mean 0), so a single flipped continuation bit
    turns a valid frame into a DIFFERENT valid frame — the fuzzer found exactly
    that. A redundant trailing group is refused here, which makes the encoding
    a bijection: one frame has exactly one byte string, so these bytes can be
    hashed or ETagged, and a corrupted frame is far likelier to be rejected
    than to decode into plausible-but-wrong vote counts."""
    value = 0
    shift = 0
    start = pos
    while True:
        if pos >= len(buf):
            raise WireFormatError("varint runs past end of buffer")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            # A multi-byte varint whose last group is zero carries no
            # information — it is a longer spelling of a shorter number.
            if byte == 0 and pos - start > 1:
                raise WireFormatError("non-canonical varint (redundant trailing zero)")
            break
        shift += 7
        if shift > 56:
            raise WireFormatError("varint too long")
    if value > MAX_VARINT:
        raise WireFormatError(f"varint exceeds 2^53-1: {value}")
    return value, pos


# ── Group ─────────────────────────────────────────────────────────────────

class Group:
    """One vote type's authoritative counts at one revision.

    `edges` / `blocks` are {id: (up, down)}. They map 1:1 onto the JSON
    delta's vtCounts / blockCounts, so a decoded group is applied by exactly
    the code path that already applies a JSON delta."""

    __slots__ = ("rev", "mode", "vt_id", "label", "edges", "blocks")

    def __init__(self, rev: int, mode: int, vt_id: int, label: str,
                 edges: dict | None = None, blocks: dict | None = None):
        self.rev = rev
        self.mode = mode
        self.vt_id = vt_id
        self.label = label
        self.edges = dict(edges or {})
        self.blocks = dict(blocks or {})

    def __eq__(self, other):
        return (isinstance(other, Group)
                and (self.rev, self.mode, self.vt_id, self.label)
                == (other.rev, other.mode, other.vt_id, other.label)
                and self.edges == other.edges and self.blocks == other.blocks)

    def __repr__(self):
        return (f"Group(rev={self.rev}, mode={self.mode}, vt_id={self.vt_id}, "
                f"label={self.label!r}, edges={len(self.edges)}, "
                f"blocks={len(self.blocks)})")


class Frame:
    __slots__ = ("kind", "rev", "base_rev", "truncated", "groups")

    def __init__(self, kind: int, rev: int, base_rev: int,
                 groups: list, truncated: bool = False):
        self.kind = kind
        self.rev = rev
        self.base_rev = base_rev
        self.truncated = truncated
        self.groups = groups

    def __eq__(self, other):
        return (isinstance(other, Frame)
                and (self.kind, self.rev, self.base_rev, self.truncated)
                == (other.kind, other.rev, other.base_rev, other.truncated)
                and self.groups == other.groups)

    def __repr__(self):
        return (f"Frame(kind={self.kind}, rev={self.rev}, "
                f"base_rev={self.base_rev}, truncated={self.truncated}, "
                f"groups={self.groups!r})")


def _write_cells(out: bytearray, cells: dict) -> None:
    """Cells as (idDelta, up, down) varint triples, ids ascending.

    Sorting is what makes the delta encoding pay: a route's edge ids are
    near-consecutive, so most idDeltas are single-byte."""
    write_varint(out, len(cells))
    prev = 0
    for cid in sorted(cells):
        if cid < prev:
            raise WireFormatError("cell ids must be ascending")
        write_varint(out, cid - prev)
        prev = cid
        up, down = cells[cid]
        # Redis counts can go transiently negative under a racing decrement;
        # an unsigned varint can't carry that, and the client clamps anyway.
        if up < 0 or down < 0:
            logger.warning("[WIRE] clamping negative count id=%s up=%s down=%s",
                           cid, up, down)
        write_varint(out, max(0, int(up)))
        write_varint(out, max(0, int(down)))


def _read_cells(buf: bytes, pos: int) -> tuple[dict, int]:
    n, pos = read_varint(buf, pos)
    # A cell is at least 3 bytes, so a length larger than the remaining buffer
    # is corrupt — refuse it before allocating anything.
    if n * 3 > len(buf) - pos:
        raise WireFormatError(f"cell count {n} exceeds remaining buffer")
    cells: dict = {}
    cid = 0
    for i in range(n):
        delta, pos = read_varint(buf, pos)
        # Ids are STRICTLY ascending, so only the first cell may have a zero
        # delta (that is edge/block id 0). A zero delta later would land on the
        # id already in the dict and silently collapse two cells into one — a
        # corrupt frame that still decodes, which the fuzzer found.
        if delta == 0 and i > 0:
            raise WireFormatError("non-ascending cell id")
        cid += delta
        up, pos = read_varint(buf, pos)
        down, pos = read_varint(buf, pos)
        cells[cid] = (up, down)
    return cells, pos


def encode_frame(frame: Frame) -> bytes:
    out = bytearray()
    out.append(MAGIC)
    out.append(VERSION)
    if frame.kind not in (KIND_DELTA, KIND_SYNC):
        raise WireFormatError(f"unknown frame kind {frame.kind}")
    out.append(frame.kind)
    out.append(FLAG_TRUNCATED if frame.truncated else 0)
    write_varint(out, frame.rev)
    write_varint(out, frame.base_rev)
    write_varint(out, len(frame.groups))
    for g in frame.groups:
        write_varint(out, g.rev)
        if not (0 <= g.mode <= 255):
            raise WireFormatError(f"mode {g.mode} does not fit one byte")
        out.append(g.mode)
        write_varint(out, g.vt_id)
        label = g.label.encode("utf-8")
        if len(label) > MAX_LABEL_BYTES:
            raise WireFormatError(f"label too long: {len(label)} bytes")
        write_varint(out, len(label))
        out += label
        _write_cells(out, g.edges)
        _write_cells(out, g.blocks)
    return bytes(out)


def decode_frame(buf: bytes) -> Frame:
    buf = bytes(buf)
    if len(buf) < 4:
        raise WireFormatError("frame shorter than header")
    if buf[0] != MAGIC:
        raise WireFormatError(f"bad magic 0x{buf[0]:02x}")
    if buf[1] != VERSION:
        raise WireFormatError(f"unsupported frame version {buf[1]}")
    kind = buf[2]
    if kind not in (KIND_DELTA, KIND_SYNC):
        raise WireFormatError(f"unknown frame kind {kind}")
    truncated = bool(buf[3] & FLAG_TRUNCATED)
    pos = 4
    rev, pos = read_varint(buf, pos)
    base_rev, pos = read_varint(buf, pos)
    n_groups, pos = read_varint(buf, pos)
    # Smallest possible group is 6 bytes (rev, mode, vt, labelLen, 2 counts).
    if n_groups * 6 > len(buf) - pos:
        raise WireFormatError(f"group count {n_groups} exceeds remaining buffer")
    groups = []
    for _ in range(n_groups):
        g_rev, pos = read_varint(buf, pos)
        if pos >= len(buf):
            raise WireFormatError("group truncated at mode")
        mode = buf[pos]
        pos += 1
        vt_id, pos = read_varint(buf, pos)
        label_len, pos = read_varint(buf, pos)
        if label_len > len(buf) - pos:
            raise WireFormatError("label runs past end of buffer")
        try:
            # STRICT: a lossy decode would silently invent a vote-type label,
            # and the client appends unknown labels to its legend — so a
            # corrupted byte would materialize a phantom proposal type. Reject
            # the frame instead and let the client refetch.
            label = buf[pos:pos + label_len].decode("utf-8")
        except UnicodeDecodeError as e:
            raise WireFormatError(f"label is not valid UTF-8: {e}") from e
        pos += label_len
        edges, pos = _read_cells(buf, pos)
        blocks, pos = _read_cells(buf, pos)
        groups.append(Group(g_rev, mode, vt_id, label, edges, blocks))
    # No trailing garbage: a frame's bytes describe the frame exactly, so a
    # length field corrupted SHORT (which would otherwise decode a valid
    # prefix and drop real cells on the floor) is caught here.
    if pos != len(buf):
        raise WireFormatError(f"{len(buf) - pos} trailing bytes after frame")
    return Frame(kind, rev, base_rev, groups, truncated)


# ── Adapters to the existing JSON delta shape ─────────────────────────────

def group_from_delta(delta: dict, mode_int: int) -> Group:
    """Build a Group from the JSON delta vote_store.publish_delta emits.

    Only deltas carrying `vtCounts` can become a group: the counts ARE the
    payload. A delta without them (there is no such producer today; the field
    has been unconditional since the authoritative-SET change) falls back to
    the JSON path rather than being encoded lossily."""
    vt_counts = delta.get("vtCounts")
    if not vt_counts:
        raise WireFormatError("delta has no vtCounts — not encodable")
    edges = {int(k): (int(v[0]), int(v[1])) for k, v in vt_counts.items()}
    blocks = {int(k): (int(v[0]), int(v[1]))
              for k, v in (delta.get("blockCounts") or {}).items()}
    return Group(int(delta.get("rev", 0)), mode_int, int(delta.get("vt", 0)),
                 delta.get("vtLabel") or "", edges, blocks)


def coalesce(groups: list) -> list:
    """Merge groups of the same (mode, vt_id) into one, last rev winning.

    Counts are ABSOLUTE post-write values, so merging two revisions' cells is
    a dict update in rev order — the same losslessness argument DeltaHub
    already makes for coalescing its flush window. Used to build a SYNC frame
    from a run of logged deltas; the DELTA path deliberately does NOT coalesce,
    so the client keeps seeing one group per revision for its gap detection."""
    merged: dict = {}
    for g in sorted(groups, key=lambda x: x.rev):
        key = (g.mode, g.vt_id)
        cur = merged.get(key)
        if cur is None:
            merged[key] = Group(g.rev, g.mode, g.vt_id, g.label,
                                dict(g.edges), dict(g.blocks))
            continue
        cur.rev = g.rev
        if g.label:
            cur.label = g.label
        cur.edges.update(g.edges)
        cur.blocks.update(g.blocks)
    return sorted(merged.values(), key=lambda x: (x.rev, x.vt_id))
