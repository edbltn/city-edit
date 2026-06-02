"""
Locust load test for City Edit.

Each simulated user is a "commuter":
  1. Holds an open /ws?map=<slug> connection — this exercises the WebSocket
     fan-out path (the same broadcast cost the One Million Checkboxes write-up
     flags as the real scaling bottleneck: one vote → N deltas → N clients).
  2. Picks two nearby points, routes between them via /api/routes (OSRM), then
     votes on the returned edge IDs. Routes cluster around real neighborhoods
     of the target city so paths light up like genuine commutes.

Vote behavior is randomized:
  - route_cast        — bulk upvote (the "I travel here" cast, no direction)
  - upvote_proposal   — directional +1 on a fresh route
  - downvote_proposal — directional -1, usually reversing one of this user's
                        own earlier casts (exercises apply_directional reversal)

Each user carries a unique voter_id so directional reversal behaves like
distinct people, not one shared voter.

A custom "ws self-delta" metric records the round trip from a user's own vote
POST to seeing that vote echoed back over its WebSocket — i.e. real end-to-end
broadcast latency under load.

The map is map-driven: at startup the test fetches /api/maps/<MAP> to learn the
map's vote mode, its route vote-type labels, and the city geography. Pick the
map with the MAP env var (default "sf-bike-lanes").

Run (from repo root):
  # Web UI (charts at http://localhost:8089), against the SF map
  MAP=sf-bike-lanes locust -f loadtest/locustfile.py --host http://localhost:5001

  # Headless, 10 users for 5 minutes
  MAP=sf-bike-lanes locust -f loadtest/locustfile.py --host http://localhost:5001 \
      --users 10 --spawn-rate 2 --headless --run-time 5m

See loadtest/README.md for prod usage.
"""

import json
import math
import os
import random
import time
import uuid
from urllib.parse import urlparse

import gevent
from websocket import create_connection, WebSocketTimeoutException
from locust import HttpUser, task, between, events


# ── Which map to load-test ─────────────────────────────────────────────────

MAP_SLUG = os.environ.get("MAP", "sf-bike-lanes").strip()


# ── Geography: real neighborhood centers per city ──────────────────────────

# Routing between nearby points seeded here succeeds far more often than
# between two uniformly random bbox points (which often land in water or
# parks — SF's bbox is half ocean/bay) while still spreading votes across the
# whole street network.
CITY_SEEDS = {
    "sf": [
        (37.7944, -122.3977),  # Financial District / Ferry Building
        (37.7599, -122.4148),  # Mission District
        (37.7785, -122.4056),  # SoMa
        (37.7609, -122.4350),  # Castro
        (37.7615, -122.4683),  # Sunset (inner)
        (37.7806, -122.4644),  # Richmond (inner)
        (37.8005, -122.4090),  # North Beach
        (37.7692, -122.4481),  # Haight-Ashbury
        (37.8008, -122.4368),  # Marina
        (37.7349, -122.4324),  # Glen Park / Bernal edge
    ],
    "nyc": [
        (40.7580, -73.9855),  # Times Square (Manhattan)
        (40.7061, -74.0087),  # Financial District
        (40.8116, -73.9465),  # Harlem
        (40.6892, -73.9900),  # Downtown Brooklyn
        (40.6782, -73.9442),  # Bed-Stuy (Brooklyn)
        (40.7440, -73.9489),  # Long Island City (Queens)
        (40.7282, -73.7949),  # Flushing (Queens)
        (40.8448, -73.8648),  # South Bronx
        (40.5795, -74.1502),  # St. George (Staten Island)
        (40.6501, -73.9496),  # Crown Heights (Brooklyn)
    ],
    "chicago": [
        (41.8781, -87.6298),  # The Loop
        (41.9214, -87.6513),  # Lincoln Park
        (41.9092, -87.6776),  # Wicker Park
        (41.8500, -87.6505),  # Pilsen
        (41.7943, -87.5917),  # Hyde Park
        (41.9484, -87.6553),  # Lakeview
        (41.8847, -87.6612),  # West Loop
        (41.8019, -87.6147),  # Bronzeville
    ],
}

EARTH_KM_PER_DEG_LAT = 111.0


def nearby_point(lat: float, lon: float, max_km: float) -> tuple[float, float]:
    """Random point within max_km of (lat, lon), uniform over the disk."""
    r_km = max_km * math.sqrt(random.random())
    theta = random.uniform(0, 2 * math.pi)
    dlat = (r_km * math.cos(theta)) / EARTH_KM_PER_DEG_LAT
    dlon = (r_km * math.sin(theta)) / (
        EARTH_KM_PER_DEG_LAT * math.cos(math.radians(lat))
    )
    return (lat + dlat, lon + dlon)


# ── Map metadata, fetched once and shared across all simulated users ────────

class MapMeta:
    """Lazily-loaded, process-wide metadata for the target map."""

    loaded = False
    mode = "walk"
    vote_types = ["Improve bike lane"]  # route-type labels
    seeds = CITY_SEEDS["sf"]
    center = (37.7749, -122.4194)
    _lock = gevent.lock.Semaphore()

    @classmethod
    def ensure(cls, client):
        if cls.loaded:
            return
        with cls._lock:
            if cls.loaded:
                return
            resp = client.get(
                f"/api/maps/{MAP_SLUG}", name="/api/maps/<slug>"
            )
            data = resp.json()
            cls.mode = data.get("mode") or cls.mode
            route_types = [
                vt["label"]
                for vt in (data.get("voteTypes") or [])
                if vt.get("pointType") == "route"
            ]
            if route_types:
                cls.vote_types = route_types
            city = data.get("city") or {}
            city_id = data.get("cityId") or city.get("id")
            ctr = city.get("center") or {}
            if ctr:
                cls.center = (ctr["lat"], ctr["lon"])
            cls.seeds = CITY_SEEDS.get(city_id) or [cls.center]
            cls.loaded = True
            print(
                f"[loadtest] map={MAP_SLUG} city={city_id} mode={cls.mode} "
                f"seeds={len(cls.seeds)} vote_types={len(cls.vote_types)}"
            )


