# Desire Path Mapper

Crowdsourced map of how people actually travel through a city.

## Quickstart

### Prerequisites
- Redis
- Python 3.12+
- Node.js 20+

```bash
# Terminal 1: Start Redis
redis-server

# Terminal 2: Start Flask backend
cd server
cp .env.example .env  # Add your ORS_API_KEY
python3 -m venv env
source env/bin/activate
pip install uv
uv pip compile requirements.in -o requirements.txt && uv pip install -r requirements.txt
python app.py

# Terminal 3: Start frontend (auto-reloads on file changes)
cd client-react
npm run dev
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

### Production
```bash
docker compose up --build
```
Open http://localhost:8080

### Development (with hot reload)
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```
Open http://localhost:3000

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

---

## Roam: Multi-Modal Routing Engine

**Roam** ("Freedom to Roam") is our custom routing algorithm that treats the city as unified public space. Unlike the hybrid router above, Roam:

- **Ignores one-way restrictions**: All edges become bidirectional
- **Zero mode-switch penalty**: Seamlessly transitions between walk/bike/drive
- **Tile-based caching**: Pre-computes paths between geographic tiles for sub-second routing
- **Daily OSM refresh**: Automatically detects and invalidates changed tiles

### Design Philosophy

Roam operates under the hypothesis that all walkable, bikeable, and driveable surfaces are public land. This "freedom to roam" approach helps identify where infrastructure improvements (footbridges, bike lanes, pedestrian crossings) would have the greatest impact on desire paths.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Tile system** | Lat-lon degree grid | Human-readable keys; easy to debug (you can tell where `40.71_-74.00` is by looking at it) |
| **Tile size** | 0.005° (~500m at NYC latitude) | Balance between cache granularity and overhead |
| **Portal distance** | 0.001° (~111m) | Matches current hybrid router's 100m threshold |
| **Scope** | NYC only | Richest OSM data quality; proves concept before expanding |

### How Tiles Work

Each tile is identified by its **southwest corner** coordinates, truncated to 3 decimal places:

```
Tile "40.710_-74.010" covers:
  SW corner: (40.710, -74.010)
  NE corner: (40.715, -74.005)
  Size: 0.005° × 0.005° ≈ 555m × 400m at NYC latitude
```

This convention means:
- Tile IDs are human-readable (you know `40.75_-73.99` is Midtown)
- Simple math: `tile_lat = floor(lat / 0.005) * 0.005`
- Easy neighbor lookup: north tile is `tile_lat + 0.005`

### API

```bash
# Roam routing (finds optimal multi-modal path)
curl -X POST http://localhost:5001/api/routes/roam \
  -H "Content-Type: application/json" \
  -d '{"start": [40.7128, -74.0060], "end": [40.7580, -73.9855]}'
```

Response includes mode annotations per segment, allowing the client to render each segment in a different color.

For detailed implementation documentation, see [docs/roam.md](docs/roam.md).

---

## Deploy to GCP

See [docs/gcp-deployment.md](docs/gcp-deployment.md) for detailed deployment documentation.

### Quick Deploy

```bash
# Build and deploy (from project root)
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

### Environment Variables

After deployment, set environment variables:

```bash
gcloud run services update desire-path-mapper-prod \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey \
  --set-env-vars="REDIS_HOST=10.63.107.3,REDIS_PORT=6379,ORS_API_KEY=your-key-here"
```

**Important**: Make sure the ORS_API_KEY has no trailing newline or whitespace.

### View Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=desire-path-mapper-prod" \
  --project=google-mpf-ywspom2sxeey \
  --limit=30 \
  --format="table(timestamp,textPayload)"
```

### Live URLs

- **Cloud Run**: https://desire-path-mapper-prod-katze52zaq-uc.a.run.app
- **Custom Domain**: https://demo.sphericalharmonics.org

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
