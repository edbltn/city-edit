"""The sync packet: delta log, coverage proof, and the hub's binary twin.

The sync frame is what repairs a backgrounded tab. Its whole safety argument is
one property: it may only claim to carry a client forward when the log can
PROVE it covers every revision in the gap. Everything else here is in service
of pinning that.
"""
import base64
import json
from types import SimpleNamespace

import pytest

import delta_hub as dh
import vote_store as vs
import wire_codec as w

_FAKE_THREAD = SimpleNamespace(is_alive=lambda: True)


class FakeRedis:
    """Just enough Redis for the sync log: strings + one sorted set.

    decode_responses=True is modelled deliberately — members come back as str,
    which is why the log stores base64 rather than raw frame bytes."""

    def __init__(self):
        self.kv: dict = {}
        self.z: dict = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, **kw):
        self.kv[k] = v
        return True

    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k) or 0) + 1)
        return int(self.kv[k])

    def publish(self, *_a, **_kw):
        return 0

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    def zremrangebyrank(self, k, start, stop):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        for member, _ in items[start:stop + 1] if stop != -1 else items[start:]:
            self.z[k].pop(member, None)

    def expire(self, *_a, **_kw):
        return True

    def zrangebyscore(self, k, lo, _hi):
        exclusive = str(lo).startswith("(")
        bound = float(str(lo).lstrip("("))
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        return [m for m, s in items if (s > bound if exclusive else s >= bound)]

    def zrange(self, k, start, stop, withscores=False):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        sl = items[start:stop + 1] if stop != -1 else items[start:]
        return sl if withscores else [m for m, _ in sl]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, r):
        self.r = r
        self.ops = []

    def zadd(self, *a):
        self.ops.append(("zadd", a)); return self

    def zremrangebyrank(self, *a):
        self.ops.append(("zremrangebyrank", a)); return self

    def expire(self, *a):
        self.ops.append(("expire", a)); return self

    def execute(self):
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


def cast(r, slug, rev, vt=15, label="Protected bike lane", edges=(1, 2), mode=0):
    """Log one delta at `rev`, exactly as publish_delta would."""
    r.kv[vs.revision_key(slug)] = str(rev)
    delta = {
        "rev": rev, "vt": vt, "vtLabel": label,
        "vtCounts": {str(e): [rev, 0] for e in edges},
        "blockCounts": {"7": [rev, 0]},
    }
    g = w.group_from_delta(delta, mode)
    frame = w.encode_frame(w.Frame(w.KIND_DELTA, rev, rev - 1, [g]))
    vs.append_delta_log(r, slug, rev, base64.b64encode(frame).decode("ascii"))


# ── coverage proof ────────────────────────────────────────────────────────

def test_sync_carries_only_what_changed():
    r = FakeRedis()
    cast(r, "m", 1, edges=(1, 2))
    cast(r, "m", 2, edges=(5,))
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    assert f.kind == w.KIND_SYNC
    assert (f.rev, f.base_rev, f.truncated) == (2, 0, False)
    # Both revisions were the same vote type, so they coalesce into one group.
    assert len(f.groups) == 1
    assert f.groups[0].edges == {1: (1, 0), 2: (1, 0), 5: (2, 0)}


def test_sync_from_a_later_rev_skips_what_the_client_already_has():
    r = FakeRedis()
    cast(r, "m", 1, edges=(1,))
    cast(r, "m", 2, edges=(5,))
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=1))
    assert f.base_rev == 1
    assert f.groups[0].edges == {5: (2, 0)}


def test_sync_coalesces_repeat_votes_on_one_edge_last_write_wins():
    r = FakeRedis()
    cast(r, "m", 1, edges=(1,))
    cast(r, "m", 2, edges=(1,))
    cast(r, "m", 3, edges=(1,))
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    # Three revisions of the same cell arrive as ONE cell at its final count —
    # lossless because the counts are absolute SETs, not increments.
    assert f.groups[0].edges == {1: (3, 0)}
    assert f.rev == 3


def test_sync_keeps_vote_types_in_separate_groups():
    r = FakeRedis()
    cast(r, "m", 1, vt=15, label="Bike lane", edges=(1,))
    cast(r, "m", 2, vt=75107, label="Add tree", edges=(2,))
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    assert {g.vt_id for g in f.groups} == {15, 75107}


def test_up_to_date_client_gets_an_empty_untruncated_sync():
    r = FakeRedis()
    cast(r, "m", 1)
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=1))
    assert (f.groups, f.truncated, f.rev) == ([], False, 1)


def test_client_ahead_of_the_server_is_not_a_gap():
    """The client's own optimistic cast can race this read; that is not a
    reason to make it refetch the whole city."""
    r = FakeRedis()
    cast(r, "m", 1)
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=5))
    assert (f.groups, f.truncated) == ([], False)


# ── truncation: the safety property ───────────────────────────────────────

def test_truncated_when_the_log_starts_after_the_gap():
    r = FakeRedis()
    for rev in range(1, 6):
        cast(r, "m", rev)
    # Simulate the trim/TTL dropping revs 1-2.
    key = vs.delta_log_key("m")
    for m, s in list(r.z[key].items()):
        if s <= 2:
            del r.z[key][m]
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    assert f.truncated is True
    assert f.groups == []


def test_truncated_when_a_revision_moved_without_publishing_a_delta():
    """Hydration, a resnap and a manual `INCR vote_rev:<slug>` all bump the
    revision with no delta behind it. The log cannot describe those, so the
    sync must refuse rather than under-report."""
    r = FakeRedis()
    cast(r, "m", 1)
    cast(r, "m", 2)
    r.kv[vs.revision_key("m")] = "7"  # rev jumped; revs 3..7 have no delta
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    assert f.truncated is True
    assert f.rev == 7


