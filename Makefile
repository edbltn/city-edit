# Desire Path Mapper - Makefile

# Configuration
PROJECT_ID := google-mpf-ywspom2sxeey
REGION := us-central1
SERVICE := desire-path-mapper-prod
REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/app

.PHONY: help dev redis flask client docker push deploy test-cloud tf-init tf-plan tf-apply logs clean monitoring monitoring-down

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

# Logs
logs:
	gcloud run services logs read desire-path-mapper --project=$(PROJECT_ID) --region=$(REGION) --limit=50

# Utilities
clean:
	-pkill -f "redis-server" 2>/dev/null || true
	-pkill -f "python app.py" 2>/dev/null || true
	-pkill -f "bun run dev" 2>/dev/null || true
	@echo "Stopped all services"
