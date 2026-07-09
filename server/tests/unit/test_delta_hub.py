"""Unit tests for delta_hub — merged fan-out of vote deltas.

These drive _ingest/_flush synchronously (no listener thread, no timing) and
pin: rev-ordered merging, inversion absorption within a window, the hole-grace
hold for out-of-order stragglers, per-map isolation, and the slow-client
overflow signal.
"""
import json
from types import SimpleNamespace

import delta_hub as dh

# Stands in for the listener thread so subscribe() never starts a real one.
_FAKE_THREAD = SimpleNamespace(is_alive=lambda: True)


def make_hub(**kw):
    hub = dh.DeltaHub(redis_factory=lambda: None, **kw)
    hub._thread = _FAKE_THREAD
    return hub


def ingest(hub, slug, rev, edges=(1,)):
    hub._ingest(f"vote_deltas:{slug}",
                json.dumps({"type": "delta", "rev": rev, "edges": list(edges)}))


def drain(sub):
    out = []
    while not sub.q.empty():
        out.append(json.loads(sub.q.get_nowait()))
    return out


def test_merges_a_window_in_rev_order():
    hub = make_hub()

    sub = hub.subscribe("m1")
    ingest(hub, "m1", 2)
    ingest(hub, "m1", 1)  # inverted arrival inside one window
    ingest(hub, "m1", 3)
    hub._flush(now=100.0)
    msgs = drain(sub)
    assert len(msgs) == 1
    assert msgs[0]["type"] == "deltas"
    assert msgs[0]["rev"] == 3
    assert [i["rev"] for i in msgs[0]["items"]] == [1, 2, 3]


def test_ignores_maps_without_subscribers():
    hub = make_hub()

    sub = hub.subscribe("m1")
    ingest(hub, "other-map", 1)
    hub._flush(now=100.0)
    assert drain(sub) == []
    assert hub._buf.get("other-map") is None


def test_hole_grace_holds_then_releases():
    hub = make_hub(hole_grace=0.3)

    sub = hub.subscribe("m1")
    ingest(hub, "m1", 1)
    hub._flush(now=100.0)
    assert drain(sub)[0]["rev"] == 1

    # rev 3 arrives but 2 hasn't — the batch holds inside the grace window...
    ingest(hub, "m1", 3)
    hub._buf_since["m1"] = 100.0
    hub._flush(now=100.1)
    assert drain(sub) == []

    # ...the straggler lands, and the next flush merges both in order.
    ingest(hub, "m1", 2)
    hub._flush(now=100.2)
    msgs = drain(sub)
    assert [i["rev"] for i in msgs[0]["items"]] == [2, 3]


def test_hole_grace_expires_and_flushes_anyway():
    hub = make_hub(hole_grace=0.3)

    sub = hub.subscribe("m1")
    ingest(hub, "m1", 1)
    hub._flush(now=100.0)
    drain(sub)

    ingest(hub, "m1", 5)  # rev bumped without deltas 2-4 (e.g. manual INCR)
    hub._buf_since["m1"] = 100.0
    hub._flush(now=100.5)  # grace expired
    msgs = drain(sub)
    assert len(msgs) == 1
    assert [i["rev"] for i in msgs[0]["items"]] == [5]
    # the client sees first-item rev 5 vs its last-seen 1 -> gap -> refetch


def test_unsubscribe_stops_delivery_and_clears_state():
    hub = make_hub()

    sub = hub.subscribe("m1")
    hub.unsubscribe("m1", sub)
    ingest(hub, "m1", 1)
    hub._flush(now=100.0)
    assert drain(sub) == []
    assert hub.subscriber_count() == 0


def test_overflow_sets_flag_instead_of_blocking():
    hub = make_hub()

    sub = hub.subscribe("m1")
    for i in range(dh.QUEUE_MAX):
        ingest(hub, "m1", i + 1)
        hub._flush(now=100.0 + i)
    assert not sub.overflowed.is_set()
    ingest(hub, "m1", dh.QUEUE_MAX + 1)
    hub._flush(now=999.0)
    assert sub.overflowed.is_set()


def test_two_maps_are_isolated():
    hub = make_hub()

    s1 = hub.subscribe("m1")
    s2 = hub.subscribe("m2")
    ingest(hub, "m1", 1)
    ingest(hub, "m2", 7)
    hub._flush(now=100.0)
    assert [i["rev"] for i in drain(s1)[0]["items"]] == [1]
    assert [i["rev"] for i in drain(s2)[0]["items"]] == [7]
