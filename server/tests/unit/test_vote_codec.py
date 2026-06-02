"""Codec parity + round-trip.

The VECTORS table holds the EXACT packed integers also asserted in the frontend
(client-react/src/utils/voteKey.test.ts). If either side's bit layout drifts,
one of the two suites fails — that's the guard that keeps the client store key
and the Redis hash field the same number.
"""
import vote_store as v

# [edge_id, mode, vote_type_id, pack, redis_field(down)]
VECTORS = [
    (0, 3, 0, 50331648, 17592236376064),
    (1, 3, 0, 50331649, 17592236376065),
    (5, 2, 7, 1912602629, 17594098647045),
    (16777215, 0, 0, 16777215, 17592202821631),
    (123456, 3, 42, 11324744256, 17603510788672),
    (99, 1, 65535, 17591934386275, 35184120430691),
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
