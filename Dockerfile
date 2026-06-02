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

RUN apt-get update && apt-get install -y nginx supervisor curl && rm -rf /var/lib/apt/lists/*

COPY server/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt gunicorn

COPY server/*.py ./

# Build routing graphs during image build (bakes per-city graphs into image).
# Separate processes so each city's osmnx memory is freed before the next.
RUN mkdir -p osm_data && \
    python refresh_osm.py --region nyc --force && \
    python refresh_osm.py --region sf --force && \
    python refresh_osm.py --region chicago --force

COPY --from=client-builder /app/dist /var/www/html/

# Build PMTiles from the NYC walk graph (served at /api/tiles/nyc/graph.pmtiles;
# SF/Chicago render from the graph API like local, which has no PMTiles for them).
RUN python build_pmtiles.py -o osm_data/nyc/graph.pmtiles && \
    mkdir -p /var/www/html/tiles && \
    cp osm_data/nyc/graph.pmtiles /var/www/html/tiles/graph.pmtiles

COPY deploy/nginx-cloudrun.conf /etc/nginx/nginx.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY deploy/healthcheck.sh /app/deploy/healthcheck.sh
RUN chmod +x /app/deploy/healthcheck.sh

EXPOSE 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
