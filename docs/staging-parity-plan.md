# Staging ↔ Prod Parity — Plan

*Drafted 2026-07-23. Status: proposal, nothing provisioned yet.*

## Goal

A always-on staging deployment of City Edit that:

1. **Matches prod by construction** — same container image (same baked graphs,
   PMTiles, venv), same service shape, its own copy of prod-like data — so
   "worked on staging" actually predicts "works on prod".
2. Is reachable at an **unguessable URL** we can hand to testers, with no
   Google-account ceremony.
3. Becomes the **first stop for every deploy**: build → deploy to staging →
   verify → promote the *same image digest* to prod. No more first-visitor
   experiments on prod (today's cold-load work was measured live on prod
   because there was nowhere else to measure it).

## Current prod topology (what we're mirroring)

| Piece | Prod resource | Notes |
|---|---|---|
| App | Cloud Run `desire-path-mapper` (8Gi/4CPU, minScale 1, maxScale 8) | nginx + Flask in one container; graphs baked into image |
| Routing | Cloud Run `desire-path-osrm` | Stateless; called with a Cloud Run ID token (audience = its URL) |
| Votes (serving) | Memorystore Redis 1GB (`allkeys-lru`) | The heatmap serves ONLY from Redis |
| Votes (durable) | Cloud SQL `desire-path-votes-prod` (private IP, via VPC connector) | `_populate_redis` hydrates Redis from Postgres at boot |
| Secrets | Secret Manager: `database-url-prod`, `admin-token`, `secret-key` | secretRef'd into the service |
| Previews | Private GCS bucket, Flask proxies `/previews/` | |
| Domains | `cityedit.org` + per-map subdomains via Cloud Run domain mappings | |

## Proposed staging topology

**Duplicate** (state-bearing or config-bearing — must not be shared):

- **Cloud Run app service** — name carries the unguessable token (see below),
  e.g. `ce-stg-<token>`. Same image reference, same env shape, same 8Gi/4CPU
  (parity beats savings here — memory behavior is exactly what staging must
  reproduce), but `minScale = 0` and `maxScale = 2` (cost: ~$0 idle; the
  ~5-min prewarm window after cold start is acceptable for staging and is
  itself a thing we want to observe).
- **Redis** — second Memorystore Basic 1GB instance (`redis-staging`). The
  codebase does not namespace keys per environment, so sharing prod Redis is
  off the table. (~$35/mo — the main recurring cost.)
- **Database** — a separate *database* `votes_staging` + user on the **same**
  Cloud SQL instance, with its own secret `database-url-staging`. A second
  db-f1-micro instance (~$10/mo) is the upgrade path if staging load ever
  visibly disturbs prod queries — start cheap, the instance is idle most of
  the day. Seeded from our existing `pg_dump` snapshots (see below).
- **ADMIN_TOKEN / SECRET_KEY** — staging-specific secrets, so a leaked
  staging token can't administer prod.

**Share** (stateless or read-only):

- **OSRM service** — routing is stateless and read-only; grant the staging
  service account `run.invoker` on `desire-path-osrm`. Fork it later only if
  we start testing profile/dataset changes on staging.
- **Previews bucket** — read-only from staging's point of view.
- **Artifact Registry image** — the whole point: staging runs the digest prod
  will run next.

**Explicitly out of scope for staging:** the screenshot job, OSM refresh
scheduler, domain mappings for map subdomains, and alerting (see Monitoring).

## The unguessable URL

Options, with the honest trade-offs:

1. **Randomized Cloud Run service name → default run.app URL** ⭐ recommended
   first step. Name the service with a random token, e.g. `ce-stg-x7k2m9q4` →
   `https://ce-stg-x7k2m9q4-katze52zaq-uc.a.run.app`. The TLS cert is Google's
   shared `*.a.run.app` wildcard, so **nothing about the hostname is ever
   published**. Zero DNS/cert work. Caveat: the `katze52zaq` project hash is
   shared with prod's run.app URL, so treat the random token as the secret —
   16+ chars of entropy in the service name.
2. **`stg-<token>.cityedit.org` domain mapping** — prettier, but a Google-
   managed certificate is issued per hostname and **Certificate Transparency
   logs publish every issued hostname**. Anyone watching CT for
   `%.cityedit.org` learns the "unguessable" name within hours. Only viable
   with a wildcard cert (`*.cityedit.org`) on a load balancer — real money and
   infra for a staging nicety. Skip unless/until we want it.
3. **Belt-and-suspenders app gate** (optional layer on top of 1): a
   `STAGING_ACCESS_TOKEN` env — nginx or Flask middleware 404s every request
   unless a `?key=` param or cookie matches, and the param sets the cookie.
   The shareable link becomes `https://…/?key=<token>`. Cheap to add if we
   ever suspect the URL leaked; not needed day one.

Recommendation: **option 1 now**, option 3 in the back pocket. URL rotation =
deploy under a new random name and delete the old service (10 minutes).

## Code changes needed (small but real)

1. **Subdomain redirect must be gated.** `MapApp` redirects any map with a
   canonical subdomain to `<subdomain>.cityedit.org` — on staging, opening
   `/m/nyc-bikes` would bounce the tester to **prod**. Add an env-driven flag
   (e.g. server injects `staging: true` into `/api/maps/<slug>` config or an
   `APP_ENV=staging` reflected in a `/api/env` or the existing config
   endpoint) and skip `subdomainRedirectUrl` when set. This is the one change
   that touches app logic; everything else is infra.
