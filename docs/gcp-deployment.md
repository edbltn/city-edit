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

> **Deploy to [staging](#staging-deploy-here-first) first**, verify, then
> promote the *same image digest* to prod.

> ## ⚠️ ALWAYS back up the prod DB locally before deploying.
>
> Every deploy — no exceptions. Take a fresh local snapshot of Cloud SQL
> (`desire-path-votes-prod`) **before** you run any deploy command below, so a
> bad migration or resnap can be rolled back. See
> [Making a snapshot](#making-a-snapshot); snapshots live in
> `~/city-edit-prod-backups/<UTC-timestamp>/`, never in the repo.
>
> ```bash
> # 0. BACKUP FIRST — open the tunnel, then snapshot (see "Database Access & Backups")
> DIR=~/city-edit-prod-backups/$(date -u +%Y%m%d-%H%M%S)
> mkdir -p "$DIR" && pg_dump "$PROD_DB_URL" -Fc -f "$DIR/prod-full.dump"
> # …only then deploy.
> ```

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

## Staging (deploy here FIRST)

Full plan/rationale: [staging-parity-plan.md](staging-parity-plan.md). Staging
is a parity copy of the app service — same image digests, same 8Gi/4CPU shape —
with its own Redis (`desire-path-staging`), its own database (`votes_staging`,
co-located on the prod Cloud SQL instance), and staging-scoped
`admin-token-staging` / `secret-key-staging` secrets. OSRM, the previews
bucket, and the image registry are shared. It runs with `APP_ENV=staging`,
which gates the client's canonical-subdomain redirect (otherwise testers
bounce to prod), shows the STAGING ribbon, and noindexes every response.

**The URL is the secret.** The service name carries a random token
(`ce-stg-<token>`), so its `run.app` URL is unguessable and — because run.app
uses Google's shared wildcard cert — never published in Certificate
Transparency logs. Never commit or publicly paste it. Look it up with:

```bash
cd terraform && terraform output -raw staging_url    # or:
gcloud run services list --project=google-mpf-ywspom2sxeey --format='value(URL)' | grep ce-stg
```

To rotate a leaked URL: change `staging_token` in `terraform.tfvars`, targeted
apply (creates the new service), delete the old one.

### Digest promotion workflow

```bash
# 1. Build the overlay image (unchanged, ~2-3 min) → note the digest D
gcloud builds submit --config=cloudbuild.overlay.yaml ...

# 2. Deploy D to STAGING and verify there (load waterfall, smoke vote,
#    [MAPLOAD] beacons filtered to the staging service name)
gcloud run services update ce-stg-<token> --region=us-central1 \
  --project=google-mpf-ywspom2sxeey --image=<registry>/app@sha256:D

# 3. BACKUP PROD DB (mandatory, unchanged), then promote the SAME digest
gcloud run services update desire-path-mapper --region=us-central1 \
  --project=google-mpf-ywspom2sxeey --image=<registry>/app@sha256:D

# 4. Served-asset-hash check on prod (unchanged)
```

Because staging runs the exact digest, graph/edge-id parity is automatic; a
full-bake deploy (graph changes) rehearses its resnap on staging's DB the same
way.

### Seeding / refreshing staging data

`make stage-refresh` restores the newest `~/city-edit-prod-backups/*/prod-full.dump`
into `votes_staging` and flushes staging Redis (the app then self-heals: each
map's first request replays Postgres → Redis via `_hydrate_map_redis`). It
needs two bastion tunnels open:

```bash
# :5433 → Cloud SQL (same tunnel as prod DB access — see below)
# :6380 → staging Redis (host: cd terraform && terraform output -raw staging_redis_host)
gcloud compute ssh bastion-prod --zone=us-central1-a \
  --project=google-mpf-ywspom2sxeey --tunnel-through-iap \
  --ssh-flag="-N" --ssh-flag="-L 5433:10.39.0.3:5432" \
  --ssh-flag="-L 6380:<staging-redis-host>:6379"
```

The staging terraform lives in `terraform/staging.tf` — apply it **only with
`-target`** on staging resources, and only after the plan shows adds-only (the
blanket-apply landmines below still stand).

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

## Database Access & Backups

### Reaching prod Postgres (IAP tunnel)

Cloud SQL (`desire-path-votes-prod`) has no public IP. Reach it through the
`bastion-prod` VM over IAP, binding the tunnel to **local port 5433**.

> **⚠️ Use 5433, never 5432.** Local dev's own Postgres lives on `localhost:5432`
> (`server/.env` `DATABASE_URL`). A tunnel on `5432` shadows it, so a host-run
> Flask or `psql` silently connects to **prod**. Keep prod on 5433 and never
> repoint `server/.env` at it — pass the prod URL inline for the one command
> that needs it, then kill the tunnel.

```bash
# Terminal A — open the tunnel (prod → local 5433)
gcloud compute ssh bastion-prod --zone=us-central1-a \
  --project=google-mpf-ywspom2sxeey --tunnel-through-iap \
  --ssh-flag="-N" --ssh-flag="-L 5433:10.39.0.3:5432"

# Terminal B — connect with prod creds via localhost:5433
PROD_DB_URL="$(gcloud secrets versions access latest --secret=database-url-prod \
  --project=google-mpf-ywspom2sxeey | sed -E 's#@[^/]+/#@localhost:5433/#')"
psql "$PROD_DB_URL"
```

### Where backups live

Manual prod snapshots are stored **outside the repo**, on the operator's machine
at `~/city-edit-prod-backups/<UTC-timestamp>/` (e.g. `20260603-192923/`). They
hold real vote data, so they are intentionally not committed — the repo's
`.gitignore` also drops loose `*.dump` files. Each snapshot directory contains:

| File | What it is |
|------|------------|
| `prod-full.dump` | `pg_dump -Fc` custom-format dump of the whole DB — restore with `pg_restore`. |
| `prod-full.sql.gz` | Same dump as gzipped plain SQL, for `psql`-based restore or eyeballing. |
| `edge_votes.csv`, `maps.csv`, `vote_types.csv` | Per-table CSV exports (the load-bearing vote tables) for quick inspection or partial reload. |
| `edge_votes.postbackfill.csv` | A second `edge_votes` export taken *after* a backfill/migration step, when one was run. |
| `SHA256SUMS.txt` | Checksums of every file above, for integrity verification. |

### Making a snapshot

With the tunnel open (above), from a fresh timestamped directory:

```bash
DIR=~/city-edit-prod-backups/$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$DIR" && cd "$DIR"

# Full dumps (custom + plain SQL)
pg_dump "$PROD_DB_URL" -Fc -f prod-full.dump
pg_dump "$PROD_DB_URL" | gzip > prod-full.sql.gz

# Per-table CSV exports
for t in maps vote_types edge_votes; do
  psql "$PROD_DB_URL" -c "\copy $t TO '$DIR/$t.csv' WITH CSV HEADER"
done

shasum -a 256 * > SHA256SUMS.txt
```

### Restoring

Verify integrity first, then restore into a target DB. **Never restore into prod
casually** — `pg_restore --clean` drops existing objects. The safe path is to
restore into **local dev** (`localhost:5432`, the `votes` database):

```bash
cd ~/city-edit-prod-backups/<timestamp>
shasum -a 256 -c SHA256SUMS.txt          # confirm files are intact

# Restore the full dump into local dev's votes DB
pg_restore --clean --if-exists --no-owner \
  -d "postgresql://app:devpassword@localhost:5432/votes" prod-full.dump
```

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
