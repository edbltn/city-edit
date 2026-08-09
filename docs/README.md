# City Edit — Documentation

Index and conventions for everything under `docs/`. Start here.

Published at **<https://edbltn.github.io/city-edit/>** (MkDocs Material, rebuilt
from `main` on every push). Everything here reads equally well on GitHub.

## Algorithms

**If you are new to the codebase, start here.** City Edit runs on bespoke
algorithms no library provided — block identification, two families of top
proposal, corridor growth, signed heat. Each has a **dossier** in
[algorithms/](algorithms/README.md): pseudocode, tuning knobs, invariants, the
failure that produced each rule, and where to extend it.

Dossiers are **machine-bound to their source** — `make docs-check` verifies that
every cited symbol still exists and every documented constant still matches. If
you change one of these algorithms, updating its dossier is part of the change,
not a follow-up. [The contract is here](algorithms/README.md#the-contract).

## Documents

| Doc | What it covers |
|-----|----------------|
| [algorithms/](algorithms/README.md) | **Source of truth** for how the bespoke algorithms actually work — one pseudocode dossier per algorithm, bound to the code by a CI check. |
| [three-layer-model.md](three-layer-model.md) | **Source of truth** for the three-layer separation — edge/node graph (storage) → blocks (aggregation, display, interaction) → route proposals (derived clustering) — and the block-scoped vote semantics (clear-then-cast, the one-direction-per-block invariant). |
| [voting-architecture.md](voting-architecture.md) | **Source of truth** for Layer-1 vote mechanics — how a vote is identified, stored, reconciled, and migrated (the unified edge-based model). Write-path *semantics* are defined by three-layer-model.md §4. |
| [url-routing.md](url-routing.md) | How the SPA resolves *which map* to show (slug / subdomain / apex), how links are built, and how an admin adds a vanity subdomain or renames a slug. **Source of truth** for the redirect inventory (every redirect/rewrite the system performs) and `?src=` visit-source tracking. |
| [flask-considerations.md](flask-considerations.md) | Backend architecture notes for the real-time vote broadcast path and running across multiple Flask servers. |
| [testing.md](testing.md) | The test taxonomy — (backend, frontend) × (unit, integration, E2E) — and how to run each. |
| [agents.md](agents.md) | Every way this project has used agents: the simulated-user load/stress fleets (Locust, the tiered saturation test, the budgeted multi-tenant swarm) and the Claude agents that built and reviewed the code (subagent roster, worktree parallelism, the overnight Haiku swarm and what its reviewers caught). |
| [debugging.md](debugging.md) | The debugging workflow: named debug tabs (`?tab=<name>`), client `[channel]` debug logging + `cityedit.dumpState()`, the server log-tag table, and curl probes. |
| [gcp-deployment.md](gcp-deployment.md) | Deploying to Google Cloud (Cloud Run + Memorystore + Artifact Registry + Cloud Build), mapping custom domains, and reaching/backing up prod Postgres. |
| [nyc-proposal-data-sources.md](nyc-proposal-data-sources.md) | Where NYC's **official** street-change proposals live (nycdotprojects.info, the VZV SIPs Socrata datasets, DOT yearly project pages) and verified scrape recipes for each — companion script `tools/nyc_proposals/fetch_latest.py`. |
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
- Heading anchors are GitHub-style on both GitHub and the published site (`mkdocs.yml` configures a GFM slugifier), so an in-page link written against one works on the other.

## The published site

```bash
uv pip install -r docs/requirements.txt   # once
make docs-serve                           # http://127.0.0.1:8000
make docs-check                           # the algorithm-dossier binding check
```

`.github/workflows/docs.yml` runs the binding check and a `--strict` build on
every push and PR, then deploys `main` to GitHub Pages. `mkdocs.yml` holds the
nav.

**Not published:** `archive/` (superseded designs — publishing them next to the
current docs is how someone implements the wrong thing) and `course/` (an
interactive tutoring workspace, not reference material). Both stay in the repo
and remain readable on GitHub.

**Custom domain.** To move the site to `docs.cityedit.org`: add a `CNAME` file
containing that hostname under `docs/`, point a DNS `CNAME` record at
`edbltn.github.io`, and set the custom domain in the repo's Pages settings. Until
then the `github.io` URL is canonical — `site_url` in `mkdocs.yml` is the one
place to change.

### Proposal: where the docs link belongs in the app

*Not implemented — the header rail is being reworked separately. Written down so
whoever finishes that work can pick it up.*

The nav rail already carries five items (How it works · Blog · About · Feedback ·
Donate). A sixth button works against the decluttering, so:

1. **Preferred — the landing footer** (`client-react/src/components/Landing/Landing.tsx`,
   the `landing-footer` element, currently just a copyright line). A "How it
   works, in depth" link there costs no header real estate and catches exactly
   the reader who scrolled to the bottom of a map picker.
2. **Fold it into "How it works"** rather than adding a peer — that item already
   promises this content; the docs site is its long form.
3. **Also link it from the repo README and the About page**, which is where most
   would-be contributors actually arrive.

Whichever lands, the target is the `site_url` above.
</content>
</invoke>
