"""The vote-type id ceiling: where it really is, and that creation refuses it.

vote_types.id is a Postgres int4 SERIAL (max 2,147,483,647), but three separate
packed layouts carry that id in a 24-bit field. Nothing rounds or errors when an
id outgrows them — it overflows onto a DIRECTION bit, and the vote records as its
own opposite. That already happened once, at 16 bits (see the note in
vote_store.py). These tests pin the narrowest real limit and prove the guard
fires at creation, where a human is there to be told.
"""
from unittest import mock

import pytest

import block_votes as b
import database
import vote_store as v


# ── where the boundary actually is ────────────────────────────────────────

def test_the_limit_is_the_narrowest_packed_field_not_the_db_column():
    # 24 bits, in all three packed layouts; NOT the int4 SERIAL that mints it.
    assert v.VT_BITS == 24
    assert v.MAX_VOTE_TYPE_ID == 16_777_215 == (1 << 24) - 1
    assert b.BLOCK_VT_BITS == v.VT_BITS


def test_max_id_still_packs_and_unpacks_in_every_layout():
    vt = v.MAX_VOTE_TYPE_ID
    # Redis vote field, both directions.
    for direction, want_bit in ((v.UP, 0), (v.DOWN, 1)):
        field = v.redis_field(1234, 2, vt, direction)
        assert field <= 2 ** 53 - 1, "must stay inside Number.MAX_SAFE_INTEGER"
        eid, mode, got_vt, dbit = v.unpack(field)
        assert (eid, mode, got_vt, dbit) == (1234, 2, vt, want_bit)
    # Block aggregate field.
    assert b.unpack_block_field(b.pack_block_field(99, vt, 0)) == (99, vt, 0)
    assert b.unpack_block_field(b.pack_block_field(99, vt, 1)) == (99, vt, 1)


def test_one_past_the_limit_would_land_on_a_direction_bit():
    """The exact failure this guard prevents, demonstrated on both layouts.

    Computed WITHOUT the bounds checks, to show what the raise is standing in
    front of: at 2**24 the vote field's overflow lands on DIR_BIT (52) and the
    block field's on BLOCK_DIR_BIT (48) — an upvote read back as a downvote,
    which is precisely the 16-bit bug relocated by the widening."""
    vt = v.MAX_VOTE_TYPE_ID + 1
    raw_vote_field = (vt << 28) | (0 << 24) | 1          # what pack() would build
    assert raw_vote_field & (1 << v.DIR_BIT), "overflow misses the direction bit?"
    _e, _m, got_vt, dbit = v.unpack(raw_vote_field)
    assert (got_vt, dbit) == (0, 1), "an UPVOTE decodes as vote type 0, DOWN"

    raw_block_field = (0 << b.BLOCK_DIR_BIT) | (vt << 24) | 5
    assert b.unpack_block_field(raw_block_field) == (5, 0, 1), \
        "an UPVOTE aggregates onto the block as a DOWNVOTE"


@pytest.mark.parametrize("vt", [v.MAX_VOTE_TYPE_ID + 1, 2 ** 25, 2 ** 31 - 1])
def test_both_packers_raise_rather_than_wrap(vt):
    """Last line of defence: a bad id must reach an exception, never the map."""
    with pytest.raises(ValueError, match="vote_type_id"):
        v.pack(1, 0, vt)
    with pytest.raises(ValueError, match="vote_type_id"):
        b.pack_block_field(5, vt, 0)


# ── the creation guard ────────────────────────────────────────────────────

class FakeCursor:
    """Stands in for the INSERT ... RETURNING id, handing back a chosen id."""

    def __init__(self, vt_id):
        self.vt_id = vt_id

    def execute(self, *_a, **_kw):
        pass

    def fetchone(self):
        return (self.vt_id,)


def mint(vt_id):
    """Call get_or_create_vote_type_id with the DB minting exactly `vt_id`."""
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(return_value=FakeCursor(vt_id))
    cm.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"), \
            mock.patch.object(database, "get_cursor", return_value=cm):
        return database.get_or_create_vote_type_id("Add protected bike lane", "route")


def test_ids_within_the_limit_pass_through():
    assert mint(1) == 1
    assert mint(75_607) == 75_607          # the largest id in the registry today
    assert mint(v.MAX_VOTE_TYPE_ID) == v.MAX_VOTE_TYPE_ID


@pytest.mark.parametrize("vt_id", [
    v.MAX_VOTE_TYPE_ID + 1,
    2 ** 25,
    2_147_483_647,      # the largest id the int4 SERIAL can actually mint
])
def test_creation_raises_past_the_limit(vt_id):
    with pytest.raises(v.VoteTypeLimitError):
        mint(vt_id)


def test_the_error_message_states_the_limit_and_names_the_type():
    with pytest.raises(v.VoteTypeLimitError) as ei:
        mint(v.MAX_VOTE_TYPE_ID + 1)
    msg = str(ei.value)
    assert "16,777,215" in msg, "the message must say what the limit IS"
    assert "Add protected bike lane" in msg, "and which type was refused"
    # It has to read as an explanation to a person, not a stack trace.
    assert "vote types" in msg and "Existing vote types still work" in msg


def test_the_limit_is_never_swallowed_into_a_zero_id():
    """get_or_create_vote_type_id returns 0 on any DB error, and 0 reads as
    'untyped vote' downstream — the exact silent wrong answer this guard is for.
    The check therefore sits OUTSIDE that try/except."""
    with pytest.raises(v.VoteTypeLimitError):
        mint(2 ** 30)


def test_db_errors_still_degrade_to_zero_rather_than_raising():
    """The guard must not turn ordinary DB trouble into a 500 — only the id
    ceiling raises."""
    cm = mock.MagicMock()
    cm.__enter__ = mock.Mock(side_effect=RuntimeError("connection refused"))
    with mock.patch.object(database, "DATABASE_URL", "postgresql://fake"), \
            mock.patch.object(database, "get_cursor", return_value=cm):
        assert database.get_or_create_vote_type_id("Whatever") == 0


def test_no_db_configured_is_still_a_quiet_zero():
    with mock.patch.object(database, "DATABASE_URL", ""):
        assert database.get_or_create_vote_type_id("Whatever") == 0


# ── the paths that do NOT have a 16-bit assumption ────────────────────────

def test_the_ws_frame_carries_ids_far_past_the_packed_limit():
    """The binary WS codec must not be the narrowest link — its vtId is a
    varint, so it is bounded by 2^53-1, not by 24 bits. If it were narrower it
    would become the limit the creation guard has to enforce."""
    import wire_codec as w

    for vt in (v.MAX_VOTE_TYPE_ID, 2 ** 31 - 1, w.MAX_VARINT):
        f = w.Frame(w.KIND_DELTA, 1, 0, [w.Group(1, 0, vt, "x", {5: (1, 0)}, {})])
        assert w.decode_frame(w.encode_frame(f)).groups[0].vt_id == vt
