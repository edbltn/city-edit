# City Edit - Makefile

# Configuration
PROJECT_ID := google-mpf-ywspom2sxeey
REGION := us-central1
SERVICE := desire-path-mapper
REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/app
SCREENSHOT_REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/screenshot

.PHONY: help dev redis flask client docker push deploy test test-frontend test-backend test-cloud tf-init tf-plan tf-apply logs clean monitoring monitoring-down loadtest-local loadtest-prod loadtest-verify

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
	@echo "Local Development:"
	@echo "  dev          Start all local services (redis, flask, client)"
	@echo "  redis        Start Redis server"
	@echo "  flask        Start Flask backend"
	@echo "  client       Start frontend dev server"
	@echo ""
	@echo "Docker:"
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
redis:
	redis-server

flask:
	cd server && source env/bin/activate && python app.py

client:
	cd client-react && npm run dev

dev:
	@echo "Starting all services..."
	@echo "Run these in separate terminals:"
	@echo "  make redis"
	@echo "  make flask"
	@echo "  make client"

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
clean:
	-pkill -f "redis-server" 2>/dev/null || true
	-pkill -f "python app.py" 2>/dev/null || true
	-pkill -f "bun run dev" 2>/dev/null || true
	@echo "Stopped all services"
