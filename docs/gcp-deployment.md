# GCP Deployment Guide

## Overview

The app runs on Google Cloud Platform using:
- **Cloud Run**: Serves the Flask API + static files in a single container
- **Memorystore Redis**: Managed Redis 7.0 instance (1GB)
- **Artifact Registry**: Docker image storage
- **Cloud Build**: CI/CD pipeline for building and deploying

Live URL: `https://desire-path-mapper-prod-906562157830.us-central1.run.app`
Custom domain (pending): `https://demo.sphericalharmonics.org`

## Current Status

### Working
- Cloud Run service deployed and public
- Artifact Registry configured
- Cloud Build permissions configured via Terraform
- Static files (HTML, CSS, JS) serving correctly

### Pending
- **Domain verification**: Need to verify `sphericalharmonics.org` ownership at [Google Webmaster Central](https://www.google.com/webmasters/verification/verification)
- **Redis connection**: Cloud Run needs VPC connector to reach Memorystore Redis

### Recent Fixes
- **Dockerfile**: Changed `COPY server/app.py .` to `COPY server/*.py .` (was missing roam_cache, tiles, desire_path_voting modules)
- **Dockerfile**: Changed `location /ws` to `location = /ws` (exact match to prevent `/ws.js` routing to Flask)
- **Added `.gcloudignore`**: Excludes node_modules, google-cloud-sdk, etc. from Cloud Build uploads

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)
- Docker Desktop (optional, Cloud Build handles builds)

## Project Configuration

- **Project ID**: `google-mpf-ywspom2sxeey`
- **Region**: `us-central1`
- **Environment**: `prod`

## Authentication

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project google-mpf-ywspom2sxeey
```

## Deploying Changes

### Option 1: Cloud Build (Recommended)

Build, push, and deploy in one command:

```bash
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

### Option 2: Manual Docker Build

```bash
# Build for Cloud Run (linux/amd64 required for M-series Macs)
docker build --platform linux/amd64 \
  -t us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest .

# Push to Artifact Registry
docker push us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest

# Deploy via Terraform
cd terraform && terraform apply
```

### Option 3: Quick Update (no Terraform)

```bash
gcloud run services update desire-path-mapper-prod \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey \
  --image=us-central1-docker.pkg.dev/google-mpf-ywspom2sxeey/desire-path-mapper/app:latest
```

## Environment Variables

Set the ORS API key:

```bash
gcloud run services update desire-path-mapper-prod \
  --set-env-vars="ORS_API_KEY=your-key-here" \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey
```

## Custom Domain Setup

### Step 1: Verify Domain Ownership

Before mapping a custom domain, verify ownership at Google Webmaster Central:

1. Go to: https://www.google.com/webmasters/verification/verification?domain=sphericalharmonics.org
2. Add a TXT record to your DNS as instructed
3. Click "Verify"

### Step 2: Add DNS Record (Namecheap)

In Namecheap: **Domain List > sphericalharmonics.org > Advanced DNS**

| Type | Host | Value | TTL |
|------|------|-------|-----|
| CNAME | demo | ghs.googlehosted.com. | Automatic |

### Step 3: Apply Terraform

```bash
cd terraform
terraform apply
```

This creates the `google_cloud_run_domain_mapping.custom` resource.

### Step 4: Verify

```bash
# Check DNS propagation
dig demo.sphericalharmonics.org CNAME

# Check mapping status
gcloud run domain-mappings describe \
  --domain=demo.sphericalharmonics.org \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey
```

SSL is auto-provisioned once DNS propagates (5-30 minutes).

## Infrastructure from Scratch

```bash
cd terraform
terraform init
terraform apply
```

This creates:
- Artifact Registry repository
- Memorystore Redis instance (has `prevent_destroy = true`)
- Cloud Run service with public access
- Cloud Build API + IAM permissions
- Custom domain mapping

## Terraform Outputs

```bash
terraform output service_url       # App URL
terraform output redis_host        # Redis internal IP
terraform output registry_url      # Where to push Docker images
terraform output custom_domain     # Custom domain name
terraform output domain_mapping_dns # DNS records to add
```

## IAM Permissions Required

Your GCP account needs:
- `roles/cloudbuild.builds.editor` - Submit Cloud Build jobs
- `roles/run.admin` - Manage Cloud Run services
- `roles/artifactregistry.writer` - Push Docker images

Or just grant Owner:
```bash
gcloud projects add-iam-policy-binding google-mpf-ywspom2sxeey \
  --member="user:eric@sphericalharmonics.org" \
  --role="roles/owner"
```

Cloud Build service account permissions are managed by Terraform.

## Organization Policy

To allow public access (`allUsers`), the organization policy for `iam.allowedPolicyMemberDomains` must be disabled:

1. Go to: https://console.cloud.google.com/iam-admin/orgpolicies/iam-allowedPolicyMemberDomains?project=google-mpf-ywspom2sxeey
2. Click "Manage Policy"
3. Select "Override parent's policy"
4. Set "Policy enforcement" to "Off"
5. Save

## Architecture

The Dockerfile creates a single container running:
- **nginx** on port 8080 (Cloud Run's expected port)
  - Serves static files from `/usr/share/nginx/html`
  - Proxies `/api/` to Flask
  - Proxies `/ws` (exact match) to Flask for WebSocket
- **Flask** on localhost:5001

This differs from local Docker Compose which uses separate containers.

## Troubleshooting

### "ModuleNotFoundError: No module named 'roam_cache'"
The Dockerfile wasn't copying all Python files. Fixed by changing to `COPY server/*.py .`

### "/ws.js returns 502"
Nginx `location /ws` was matching `/ws.js`. Fixed by using `location = /ws` for exact match.

### "Uploading 25k files to Cloud Build"
Missing `.gcloudignore`. Created one to exclude node_modules, google-cloud-sdk, etc.

### "PERMISSION_DENIED on gcloud builds submit"
Grant yourself Cloud Build permissions:
```bash
gcloud projects add-iam-policy-binding google-mpf-ywspom2sxeey \
  --member="user:eric@sphericalharmonics.org" \
  --role="roles/cloudbuild.builds.editor"
```

### "Caller is not authorized to administer the domain"
Verify domain ownership at Google Webmaster Central before mapping custom domains.

### "Image not found"
Build and push the image before running `terraform apply`.

### "allUsers not permitted"
Update the organization policy (see above).

### ARM architecture error
Use `--platform linux/amd64` when building on M-series Macs, or use Cloud Build.

## File Locations

| File | Purpose |
|------|---------|
| `Dockerfile` | Cloud Run container (nginx + Flask) |
| `cloudbuild.yaml` | Cloud Build pipeline definition |
| `.gcloudignore` | Files to exclude from Cloud Build uploads |
| `terraform/main.tf` | Infrastructure definition |
| `terraform/terraform.tfvars` | Project config (not in git) |
| `server/Dockerfile` | Local development container |

## Viewing Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=desire-path-mapper-prod" \
  --project=google-mpf-ywspom2sxeey \
  --limit=30 \
  --format="table(timestamp,severity,textPayload)"
```
