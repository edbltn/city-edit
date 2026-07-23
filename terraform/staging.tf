# =============================================================================
# Staging environment (docs/staging-parity-plan.md)
#
# A parity copy of the app service at an UNGUESSABLE URL: the service name
# carries a random token (var.staging_token), so the default run.app hostname
# is the access control — Google's shared *.a.run.app wildcard cert means the
# hostname is never published anywhere (unlike a domain mapping, whose
# per-hostname managed cert would land in Certificate Transparency logs).
#
# Staging DUPLICATES the state-bearing pieces (Redis, database, admin/session
# secrets) and SHARES the stateless ones (OSRM, previews bucket, the container
# image — staging runs the exact digest prod runs next). It runs as the same
# default compute service account as prod: identical runtime behavior, and the
# existing OSRM-invoker + previews grants apply without new IAM.
#
# ⚠️ Apply ONLY with -target on these resources (same rule as monitoring.tf):
#     terraform apply -target=google_redis_instance.cache_staging \
#                     -target=google_sql_database.votes_staging ...
# A blanket apply still carries the known prod landmines (secret wipe, ebikes
# domain mapping). Verify the plan shows ONLY adds before applying.
#
# Image updates are pushed with gcloud (deploy staging first, then promote the
# SAME digest to prod — see docs/gcp-deployment.md); lifecycle.ignore_changes
# keeps terraform from fighting those digest flips.
# =============================================================================

variable "staging_token" {
  description = "Random token embedded in the staging service name (the unguessable URL). Generate once: openssl rand -hex 8. Never commit the value."
  type        = string
  sensitive   = true
}

variable "staging_db_password" {
  description = "Password for the app_staging PostgreSQL user"
  type        = string
  sensitive   = true
}

variable "staging_admin_token" {
  description = "Staging admin gate token — distinct from prod so a leaked staging token can't administer prod"
  type        = string
  sensitive   = true
}

variable "staging_secret_key" {
  description = "Staging session/passcode signing key — distinct from prod"
  type        = string
  sensitive   = true
}

# OSRM's URL read via a data source, NOT google_cloud_run_service.osrm: a
# resource reference drags prod OSRM into every -target closure, and any
# annotation drift there (e.g. gcloud's client-name stamps) would make the
# "staging-only" apply roll a new prod OSRM revision. The data source keeps
# staging plans strictly additive.
data "google_cloud_run_service" "osrm_live" {
  name     = "desire-path-osrm"
  location = var.region
}

# Staging Redis. The codebase does not namespace Redis keys per environment,
# so staging cannot share prod's instance — this Basic 1GB twin (~$35/mo) is
# the main recurring cost of staging. No prevent_destroy: staging is meant to
# be cheap to tear down / rotate.
resource "google_redis_instance" "cache_staging" {
  name           = "desire-path-staging"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region

  redis_version = "REDIS_7_0"
  display_name  = "City Edit Redis (staging)"

  redis_configs = {
    maxmemory-policy = "allkeys-lru"
  }
}

# Separate DATABASE on the shared prod Cloud SQL instance (not a second
# instance): the instance idles most of the day, and a same-instance database
# keeps seeding trivial (same bastion tunnel, different dbname). Upgrade path
# if staging load ever shows up in prod queries: its own db-f1-micro.
resource "google_sql_database" "votes_staging" {
  name     = "votes_staging"
  instance = google_sql_database_instance.votes.name
}

resource "google_sql_user" "app_staging" {
  name     = "app_staging"
  instance = google_sql_database_instance.votes.name
  password = var.staging_db_password
}

