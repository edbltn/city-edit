# Python app with osmnx + rustworkx routing
FROM python:3.13-slim

WORKDIR /app

# Unbuffered so logs stream to docker logs; no .pyc litter in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install Python dependencies
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy Python source files
COPY *.py ./

# Create osm_data directory (graphs will be built on first use or mounted)
RUN mkdir -p osm_data

EXPOSE 5001

# Health checking lives at the compose level: this image is shared by the
# `flask` service (curl /health) and the `osm-refresh` sidecar, whose command
# runs no server — an image-level probe would pin the sidecar "unhealthy".
HEALTHCHECK NONE

CMD ["python", "app.py"]
