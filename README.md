# Desire Path Mapper

Crowdsourced map of how people actually travel through a city.

## Quickstart

```bash
# Terminal 1: Start Redis
redis-server

# Terminal 2: Start Flask backend
cd server
cp .env.example .env  # Add your ORS_API_KEY
python3 -m venv env
source env/bin/activate
pip install uv
uv pip compile requirements.in > requirements.txt && pip install -r requirements.txt
python app.py

# Terminal 3: Start frontend
cd client
npx serve
```

Open http://localhost:3000

## Environment Variables

Copy `server/.env.example` to `server/.env` and configure:

| Variable | Description | Required |
|----------|-------------|----------|
| `ORS_API_KEY` | OpenRouteService API key ([get free key](https://openrouteservice.org/)) | Yes |
| `REDIS_HOST` | Redis host (default: `localhost`) | No |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Client                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    Map UI (Leaflet)                  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         Nginx                               │
│              (Reverse proxy + static files)                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Flask (multiple replicas)                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   Routes     │  │   Votes      │  │  WebSocket   │      │
│  │   (ORS API)  │  │   (cast)     │  │  (map state) │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                         Redis                               │
│         (Vote storage, real-time state, pub/sub)            │
└─────────────────────────────────────────────────────────────┘
```

## Docker

```bash
docker compose up --build
```

Open http://localhost:8080

## Experimental: Hybrid Routing

The codebase includes an experimental local routing engine (`server/hybrid_router.py`) that uses OpenStreetMap data directly instead of the ORS API. This enables:

- **No API rate limits**: Route calculations happen locally
- **Hybrid routing**: Optimal routes that switch between walk/bike/drive modes
- **Custom routing logic**: Weight paths based on desire path data

### How it works

1. Downloads NYC street networks from OSM via `osmnx`
2. Builds mode-specific graphs (walk, bike, drive) with `networkx`
3. Implements A* routing with mode switching at intersections
4. Caches graphs to disk for fast startup (~2-5 min first download per mode)

### Hybrid routing algorithm

The router creates a multi-layer graph where each intersection exists in each mode layer. It runs A* across all layers, allowing mode switches at any intersection with a configurable time penalty (default 2 minutes). This naturally finds the optimal combination of modes.

### Usage

The local routing endpoint is available at `POST /api/routes/local`:

```bash
curl -X POST http://localhost:5001/api/routes/local \
  -H "Content-Type: application/json" \
  -d '{"start": [40.7128, -74.0060], "end": [40.7580, -73.9855], "mode": "hybrid"}'
```

Supported modes: `walk`, `bike`, `drive`, `hybrid`

### Memory requirements

The NYC street network graphs require approximately 2-5 GB of memory when loaded.

## Deploy to GCP

### Prerequisites

1. Apply for [Google for Nonprofits](https://www.google.com/nonprofits/) ($10k/year credits)
2. Create a GCP project
3. Install Terraform and gcloud CLI

### Initial Setup (one-time)

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Deploy infrastructure
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID
terraform init
terraform apply
```

### CI/CD Setup

Add these secrets to your GitHub repository:

- `GCP_PROJECT_ID`: Your GCP project ID
- `GCP_SA_KEY`: Service account JSON key with Cloud Run Admin + Artifact Registry Writer roles

Pushes to `main` auto-deploy to Cloud Run.