# How long after a vote we still attribute an incoming delta to it.
SELF_DELTA_WINDOW_S = 8.0


class DesirePathUser(HttpUser):
    """One simulated commuter casting realistic routed votes."""

    # Realistic pacing between votes. Bump --users (not this) to load-test.
    wait_time = between(1, 4)

    def on_start(self):
        MapMeta.ensure(self.client)
        self.voter_id = str(uuid.uuid4())
        # (edge_ids, vote_type) tuples this user has cast, for later downvotes.
        self.recent: list[tuple[list[int], str]] = []
        # (sent_time, set(edge_ids)) of the most recent vote, for latency.
        self._last_vote: tuple[float, set[int]] | None = None
        self._ws = None
        self._ws_greenlet = None
        self._stopping = False
        self._connect_ws()

    def on_stop(self):
        self._stopping = True
        if self._ws_greenlet is not None:
            self._ws_greenlet.kill(block=False)
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    # ── WebSocket ──────────────────────────────────────────────────────────

    def _ws_url(self) -> str:
        parsed = urlparse(self.host)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/ws?map={MAP_SLUG}"

    def _connect_ws(self):
        start = time.time()
        try:
            self._ws = create_connection(self._ws_url(), timeout=10)
            self._fire("WS", "connect", (time.time() - start) * 1000)
            self._ws_greenlet = gevent.spawn(self._ws_loop)
        except Exception as e:
            self._fire("WS", "connect", (time.time() - start) * 1000, exc=e)

    def _ws_loop(self):
        ws = self._ws
        ws.settimeout(1.0)
        while not self._stopping:
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue
            except Exception:
                return
            if not raw:
                return
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue

            # Count every delta we receive — this is the fan-out load.
            if msg.get("type") == "delta":
                self._fire("WS", "delta recv", 0, length=len(raw))
                self._maybe_self_delta(msg)

    def _maybe_self_delta(self, msg: dict):
        """If this delta carries one of our just-voted edges, record the
        POST → broadcast → receive round-trip latency."""
        if self._last_vote is None:
            return
        sent_at, edges = self._last_vote
        if time.time() - sent_at > SELF_DELTA_WINDOW_S:
            self._last_vote = None
            return
        delta_edges = msg.get("edges") or []
        if any(e in edges for e in delta_edges):
            self._fire("WS", "self-delta", (time.time() - sent_at) * 1000)
            self._last_vote = None

    def _fire(self, req_type, name, response_ms, length=0, exc=None):
        self.environment.events.request.fire(
            request_type=req_type,
            name=name,
            response_time=response_ms,
            response_length=length,
            exception=exc,
            context={},
        )

    # ── Routing + voting ─────────────────────────────────────────────────────

    def _route(self) -> tuple[list[int], str] | None:
        """Route between two nearby points; return (edge_ids, vote_type)."""
        center = nearby_point(*random.choice(MapMeta.seeds), max_km=2.0)
        start = nearby_point(*center, max_km=0.3)
        end = nearby_point(*center, max_km=1.2)
        with self.client.post(
            "/api/routes",
            json={
                "map": MAP_SLUG,
                "start": [start[0], start[1]],
                "end": [end[0], end[1]],
                "waypoints": [],
            },
            name="/api/routes",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
                return None
            try:
                edge_ids = resp.json().get("edge_ids") or []
            except (ValueError, KeyError):
                resp.failure("bad body")
                return None
            if not edge_ids:
                # No edges mapped (e.g. route over water) — not a server error.
                resp.success()
                return None
            resp.success()
        return edge_ids, random.choice(MapMeta.vote_types)

    def _vote(self, edge_ids: list[int], vote_type: str, direction: int | None):
        body: dict = {
            "map": MAP_SLUG,
            "edge_ids": edge_ids,
            "mode": MapMeta.mode,
            "vote_type": vote_type,
            "voter_id": self.voter_id,
        }
        if direction is not None:
            body["direction"] = direction

        if direction is None:
            name = "/api/vote [cast]"
        elif direction > 0:
            name = "/api/vote [up]"
        else:
            name = "/api/vote [down]"

        self._last_vote = (time.time(), set(edge_ids))
        with self.client.post(
            "/api/vote", json=body, name=name, catch_response=True
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status {resp.status_code}")
                return
            resp.success()

        # Remember casts/upvotes so we can downvote them later.
        if direction is None or direction > 0:
            self.recent.append((edge_ids, vote_type))
            if len(self.recent) > 20:
                self.recent.pop(0)

    # ── Tasks ────────────────────────────────────────────────────────────────

    @task(5)
    def route_cast(self):
        """The common case: travel a route and cast a bulk upvote on it."""
        routed = self._route()
        if routed:
            self._vote(routed[0], routed[1], direction=None)

    @task(2)
    def upvote_proposal(self):
        """Directional +1 on a fresh route."""
        routed = self._route()
        if routed:
            self._vote(routed[0], routed[1], direction=1)

    @task(2)
    def downvote_proposal(self):
        """Directional -1 — usually reversing one of our own earlier casts."""
        if self.recent and random.random() < 0.7:
            edge_ids, vote_type = random.choice(self.recent)
            self._vote(edge_ids, vote_type, direction=-1)
            return
        routed = self._route()
        if routed:
            self._vote(routed[0], routed[1], direction=-1)
