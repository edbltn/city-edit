FROM python:3.13-slim

WORKDIR /app

# Unbuffered so Cloud Run job logs stream live; no .pyc litter in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Stdlib-only pipeline — no requirements to install.
COPY fetch_latest.py import_to_map.py weekly_import.py ./

# One-shot batch job (fetch → plan → cast → exit); nothing to health-probe.
HEALTHCHECK NONE

CMD ["python", "weekly_import.py"]
