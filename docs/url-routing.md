# URL & Routing Architecture

How the app decides *which map* to show, how links are built, and how an admin
adds a vanity subdomain. There is **no router library** — the SPA inspects the
URL directly. Maps are addressed by **slug**; the legacy `?theme=` query param
is gone (the only mention left is a comment in `utils/shareLink.ts` documenting
that it no longer exists).

## The address space

| Form | Example | Meaning |
|------|---------|---------|
| Slug path | `cityedit.org/m/ny-bike-test` | Canonical address for any map |
| Slug query | `cityedit.org/?map=ny-bike-test` | Equivalent fallback (used for API/WS calls via `withMap`) |
| Subdomain | `bikepaths.cityedit.org` | A vanity host mapped to a map's slug in the DB |
| Apex | `cityedit.org` | Landing page (map picker) |
| Deep link | `/m/<slug>?z=&lat=&lng=&slat=&slng=&elat=&elng=&vt=` | A map plus camera + a pre-selected point/route + vote type |

The slug is the single source of truth. Subdomains are an optional alias that
resolve **to** a slug; the camera/selection params ride along on either form.

## Client resolution (the load path)

`App.tsx` → `resolveMapConfig()` (`map/runtime.ts`) decides what to load, with
**no hardcoded theme table** — it is fully data-driven:

1. **Explicit slug** — `/m/<slug>` (regex on the path) or `?map=<slug>`. Loads
   `GET /api/maps/<slug>`.
2. **Subdomain** — `detectSubdomain()` (`themes.ts`) returns the first host label
   (skipping apex, `localhost`, and reserved `www`/`demo`). Loads
   `GET /api/maps/by-subdomain/<sub>`, which looks up the `maps.subdomain` column.
3. **Default** — `nyc-walkways`.

The apex with no slug renders `Landing` instead (see `App.tsx` / `isLandingHost`).

### Canonical-subdomain redirect

If the resolved map has a `subdomain` and we're **not already on it** (and not on
localhost), `subdomainRedirectUrl()` sends the visitor there — e.g. opening
`cityedit.org/m/nyc-bikes` bounces to `bikepaths.cityedit.org`. The **query
string is preserved**, so a shared deep link
(`/m/nyc-bikes?slat=…&slng=…&vt=Add%20bike%20lane`) survives the hop and the
selection is restored on the subdomain. No redirect loop: on the canonical host
`detectSubdomain()` already equals the map's subdomain, so the helper returns
`null`.

### Theme/styling

`ThemeContext` derives the active theme from the **loaded map** (`themeFromMap`),
falling back to `detectTheme()` (hardcoded preset prefixes) only when no map
could be loaded. The map's `style` column drives the basemap/accent/heat ramp.

## Link building (sharing)

- `mapHref(slug, navState)` (`themes.ts`) → `/m/<slug>?…`. Used by `ModeSwitcher`
  and `Landing`.
- `buildSelectionUrl(point, voteType)` (`utils/shareLink.ts`) → absolute
  `/m/<slug>?z&lat&lng&slat&slng&vt` against `window.location.origin`. Because it
  uses the current origin, a link shared from `bikepaths.cityedit.org` stays on
  that host (no redirect), and a link shared from a slug URL redirects once to the
  canonical subdomain with params intact. **Proposal/selection sharing is slug +
  query only — never a theme param.**

## Server resolution

Slugs/subdomains are resolved per request from Postgres:

- `GET /api/maps` — all maps (landing grid), ranked by votes.
- `GET /api/maps/<slug>` — one map's config.
- `GET /api/maps/by-subdomain/<sub>` — one map by its `subdomain` column.
- `resolve_map(slug)` (`app.py`) maps slug → city → graph/OSRM/policy for vote
  endpoints. The active slug rides on API/WS calls via `?map=` (`withMap`).

`maps.subdomain` has a partial unique index (`idx_maps_subdomain`): any number of
maps may have no subdomain, but a set one is unique.

## Production (cityedit.org)

Nginx is **host-agnostic** — no `server_name` directives. The single server block
serves the SPA shell for any host/path via `try_files $uri $uri/ /index.html`, so
`cityedit.org/m/<anything>` and `*.cityedit.org` all load the same bundle and the
client resolves the rest. `/api`, `/ws`, `/tiles` proxy to Flask
(`deploy/nginx-cloudrun.conf` in prod, `nginx.conf` in dev).

For a new subdomain to work in production you need, **once**:

1. **DNS** — a wildcard `*.cityedit.org` record (or a per-subdomain record)
   pointing at the service.
2. **TLS** — a certificate covering the subdomain. On Cloud Run, either add a
   per-subdomain domain mapping (each gets a managed cert) or front the service
   with a Google HTTPS Load Balancer holding a **wildcard managed cert** for
   `*.cityedit.org`. The load-balancer + wildcard-cert route is what makes "add a
   subdomain" a pure data change afterward.

## Admin runbook — add a vanity subdomain

Goal: `bikes.cityedit.org` (or any host) serves an existing map by slug.

**Option A — CLI (direct DB).** With `DATABASE_URL` pointing at the target DB:

```bash
cd server && source env/bin/activate
python manage_maps.py list
python manage_maps.py set ny-bike-test bikes     # attach
python manage_maps.py clear ny-bike-test         # detach
```

**Option B — HTTP (remote).** Requires `ADMIN_TOKEN` set in the server env:

```bash
curl -X POST https://cityedit.org/api/admin/maps/ny-bike-test/subdomain \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"subdomain":"bikes"}'

curl -X DELETE https://cityedit.org/api/admin/maps/ny-bike-test/subdomain \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

If `ADMIN_TOKEN` is unset the endpoint returns 403 (disabled by default).

Then ensure DNS + TLS cover the subdomain (the one-time step above). Once the
column is set and the host resolves, `bikes.cityedit.org` serves that map and
`cityedit.org/m/ny-bike-test` redirects to it. The preset hosts
(`bikepaths`/`trees`/`walkways`) are just rows seeded this same way in
`presets.py`.
