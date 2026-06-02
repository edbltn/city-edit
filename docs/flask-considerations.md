# Flask Considerations — WebSocket Voting & Multi-Server

Architectural considerations for the City Edit Flask backend, focused on the
real-time vote broadcast path and running across multiple Flask servers.

**Topology today:** multi-server. Local Docker runs `flask` with
`deploy: replicas: 3` (`docker-compose.yml:28-29`) behind the nginx
`upstream flask` (`nginx.conf:14`). Prod (Cloud Run, `terraform/main.tf:247-248`)
autoscales `minScale=1` → `maxScale=3` instances, each running supervisord →
nginx → gunicorn (`--worker-class gevent --workers 1`, `deploy/supervisord.conf:15`).

**Why the WS approach works across servers:** broadcast goes through Redis
pub/sub, not process memory. A vote POST lands on any backend →
`vote_store.publish_delta()` → `redis_client.publish(channel, delta)` to the
shared Redis. Every WS connection on any backend subscribes to that map's
channel (`server/app.py:418-420`) and forwards the delta to its client
(`app.py:436-438`). Publisher and subscriber never need to share a process or
host. The one hard requirement: every server points at the **same** Redis.

Prioritized by blast radius.

---

## 🔴 1. The single shared DB connection is a latent concurrency bug

`server/database.py:21` keeps **one module-level psycopg2 connection**
(`_connection`) shared by every greenlet, `autocommit=True`. Today it doesn't
corrupt *only* because psycopg2 isn't gevent-cooperative — its blocking C calls
serialize access by accident. Two consequences:

- All DB access in a worker is **serialized**, and every query **blocks the
  gevent event loop**, stalling WS broadcasts/keepalives for the query duration.
- The moment anyone adds `psycogreen` / `set_wait_callback` to "make the DB
  async," concurrent greenlets interleave on that one connection →
  `another command is already in progress`, silent corruption.

**Fix as a bundle, not piecemeal:** introduce a gevent-safe connection pool
(psycopg2 `ThreadedConnectionPool`, one conn checked out per greenlet/request)
**and** psycogreen together. Pool-without-green still blocks the loop;
green-without-pool corrupts. Do both or neither.

## 🔴 2. Per-connection Redis pubsub caps total concurrent viewers

`server/app.py:418-420` — **each** WS connection opens its own Redis connection
+ `pubsub()`. So Redis connections = total concurrent clients across *all*
servers; you hit Redis `maxclients` and per-conn memory long before CPU.
Refactor to **one pubsub subscriber per process** that fans out in-process to
that process's local sockets (the One Million Checkboxes pattern). Then Redis
connections = number of processes, not clients.

## 🟠 3. Never run the dev server for WS/concurrent loads

`app.py:1034` `app.run()` is single-threaded Werkzeug — it collapsed at 10
held-open WS in testing (routes → 23s). Always serve via gunicorn
`--worker-class gevent` (already prod, `deploy/supervisord.conf:15`). flask-sock
requires a gevent/async-capable server. `make flask` / `app.run` is for one
human clicking, never for load.

## 🟠 4. CPU-bound work blocks every greenlet

The Python graph router (fallback when OSRM is absent) is CPU-bound
(~11ms/route). gevent has **no preemption** — that 11ms freezes all greenlets in
the worker, including WS keepalives. Prefer OSRM (network I/O yields
cooperatively) in prod; offload any heavy CPU to a thread/process pool. A
routing storm otherwise shows up as WS lag.

## 🟠 5. Prod has a hard concurrent-WS ceiling

Cloud Run: `maxScale=3` (`terraform/main.tf:248`) × `container_concurrency=320`
≈ **~960 simultaneous WebSockets max**. Each open WS occupies one concurrency
slot for its *entire* lifetime, crowding out vote POSTs on that instance.
Options: raise maxScale, lower the WS share per instance, or split WS onto a
dedicated Cloud Run service so long-lived sockets don't starve HTTP.

## 🟡 6. The shared-Redis invariant is load-bearing

Every Flask server **must** point at the *same* Redis — it carries both pub/sub
*and* the vote counts (`hincrby`). So: managed Redis in prod (single point of
failure — plan HA), and `maxmemory-policy` must **not** evict vote data
(`noeviction`, or isolate durable counts from ephemeral pub/sub). Sharding Redis
breaks cross-server pub/sub.

## 🟡 7. Keep warmup off the boot path

Graph warmup is slow; it must run in a background greenlet
(`app.py:225-231` already notes this) or it blocks worker startup past Cloud
Run's startup probe / gunicorn timeout. Preserve that. `SKIP_WARMUP=1` for fast
local/CI boot.

## 🟡 8. Smaller things

- **Per-process memory:** each worker/instance loads its own city graphs
  (`graph_registry max_loaded=3` LRU) — memory multiplies by worker × instance
  count; size Cloud Run mem accordingly.
- **No sticky sessions needed** (state's in Redis) — good — but confirm
  nginx/Cloud Run forward WS `Upgrade`/`Connection` headers.
- **SECRET_KEY** defaults to `dev-secret-change-me` (`app.py:48`) — must be set
  and stable in prod or passcode tokens break across instances/restarts.

---

## Highest-leverage next steps

#1 (DB pool + psycogreen) and #2 (single-subscriber fan-out) are the two that
matter most. The Locust load test in `loadtest/` (see
[loadtest/README.md](../loadtest/README.md)) is the harness to prove they help —
watch the `WS self-delta` and `WS delta recv` metrics before/after.
