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
  description   = "Docker repository for City Edit"
  format        = "DOCKER"
}

# Redis instance
resource "google_redis_instance" "cache" {
  name           = "desire-path-${var.environment}"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region

  redis_version = "REDIS_7_0"
  display_name  = "City Edit Redis"

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
    # g1-small (1.7GB shared-core): f1-micro (0.6GB) ran out of steam under
    # concurrent tenant writes — vote persistence is on the request path.
    tier = "db-g1-small"

    ip_configuration {
      ipv4_enabled    = false
      private_network = "projects/${var.project_id}/global/networks/default"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
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

# Admin gate token + session-signing key. Previously set out-of-band via
# `gcloud run services update --update-env-vars`, which made a plain
# `terraform apply` silently DROP them (breaking admin endpoints and
# invalidating every passcode session). Managing them here keeps apply safe.
resource "google_secret_manager_secret" "admin_token" {
  secret_id = "admin-token-${var.environment}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "admin_token" {
  secret      = google_secret_manager_secret.admin_token.id
  secret_data = var.admin_token
}

resource "google_secret_manager_secret" "secret_key" {
  secret_id = "secret-key-${var.environment}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "secret_key" {
  secret      = google_secret_manager_secret.secret_key.id
  secret_data = var.secret_key
}

resource "google_secret_manager_secret_iam_member" "cloud_run_admin_token_access" {
  secret_id = google_secret_manager_secret.admin_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_secret_key_access" {
  secret_id = google_secret_manager_secret.secret_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# Merged OSRM routing service.
#
# One routed instance serves every city from a single baked-in dataset (NYC +
# SF + Chicago, clipped + osmium-merged at image build). Runs as its own service
# so its RAM lives off the app's strained budget. Not public: the app calls it
# with a Google-signed ID token (ingress stays "all" so the call works over the
# app's private-ranges-only VPC egress, but IAM rejects anything unauthenticated).
resource "google_cloud_run_service" "osrm" {
  name     = "desire-path-osrm"
  location = var.region

  template {
    spec {
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper/osrm:latest"

        # osrm-routed binds $PORT (Cloud Run injects 8080); see osrm/Dockerfile.
        ports {
          container_port = 8080
        }

        resources {
          # osrm-routed maps the merged foot dataset into resident memory; the
          # MLD files total ~1.6GB (cell_metrics alone is 756MB), so 2Gi OOM'd on
          # first deploy. 4Gi gives >2x headroom; 1 vCPU is plenty for sub-ms MLD
          # queries at this traffic.
          limits = {
            cpu    = "1"
            memory = "4Gi"
          }
        }

        # osrm-routed (v5.25.0) has no /health endpoint — it parses any path as a
        # route and 400s — so probe the TCP port instead. It binds 8080 ~15s after
        # start, so this passes quickly.
        startup_probe {
          tcp_socket {
            port = 8080
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
        # Keep one instance warm so routing never eats an OSRM cold start; one
        # instance easily serves this traffic (MLD queries are sub-ms), so cap at 1.
        "autoscaling.knative.dev/minScale" = "1"
        "autoscaling.knative.dev/maxScale" = "1"
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
  ]
}

# Only the app's service account may invoke OSRM (no allUsers → not open to the
# internet). The app's default compute SA carries the run.invoker grant.
resource "google_cloud_run_service_iam_member" "osrm_invoker" {
  service  = google_cloud_run_service.osrm.name
  location = google_cloud_run_service.osrm.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# Cloud Run service
resource "google_cloud_run_service" "app" {
  name     = "desire-path-mapper"
  location = var.region

  template {
    spec {
      # Explicit cap (Cloud Run default is 80). Requests are gevent-multiplexed
      # on one worker; each held-open WebSocket occupies a slot for its
      # lifetime, so the ceiling is concurrent VIEWERS per instance, not RPS.
      # 200 × maxScale 8 ≈ 1600 concurrent viewers before saturation.
      container_concurrency = 200

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

        # Warm graphs at boot in a background thread (the worker still goes Ready
        # immediately, so the startup probe passes). This pre-loads the NYC graph
        # off the request path — its ~60-120s load would otherwise blow the
        # gunicorn 120s worker timeout when loaded lazily inside a request (502).
        # Safe at 16Gi (the OOM that forced SKIP_WARMUP=1 was at 8Gi).
        env {
          name  = "SKIP_WARMUP"
          value = "0"
        }

        # All cities route through the single merged OSRM service. HTTPS target
        # ⇒ osrm_router.py attaches a Cloud Run ID token (audience = this URL).
        env {
          name  = "OSRM_URL"
          value = google_cloud_run_service.osrm.status[0].url
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

        # Private bucket of map previews; Flask streams /previews/<slug>.png from
        # here (see serve_preview in app.py).
        env {
          name  = "PREVIEW_BUCKET"
          value = google_storage_bucket.previews.name
        }

        env {
          name = "ADMIN_TOKEN"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.admin_token.secret_id
              key  = "latest"
            }
          }
        }

        env {
          name = "SECRET_KEY"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.secret_key.secret_id
              key  = "latest"
            }
          }
        }

        resources {
          # Holds every city walk graph (nyc/sf/chicago/dc/philly + test cities)
          # + station networks resident. The runtime loads prebaked compact
          # arrays (walk_graph_arrays.npz — no networkx unpickle, whose multi-GB
          # transient caused the 2026-07 OOM crash loop at 8Gi/old code).
          # Measured locally with ALL cities + 21 map vote bodies warm: 1.35Gi
          # RSS, so 8Gi is ~6x headroom. 4 vCPU: the single gevent worker plus
          # nginx gzip are the concurrency ceiling per instance — extra cores
          # keep topology/vote-snapshot compression off the request path's back.
          limits = {
            cpu    = "4"
            memory = "8Gi"
          }
        }

        # 120×5s = 10 min: the background warmup freezes the gevent hub (GIL-bound
        # graph loads) for a few minutes, so /health only starts answering once the
        # warmup completes. Give it a generous window so the first revision goes
        # Ready instead of timing out mid-warmup.
        startup_probe {
          http_get {
            path = "/health"
          }
          initial_delay_seconds = 5
          period_seconds        = 5
          failure_threshold     = 120
          timeout_seconds       = 3
        }
      }
    }

    metadata {
      annotations = {
        "autoscaling.knative.dev/minScale"        = "1"
        # Horizontal scaling is now SAFE across instances: the voter
        # read-modify-write is serialized fleet-wide by a Redis SET-NX lock
        # (vote_store.voter_lock), and each instance's vote-response cache is a
        # bounded, shared-nothing LRU (Redis holds the authoritative votes). So
        # the autoscaler may spill to additional instances under concurrent load
        # instead of head-of-line-blocking on the single gevent worker. Added
        # instances are 8Gi/4CPU each and only run under load (minScale stays 1);
        # graph loads from the array artifacts take seconds, so a scale-out
        # instance is useful almost immediately.
        "autoscaling.knative.dev/maxScale"        = "8"
        # Extra CPU during startup so imports + _populate_redis + the background
        # graph prewarm finish quickly (we run at only 2 vCPU steady-state).
        "run.googleapis.com/startup-cpu-boost"    = "true"
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
    google_secret_manager_secret_iam_member.cloud_run_db_access,
    google_secret_manager_secret_version.admin_token,
    google_secret_manager_secret_iam_member.cloud_run_admin_token_access,
    google_secret_manager_secret_version.secret_key,
    google_secret_manager_secret_iam_member.cloud_run_secret_key_access
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
    "ebikes.cityedit.org",
    "demo.cityedit.org",
  ]
}

variable "db_password" {
  description = "PostgreSQL database password"
  type        = string
  sensitive   = true
}

variable "admin_token" {
  description = "Token gating state-changing admin endpoints"
  type        = string
  sensitive   = true
}

variable "secret_key" {
  description = "Session/passcode signing key (itsdangerous). Changing it invalidates all passcode sessions."
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

  # certificate_mode defaults to AUTOMATIC, but a mapping created outside
  # terraform and later imported (e.g. ebikes.cityedit.org, originally made via
  # gcloud) reads it back empty — so terraform would REPLACE the live mapping
  # (a brief ebikes outage + cert re-provision) just to set a field it already
  # effectively has. Ignore it so the imported mapping plans as a no-op.
  lifecycle {
    ignore_changes = [spec[0].certificate_mode]
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

# =============================================================================
# Map Preview Screenshots
# =============================================================================

resource "google_storage_bucket" "previews" {
  name     = "cityedit-map-previews-${var.environment}"
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "inherited"
}

resource "google_service_account" "screenshot_sa" {
  account_id   = "map-screenshot"
  display_name = "Map Screenshot Job"
  description  = "Service account for hourly map preview screenshot capture"
}

resource "google_storage_bucket_iam_member" "screenshot_writer" {
  bucket = google_storage_bucket.previews.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.screenshot_sa.email}"
}

# The app (running as the default compute SA) streams previews from this private
# bucket — the project enforces Public Access Prevention, so it can't be public.
# Read-only is enough; the screenshot job is the only writer.
resource "google_storage_bucket_iam_member" "app_preview_reader" {
  bucket = google_storage_bucket.previews.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_cloud_run_v2_job" "screenshot" {
  name     = "map-screenshot-${var.environment}"
  location = var.region

  template {
    template {
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper/screenshot:latest"

        env {
          name  = "PREVIEW_BUCKET"
          value = google_storage_bucket.previews.name
        }

        resources {
          limits = {
            cpu    = "2"
            memory = "2Gi"
          }
        }
      }

      service_account = google_service_account.screenshot_sa.email
      timeout         = "300s"
      max_retries     = 1
    }
  }

  depends_on = [
    google_project_service.cloud_run,
    google_artifact_registry_repository.app,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "screenshot_invoker" {
  name     = google_cloud_run_v2_job.screenshot.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.screenshot_sa.email}"
}

resource "google_cloud_scheduler_job" "screenshot" {
  name        = "map-screenshot-daily-${var.environment}"
  description = "Daily map preview screenshot capture"
  schedule    = "0 6 * * *"
  time_zone   = "America/New_York"
  region      = var.region

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.screenshot.name}:run"

    oauth_token {
      service_account_email = google_service_account.screenshot_sa.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.cloudscheduler,
    google_cloud_run_v2_job.screenshot,
  ]
}

output "preview_bucket_url" {
  value = "https://storage.googleapis.com/${google_storage_bucket.previews.name}"
}

output "screenshot_job" {
  value = google_cloud_run_v2_job.screenshot.name
}