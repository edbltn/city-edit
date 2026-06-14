# City Edit

Crowdsourced map of how people actually travel through a city. Users route
between two points, then vote on the streets and crossings that should change —
the aggregate renders as a live heatmap of desire paths.

Live at **[cityedit.org](https://cityedit.org)**.

## Quickstart

### Prerequisites

- Redis
- Python 3.12+
- Node.js 20+
- [`uv`](https://github.com/astral-sh/uv) (`brew install uv`)
- PostgreSQL — optional; only needed for durable vote storage (see below)

```bash
# Terminal 1 — Redis
redis-server

# Terminal 2 — Flask backend
cd server
cp .env.example .env          # then edit as needed (see below)
uv venv env && source env/bin/activate
uv pip compile requirements.in -o requirements.txt
uv pip install -r requirements.txt
python app.py                 # http://localhost:5001

# Terminal 3 — frontend (hot reload)
cd client-react
npm install
npm run dev                   # http://localhost:3000
```

Open <http://localhost:3000>.

> The Python dev server (`python app.py`) is single-threaded and fine for one
> person clicking. Never use it for WebSocket or concurrent load — serve via
> gunicorn with the gevent worker (as in Docker/prod). See
> [docs/flask-considerations.md](docs/flask-considerations.md).

## Environment Variables

Copy `server/.env.example` to `server/.env` and configure:

| Variable | Description | Required |
|----------|-------------|----------|
| `REDIS_HOST` | Redis host (default: `localhost`) | No |
| `DATABASE_URL` | Postgres connection string for durable vote storage. Unset = Redis-only (the app runs, but votes are lost if Redis is flushed) | No |
| `SECRET_KEY` | Signing key for map-passcode tokens — set a stable value in prod | Prod |
| `ADMIN_TOKEN` | Guards admin endpoints (e.g. assigning vanity subdomains). Unset = those endpoints are disabled | No |

Routing is served by a self-hosted **OSRM** instance (per city), with an
in-process Python/Dijkstra router as a fallback. There is no external routing
API key to configure.

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

## Docker

### Production stack

```bash
docker compose up --build
```

Open <http://localhost:8080>.

### Development (hot reload)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Open <http://localhost:3000>.

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
