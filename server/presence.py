"""Who is looking at this map right now.

The product here is COMMON KNOWLEDGE — "we are both looking at this, and we
both know we are" — so the only number worth showing is one nobody has a reason
to doubt. That constraint drives every choice below, and it points the opposite
way from the usual analytics instinct: when in doubt, count LESS.

Identity
--------
Presence is keyed on the same COUNTING identity votes use (vote_identity.py):
the hashed client IP, falling back to a per-connection token only when there is
no IP at all. It deliberately does NOT use `device_id`.

That is not a stylistic choice. `device_id` is the sha256 of a localStorage
UUID and localStorage on this app's traffic is *actively fragmenting* — one
iPhone produced six voter_ids in three minutes on prod (2026-08-13, see
vote_identity.py). A presence count keyed on device_id would report that one
person as six people looking at the map together. That is exactly the lie that
destroys the ritual: the number goes UP as identity degrades, so the worse our
identity gets, the more impressive the product looks. Nobody would be able to
tell the difference from the outside, which is why it has to be structurally
impossible rather than monitored.

Keying on the IP hash inverts the failure. The two ways it can be wrong:

  · Many people, one IP (household, office, school, carrier NAT) → they count
    as ONE. The number is too LOW. Safe: the ritual survives a modest count,
    it does not survive an inflated one.
  · One person, several IPs inside the 35s window (wifi → LTE handoff, a VPN
    flipping egress) → they briefly count as two. This is the only inflation
    path left, it is bounded by the staleness window, and it needs a NETWORK
    change rather than a page reload — so the device_id churn that is currently
    under investigation cannot trigger it. Reload the tab a hundred times and
    the presence count does not move.

Neither key is a person. This is the honest ceiling of what we can claim, and
the UI copy is written to stay inside it.

Why a sorted set and not a HyperLogLog
--------------------------------------
HLL is the right structure for the cumulative view counts (view_counts.py) and
the wrong one here: presence has to go DOWN. A HLL cannot forget a member, so a
map that 40 people visited over an afternoon would read "40 people are here
now" forever — monotonically inflating, the failure mode above. A ZSET of
identity → last-heartbeat timestamp both expires (score below the cutoff) and
retracts (ZREM on disconnect), and it stays small: its cardinality is people
currently on one map, not people ever.

Only VISIBLE tabs are counted. The client reports visibilitychange over the
same socket, and a hidden tab is removed immediately. "You are looking at this
with 3 other people" has to mean *looking*, not "has a socket open" — and this
app throttles background tabs hard enough that a backgrounded viewer is doing
precisely nothing. This is also why departure is explicit (ZREM) rather than
left to expiry: a count that stays high for 35 seconds after everyone leaves is
over-reporting, which is the one direction we do not accept.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# How long a heartbeat keeps an identity "here". Must exceed HEARTBEAT_INTERVAL
# by enough to ride out a slow flush or a GC pause without flickering someone
# out of the room.
PRESENCE_TTL = 35

# How often a live connection refreshes its score.
HEARTBEAT_INTERVAL = 10

# Per-process memo so that N sockets on one map share ONE Redis read per tick
# instead of issuing N. Short enough that arrivals/departures feel immediate.
COUNT_CACHE_TTL = 2.0

# How often a connection re-checks the count. The push itself is edge-triggered
# (only sent when the number actually changes), so this is a poll interval, not
# a send interval.
PUSH_INTERVAL = 2.0

# Garbage-collect the key on a map nobody visits, without ever expiring it out
# from under a live room.
KEY_TTL = PRESENCE_TTL * 8


def presence_key(slug: str) -> str:
    return f"pres:{slug}"


class Presence:
    """Cross-instance live-viewer registry for every map this process serves.

    One instance per Flask process, sharing the app's Redis client. All state
    that matters lives in Redis so the count is correct across replicas; the
    per-process dicts are only a local refcount (so a second tab from the same
    person doesn't ZREM the first) and a read memo.
    """

    def __init__(self, redis_client):
        self._r = redis_client
        self._lock = threading.Lock()
        # slug -> identity -> how many VISIBLE sockets this process holds for
        # that identity. Needed so closing one of a person's two tabs doesn't
        # remove them from the room.
        self._local: dict[str, dict[str, int]] = {}
        # slug -> (monotonic deadline, count)
        self._cache: dict[str, tuple[float, int]] = {}

    # ── Membership ──────────────────────────────────────────────────────────

    def arrive(self, slug: str, identity: str) -> None:
        """This identity now has one more visible socket on this map."""
        with self._lock:
            per_map = self._local.setdefault(slug, {})
            per_map[identity] = per_map.get(identity, 0) + 1
            self._cache.pop(slug, None)
        self.heartbeat(slug, identity)

    def depart(self, slug: str, identity: str) -> None:
        """One visible socket gone. Removes the identity when it was the last
        one THIS process holds.

        A sibling replica may still hold a live socket for the same identity;
        that replica's next heartbeat (≤10s) puts them back. The transient dip
        under-reports for a few seconds, which is the direction we tolerate.
        """
        with self._lock:
            per_map = self._local.get(slug)
            if not per_map:
                return
            self._cache.pop(slug, None)
            remaining = per_map.get(identity, 0) - 1
            if remaining > 0:
                per_map[identity] = remaining
                return
            per_map.pop(identity, None)
            if not per_map:
                self._local.pop(slug, None)
        try:
            self._r.zrem(presence_key(slug), identity)
        except Exception as e:
            logger.debug(f"[PRESENCE] zrem failed for {slug}: {e}")

    def heartbeat(self, slug: str, identity: str) -> None:
        """Refresh this identity's freshness. Wall-clock scores on purpose —
        several Flask replicas write into the same ZSET, so a monotonic clock
        would make their entries incomparable."""
        try:
            key = presence_key(slug)
            pipe = self._r.pipeline()
            pipe.zadd(key, {identity: time.time()})
            pipe.expire(key, KEY_TTL)
            pipe.execute()
        except Exception as e:
            logger.debug(f"[PRESENCE] heartbeat failed for {slug}: {e}")

    # ── Counting ────────────────────────────────────────────────────────────

    def count(self, slug: str) -> int:
        """Distinct identities visibly on this map, INCLUDING the caller.

        Prunes stale entries on the way through — presence has no other
        reaper, and pruning at read time means the number is never computed
        over entries we already know are dead. Returns a memoized value for
        COUNT_CACHE_TTL so a crowded map costs one Redis round-trip per tick
        rather than one per socket.

        `arrive`/`depart` drop the memo, so the two changes this process makes
        itself are never served stale. Without that, joining a map you are
        alone on showed "0 others" for two seconds, and — the direction that
        actually matters — a room someone had just left kept reporting them.
        """
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(slug)
            if cached and cached[0] > now:
                return cached[1]
        try:
            key = presence_key(slug)
            pipe = self._r.pipeline()
            pipe.zremrangebyscore(key, "-inf", time.time() - PRESENCE_TTL)
            pipe.zcard(key)
            n = int(pipe.execute()[-1])
        except Exception as e:
            logger.debug(f"[PRESENCE] count failed for {slug}: {e}")
            # Redis being unreachable must not invent company. 0 reads out as
            # "say nothing" at the client's threshold.
            return 0
        with self._lock:
            self._cache[slug] = (now + COUNT_CACHE_TTL, n)
        return n
