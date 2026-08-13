"""Binary WS frame codec: round-trip, boundaries, overflow, fuzz.

The GOLDEN vectors written by `test_write_golden_vectors` are decoded by the
frontend suite (client-react/src/utils/wireCodec.test.ts) so a drift in either
side's byte layout fails one of the two suites — the same guard test_vote_codec
↔ voteKey.test.ts already puts on the 53-bit vote key.
"""
import json
import os
import random

import pytest

import wire_codec as w
from wire_codec import Frame, Group, WireFormatError

GOLDEN_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "client-react", "src", "utils", "__fixtures__", "wireFrames.json")


# ── varint ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    0, 1, 127, 128, 129, 255, 256, 16383, 16384, 2 ** 20, 2 ** 31 - 1, 2 ** 32,
    75107,          # a real vote_type_id on this deployment — past 16 bits
    3299151,        # the largest NYC edge id
    287351,         # the largest NYC block id
    w.MAX_VARINT,   # 2^53 - 1
])
def test_varint_round_trip(value):
    out = bytearray()
    w.write_varint(out, value)
    got, pos = w.read_varint(bytes(out), 0)
    assert got == value
    assert pos == len(out)


def test_varint_widths():
    """The width claims in the module docstring, asserted."""
    widths = {}
    for v in (0, 127, 128, 16383, 16384, 2 ** 21 - 1, 2 ** 21):
        out = bytearray()
        w.write_varint(out, v)
        widths[v] = len(out)
    assert widths == {0: 1, 127: 1, 128: 2, 16383: 2, 16384: 3,
                      2 ** 21 - 1: 3, 2 ** 21: 4}


def test_varint_rejects_out_of_range():
    # The failure mode this codec exists to prevent: no silent wrap. The
    # 16-bit vt field wrapped into the direction bit; this raises instead.
    with pytest.raises(WireFormatError):
        w.write_varint(bytearray(), w.MAX_VARINT + 1)
    with pytest.raises(WireFormatError):
        w.write_varint(bytearray(), -1)


def test_varint_decode_rejects_oversize():
    # 2^53 encoded as a varint must be refused rather than silently losing
    # precision when it reaches JS.
    out = bytearray()
    value = 2 ** 53
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            break
    with pytest.raises(WireFormatError):
        w.read_varint(bytes(out), 0)


# ── frame round-trip ──────────────────────────────────────────────────────

def test_empty_frame_round_trips():
    f = Frame(w.KIND_DELTA, 7, 6, [])
    assert w.decode_frame(w.encode_frame(f)) == f


def test_single_vote_frame_round_trips():
    g = Group(41, 0, 75107, "Protected bike lane",
              edges={2079: (3, 0), 2080: (3, 0), 2088: (1, 2)},
              blocks={1841: (3, 0)})
    f = Frame(w.KIND_DELTA, 41, 40, [g])
    assert w.decode_frame(w.encode_frame(f)) == f


def test_multi_group_sync_frame_round_trips():
    f = Frame(w.KIND_SYNC, 120, 100, [
        Group(118, 0, 15, "Protected bike lane", {5: (1, 0)}, {2: (1, 0)}),
        Group(120, 2, 74421, "Widen sidewalk", {9: (0, 4)}, {}),
    ], truncated=True)
    got = w.decode_frame(w.encode_frame(f))
    assert got == f
    assert got.truncated is True


def test_unicode_label_round_trips():
    g = Group(1, 0, 3, "Bäume pflanzen 🌳", {0: (1, 0)}, {})
    got = w.decode_frame(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [g])))
    assert got.groups[0].label == "Bäume pflanzen 🌳"


def test_edge_id_zero_and_max_round_trip():
    """id 0 encodes as delta 0 from the initial prev=0; that must not be read
    as 'no cell'. The largest NYC edge id rides in the same run."""
    g = Group(1, 0, 3, "x", {0: (1, 0), 3299151: (2, 1)}, {})
    got = w.decode_frame(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [g])))
    assert got.groups[0].edges == {0: (1, 0), 3299151: (2, 1)}


def test_counts_are_absolute_not_clamped_at_top():
    g = Group(1, 0, 3, "x", {5: (65535, 65536)}, {})
    got = w.decode_frame(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [g])))
    # 16 bits would have wrapped 65536 to 0 — the varint does not.
    assert got.groups[0].edges[5] == (65535, 65536)


