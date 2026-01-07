# Desire Path Mapper - Makefile

# Configuration
PROJECT_ID := google-mpf-ywspom2sxeey
REGION := us-central1
SERVICE := desire-path-mapper-prod
REGISTRY := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/desire-path-mapper/app

.PHONY: help dev redis flask client docker push deploy test-cloud clean

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
	@echo "Utilities:"
	@echo "  clean        Stop all local services"

# Local Development
redis:
	redis-server

flask:
	cd server && source env/bin/activate && python app.py

client:
	cd client && bun run dev

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

# Utilities
clean:
	-pkill -f "redis-server" 2>/dev/null || true
	-pkill -f "python app.py" 2>/dev/null || true
	-pkill -f "bun run dev" 2>/dev/null || true
	@echo "Stopped all services"
