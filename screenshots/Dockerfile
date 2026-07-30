FROM python:3.13-slim

WORKDIR /app

# Unbuffered so Cloud Run job logs stream live; no .pyc litter in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt \
    && playwright install --with-deps chromium

COPY capture.py .

# One-shot batch job (capture → upload → exit); nothing to health-probe.
HEALTHCHECK NONE

CMD ["python", "capture.py", "--output", "/tmp/previews"]
