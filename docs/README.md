# City Edit — Documentation

Index and conventions for everything under `docs/`. Start here.

## Documents

| Doc | What it covers |
|-----|----------------|
| [voting-architecture.md](voting-architecture.md) | **Source of truth** for how a vote is identified, stored, cast, reconciled, and migrated (the unified edge-based model). |
| [url-routing.md](url-routing.md) | How the SPA resolves *which map* to show (slug / subdomain / apex), how links are built, and how an admin adds a vanity subdomain. |
| [flask-considerations.md](flask-considerations.md) | Backend architecture notes for the real-time vote broadcast path and running across multiple Flask servers. |
| [testing.md](testing.md) | The test taxonomy — (backend, frontend) × (unit, integration, E2E) — and how to run each. |
| [gcp-deployment.md](gcp-deployment.md) | Deploying to Google Cloud (Cloud Run + Memorystore + Artifact Registry + Cloud Build), mapping custom domains, and reaching/backing up prod Postgres. |
| [archive/](archive/) | Historical design docs that no longer match the code. See [archive/README.md](archive/README.md). |

The frontend and load test have their own local READMEs: [`client-react/README.md`](../client-react/README.md) and [`loadtest/README.md`](../loadtest/README.md). The top-level [`README.md`](../README.md) is the project front door (quickstart + architecture).

## Naming canon

To keep docs consistent, these names are fixed. Use them verbatim.

| Concept | Canonical name | Notes |
|---------|----------------|-------|
| Product / brand | **City Edit** | Not "Desire Path Mapper" (the former name). |
| Primary domain | **cityedit.org** | Apex = landing/map picker; subdomains (e.g. `bikepaths.cityedit.org`) are vanity aliases for a map slug. `demo.sphericalharmonics.org` is **legacy** — do not document it. |
| Cloud Run app service | `desire-path-mapper` | A real GCP resource name — **not** `desire-path-mapper-prod`. Infra names below keep the `desire-path-*` prefix and must not be "rebranded" in docs. |
| Cloud Run OSRM service | `desire-path-osrm` | Self-hosted routing engine, one dataset per supported city. |
| Artifact Registry repo | `desire-path-mapper` | Holds the `app` and `osrm` images. |
| Memorystore Redis | `desire-path-prod` | Vote counts + pub/sub. Single shared instance (load-bearing — see flask-considerations). |
| Cloud SQL Postgres | `desire-path-votes-prod` | Durable vote rows. |
| GCP project / region | `google-mpf-ywspom2sxeey` / `us-central1` | |

**Routing:** OSRM-per-city is the only router in prod; the in-process Python/Dijkstra router (`python_router.py`) is a fallback. There is no ORS, "hybrid", or "roam" routing in the codebase — those were designs that were never built (see `archive/`).

## Style conventions

- Every doc opens with an `# H1` title and a one-line statement of what it covers.
- Section headings use Title Case.
- Fenced code blocks always declare a language (` ```bash `, ` ```python `, ` ```tsx `).
- Reference code as `path/to/file.py:line` so it's clickable; prefer naming the function/symbol over a bare line number when the file changes often.
- Cross-link other docs with relative paths (`[testing.md](testing.md)`).
- When a doc is the authority for something, say so ("source of truth") and link to it from the others instead of duplicating.
</content>
</invoke>
