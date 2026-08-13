# Weekly NYC DOT proposals import (tools/nyc_proposals/weekly_import.py):
# fetches street-change proposals from nycdotprojects.info and casts one vote
# per new/changed proposal on the nyc-proposals map, through the public app
# API. Mirrors the map-screenshot job pattern. Apply with -target (see
# docs/gcp-deployment.md on blanket-apply landmines):
#
#   terraform apply \
#     -target=google_service_account.dot_import_sa \
#     -target=google_secret_manager_secret_iam_member.dot_import_admin_token_access \
#     -target=google_cloud_run_v2_job.dot_import \
#     -target=google_cloud_run_v2_job_iam_member.dot_import_invoker \
#     -target=google_cloud_scheduler_job.dot_import
#
# Image build: gcloud builds submit tools/nyc_proposals \
#   --tag ${region}-docker.pkg.dev/${project}/desire-path-mapper/dot-import:latest

resource "google_service_account" "dot_import_sa" {
  account_id   = "dot-import"
  display_name = "DOT Proposals Import Job"
  description  = "Runs the weekly nycdotprojects.info → nyc-proposals vote import"
}

resource "google_cloud_run_v2_job" "dot_import" {
  name     = "dot-proposals-import-${var.environment}"
  location = var.region

  template {
    template {
      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/desire-path-mapper/dot-import:latest"

        env {
          name  = "BASE_URL"
          value = "https://cityedit.org"
        }
        env {
          name  = "MAP_SLUG"
          value = "nyc-proposals"
        }

        # Gates the admin source-registration call the job makes after casting,
        # so each imported proposal cites its own nycdotprojects.info page.
        env {
          name = "ADMIN_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.admin_token.secret_id
              version = "latest"
            }
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }

      service_account = google_service_account.dot_import_sa.email
      # Fetch (1 req/s, polite) + geocode + cast for a week's changed pages;
      # generous headroom over the observed full-sweep runtime.
      timeout     = "1800s"
      max_retries = 1
    }
  }

  depends_on = [
    google_project_service.cloud_run,
    google_artifact_registry_repository.app,
    # Cloud Run validates the secret reference against the job's service
    # account at update time, so the grant has to exist first — nothing in the
    # container spec refers to it, so without this Terraform is free to order
    # the update ahead of the binding, and the update fails.
    google_secret_manager_secret_iam_member.dot_import_admin_token_access,
  ]
}

resource "google_secret_manager_secret_iam_member" "dot_import_admin_token_access" {
  secret_id = google_secret_manager_secret.admin_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dot_import_sa.email}"
}

resource "google_cloud_run_v2_job_iam_member" "dot_import_invoker" {
  name     = google_cloud_run_v2_job.dot_import.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.dot_import_sa.email}"
}

resource "google_cloud_scheduler_job" "dot_import" {
  name        = "dot-proposals-import-weekly-${var.environment}"
  description = "Weekly NYC DOT proposals → nyc-proposals vote import"
  # Monday 07:00 New York — after DOT's typical end-of-week page updates,
  # before US morning traffic.
  schedule  = "0 7 * * 1"
  time_zone = "America/New_York"
  region    = var.region

  # PAUSED 2026-08-13. The import re-casts any project page whose sitemap
  # lastmod falls in its window and has no notion of a project being finished,
  # so it would resurrect proposals the 2026-08-12 nyc-proposals audit removed
  # as Past/Complete — re-registering their citations on the way. Resume once
  # weekly_import.py skips projects in a completed DOT state; until then the
  # map is better stale than wrong. Paused here rather than with `gcloud
  # scheduler jobs pause`, which the next apply would silently undo.
  paused = true

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.dot_import.name}:run"

    oauth_token {
      service_account_email = google_service_account.dot_import_sa.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.cloudscheduler,
    google_cloud_run_v2_job.dot_import,
  ]
}

output "dot_import_job" {
  value = google_cloud_run_v2_job.dot_import.name
}