def test_negative_counts_clamp_to_zero():
    """A racing decrement can leave a transiently negative HINCRBY value. An
    unsigned varint can't carry it; the client already clamps with
    Math.max(0, …), so the encoder clamps rather than raising on the vote
    path."""
    g = Group(1, 0, 3, "x", {5: (-2, 3)}, {})
    got = w.decode_frame(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [g])))
    assert got.groups[0].edges[5] == (0, 3)


# ── rejection ─────────────────────────────────────────────────────────────

def test_rejects_bad_magic():
    buf = bytearray(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [])))
    buf[0] = 0x00
    with pytest.raises(WireFormatError):
        w.decode_frame(bytes(buf))


def test_rejects_unknown_version():
    buf = bytearray(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [])))
    buf[1] = 2
    with pytest.raises(WireFormatError):
        w.decode_frame(bytes(buf))


def test_rejects_unknown_kind():
    with pytest.raises(WireFormatError):
        w.encode_frame(Frame(9, 1, 0, []))


def test_unknown_flag_bits_are_ignored():
    """Adding a flag must stay backward compatible."""
    buf = bytearray(w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [])))
    buf[3] |= 0x80
    got = w.decode_frame(bytes(buf))
    assert got.truncated is False


def test_rejects_mode_wider_than_a_byte():
    with pytest.raises(WireFormatError):
        w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [Group(1, 256, 3, "x")]))


def test_rejects_overlong_label():
    with pytest.raises(WireFormatError):
        w.encode_frame(Frame(w.KIND_DELTA, 1, 0,
                             [Group(1, 0, 3, "x" * (w.MAX_LABEL_BYTES + 1))]))


def test_rejects_absurd_cell_count_without_allocating():
    """A corrupt length must not drive a huge allocation."""
    buf = bytearray([w.MAGIC, w.VERSION, w.KIND_DELTA, 0])
    w.write_varint(buf, 1)      # rev
    w.write_varint(buf, 0)      # baseRev
    w.write_varint(buf, 1)      # nGroups
    w.write_varint(buf, 1)      # group rev
    buf.append(0)               # mode
    w.write_varint(buf, 3)      # vtId
    w.write_varint(buf, 0)      # labelLen
    w.write_varint(buf, 2 ** 40)  # nEdge — absurd
    with pytest.raises(WireFormatError):
        w.decode_frame(bytes(buf))


def test_truncated_buffer_raises_rather_than_returning_partial():
    full = w.encode_frame(Frame(w.KIND_DELTA, 41, 40, [
        Group(41, 0, 75107, "Protected bike lane", {1: (1, 0), 9: (2, 0)}, {3: (1, 0)})]))
    for cut in range(4, len(full)):
        with pytest.raises(WireFormatError):
            w.decode_frame(full[:cut])


# ── canonicality ──────────────────────────────────────────────────────────
# Three rules the fuzz test found the hard way. Each closes a case where a
# corrupted frame decoded successfully into DIFFERENT vote counts instead of
# being rejected — the worst possible failure for a codec whose output is
# applied as an authoritative SET.

def test_rejects_non_canonical_varint():
    """0x80 0x00 is a redundant spelling of 0x00. One flipped continuation bit
    turns a valid frame into a different valid frame unless we refuse it."""
    with pytest.raises(WireFormatError, match="non-canonical"):
        w.read_varint(b"\x80\x00", 0)
    with pytest.raises(WireFormatError, match="non-canonical"):
        w.read_varint(b"\xff\x80\x00", 0)
    # The canonical spellings of the same values still decode.
    assert w.read_varint(b"\x00", 0) == (0, 1)
    assert w.read_varint(b"\xff\x01", 0) == (255, 2)


def test_rejects_non_ascending_cell_ids():
    """A zero id-delta after the first cell lands on the previous id and
    silently collapses two cells into one."""
    buf = bytearray([w.MAGIC, w.VERSION, w.KIND_DELTA, 0])
    for v in (1, 0, 1, 1):          # rev, baseRev, nGroups, group rev
        w.write_varint(buf, v)
    buf.append(0)                    # mode
    for v in (3, 0, 2):              # vtId, labelLen, nEdge = 2
        w.write_varint(buf, v)
    for v in (5, 1, 0, 0, 1, 0):     # cell 1 (id 5), cell 2 with delta 0
        w.write_varint(buf, v)
    w.write_varint(buf, 0)           # nBlock
    with pytest.raises(WireFormatError, match="non-ascending"):
        w.decode_frame(bytes(buf))