def test_truncated_when_the_log_is_empty_but_the_map_has_moved():
    r = FakeRedis()
    r.kv[vs.revision_key("m")] = "9"
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=3))
    assert f.truncated is True


def test_truncated_when_a_log_entry_is_undecodable():
    r = FakeRedis()
    cast(r, "m", 1)
    r.z[vs.delta_log_key("m")] = {"not-base64-at-all!!": 1}
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=0))
    assert f.truncated is True


def test_truncated_frame_reports_head_so_the_client_knows_where_to_land():
    r = FakeRedis()
    r.kv[vs.revision_key("m")] = "42"
    f = w.decode_frame(vs.build_sync_frame(r, "m", since=1))
    assert (f.rev, f.base_rev, f.truncated) == (42, 42, True)


# ── the log itself ────────────────────────────────────────────────────────

def test_log_is_bounded_to_the_newest_entries():
    r = FakeRedis()
    for rev in range(1, vs.SYNC_LOG_MAX + 21):
        cast(r, "m", rev)
    entries = r.z[vs.delta_log_key("m")]
    assert len(entries) == vs.SYNC_LOG_MAX
    # Revisions 1..148 were logged; the newest SYNC_LOG_MAX survive.
    assert max(entries.values()) == vs.SYNC_LOG_MAX + 20
    assert min(entries.values()) == 21


def test_publish_delta_logs_the_frame_it_published():
    """The log is written in the same call that publishes, so it cannot
    drift from the stream it replays."""
    r = FakeRedis()
    vs.publish_delta(r, "m", [1, 2], mode=0, vt_id=15,
                     vt_counts={1: [3, 0], 2: [3, 0]}, block_counts={7: [3, 0]})
    entries, oldest = vs.read_delta_log(r, "m", since=0)
    assert oldest == 1 and len(entries) == 1
    f = w.decode_frame(base64.b64decode(entries[0]))
    assert f.groups[0].edges == {1: (3, 0), 2: (3, 0)}
    assert f.groups[0].blocks == {7: (3, 0)}


def test_publish_delta_survives_a_broken_log():
    """A vote must never fail because the cache behind the sync packet did."""
    class Broken(FakeRedis):
        def pipeline(self):
            raise RuntimeError("redis down")

    r = Broken()
    rev = vs.publish_delta(r, "m", [1], mode=0, vt_id=15, vt_counts={1: [1, 0]})
    assert rev == 1


# ── the hub's binary twin ─────────────────────────────────────────────────

def make_hub(**kw):
    hub = dh.DeltaHub(redis_factory=lambda: None, **kw)
    hub._thread = _FAKE_THREAD
    return hub


def ingest(hub, slug, rev, vt=15, edges=(1, 2)):
    hub._ingest(f"vote_deltas:{slug}", json.dumps({
        "type": "delta", "rev": rev, "m": "bikepaths", "vt": vt,
        "vtLabel": "Protected bike lane", "edges": list(edges),
        "vtCounts": {str(e): [1, 0] for e in edges},
        "blockCounts": {"7": [1, 0]},
    }))


def test_binary_subscriber_gets_a_frame_and_json_subscriber_gets_json():
    hub = make_hub()
    js = hub.subscribe("m1")
    bi = hub.subscribe("m1", binary=True)
    ingest(hub, "m1", 1)
    ingest(hub, "m1", 2)
    hub._flush(now=100.0)

    j = json.loads(js.q.get_nowait())
    assert j["type"] == "deltas" and [i["rev"] for i in j["items"]] == [1, 2]

    f = w.decode_frame(bi.q.get_nowait())
    # One group PER REVISION, not coalesced: a binary client must see the same
    # per-rev stream its gap detection is built around.
    assert [g.rev for g in f.groups] == [1, 2]
    assert (f.kind, f.rev, f.base_rev) == (w.KIND_DELTA, 2, 0)


def test_binary_twin_is_skipped_when_nobody_can_read_it():
    hub = make_hub()
    js = hub.subscribe("m1")
    ingest(hub, "m1", 1)
    hub._flush(now=100.0)
    assert isinstance(js.q.get_nowait(), str)


def test_unencodable_delta_falls_back_to_json_for_everyone():
    """A delta without vtCounts can't be encoded. One malformed delta must not
    take down the flush for every binary client on the map."""
    hub = make_hub()
    bi = hub.subscribe("m1", binary=True)
    hub._ingest("vote_deltas:m1",
                json.dumps({"type": "delta", "rev": 1, "edges": [1]}))
    hub._flush(now=100.0)
    payload = bi.q.get_nowait()
    assert isinstance(payload, str)
    assert json.loads(payload)["type"] == "deltas"


@pytest.mark.parametrize("mode_name,mode_int", list(vs.MODE_IDS.items()))
def test_mode_survives_the_round_trip_through_the_hub(mode_name, mode_int):
    """The client drops deltas whose mode isn't its theme mode, so a mangled
    mode byte would silently discard live votes."""
    hub = make_hub()
    bi = hub.subscribe("m1", binary=True)
    hub._ingest("vote_deltas:m1", json.dumps({
        "type": "delta", "rev": 1, "m": mode_name, "vt": 15, "vtLabel": "x",
        "edges": [1], "vtCounts": {"1": [1, 0]},
    }))
    hub._flush(now=100.0)
    f = w.decode_frame(bi.q.get_nowait())
    assert f.groups[0].mode == mode_int
