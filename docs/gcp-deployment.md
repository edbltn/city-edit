# GCP Deployment Guide

How City Edit runs on Google Cloud, how to deploy a change, and how to map a
custom domain.

> **Naming:** the product is *City Edit* (cityedit.org), but the GCP resources
> keep their original `desire-path-*` IDs. The Cloud Run app service is
> `desire-path-mapper` — **not** `desire-path-mapper-prod`. See the
> [naming canon](README.md#naming-canon).

## Overview

| Component | Resource | Purpose |
|-----------|----------|---------|
| Cloud Run | `desire-path-mapper` | The app: nginx + gunicorn (Flask) in one container, serving the API, WebSocket, and the static SPA. |
| Cloud Run | `desire-path-osrm` | Self-hosted OSRM routing engine (one dataset per supported city). |
| Memorystore Redis | `desire-path-prod` | Vote counts + real-time pub/sub. Reached over the `redis-connector` VPC connector. |
| Cloud SQL Postgres | `desire-path-votes-prod` | Durable vote rows. |
| Artifact Registry | `desire-path-mapper` | Holds the `app` and `osrm` Docker images. |
| Cloud Build | `cloudbuild.yaml` | Builds + pushes both images and updates both services. |
| Secret Manager | `database_url`, `secret_key`, `admin_token` | App secrets injected as env vars. |

Live domains (mapped in `terraform/main.tf` via `custom_domains`): `cityedit.org`
plus `bikepaths`, `trees`, `walkways`, `ebikes`, and `demo` subdomains.

## Project Configuration

- **Project ID**: `google-mpf-ywspom2sxeey`
- **Region**: `us-central1`
- **Environment**: `prod`

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- Docker Desktop (optional — Cloud Build handles builds)

## Authentication

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project google-mpf-ywspom2sxeey
```

## Deploying Changes

### Option 1 — Cloud Build (recommended)

Builds both images (osrm, app), pushes them, and updates both Cloud Run
services in one command:

```bash
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

Pushes to `main` run this automatically via `.github/workflows/deploy.yml`.

### Option 2 — Manual Docker build

```bash
# Build for Cloud Run (linux/amd64 required on M-series Macs)
docker build --platform linux/amd64 \
  -t us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest .

docker push us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest

cd terraform && terraform apply
```

### Option 3 — Quick image swap (no Terraform)

```bash
gcloud run services update desire-path-mapper \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey \
  --image=us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest
```

## Environment & Secrets

App secrets are managed in Secret Manager and wired into the Cloud Run service by
Terraform, so they persist across deploys (Cloud Build uses `services update`,
which preserves existing env vars):

| Secret / env | Purpose |
|--------------|---------|
| `DATABASE_URL` | Cloud SQL Postgres connection (durable votes). |
| `SECRET_KEY` | Signing key for map-passcode tokens — must be stable across instances. |
| `ADMIN_TOKEN` | Guards admin endpoints (e.g. assigning vanity subdomains). |
| `REDIS_HOST` / `REDIS_PORT` | Memorystore Redis (set from the Terraform output). |
| `OSRM_URL` | URL of the `desire-path-osrm` service (set from its status URL). |
| `SKIP_WARMUP` | Skip graph warmup on boot for faster startup. |

There is **no routing API key** — routing is self-hosted OSRM.

## Custom Domains

Domains are declared in the `custom_domains` variable in `terraform/main.tf` and
mapped with one `google_cloud_run_domain_mapping` per entry. To add one:

1. **Add the domain** to `custom_domains` and `terraform apply` (creates the
   mapping + managed cert).
2. **Add DNS** at the registrar — a `CNAME` to `ghs.googlehosted.com.` for each
   subdomain (or an `A`/`AAAA` set for the apex), as printed by:

   ```bash
   terraform output domain_mapping_dns
   ```

3. **Verify domain ownership** (one-time per registrable domain) at
   [Google Webmaster Central](https://www.google.com/webmasters/verification/verification)
   if Cloud Run reports the domain isn't verified.

SSL is auto-provisioned once DNS propagates (5–30 minutes). Note that *attaching
a map to a subdomain* (e.g. `bikepaths.cityedit.org` → a slug) is a separate,
data-only step — see [url-routing.md](url-routing.md).

## Infrastructure From Scratch

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # set project_id, etc.
terraform init
terraform apply
```

This provisions Artifact Registry, Memorystore Redis (`prevent_destroy = true`),
Cloud SQL Postgres, the VPC connector, both Cloud Run services, Cloud Build IAM,
Secret Manager secrets, and the custom domain mappings.

> Build and push the `app` and `osrm` images (Option 1 or 2) **before** the first
> `terraform apply` — the services reference images that must already exist.

## Terraform Outputs

```bash
terraform output service_url          # app URL
terraform output redis_host           # Redis internal IP
terraform output database_instance    # Cloud SQL instance
terraform output database_private_ip  # Cloud SQL private IP
terraform output registry_url         # where to push images
terraform output custom_domains       # mapped domains
terraform output domain_mapping_dns   # DNS records to add, keyed by domain
```

## IAM Permissions

The deploying account needs:

- `roles/cloudbuild.builds.editor` — submit Cloud Build jobs
- `roles/run.admin` — manage Cloud Run services
- `roles/artifactregistry.writer` — push images

Or grant Owner:

```bash
gcloud projects add-iam-policy-binding google-mpf-ywspom2sxeey \
  --member="user:YOUR_EMAIL" \
  --role="roles/owner"
```

Cloud Build's own service-account permissions are managed by Terraform.

To allow public access (`allUsers`), the org policy
`iam.allowedPolicyMemberDomains` must permit it (override to "Off" for this
project in the Org Policies console).

## Container Layout

The `app` image (`Dockerfile`) is a multi-stage build: a `node:20` stage builds
the client, then a `python:3.13-slim` stage runs supervisord →

- **nginx** on `:8080` (Cloud Run's port) — serves the SPA from `/var/www/html`,
  proxies `/api`, `/ws` (exact match), and `/tiles` to Flask, and aliases
  PMTiles straight from disk. Config: `deploy/nginx-cloudrun.conf`.
- **gunicorn** on `127.0.0.1:5001` — `--worker-class gevent --workers 1`, long
  timeouts for graph warmup. Config: `deploy/supervisord.conf`.

This differs from local `docker compose`, which runs separate containers.

## Viewing Logs

```bash
gcloud run services logs read desire-path-mapper \
  --project=google-mpf-ywspom2sxeey --region=us-central1 --limit=50

# Or the structured log reader:
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=desire-path-mapper" \
  --project=google-mpf-ywspom2sxeey --limit=30 \
  --format="table(timestamp,severity,textPayload)"
```

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `PERMISSION_DENIED` on `gcloud builds submit` | Grant yourself `roles/cloudbuild.builds.editor` (see IAM above). |
| "Caller is not authorized to administer the domain" | Verify domain ownership at Google Webmaster Central before mapping. |
| "Image not found" on `terraform apply` | Build and push the `app`/`osrm` images first. |
| ARM architecture error | Build with `--platform linux/amd64` on M-series Macs, or use Cloud Build. |
| "allUsers not permitted" | Update the `iam.allowedPolicyMemberDomains` org policy (see IAM above). |
| Uploading tens of thousands of files to Cloud Build | Ensure `.gcloudignore` excludes `node_modules`, the SDK, data dirs, etc. |

## File Reference

| File | Purpose |
|------|---------|
| `Dockerfile` | Cloud Run `app` container (nginx + gunicorn). |
| `osrm/` | The `desire-path-osrm` container + city datasets. |
| `cloudbuild.yaml` | Build + deploy pipeline for both services. |
| `.gcloudignore` | Files excluded from Cloud Build uploads. |
| `terraform/main.tf` | All infrastructure. |
| `terraform/terraform.tfvars` | Project config (gitignored). |
| `deploy/nginx-cloudrun.conf`, `deploy/supervisord.conf` | In-container process config. |
</content>
