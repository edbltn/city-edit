"""The two properties the audience features live or die on.

Both features exist to make co-presence legible, which only works if the number
is trustworthy. These tests pin the two ways it could lie:

  · a route proposal double-counting one person who opened two of its blocks
    (the number silently inflates as a corridor grows), and
  · a person whose device_id fragments being counted once per fragment (the
    number inflates exactly when identity degrades, which is the failure that
    is invisible from outside).

They run against a real Redis because both properties are properties of Redis
data structures — PFCOUNT-over-many-keys returning a union, and PFADD being
idempotent — not of our code. Faking them would test the wrong thing.
"""

import os

import pytest

redis = pytest.importorskip("redis")

import presence
import view_counts


SLUG = "test-audience"
LABEL = "Protected bike lane"


@pytest.fixture
def r():
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=0, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("redis not available")
    _clean(client)
    yield client
    _clean(client)


def _clean(client):
    for pattern in (f"pv:{SLUG}:*", presence.presence_key(SLUG)):
        keys = list(client.scan_iter(match=pattern, count=500))
        if keys:
            client.delete(*keys)


# ── Views ───────────────────────────────────────────────────────────────────

def test_one_person_two_blocks_of_one_corridor_counts_once(r):
    """The headline property: no double count at the route level."""
    corridor = [11, 12, 13, 14, 15]
    # Alice opens the corridor from block 11's card, then later from block 14's.
    # Two separate views of the same proposal.
    view_counts.record_view(r, SLUG, LABEL, corridor, "alice")
    view_counts.record_view(r, SLUG, LABEL, corridor, "alice")
    assert view_counts.count_views(r, SLUG, LABEL, corridor) == (1, True)

    # Summing the per-block sketches — the naive implementation — would say 5.
    assert sum(r.pfcount(view_counts.block_key(SLUG, view_counts.type_token(LABEL), b))
               for b in corridor) == 5


def test_partial_overlap_still_unions(r):
    """Two people who entered the same corridor by different blocks, when the
    corridor is later recomputed to a different extent."""
    view_counts.record_view(r, SLUG, LABEL, [11, 12, 13], "alice")
    view_counts.record_view(r, SLUG, LABEL, [13, 14, 15], "bob")
    # A recompute lengthens the corridor. The id would have rotated; the count
    # does not care.
    assert view_counts.count_views(r, SLUG, LABEL, [11, 12, 13, 14, 15]) == (2, True)
    # And a sub-corridor sees only the people whose view covered it.
    assert view_counts.count_views(r, SLUG, LABEL, [11, 12]) == (1, True)


def test_device_id_fragmentation_does_not_inflate(r):
    """The constraint this design was built around.

    One phone, six voter_ids in three minutes (prod 2026-08-13). Keyed on
    device_id this reads as six people; keyed on the counting identity it is
    one member added six times, and PFADD does not care.
    """
    blocks = [21, 22]
    for _ in range(6):
        view_counts.record_view(r, SLUG, LABEL, blocks, "one-ip-hash")
    assert view_counts.count_views(r, SLUG, LABEL, blocks) == (1, True)


def test_types_do_not_bleed_into_each_other(r):
    """A signal-timing pin must not inherit a bike corridor's audience."""
    view_counts.record_view(r, SLUG, "Protected bike lane", [31], "alice")
    view_counts.record_view(r, SLUG, "Protected bike lane", [31], "bob")
    assert view_counts.count_views(r, SLUG, "Fix signal timing", [31]) == (0, True)
    assert view_counts.count_views(r, SLUG, "Protected bike lane", [31]) == (2, True)


def test_empty_and_broken_never_invent_an_audience(r):
    assert view_counts.count_views(r, SLUG, LABEL, []) == view_counts.COUNT_ERROR

    class Broken:
        def pfcount(self, *a):
            raise RuntimeError("redis down")

    assert view_counts.count_views(Broken(), SLUG, LABEL, [1, 2]) == (0, True)


def test_negative_block_ids_survive(r):
    """`blockKeyOf` returns -(edgeId + 2) for an edge in no baked block."""
    view_counts.record_view(r, SLUG, LABEL, [-7, -8], "alice")
    assert view_counts.count_views(r, SLUG, LABEL, [-7, -8]) == (1, True)


def test_blocks_are_capped(r):
    assert len(view_counts.normalize_blocks(range(10_000))) == \
        view_counts.MAX_PROPOSAL_BLOCKS


# ── Presence ────────────────────────────────────────────────────────────────

def test_presence_counts_people_not_tabs(r):
    p = presence.Presence(r)
    p.arrive(SLUG, "alice")
    p.arrive(SLUG, "alice")   # second tab, same person
    p.arrive(SLUG, "bob")
    assert p.count(SLUG) == 2

    # Closing one of Alice's two tabs must not remove her from the room.
    p.depart(SLUG, "alice")
    assert p.count(SLUG) == 2

    p.depart(SLUG, "alice")
    assert p.count(SLUG) == 1


def test_presence_retracts_rather_than_only_expiring(r):
    """A count that stays high after everyone leaves is over-reporting, which
    is the one direction this feature does not accept."""
    p = presence.Presence(r)
    p.arrive(SLUG, "alice")
    assert p.count(SLUG) == 1
    p.depart(SLUG, "alice")
    # No cache clear: a departure must be visible IMMEDIATELY, not after the
    # memo lapses. Reporting someone who has left is over-reporting.
    assert p.count(SLUG) == 0


def test_presence_expires_stale_entries(r):
    p = presence.Presence(r)
    p.arrive(SLUG, "ghost")
    # A viewer whose machine slept: last heartbeat older than the TTL, no
    # disconnect ever seen.
    r.zadd(presence.presence_key(SLUG),
           {"ghost": __import__("time").time() - presence.PRESENCE_TTL - 5})
    p._cache.clear()
    assert p.count(SLUG) == 0
    assert r.zscore(presence.presence_key(SLUG), "ghost") is None


def test_presence_returns_zero_when_redis_is_down():
    class Broken:
        def pipeline(self):
            raise RuntimeError("redis down")

    assert presence.Presence(Broken()).count(SLUG) == 0
