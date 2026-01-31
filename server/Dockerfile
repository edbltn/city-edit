# Python app with osmnx + rustworkx routing
FROM python:3.13-slim

WORKDIR /app

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

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

CMD ["python", "app.py"]
