# City Edit - Makefile

# Configuration
PROJECT_ID := google-mpf-ywspom2sxeey
REGION := us-central1
SERVICE := desire-path-mapper
REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/app
SCREENSHOT_REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/screenshot

.PHONY: help dev deps deps-down flask client graphs tiles docker push deploy test test-frontend test-backend test-cloud tf-init tf-plan tf-apply logs clean monitoring monitoring-down loadtest-local loadtest-prod loadtest-verify

# Python in the server venv (no activation needed inside make recipes)
PY := env/bin/python

# Load test (override on the command line, e.g. USERS=25 RATE=5 TIME=3m)
PROD_URL := https://desire-path-mapper-katze52zaq-uc.a.run.app
USERS :=
RATE :=
TIME :=
# When USERS is set, run headless for the given run-time; otherwise open the web UI.
LOCUST_FLAGS := $(if $(USERS),--headless --users $(USERS) --spawn-rate $(or $(RATE),2)$(if $(TIME), --run-time $(TIME)),)

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Local Development (hybrid: Docker deps + host Flask/Vite):"
	@echo "  dev          Build graphs+tiles if needed, start Docker deps, run flask+client on host"
	@echo "  deps         Start backing services in Docker (redis, postgres, osrm on :5005)"
	@echo "  deps-down    Stop the Docker backing services"
	@echo "  graphs       Build any missing per-city walk graphs"
	@echo "  tiles        Build per-city PMTiles (dev profile; matches prod content)"
	@echo "  flask        Start Flask backend on the host"
	@echo "  client       Start frontend dev server on the host"
	@echo ""
	@echo "Docker (full stack, alternative to the hybrid loop):"
	@echo "  docker       Run with Docker Compose"
	@echo "  docker-dev   Run with Docker Compose (dev mode)"
	@echo ""
	@echo "Deployment:"
	@echo "  push         Commit and push to git"
	@echo "  deploy       Build and deploy to Cloud Run"
	@echo "  test-cloud   Test the cloud instance"
	@echo ""
	@echo "Terraform:"
	@echo "  tf-init      Initialize Terraform"
	@echo "  tf-plan      Preview infrastructure changes"
	@echo "  tf-apply     Apply infrastructure changes"
	@echo ""
	@echo "Monitoring:"
	@echo "  monitoring       Start Grafana dashboard (localhost:3001)"
	@echo "  monitoring-down  Stop Grafana dashboard"
	@echo ""
	@echo "Load testing (USERS=10 RATE=2 TIME=5m for headless; omit USERS for web UI):"
	@echo "  loadtest-local   Simulate concurrent voters against localhost:8080"
	@echo "  loadtest-prod    Simulate concurrent voters against prod"
	@echo ""
	@echo "Screenshots:"
	@echo "  screenshot-build  Build screenshot container image"
	@echo "  screenshot-push   Push screenshot image to Artifact Registry"
	@echo "  screenshot-run    Trigger screenshot Cloud Run Job"
	@echo ""
	@echo "Utilities:"
	@echo "  clean        Stop all local services"
	@echo "  logs         Tail Cloud Run logs"

# Local Development
# Backing services in Docker; the osrmport override publishes OSRM on host :5005
# (not :5000 — macOS AirPlay squats it) for the host-run Flask.
deps:
	docker compose -f docker-compose.yml -f docker-compose.osrmport.yml up -d redis postgres osrm

deps-down:
	docker compose stop redis postgres osrm

flask:
	cd server && source env/bin/activate && python app.py

client:
	cd client-react && npm run dev

# Build any missing per-city walk graphs (skips cities already on disk).
graphs:
	cd server && $(PY) refresh_osm.py --all

# Build per-city PMTiles with the 'dev' profile — full city coverage at a lighter
# zoom band. Same builder/source as prod (which uses --profile full), so the local
# background network matches prod's content. Skips tiles already newer than their graph.
tiles: graphs
	cd server && $(PY) build_pmtiles.py --all --profile dev
	cd server && $(PY) build_place_labels.py --all

# The hybrid dev loop: ensure graphs + tiles exist, start the Docker backing
# services, then run flask + the Vite client on the host. Ctrl-C stops flask
# too; the Docker services keep running (`make deps-down` to stop them).
dev: tiles deps
	@echo "Starting flask (background) + client (foreground)..."
	@cd server && $(PY) app.py & echo $$! > /tmp/cityedit-flask.pid
	@trap 'kill `cat /tmp/cityedit-flask.pid` 2>/dev/null; rm -f /tmp/cityedit-flask.pid' EXIT; \
		cd client-react && npm run dev

# Docker
docker:
	docker compose up --build

docker-dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# Git
push:
	@if [ -z "$(m)" ]; then \
		echo "Usage: make push m='commit message'"; \
		exit 1; \
	fi
	git add -A
	git commit -m "$(m)"
	git push

# Deployment
deploy:
	@echo "Deploying via Cloud Build..."
	gcloud builds submit --config=cloudbuild.yaml --project=$(PROJECT_ID)
	@echo "Deployment complete!"
	@make test-cloud

test-cloud:
	./scripts/test-cloud.sh

# Unit/integration tests (offline). Frontend: vitest. Backend: pytest (fakeredis,
# no DB/Redis needed for the unit suite).
test: test-frontend test-backend

test-frontend:
	cd client-react && npm test

test-backend:
	cd server && env/bin/python -m pytest

# Stateful load test: assign each agent an expected final vote state, march them
# there concurrently, then verify the server converged. USERS defaults to 10.
loadtest-verify:
	cd loadtest && . env/bin/activate && \
		python verify_loadtest.py --host $(or $(HOST),http://localhost:8080) --users $(or $(USERS),10)

# Terraform
tf-init:
	cd terraform && terraform init

tf-plan:
	cd terraform && terraform plan

tf-apply:
	cd terraform && terraform apply

# Monitoring
monitoring:
	cd monitoring && docker compose up -d
	@echo "Grafana running at http://localhost:3001"
	@echo "Login: admin / desirepath (change via GRAFANA_PASSWORD env var)"

monitoring-down:
	cd monitoring && docker compose down

# Load testing
loadtest-local:
	cd loadtest && . env/bin/activate && \
		locust -f locustfile.py --host http://localhost:8080 $(LOCUST_FLAGS)

loadtest-prod:
	cd loadtest && . env/bin/activate && \
		locust -f locustfile.py --host $(PROD_URL) $(LOCUST_FLAGS)

# Logs
logs:
	gcloud run services logs read desire-path-mapper --project=$(PROJECT_ID) --region=$(REGION) --limit=50

# Screenshots
screenshot-build:
	docker build -t $(SCREENSHOT_REGISTRY):latest screenshots/

screenshot-push: screenshot-build
	docker push $(SCREENSHOT_REGISTRY):latest

screenshot-run:
	gcloud run jobs execute map-screenshot-prod --region=$(REGION) --project=$(PROJECT_ID)

# Utilities
clean: deps-down
	-pkill -f "python app.py" 2>/dev/null || true
	-pkill -f "vite" 2>/dev/null || true
	@echo "Stopped all services"