def test_rejects_trailing_bytes():
    """A length corrupted SHORT would decode a valid prefix and drop real
    cells on the floor."""
    good = w.encode_frame(Frame(w.KIND_DELTA, 1, 0, [
        Group(1, 0, 3, "x", {5: (1, 0)}, {})]))
    with pytest.raises(WireFormatError, match="trailing"):
        w.decode_frame(good + b"\x00")


# ── coalescing ────────────────────────────────────────────────────────────

def test_coalesce_is_last_write_wins_per_cell():
    merged = w.coalesce([
        Group(10, 0, 15, "Bike lane", {1: (1, 0), 2: (1, 0)}, {7: (1, 0)}),
        Group(12, 0, 15, "Bike lane", {2: (5, 1)}, {7: (5, 1)}),
        Group(11, 0, 99, "Trees", {1: (3, 0)}, {}),
    ])
    by_vt = {g.vt_id: g for g in merged}
    assert by_vt[15].edges == {1: (1, 0), 2: (5, 1)}   # rev 12 wins on edge 2
    assert by_vt[15].blocks == {7: (5, 1)}
    assert by_vt[15].rev == 12
    assert by_vt[99].edges == {1: (3, 0)}


def test_coalesce_orders_by_rev_regardless_of_input_order():
    merged = w.coalesce([
        Group(12, 0, 15, "a", {2: (5, 1)}, {}),
        Group(10, 0, 15, "a", {1: (1, 0), 2: (1, 0)}, {}),
    ])
    assert merged[0].edges == {1: (1, 0), 2: (5, 1)}


# ── delta adapter ─────────────────────────────────────────────────────────

def test_group_from_delta_matches_publish_delta_shape():
    delta = {
        "type": "delta", "rev": 41, "edges": [2079, 2080], "m": "bikepaths",
        "vt": 15, "dir": 1, "reversed": False, "vtLabel": "Protected bike lane",
        "vtCounts": {"2079": [3, 0], "2080": [3, 0]},
        "blockCounts": {"1841": [3, 0]},
    }
    g = w.group_from_delta(delta, 0)
    assert g == Group(41, 0, 15, "Protected bike lane",
                      {2079: (3, 0), 2080: (3, 0)}, {1841: (3, 0)})


def test_group_from_delta_refuses_countless_delta():
    with pytest.raises(WireFormatError):
        w.group_from_delta({"rev": 1, "vt": 3, "edges": [1]}, 0)


# ── fuzz ──────────────────────────────────────────────────────────────────

def _random_frame(rng: random.Random) -> Frame:
    kind = rng.choice([w.KIND_DELTA, w.KIND_SYNC])
    rev = rng.randrange(0, 2 ** 40)
    base = rng.randrange(0, rev + 1)
    groups = []
    for _ in range(rng.randrange(0, 5)):
        # Bias hard toward the values that actually occur, but keep the
        # extremes in the pool: vt ids past 65535 (the real overflow that
        # happened), the top edge id, and counts past 16 bits.
        vt = rng.choice([rng.randrange(0, 100), rng.randrange(65000, 80000),
                         rng.randrange(0, 2 ** 31)])
        n_edges = rng.randrange(0, 60)
        base_id = rng.randrange(0, 3_299_152)
        edges, cid = {}, base_id
        for _ in range(n_edges):
            cid += rng.randrange(1, 40)
            if cid > 3_299_151:
                break
            edges[cid] = (rng.choice([0, 1, 5, 127, 128, 65536]),
                          rng.choice([0, 1, 3, 300]))
        blocks, bid = {}, rng.randrange(0, 287_352)
        for _ in range(rng.randrange(0, 20)):
            bid += rng.randrange(1, 10)
            if bid > 287_351:
                break
            blocks[bid] = (rng.randrange(0, 500), rng.randrange(0, 500))
        label = "".join(rng.choice("abc XYZ éü🌳_-#") for _ in range(rng.randrange(0, 30)))
        groups.append(Group(rng.randrange(0, 2 ** 40), rng.randrange(0, 16),
                            vt, label, edges, blocks))
    return Frame(kind, rev, base, groups, truncated=rng.random() < 0.1)


