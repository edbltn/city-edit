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

resource "google_project_service" "cloudscheduler" {
  service = "cloudscheduler.googleapis.com"
}

resource "google_project_service" "sqladmin" {
  service = "sqladmin.googleapis.com"
}

resource "google_project_service" "secretmanager" {
  service = "secretmanager.googleapis.com"
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

  # Use LRU eviction to auto-remove old cache entries when memory is full
  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# =============================================================================
# PostgreSQL Database (Cloud SQL)
# =============================================================================

# Cloud SQL PostgreSQL instance
resource "google_sql_database_instance" "votes" {
  name             = "desire-path-votes-${var.environment}"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier = "db-f1-micro"

    ip_configuration {
      ipv4_enabled    = false
      private_network = "projects/${var.project_id}/global/networks/default"
    }

    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = true
  depends_on          = [google_project_service.sqladmin]
}

resource "google_sql_database" "votes" {
  name     = "votes"
  instance = google_sql_database_instance.votes.name
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = google_sql_database_instance.votes.name
  password = var.db_password
}

# Secret for database URL
resource "google_secret_manager_secret" "database_url" {
  secret_id = "database-url-${var.environment}"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = "postgresql://app:${var.db_password}@${google_sql_database_instance.votes.private_ip_address}:5432/votes"
}

# Grant Cloud Run access to database secret
resource "google_secret_manager_secret_iam_member" "cloud_run_db_access" {
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# Cloud Run service
resource "google_cloud_run_service" "app" {
  name     = "desire-path-mapper"
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

        env {
          name = "DATABASE_URL"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.database_url.secret_id
              key  = "latest"
            }
          }
        }

        resources {
          limits = {
            cpu    = "4"
            memory = "16Gi"
          }
        }

        startup_probe {
          http_get {
            path = "/health"
          }
          initial_delay_seconds = 5
          period_seconds        = 5
          failure_threshold     = 30
          timeout_seconds       = 3
        }
      }
    }

    metadata {
      annotations = {
        "autoscaling.knative.dev/minScale"        = "2"
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
    google_vpc_access_connector.connector,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_iam_member.cloud_run_db_access
  ]
}

# Allow unauthenticated access (public app)
resource "google_cloud_run_service_iam_member" "public" {
  service  = google_cloud_run_service.app.name
  location = google_cloud_run_service.app.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

variable "custom_domains" {
  description = "Custom domains to map to the Cloud Run service"
  type        = list(string)
  default = [
    "cityedit.org",
    "bikepaths.cityedit.org",
    "trees.cityedit.org",
    "walkways.cityedit.org",
    "demo.cityedit.org",
  ]
}

variable "db_password" {
  description = "PostgreSQL database password"
  type        = string
  sensitive   = true
}

variable "developer_email" {
  description = "IAM email of the developer who needs local DB access (e.g. user:you@gmail.com)"
  type        = string
  default     = "user:eric.didier.bolton@gmail.com"
}

# Grant developer Cloud SQL client access (for Cloud SQL Auth Proxy)
resource "google_project_iam_member" "developer_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = var.developer_email
}

# Custom domain mappings (one per subdomain)
resource "google_cloud_run_domain_mapping" "custom" {
  for_each = toset(var.custom_domains)

  location = var.region
  name     = each.value

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

output "database_instance" {
  value = google_sql_database_instance.votes.name
}

output "database_private_ip" {
  value = google_sql_database_instance.votes.private_ip_address
}

output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper"
}

output "custom_domains" {
  value = var.custom_domains
}

output "domain_mapping_dns" {
  description = "DNS records to configure at your registrar, keyed by domain"
  value = {
    for domain, mapping in google_cloud_run_domain_mapping.custom :
    domain => mapping.status[0].resource_records
  }
}

# =============================================================================
# Weekly OSM Refresh Infrastructure
# =============================================================================

# Service account for Cloud Scheduler to invoke Cloud Run
resource "google_service_account" "osm_refresh_scheduler" {
  account_id   = "osm-refresh-scheduler"
  display_name = "OSM Refresh Scheduler"
  description  = "Service account for weekly OSM refresh Cloud Scheduler job"
}

# Allow scheduler to invoke Cloud Run
resource "google_cloud_run_service_iam_member" "scheduler_invoker" {
  service  = google_cloud_run_service.app.name
  location = google_cloud_run_service.app.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.osm_refresh_scheduler.email}"
}

# Cloud Scheduler job for weekly OSM refresh
resource "google_cloud_scheduler_job" "osm_refresh" {
  name        = "osm-refresh-weekly-${var.environment}"
  description = "Weekly OSM data refresh and CH graph rebuild"
  schedule    = "0 3 * * 0" # Sunday at 3am UTC
  time_zone   = "America/New_York"
  region      = var.region

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_service.app.status[0].url}/api/admin/refresh-osm"

    oidc_token {
      service_account_email = google_service_account.osm_refresh_scheduler.email
      audience              = google_cloud_run_service.app.status[0].url
    }
  }

  depends_on = [
    google_project_service.cloudscheduler,
    google_cloud_run_service.app
  ]
}

output "osm_refresh_scheduler_job" {
  value = google_cloud_scheduler_job.osm_refresh.name
}

# =============================================================================
# Bastion VM (IAP tunnel for local DB access)
# =============================================================================

resource "google_project_service" "iap" {
  service = "iap.googleapis.com"
}

resource "google_project_service" "compute" {
  service = "compute.googleapis.com"
}

resource "google_compute_instance" "bastion" {
  name         = "bastion-${var.environment}"
  machine_type = "e2-micro"
  zone         = "${var.region}-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10
    }
  }

  network_interface {
    network = "default"
    # No access_config block = no public IP; IAP handles tunneling
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  tags = ["bastion"]

  depends_on = [google_project_service.compute]
}

# Allow IAP to SSH into the bastion
resource "google_compute_firewall" "allow_iap_ssh" {
  name    = "allow-iap-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  # IAP's IP range for TCP forwarding
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["bastion"]
}

# Grant developer IAP tunnel access to the bastion
resource "google_iap_tunnel_instance_iam_member" "developer_bastion" {
  instance = google_compute_instance.bastion.name
  zone     = google_compute_instance.bastion.zone
  role     = "roles/iap.tunnelResourceAccessor"
  member   = var.developer_email

  depends_on = [google_project_service.iap]
}

# Grant developer OS Login so gcloud can log in to the bastion
resource "google_project_iam_member" "developer_oslogin" {
  project = var.project_id
  role    = "roles/compute.osAdminLogin"
  member  = var.developer_email
}

output "bastion_name" {
  value = google_compute_instance.bastion.name
}

output "bastion_zone" {
  value = google_compute_instance.bastion.zone
}