"""Per-process fan-out hub for vote deltas — one Redis pubsub listener,
coalesced delivery to every connected WebSocket client.

Before this, EVERY WebSocket client held its own Redis pubsub connection and
its own 10Hz polling thread, and every vote's delta was sent to every client
individually — O(votes x clients) sends plus a thread and a connection per
client (findings #4 of changelog/2026-07-08-agent-load-test.html). The hub
inverts that: one listener thread receives each map's deltas once, buffers
them briefly, and flushes a merged {"type": "deltas", "rev": <max>,
"items": [...]} message to each subscribed client queue at FLUSH_INTERVAL.
Clients apply items in rev order; merged counts are authoritative SETs, so
coalescing is lossless.

Ordering: votes claim their rev via INCR then publish, so two concurrent
casts can publish out of rev order. Within a flush window the sort fixes any
inversion; a batch whose lowest rev leaves a hole above the last flushed rev
is HELD for HOLE_GRACE to let the straggler land — after that it flushes
anyway, and the client's gap detection (which compares the first delivered
rev against its own last-seen rev) decides whether to refetch. Revision bumps
that never publish a delta (manual `INCR vote_rev:<slug>`, hydration) surface
the same way: as a gap the client heals with one refetch.

A slow client whose queue overflows gets its `overflowed` flag set instead of
silently losing deltas; the WS handler closes the socket, and the client's
reconnect + gap-refetch resynchronizes it.
"""

import json
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

FLUSH_INTERVAL = 0.1   # seconds between merged flushes per map
HOLE_GRACE = 0.3       # how long to hold a batch that leaves a rev hole
QUEUE_MAX = 256        # merged payloads a client may lag before resync


class Subscriber:
    """One connected WebSocket client: its payload queue + overflow signal."""

    __slots__ = ("q", "overflowed")

    def __init__(self):
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.overflowed = threading.Event()


class DeltaHub:
    def __init__(self, redis_factory,
                 flush_interval: float = FLUSH_INTERVAL,
                 hole_grace: float = HOLE_GRACE):
        self._redis_factory = redis_factory
        self._flush_interval = flush_interval
        self._hole_grace = hole_grace
        self._lock = threading.Lock()
        self._subs: dict[str, set[Subscriber]] = {}
        self._buf: dict[str, list[dict]] = {}
        self._buf_since: dict[str, float] = {}
        self._last_flushed: dict[str, int] = {}
        self._thread: threading.Thread | None = None

    # ── Subscription (called by WS handlers) ────────────────────────────────

    def subscribe(self, slug: str) -> Subscriber:
        sub = Subscriber()
        with self._lock:
            self._subs.setdefault(slug, set()).add(sub)
            self._ensure_thread()
        return sub

    def unsubscribe(self, slug: str, sub: Subscriber) -> None:
        with self._lock:
            subs = self._subs.get(slug)
            if subs is None:
                return
            subs.discard(sub)
            if not subs:
                del self._subs[slug]
                self._buf.pop(slug, None)
                self._buf_since.pop(slug, None)

    def subscriber_count(self, slug: str | None = None) -> int:
        with self._lock:
            if slug is not None:
                return len(self._subs.get(slug, ()))
            return sum(len(s) for s in self._subs.values())

    # ── Listener thread ──────────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="delta-hub")
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self._listen_loop()
            except Exception:
                logger.exception("[DELTAHUB] listener died; reconnecting in 1s")
                time.sleep(1)

    def _listen_loop(self) -> None:
        r = self._redis_factory()
        ps = r.pubsub(ignore_subscribe_messages=True)
        ps.psubscribe("vote_deltas:*")
        logger.info("[DELTAHUB] subscribed to vote_deltas:*")
        next_flush = time.monotonic() + self._flush_interval
        while True:
            timeout = max(0.01, next_flush - time.monotonic())
            msg = ps.get_message(timeout=timeout)
            if msg and msg.get("type") == "pmessage":
                self._ingest(msg["channel"], msg["data"])
            now = time.monotonic()
            if now >= next_flush:
                self._flush(now)
                next_flush = now + self._flush_interval

    # ── Core (synchronous; unit-testable without the thread) ────────────────

    def _ingest(self, channel: str, data) -> None:
        slug = channel.split(":", 1)[1] if ":" in channel else None
        if not slug:
            return
        try:
            delta = json.loads(data)
        except (ValueError, TypeError):
            return
        with self._lock:
            if slug not in self._subs:
                return  # no local client cares about this map
            buf = self._buf.setdefault(slug, [])
            if not buf:
                self._buf_since[slug] = time.monotonic()
            buf.append(delta)

    def _flush(self, now: float) -> None:
        with self._lock:
            for slug in [s for s, b in self._buf.items() if b]:
                buf = self._buf[slug]
                buf.sort(key=lambda d: d.get("rev", 0))
                last = self._last_flushed.get(slug)
                min_rev = buf[0].get("rev", 0)
                if (last is not None and min_rev > last + 1
                        and now - self._buf_since.get(slug, now) < self._hole_grace):
                    continue  # hold for an out-of-order straggler
                self._buf[slug] = []
                self._last_flushed[slug] = max(
                    buf[-1].get("rev", 0), last or 0)
                payload = json.dumps(
                    {"type": "deltas", "rev": buf[-1].get("rev", 0),
                     "items": buf})
                for sub in self._subs.get(slug, ()):
                    try:
                        sub.q.put_nowait(payload)
                    except queue.Full:
                        # Too far behind to catch up delta-by-delta: signal the
                        # WS handler to drop the socket; reconnect + the
                        # client's gap refetch resync it.
                        sub.overflowed.set()