def test_fuzz_round_trip():
    rng = random.Random(20260813)
    for _ in range(2000):
        f = _random_frame(rng)
        buf = w.encode_frame(f)
        got = w.decode_frame(buf)
        assert got == f, f"round-trip mismatch on {f!r}"
        # Re-encoding a decoded frame must be byte-identical: the encoding is
        # canonical, so an ETag or a cache key over these bytes is stable.
        assert w.encode_frame(got) == buf


def test_fuzz_corrupted_bytes_never_return_a_wrong_frame():
    """Flipping a byte must either raise or decode to something that
    re-encodes to those exact bytes — never a silently different frame that
    would apply wrong vote counts."""
    rng = random.Random(4242)
    raised = 0
    for _ in range(2000):
        f = _random_frame(rng)
        buf = bytearray(w.encode_frame(f))
        i = rng.randrange(0, len(buf))
        buf[i] ^= 1 << rng.randrange(0, 8)
        try:
            got = w.decode_frame(bytes(buf))
        except (WireFormatError, UnicodeDecodeError):
            raised += 1
            continue
        # Reserved flag bits are deliberately IGNORED (that's what makes
        # adding a flag backward compatible), so they're the one place the
        # byte string isn't canonical. Normalize them out of the comparison.
        buf[3] &= w.FLAG_TRUNCATED
        assert w.encode_frame(got) == bytes(buf)
    # Sanity: the corruption is actually reaching the parser.
    assert raised > 0


def test_fuzz_random_bytes_never_hang_or_crash():
    rng = random.Random(99)
    for _ in range(2000):
        n = rng.randrange(0, 64)
        buf = bytes(rng.randrange(0, 256) for _ in range(n))
        if n >= 2:
            buf = bytes([w.MAGIC, w.VERSION]) + buf[2:]  # get past the header
        try:
            w.decode_frame(buf)
        except (WireFormatError, UnicodeDecodeError):
            pass


# ── golden vectors for the frontend suite ─────────────────────────────────

GOLDEN_FRAMES = [
    ("empty", Frame(w.KIND_DELTA, 7, 6, [])),
    ("single_vote", Frame(w.KIND_DELTA, 41, 40, [
        Group(41, 0, 75107, "Protected bike lane",
              {2079: (3, 0), 2080: (3, 0), 2088: (1, 2)}, {1841: (3, 0)})])),
    ("two_groups", Frame(w.KIND_DELTA, 43, 41, [
        Group(42, 0, 15, "Protected bike lane", {5: (1, 0)}, {2: (1, 0)}),
        Group(43, 2, 74421, "Widen sidewalk", {9: (0, 4)}, {})])),
    ("sync_truncated", Frame(w.KIND_SYNC, 120, 100, [
        Group(120, 3, 1, "Add tree", {0: (1, 0), 3299151: (65536, 2)}, {})],
        truncated=True)),
    ("unicode_label", Frame(w.KIND_DELTA, 1, 0, [
        Group(1, 1, 3, "Bäume pflanzen 🌳", {0: (1, 0)}, {})])),
]


def _frame_to_json(f: Frame) -> dict:
    return {
        "kind": f.kind, "rev": f.rev, "baseRev": f.base_rev,
        "truncated": f.truncated,
        "groups": [{
            "rev": g.rev, "mode": g.mode, "vtId": g.vt_id, "label": g.label,
            "edges": {str(k): list(v) for k, v in sorted(g.edges.items())},
            "blocks": {str(k): list(v) for k, v in sorted(g.blocks.items())},
        } for g in f.groups],
    }


def test_write_golden_vectors():
    """Emit the cross-language fixture. Asserts the encoding first, so a
    broken encoder can never overwrite the fixture with its own output."""
    vectors = []
    for name, f in GOLDEN_FRAMES:
        buf = w.encode_frame(f)
        assert w.decode_frame(buf) == f
        vectors.append({"name": name, "hex": buf.hex(), "frame": _frame_to_json(f)})
    os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
    with open(GOLDEN_PATH, "w") as fh:
        json.dump(vectors, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
