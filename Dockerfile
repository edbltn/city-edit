# Stage 1: Build React
FROM node:20-alpine AS client-builder
WORKDIR /app
COPY client-react/package.json client-react/package-lock.json ./
RUN npm ci
COPY client-react/ ./
RUN npm run build

# Stage 2: Python + Nginx
FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1

# brotli modules: ~15-20% smaller than gzip on the big topology/JSON payloads.
RUN apt-get update && apt-get install -y \
    nginx libnginx-mod-http-brotli-filter libnginx-mod-http-brotli-static \
    supervisor curl \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt gunicorn

COPY server/*.py ./

# Fixed station-network data (e.g. data/ebike_stations.json) read at runtime by
# graph_registry.load_station_graph — not generated in the image, so copy it in.
COPY server/data/ ./data/

# Build routing graphs during image build (bakes per-city graphs into image).
# Separate processes so each city's osmnx memory is freed before the next.
RUN mkdir -p osm_data && \
    python refresh_osm.py --region nyc --force && \
    python refresh_osm.py --region sf --force && \
    python refresh_osm.py --region chicago --force && \
    python refresh_osm.py --region dc --force && \
    rm -f osm_data/*/source.osm.pbf

COPY --from=client-builder /app/dist /var/www/html/

# Build per-city PMTiles at full resolution into osm_data/<city>/graph.pmtiles.
# Same canonical builder the local stack runs (just a denser zoom profile), so the
# background network is identical to dev — only crisper. Served at
# /api/tiles/<city>/graph.pmtiles, which nginx aliases straight to osm_data.
RUN python build_pmtiles.py --all --profile full

COPY deploy/nginx-cloudrun.conf /etc/nginx/nginx.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY deploy/healthcheck.sh /app/deploy/healthcheck.sh
RUN chmod +x /app/deploy/healthcheck.sh

EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
