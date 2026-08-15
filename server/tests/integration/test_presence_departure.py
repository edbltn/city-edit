"""A viewer who closes the tab must leave the room promptly.

This is the gap that let a real bug ship. `test_audience_counts.py` already
asserted that presence "retracts rather than only expiring" — but it called
`Presence.depart()` directly, so it tested the registry and not the one caller
that has to invoke it. The WS handler did not: `ws.receive()` raises
ConnectionClosed the moment the peer goes, and the inbound pump swallowed it by
SUBSTRING, because "connection closed" sat on its ignore list beside the benign
no-data cases. The loop kept spinning and `finally`'s ZREM only ran when a
keepalive send failed, up to KEEPALIVE (30s) later — measured at 30.2s. For
those 30 seconds every remaining viewer was told they had company who had left.

That is over-reporting, which is the single direction this feature cannot be
wrong in, and a green unit suite said nothing about it. So this test drives the
REAL handler over a REAL socket rather than mirroring its logic: a mirror would
have been written from the same wrong mental model, and could not catch someone
reordering the `except` clauses back.

Needs the dev stack (`make deps` + host Flask). Skips cleanly without it.
"""

import os
import time

import pytest

redis = pytest.importorskip("redis")
websocket = pytest.importorskip("websocket")

import presence


WS_URL = os.environ.get("CITYEDIT_WS_URL", "ws://localhost:5001/ws")
SLUG = os.environ.get("CITYEDIT_TEST_MAP", "nyc-walkways")

# The bug's signature: departure took KEEPALIVE seconds. Anything near that is
# the bug back. The fix measured 1.1s; 6s is slack for a loaded dev box without
# being anywhere near 30.
DEPART_DEADLINE = 6.0

# Documentation-range addresses (RFC 5737). One PER TEST, not one shared: these
# tests race each other through the presence set otherwise, and a failing test
# then leaves a lingering entry that makes the next one fail for a reason that
# has nothing to do with what it checks. Distinct from the dev helper's range
# so a running tools/dev_audience.py cannot be mistaken for this viewer.
TEST_IP_CLOSE = "203.0.113.201"
TEST_IP_HIDE = "203.0.113.202"


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
    return client


def _connect(ip: str):
    try:
        return websocket.create_connection(
            f"{WS_URL}?map={SLUG}&bin=1", timeout=10,
            header={"X-Forwarded-For": ip})
    except Exception as e:
        pytest.skip(f"dev server not reachable at {WS_URL}: {e}")


def _identity(ip: str) -> str:
    """What get_client_ip() will store for this connection."""
    import hashlib
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _present(r, ip: str) -> bool:
    return r.zscore(presence.presence_key(SLUG), _identity(ip)) is not None


def _clear(r, ip: str) -> None:
    """Start from a known-empty seat.

    Presence is shared state in Redis, so an entry left behind by a crashed
    run, a still-draining server or a second Flask on another port makes these
    tests fail — or worse, PASS — for reasons that have nothing to do with the
    handler under test. That flakiness was observed, not hypothesised.
    """
    r.zrem(presence.presence_key(SLUG), _identity(ip))


def _wait(r, ip: str, want: bool, timeout: float) -> float:
    """Seconds until presence matches `want`, or `timeout`."""
    start = time.time()
    while _present(r, ip) is not want and time.time() - start < timeout:
        time.sleep(0.2)
    return time.time() - start


def test_closing_the_tab_leaves_the_room_promptly(r):
    ip = TEST_IP_CLOSE
    _clear(r, ip)
    ws = _connect(ip)
    try:
        _wait(r, ip, True, 10)
        assert _present(r, ip), "viewer never joined the presence room"
    finally:
        ws.close()

    elapsed = _wait(r, ip, False, DEPART_DEADLINE)
    assert not _present(r, ip), (
        f"viewer still in the room {elapsed:.1f}s after closing the socket — "
        f"presence is expiring, not departing, and every other viewer on the "
        f"map is being told about company that has left")


def test_a_hidden_tab_leaves_without_closing(r):
    """The other way out of the room, on the same socket."""
    import json

    ip = TEST_IP_HIDE
    _clear(r, ip)
    ws = _connect(ip)
    try:
        _wait(r, ip, True, 10)
        assert _present(r, ip), "viewer never joined the presence room"

        ws.send(json.dumps({"type": "here", "visible": False}))
        _wait(r, ip, False, DEPART_DEADLINE)
        assert not _present(r, ip), "a backgrounded tab is still counted as looking"

        ws.send(json.dumps({"type": "here", "visible": True}))
        _wait(r, ip, True, DEPART_DEADLINE)
        assert _present(r, ip), "returning to the tab did not rejoin the room"
    finally:
        ws.close()
