# Desire Path Mapper - TODO

## Current Status (2026-01-05)

The app is deployed and working at https://demo.sphericalharmonics.org

## Recent Changes

- Fixed favicon to use 💠 emoji
- Renamed title to "Desire Path Mapper"
- Auto-detect prod/dev environment for API/WS URLs in client config
- Added Redis connection logging to confirm cloud vs local
- Avoid ferries for all modes (bike, walk, drive)
- Improved responsive layout for mobile

## Known Issues

### Environment Variables Reset on Deploy
Cloud Build creates new revisions that don't inherit env vars. After each deploy:

```bash
gcloud run services update desire-path-mapper-prod \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey \
  --set-env-vars="REDIS_HOST=10.63.107.3,REDIS_PORT=6379,ORS_API_KEY=<key>"
```

**Important**: ORS_API_KEY must have NO trailing newline or it causes HTTP header errors.

## Future Improvements

### High Priority
- [ ] Store env vars in Terraform/Secret Manager so they persist across deploys
- [ ] Add proper cache headers to nginx config

### Features
- [ ] Show route distance/duration in UI
- [ ] Add "reverse route" button
- [ ] Remember last route in localStorage
- [ ] Share route via URL

### Infrastructure
- [ ] Set up GitHub Actions for CI/CD
- [ ] Add health check endpoint
- [ ] Add proper gunicorn/uwsgi for production Flask

## Quick Reference

### Local Development
```bash
redis-server --daemonize yes
cd server && source env/bin/activate && python app.py
cd client-react && npm run dev
# Open http://localhost:3000
```

### Deploy
```bash
gcloud builds submit --config=cloudbuild.yaml --project=google-mpf-ywspom2sxeey
```

### View Logs
```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=desire-path-mapper-prod" \
  --project=google-mpf-ywspom2sxeey \
  --limit=30 \
  --format="table(timestamp,textPayload)"
```

### Check Env Vars
```bash
gcloud run services describe desire-path-mapper-prod \
  --region=us-central1 \
  --project=google-mpf-ywspom2sxeey \
  --format="yaml(spec.template.spec.containers[0].env)"
```
