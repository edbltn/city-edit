"""Codec parity + round-trip.

The VECTORS table holds the EXACT packed integers also asserted in the frontend
(client-react/src/utils/voteKey.test.ts). If either side's bit layout drifts,
one of the two suites fails — that's the guard that keeps the client store key
and the Redis hash field the same number.
"""
import vote_store as v

# [edge_id, mode, vote_type_id, pack, redis_field(down)]
# Down fields carry the direction bit at 52 (24-bit vt — ids past 65535 used to
# overflow into the old bit-44 direction slot and read back as downvotes).
VECTORS = [
    (0, 3, 0, 50331648, 4503599677702144),
    (1, 3, 0, 50331649, 4503599677702145),
    (5, 2, 7, 1912602629, 4503601539973125),
    (16777215, 0, 0, 16777215, 4503599644147711),
    (123456, 3, 42, 11324744256, 4503610952114752),
    (99, 1, 65535, 17591934386275, 4521191561756771),
    (323, 2, 72525, 19468315001155, 4523067942371651),
]


def test_pack_matches_shared_vectors():
    for edge, mode, vt, packed, _field_down in VECTORS:
        assert v.pack(edge, mode, vt) == packed


def test_redis_field_direction_bit():
    for edge, mode, vt, packed, field_down in VECTORS:
        # up has no direction bit → equals the identity pack
        assert v.redis_field(edge, mode, vt, v.UP) == packed
        assert v.redis_field(edge, mode, vt, v.DOWN) == field_down


def test_unpack_round_trips():
    for edge, mode, vt, _packed, _field_down in VECTORS:
        eid, m, vtid, dbit = v.unpack(v.pack(edge, mode, vt))
        assert (eid, m, vtid, dbit) == (edge, mode, vt, 0)
        eid, m, vtid, dbit = v.unpack(v.redis_field(edge, mode, vt, v.DOWN))
        assert (eid, m, vtid, dbit) == (edge, mode, vt, 1)


def test_mode_enum_and_fallback():
    assert v.MODE_IDS == {"bikepaths": 0, "trees": 1, "walkways": 2, "walk": 3}
    assert v.mode_to_int("walkways") == 2
    assert v.mode_to_int("nonsense") == v.MODE_IDS["walk"]
    assert v.int_to_mode(2) == "walkways"


def test_packed_values_fit_53_bit_safe_integer():
    # Client packs in JS, where ints are exact only below 2**53.
    for *_, field_down in VECTORS:
        assert field_down < 2 ** 53
