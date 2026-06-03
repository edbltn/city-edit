# Understand: Prod memory pressure, minScale, and getting OSRM into prod

Scope: the 4 questions from this session —
1. Why is prod so memory-intensive? What can we optimize? Cost of bumping it?
2. What does `minScale` actually do?
3. Can we serve all 3 cities from one OSRM instead of 3?
4. Get OSRM running in prod (currently absent → Python fallback) — and implement it.

## Stage 1 — Why so memory-intensive (the "why")
- [x] The OOM mechanism: 16431/16701/16814 MiB > 16384 limit → SIGKILL → cold-start loop
- [x] What's actually resident per city: networkx graph (~4-6GB, the hog) + rustworkx (~0.3-0.5GB) + kdtree + derived dicts + 153MB topology_json string
- [x] Understood *why* networkx dwarfs rustworkx: per-element Python dict/object overhead vs packed Rust arrays
- [x] Why `max_loaded=3` + boot prewarm forces *all three* cities resident at once (no headroom)

## Stage 1b — Optimizations (the "what could change")
- [x] Verified networkx is NOT dead weight: used by route geometry, reverse_geocode, get_graph_for_bbox
- [x] Investigated "do we need graphs with OSRM?" → YES (votes keyed to edge IDs, topology viz, snapping, geocoding). OSRM is only the pathfinder.
- [x] KEY FINDING: rustworkx + kdtree are built eagerly per city but only used by the Python *fallback* router → dead weight when OSRM is up → lazy-load candidate
- [ ] Remaining code wins: gzip/evict topology_json (153MB/city); consolidate onto CityGraph compact structs so networkx can be freed (big, risky)
- [ ] Infra knobs: max_loaded=2, prewarm only default city

## Stage 1c — Cost of bumping (Terraform / Cloud Run)
- [ ] How Cloud Run bills (vCPU-sec + GiB-sec), and the vCPU↔memory pairing rule that forces 4→8 vCPU for >16Gi
- [ ] Rough $/month delta for 16Gi/4vCPU → 32Gi/8vCPU on an always-on min instance
- [ ] Why "optimize the footprint" can be cheaper than "buy more RAM forever"

## Stage 2 — minScale
- [ ] What `minScale`/`maxScale` do (warm floor vs ceiling), and why minScale=1 didn't save us (OOM kills the floor instance)
- [ ] Cold start vs warm: the 43s vs 90ms you measured
- [ ] The cost implication of raising minScale (you pay for idle warm instances)

## Stage 3 — One OSRM for 3 cities (#3)
- [ ] Current design: 3 OSRM containers, one per city, each its own PBF (`cities.py` `osrm_service`)
- [ ] Why one merged dataset works (OSRM doesn't care the cities are far apart; coords are global)
- [ ] How to merge: osmium merge the 3 PBFs → one extract → one `osrm-extract/partition/customize`
- [ ] The code change: point all cities at one host (`OSRM_HOST`), collapse compose services

## Stage 4 — OSRM in prod (#4) — design + implement
- [x] Why prod has no OSRM today (Cloud Run = single image; the compose OSRM services don't exist there)
- [x] Options weighed: separate Cloud Run service (chosen — RAM isolation), sidecar, in-container supervisord
- [x] Implemented: osrm/Dockerfile + build-merged.sh, TF desire-path-osrm service + invoker IAM, OSRM_URL on app, cloudbuild steps, ID-token auth in osrm_router.py
- [ ] Verify in prod logs: routes hit OSRM (no more "OSRM failed, falling back") — pending deploy

## Bonus — graph asset cleanup (interjected request)
- [x] Corrected premise: networkx is NOT removable (backs topology endpoint, reverse_geocode, snapping)
- [x] Real win: rustworkx routing graph deferred to first fallback route (was built eagerly per city)
- [ ] Optional future: migrate get_graph_for_bbox/reverse_geocode onto CityGraph compact structs → then networkx can be freed (big refactor)

## Rollout sequence (bootstrap order matters — chicken/egg on :latest image)
1. Build + push `osrm:latest` to Artifact Registry FIRST (TF service references it).
2. `terraform apply` — creates `desire-path-osrm`, grants app SA run.invoker, sets `OSRM_URL` on app.
3. Subsequent deploys: `gcloud builds submit` (cloudbuild deploys osrm then app).
4. Verify: prod logs show OSRM 200s, no "[ROUTE] OSRM failed, falling back".
NOTE: local end-to-end build blocked by Docker Desktop storage I/O error + 97%-full disk → verify via Cloud Build.

## Status: implementation complete; prod deploy + verification pending