resource "google_secret_manager_secret" "database_url_staging" {
  secret_id = "database-url-staging"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "database_url_staging" {
  secret      = google_secret_manager_secret.database_url_staging.id
  secret_data = "postgresql://app_staging:${var.staging_db_password}@${google_sql_database_instance.votes.private_ip_address}:5432/votes_staging"
}

resource "google_secret_manager_secret" "admin_token_staging" {
  secret_id = "admin-token-staging"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "admin_token_staging" {
  secret      = google_secret_manager_secret.admin_token_staging.id
  secret_data = var.staging_admin_token
}

resource "google_secret_manager_secret" "secret_key_staging" {
  secret_id = "secret-key-staging"
  replication {
    auto {}
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "secret_key_staging" {
  secret      = google_secret_manager_secret.secret_key_staging.id
  secret_data = var.staging_secret_key
}

resource "google_secret_manager_secret_iam_member" "cloud_run_db_staging_access" {
  secret_id = google_secret_manager_secret.database_url_staging.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_admin_token_staging_access" {
  secret_id = google_secret_manager_secret.admin_token_staging.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "cloud_run_secret_key_staging_access" {
  secret_id = google_secret_manager_secret.secret_key_staging.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# The staging app service. Same container shape as prod (8Gi/4CPU/concurrency
# 200 — parity beats savings: memory behavior is exactly what staging must
# reproduce) but minScale 0 (≈$0 idle; the cold-start + prewarm window is
# itself something we want to observe on staging) and maxScale 2.
resource "google_cloud_run_service" "app_staging" {
  name     = "ce-stg-${var.staging_token}"
  location = var.region

  template {
    spec {
      container_concurrency = 200

      containers {
        # :latest only seeds the FIRST revision; day-to-day the digest is
        # pushed with gcloud (staging first, then the same digest to prod)
        # and ignore_changes below keeps terraform out of that loop.
        image = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper/app:latest"

        # Flags staging behavior in app + client: map configs carry
        # staging:true (redirect gate + ribbon) and every response is
        # X-Robots-Tag noindexed. See server/app.py IS_STAGING.
        env {
          name  = "APP_ENV"
          value = "staging"
        }

        env {
          name  = "REDIS_HOST"
          value = google_redis_instance.cache_staging.host
        }

        env {
          name  = "REDIS_PORT"
          value = google_redis_instance.cache_staging.port
        }

        env {
          name  = "SKIP_WARMUP"
          value = "0"
        }

        # Shared with prod: OSRM is stateless/read-only, and the default
        # compute SA already holds run.invoker on it.
        env {
          name  = "OSRM_URL"
          value = data.google_cloud_run_service.osrm_live.status[0].url
        }

        env {
          name = "DATABASE_URL"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.database_url_staging.secret_id
              key  = "latest"
            }
          }
        }

        env {
          name  = "PREVIEW_BUCKET"
          value = google_storage_bucket.previews.name
        }

        env {
          name = "ADMIN_TOKEN"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.admin_token_staging.secret_id
              key  = "latest"
            }
          }
        }

        env {
          name = "SECRET_KEY"
          value_from {
            secret_key_ref {
              name = google_secret_manager_secret.secret_key_staging.secret_id
              key  = "latest"
            }
          }
        }

        resources {
          limits = {
            cpu    = "4"
            memory = "8Gi"
          }
        }

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
        "autoscaling.knative.dev/minScale"        = "0"
        "autoscaling.knative.dev/maxScale"        = "2"
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

  lifecycle {
    ignore_changes = [template[0].spec[0].containers[0].image]
  }

  depends_on = [
    google_project_service.cloud_run,
    google_artifact_registry_repository.app,
    google_vpc_access_connector.connector,
    google_secret_manager_secret_version.database_url_staging,
    google_secret_manager_secret_iam_member.cloud_run_db_staging_access,
    google_secret_manager_secret_version.admin_token_staging,
    google_secret_manager_secret_iam_member.cloud_run_admin_token_staging_access,
    google_secret_manager_secret_version.secret_key_staging,
    google_secret_manager_secret_iam_member.cloud_run_secret_key_staging_access,
  ]
}

# Public like prod — the unguessable hostname is the gate.
resource "google_cloud_run_service_iam_member" "public_staging" {
  service  = google_cloud_run_service.app_staging.name
  location = google_cloud_run_service.app_staging.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "staging_url" {
  value     = google_cloud_run_service.app_staging.status[0].url
  sensitive = true # the URL IS the secret
}

output "staging_redis_host" {
  value = google_redis_instance.cache_staging.host
}
