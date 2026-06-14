# City Edit — TODO

## Current Status

Deployed and live at <https://cityedit.org>. Pushes to `main` auto-deploy via
Cloud Build.

## Recently Resolved

These were once open items and are now in place:

- **Health check** — `GET /health` (`server/app.py`).
- **Production Flask server** — gunicorn with the gevent worker, under
  supervisord (`deploy/supervisord.conf`); the dev server is local-only.
- **CI/CD** — `.github/workflows/deploy.yml` deploys on push to `main`.
- **Secrets in Secret Manager** — `DATABASE_URL`, `SECRET_KEY`, and `ADMIN_TOKEN`
  are managed as `google_secret_manager_secret` resources in `terraform/`, so
  they persist across deploys (Cloud Build uses `services update`, which keeps
  existing env vars). The old "env vars reset on every deploy" problem — which
  hinged on the now-removed `ORS_API_KEY` — no longer applies.

## Open

### Infrastructure

- [ ] Add proper cache headers to the nginx config for static assets.
- [ ] Plan Redis HA — it's a single point of failure carrying both vote counts
      and pub/sub (see [docs/flask-considerations.md](docs/flask-considerations.md)).

### Features

- [ ] Show route distance / duration in the UI.
- [ ] Add a "reverse route" button.
- [ ] Remember the last route in `localStorage`.
- [ ] Share a route via URL (selection sharing already exists for points/vote
      type — see [docs/url-routing.md](docs/url-routing.md)).

## Quick Reference

### Local development

```bash
redis-server --daemonize yes
cd server && source env/bin/activate && python app.py   # http://localhost:5001
cd client-react && npm run dev                          # http://localhost:3000
```

### Deploy

```bash
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

### View logs

```bash
gcloud run services logs read desire-path-mapper \
  --project=google-mpf-ywspom2sxeey --region=us-central1 --limit=50
```

> The Cloud Run service is named `desire-path-mapper` (a legacy resource ID), not
> `cityedit`. See [docs/README.md](docs/README.md#naming-canon).
</content>
