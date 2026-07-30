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
RUN apt-get update && apt-get install -y --no-install-recommends \
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
# build_graph also emits walk_graph_arrays.npz per city — the ONLY graph
# artifact the runtime loads (the pkl is build-time-only input for block
# bakes and array conversion). Test cities (test-cp/test-mid) come from local
# extracts, not refresh_osm — ship them via the arrays-overlay staging dir.
# NOTE: a full rebuild produces NEW edge ids → votes need the graph_reload
# resnap AND blocks need a re-bake against the new graphs before serving.
RUN mkdir -p osm_data && \
    python refresh_osm.py --region nyc --force && \
    python refresh_osm.py --region sf --force && \
    python refresh_osm.py --region chicago --force && \
    python refresh_osm.py --region dc --force && \
    python refresh_osm.py --region philly --force && \
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

# Status-only probe (Cloud Run ignores Docker HEALTHCHECK; deploy/healthcheck.sh
# under supervisor does the actual self-healing). Long start period + high
# retries: the background graph warmup can freeze the gevent hub for minutes,
# during which /health can't respond — mirror healthcheck.sh's 15×60s tolerance.
HEALTHCHECK --interval=60s --timeout=10s --start-period=900s --retries=15 \
  CMD curl -fsS http://127.0.0.1:5001/health || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
