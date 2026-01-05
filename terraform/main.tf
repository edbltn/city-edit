terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

# Enable required APIs
resource "google_project_service" "cloud_run" {
  service = "run.googleapis.com"
}

resource "google_project_service" "artifact_registry" {
  service = "artifactregistry.googleapis.com"
}

resource "google_project_service" "redis" {
  service = "redis.googleapis.com"
}

resource "google_project_service" "cloudbuild" {
  service = "cloudbuild.googleapis.com"
}

resource "google_project_service" "vpcaccess" {
  service = "vpcaccess.googleapis.com"
}

# VPC Connector for Cloud Run to reach Memorystore Redis
resource "google_vpc_access_connector" "connector" {
  name          = "redis-connector"
  region        = var.region
  ip_cidr_range = "10.8.0.0/28"
  network       = "default"

  depends_on = [google_project_service.vpcaccess]
}

# Grant Cloud Build permission to deploy to Cloud Run
resource "google_project_iam_member" "cloudbuild_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"

  depends_on = [google_project_service.cloudbuild]
}

resource "google_project_iam_member" "cloudbuild_service_account_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"

  depends_on = [google_project_service.cloudbuild]
}

# Get project number for Cloud Build service account
data "google_project" "project" {
  project_id = var.project_id
}

# Artifact Registry for Docker images
resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "desire-path-mapper"
  description   = "Docker repository for Desire Path Mapper"
  format        = "DOCKER"
}

# Redis instance
resource "google_redis_instance" "cache" {
  name           = "desire-path-${var.environment}"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region

  redis_version = "REDIS_7_0"
  display_name  = "Desire Path Mapper Redis"

  lifecycle {
    prevent_destroy = true
  }
}

# Cloud Run service
resource "google_cloud_run_service" "app" {
  name     = "desire-path-mapper-${var.environment}"
  location = var.region

  template {
    spec {
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper/app:latest"

        env {
          name  = "REDIS_HOST"
          value = google_redis_instance.cache.host
        }

        env {
          name  = "REDIS_PORT"
          value = google_redis_instance.cache.port
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }

    metadata {
      annotations = {
        "autoscaling.knative.dev/minScale"        = "1"
        "autoscaling.knative.dev/maxScale"        = "10"
        "run.googleapis.com/vpc-access-connector" = google_vpc_access_connector.connector.id
        "run.googleapis.com/vpc-access-egress"    = "private-ranges-only"
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  depends_on = [
    google_project_service.cloud_run,
    google_artifact_registry_repository.app,
    google_vpc_access_connector.connector
  ]
}

# Allow unauthenticated access (public app)
resource "google_cloud_run_service_iam_member" "public" {
  service  = google_cloud_run_service.app.name
  location = google_cloud_run_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

variable "custom_domain" {
  description = "Custom domain for the app"
  type        = string
  default     = "demo.sphericalharmonics.org"
}

# Custom domain mapping
resource "google_cloud_run_domain_mapping" "custom" {
  location = var.region
  name     = var.custom_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_service.app.name
  }

  depends_on = [google_cloud_run_service.app]
}

# Outputs
output "service_url" {
  value = google_cloud_run_service.app.status[0].url
}

output "redis_host" {
  value = google_redis_instance.cache.host
}

output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper"
}

output "custom_domain" {
  value = var.custom_domain
}

output "domain_mapping_dns" {
  description = "DNS records to configure at your registrar"
  value       = google_cloud_run_domain_mapping.custom.status[0].resource_records
}