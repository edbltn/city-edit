# Load test

A [Locust](https://locust.io) load test that simulates concurrent commuters
casting and downcasting votes. Each simulated user:

- holds an open `/ws` connection (exercises the WebSocket broadcast fan-out —
  the real scaling bottleneck for this kind of app), and
- routes between two nearby points via `/api/routes` (OSRM), then votes on the
  returned edge IDs — so paths light up like genuine commutes, clustered around
  real NYC neighborhoods across all five boroughs.

Vote behavior is randomized across bulk casts (`/api/vote [cast]`), directional
upvotes (`[up]`), and downvotes (`[down]`, usually reversing the user's own
earlier casts). Each user has a unique `voter_id` so reversal behaves like
distinct people.

## Setup

```bash
cd loadtest
python3 -m venv env && source env/bin/activate
pip install -r requirements.txt        # or: uv pip install -r requirements.txt
```

## Run it — watch votes pop up in real time

Start the app (`make docker` → http://localhost:8080), open it in your browser,
then in another terminal:

```bash
# From the repo root.

# Web UI with live charts (RPS, latency, failures) at http://localhost:8089.
# Set users / spawn rate in the form, hit Start, watch the map bloom.
make loadtest-local

# Or headless: 10 users, spawn 2/s, run for 5 minutes.
make loadtest-local USERS=10 RATE=2 TIME=5m
```

Equivalent raw commands:

```bash
locust -f loadtest/locustfile.py --host http://localhost:8080
locust -f loadtest/locustfile.py --host http://localhost:8080 \
    --users 10 --spawn-rate 2 --headless --run-time 5m
```

> Point `--host` at `http://localhost:5001` instead if you're running Flask
> directly (`make flask`) rather than the full Docker stack behind nginx.

## Run it against prod

```bash
make loadtest-prod                                   # web UI
make loadtest-prod USERS=25 RATE=5 TIME=3m           # headless

# Or directly, against any deployed URL (note: wss is derived from https):
locust -f loadtest/locustfile.py \
    --host https://desire-path-mapper-katze52zaq-uc.a.run.app \
    --users 25 --spawn-rate 5 --headless --run-time 3m
```

The WebSocket URL is derived from `--host`: `http://` → `ws://`,
`https://` → `wss://`, both at `/ws`.

## Two separated concerns

- **Behavior** lives in `DesirePathUser` (`locustfile.py`) — one realistic user.
- **Load** is just how many of them you spawn: bump `--users` / `--spawn-rate`.
  The same file scales from 1 user (eyeball the map) to hundreds (stress test).

## Reading the results

Alongside the normal HTTP rows you'll see custom `WS` metrics:

- `WS / connect` — WebSocket handshake time.
- `WS / delta recv` — every delta this fleet received (the fan-out volume).
- `WS / self-delta` — round trip from a user's own vote POST to seeing it echoed
  back over its socket: real end-to-end broadcast latency under load.