2. **`X-Robots-Tag: noindex, nofollow`** on every staging response (nginx
   `add_header` behind the same env flag) — an unguessable URL stays
   unguessable only if it never gets indexed after an accidental link.
3. **Telemetry separation** — free by default: the `cityedit_map_load_ms`
   metric and alert filters all pin `resource.labels.service_name=
   "desire-path-mapper"`, so staging beacons/logs won't pollute prod
   dashboards. Optionally add a staging row to the dashboard later.
4. **`FLASK_ENV`-style banner (optional):** a small "STAGING" ribbon in the
   client when the env flag is set, so screenshots are never mistaken for
   prod.

## Terraform strategy (respecting the landmines)

The known landmines stand: a blanket `terraform apply` wipes
`ADMIN_TOKEN`/`SECRET_KEY` secret versions and tries to create the ebikes
domain mapping — **app deploys go through gcloud, never `terraform apply`**
(docs/gcp-deployment.md, memory: prod-deploy-landmines).

- Add staging resources **additively** in a new `terraform/staging.tf`:
  `google_redis_instance.cache_staging`, `google_sql_database.votes_staging`,
  `google_sql_user.staging`, `google_secret_manager_secret.database_url_staging`
  (+ version + IAM), `google_secret_manager_secret.admin_token_staging`,
  `secret_key_staging`, `google_cloud_run_service.app_staging` (with
  `lifecycle { ignore_changes = [template[0].spec[0].containers[0].image] }`
  so gcloud-driven image updates don't fight the plan), IAM: public invoker on
  staging app, staging SA invoker on OSRM.
- Apply **only with `-target`** on the new resources, exactly like the
  monitoring workflow already documents. Verify the plan shows *only adds*
  before applying — if it wants to touch any existing prod resource, stop.
- The service name token: generate once (`openssl rand -hex 8`), store it in
  `terraform.tfvars` (`staging_token = "…"`), never commit the tfvars value.

## Data seeding & refresh

- **Seed:** `pg_restore` the newest `~/city-edit-prod-backups/<ts>/prod-full.dump`
  into `votes_staging` through the existing bastion tunnel (localhost:5433 —
  same instance, different database name, staging creds). On first boot,
  staging's `_populate_redis` hydrates its own Redis from it. No resnap needed
  as long as staging runs the same image (same graphs → same edge ids) — the
  invariant that already powers the overlay-deploy flow.
- **Refresh cadence:** manual to start — a `make stage-refresh` target that
  (1) takes the routine pre-deploy prod dump we already make, (2) restores it
  to `votes_staging`, (3) `redis-cli -h <staging-redis> FLUSHALL` via the
  bastion + restart the staging service (or PUBLISH `graph_reload`) so it
  rehydrates. Automate on a weekly Cloud Scheduler job only if manual gets
  tedious.
- The dump contains no real PII (device ids are hashes, voter ids are UUIDs),
  so no scrubbing pass is required.

## The new deploy workflow (the parity payoff)

```
1. cloudbuild.overlay.yaml → app:latest (digest D)          (~2-3 min, unchanged)
2. gcloud run services update ce-stg-<token> --image=D       (staging first)
3. Verify on staging: measure_load waterfall, cityedit.dumpState(),
   smoke-vote on a staging map, [MAPLOAD] beacon in staging logs
4. Fresh prod DB backup (unchanged, mandatory)
5. gcloud run services update desire-path-mapper --image=D   (the SAME digest)
6. Served-asset-hash check on prod (unchanged)
```

Step 2-3 replace "measure it live on prod and hope" — the exact digest gets a
dress rehearsal including the post-deploy prewarm window. Full-bake deploys
(graph changes) follow the same shape; because staging shares the image,
graph/edge-id parity is automatic, and the resnap runbook applies to staging's
DB the same way it applies to prod's.

## Costs

| Item | ~$/mo |
|---|---|
| Memorystore Basic 1GB (staging) | ~35 |
| Cloud Run staging app (minScale 0, light use) | ~1-5 |
| Extra database on existing Cloud SQL instance | 0 |
| Shared OSRM / previews / registry | 0 |
| **Total** | **~$40/mo** |

## Rollout order

1. **Code:** staging env flag (redirect gate + noindex + optional ribbon) —
   deployable to prod safely ahead of time (flag off = no behavior change).
2. **Terraform:** `staging.tf`, targeted apply (Redis first — it takes the
   longest to provision), then DB/user/secrets, then the Cloud Run service.
3. **Seed:** restore latest dump → `votes_staging`; boot staging; confirm
   hydration (`/api/graph-votes` non-empty, legend populated).
4. **Verify parity:** run the load-measurement harness against staging; numbers
   should match prod's post-00105 profile (~2.5s cold / ~1s warm).
5. **Adopt the workflow:** update docs/gcp-deployment.md + CLAUDE.md deploy
   instructions to make staging-first promotion the documented path.
6. Share `https://ce-stg-<token>-….run.app` with testers.

## Open questions for Eric

- OK to co-locate `votes_staging` on the prod Cloud SQL instance to start
  (upgrade to a separate instance only if staging load shows up in prod)?
- Is ~$40/mo acceptable for the always-available staging Redis? (The
  alternative — provision/teardown staging on demand — saves the $35 but
  makes "always available at a stable URL" false.)
- Should map *subdomain* behavior on staging show the map at the staging URL
  (recommended: flag simply disables redirects), or do we ever want
  staging-scoped subdomains (needs the wildcard-cert LB — I'd say no)?
