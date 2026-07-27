# City Edit

Crowdsourced map of how people actually travel through a city. Users route
between two points, then vote on the streets and crossings that should change —
the aggregate renders as a live heatmap of desire paths.

Live at **[cityedit.org](https://cityedit.org)**.

## Quickstart

The dev loop is a hybrid: the stateful services (**Redis, Postgres, OSRM**) run
in Docker, while **Flask and Vite run on the host** so code edits are live —
Vite hot-reloads the client instantly, and Flask restarts in seconds without a
container rebuild.

### Prerequisites

- Docker (with Compose v2)
- Python 3.12+
- Node.js 20+
- [`uv`](https://github.com/astral-sh/uv) (`brew install uv`)

### One-time setup

```bash
# Backend venv + config
cd server
cp .env.example .env          # note: server/.env.example — it lives in server/, not the repo root
uv venv env && source env/bin/activate
uv pip install -r requirements.txt
cd ..

# Frontend deps
cd client-react && npm install && cd ..

# Per-city walk graphs + background tiles (skips anything already built)
make tiles
```

### Daily loop

```bash
make dev
```

That starts the Docker services and runs Flask + Vite on the host. Open
<http://localhost:3000>. Or run the three pieces yourself:

```bash
# 1 — backing services in Docker (Redis :6379, Postgres :5432, OSRM → host :5005)
docker compose -f docker-compose.yml -f docker-compose.osrmport.yml up -d redis postgres osrm

# 2 — Flask on the host
cd server && source env/bin/activate && python app.py    # http://localhost:5001

# 3 — Vite on the host (proxies /api + /ws to :5001)
cd client-react && npm run dev                           # http://localhost:3000
```

Notes:

- **Vite hot-reloads** client edits; **Flask does not auto-reload** — restart it
  after any `server/*.py` change. `SKIP_PREWARM=1 python app.py` restarts fast
  (graphs load lazily on first request).
- OSRM is published on host port **5005**, not 5000 — macOS AirPlay squats
  `:5000`. `server/.env` (`OSRM_URL`) already points there.
- The Python dev server (`python app.py`) is single-threaded and fine for one
  person clicking. Never use it for WebSocket or concurrent load — serve via
  gunicorn with the gevent worker (as in Docker/prod). See
  [docs/flask-considerations.md](docs/flask-considerations.md).
- Coming from the old host-Redis workflow? Stop the host `redis-server` first —
  the Docker Redis needs `:6379`. Redis is the serving layer only; durable votes
  live in Postgres, and `vote_migration.rebuild_redis_for_map` repopulates a
  map's Redis state from the DB if the fresh instance comes up empty.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Client (SPA)                        │
│              Leaflet map + canvas heatmap overlay           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                          Nginx                              │
│              (reverse proxy + static SPA shell)             │
└─────────────────────────────────────────────────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  Flask (×N)    │  │      OSRM        │  │     Postgres     │
│  routes·votes· │─▶│  (routing, one   │  │  (durable vote   │
│  WebSocket     │  │   dataset/city)  │  │   rows)          │
└────────────────┘  └──────────────────┘  └──────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────┐
│                          Redis                              │
│         (vote counts, real-time state, pub/sub fan-out)     │
└─────────────────────────────────────────────────────────────┘
```

How votes flow from a click to every client's heatmap — identity, codec,
storage, reconciliation, migration — is documented in
[docs/voting-architecture.md](docs/voting-architecture.md) (the source of
truth). How the app picks *which map* to show (slug / subdomain / apex) is in
[docs/url-routing.md](docs/url-routing.md).

## Configuration

`server/.env` (copied from `.env.example` in the quickstart) works as-is
against the Docker services — nothing to edit for local dev:

| Variable | Description | Required |
|----------|-------------|----------|
| `REDIS_HOST` | Redis host (default: `localhost`) | No |
| `DATABASE_URL` | Postgres connection string for durable vote storage. Unset = Redis-only (the app runs, but votes are lost if Redis is flushed) | No |
| `OSRM_URL` | Merged-OSRM base URL. Local dev: `http://localhost:5005` (the Docker port mapping) | No |
| `SECRET_KEY` | Signing key for map-passcode tokens — set a stable value in prod | Prod |
| `ADMIN_TOKEN` | Guards admin endpoints (e.g. assigning vanity subdomains). Unset = those endpoints are disabled | No |

Routing is served by the self-hosted **OSRM** instance, with an in-process
Python/Dijkstra router as a fallback. There is no external routing API key.

## Everything in Docker (alternative)

If you don't want a host Python/Node toolchain, the whole stack runs in
Compose — at the cost of the fast edit loop:

```bash
# Production-shaped stack (nginx + 3 Flask replicas) → http://localhost:8080
docker compose up --build

# Dev mode (Vite in a container, hot reload) → http://localhost:3000
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## Testing

```bash
make test            # frontend (vitest) + backend (pytest)
make test-frontend
make test-backend
```

See [docs/testing.md](docs/testing.md) for the full taxonomy and the stateful
load test.

## Deploy to GCP

The app runs on Cloud Run (Flask + OSRM), Memorystore Redis, Cloud SQL Postgres,
and Artifact Registry, built by Cloud Build. Full guide:
[docs/gcp-deployment.md](docs/gcp-deployment.md).

> **⚠️ Always back up the prod DB locally before deploying** — every time, no
> exceptions. See [Database Access & Backups](docs/gcp-deployment.md#database-access--backups).

```bash
# Build and deploy from the repo root
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

Infrastructure (Redis, Postgres, services, custom domains) is managed in
`terraform/`. Pushes to `main` auto-deploy via Cloud Build.

### View logs

```bash
gcloud run services logs read desire-path-mapper \
  --project=google-mpf-ywspom2sxeey --region=us-central1 --limit=50
```

> **Note on naming:** the product is *City Edit* (cityedit.org), but the GCP
> resources keep their original `desire-path-*` names (e.g. the Cloud Run
> service is `desire-path-mapper`). Those are real resource IDs — don't expect
> them to say "cityedit".
</content>
