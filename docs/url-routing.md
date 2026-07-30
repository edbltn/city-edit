# URL & Routing Architecture

How the app decides *which map* to show, how links are built, and how an admin
adds a vanity subdomain or renames a slug. There is **no router library** — the
SPA inspects the URL directly. Maps are addressed by **slug**; the legacy
`?theme=` query param is gone (the only mention left is a comment in
`utils/shareLink.ts` documenting that it no longer exists). Every redirect the
system performs is listed in [Redirect inventory](#redirect-inventory) — add to
that table whenever you add one.

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
A *retired* slug (a renamed map's old name) is not a map but still resolves —
see [Slug redirects](#slug-redirects-renamed-maps).

### Visit-source tracking (`?src=`)

Any City Edit URL may carry `?src=<tag>` (e.g. `?src=qr-poster`) to attribute
the visit to a campaign. The client captures the tag once at boot
(`utils/sourceTag.ts`), strips it from the address bar (so re-shared links
don't inherit the attribution), and reports it in the map-load beacon
(`utils/loadTelemetry.ts` → `POST /api/client-timing` → the `[MAPLOAD] … src=…`
log line). The `cityedit_map_load_ms` metric (`terraform/monitoring.tf`)
extracts it as the `src` label, so **visits by source** is a Metrics Explorer /
dashboard group-by; untagged visits are labeled `direct`. Tags are
`[a-zA-Z0-9_-]`, max 32 chars. To start a new campaign, just mint a new tag in
the printed/shared URL — no code change.

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

### Slug redirects (renamed maps)

When a map is renamed, its old slug moves to the `map_redirects` table
(`from_slug → to_slug + append_query`, `server/database.py`) instead of dying:

- `GET /api/maps/<old-slug>` returns `{"slug", "redirect": {"toSlug",
  "appendQuery"}}` (200, not a 30x — nginx serves the SPA shell before any slug
  lookup, so only the client can act on it, exactly like the subdomain
  redirect). `App.tsx` then `location.replace`s to `/m/<toSlug>` via
  `slugRedirectUrl()` (`map/runtime.ts`), **keeping all current query params**
  (deep links survive) and merging `append_query` without overriding anything
  already present.
- The old slug stays **reserved forever**: `slug_available()` checks both
  `maps` and `map_redirects`, so Propose-a-Map can never re-issue it to
  someone else's map (printed links would land on a stranger's map).
- `append_query` is the retro-tagging hook: a redirect row with
  `append_query = 'src=qr-poster'` stamps campaign attribution onto every
  visit through the old printed URL.

Rows are created by `server/rename_map.py` (see the
[rename runbook](#admin-runbook--rename-a-map-slug)), never by hand-editing
the DB in isolation — the rename must also move `edge_votes.map_slug` and
rebuild Redis.

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

## Admin runbook — rename a map slug

Goal: `/m/<old>` becomes `/m/<new>`; the old slug redirects (optionally
stamping a campaign `?src=` tag) and stays reserved.

```bash
cd server && source env/bin/activate
python rename_map.py <old-slug> <new-slug> \
  --append-query "src=<campaign-tag>" \
  --note "why this redirect exists"
```

One Postgres transaction (`database.rename_map_slug`): `maps.slug`, the
denormalized `edge_votes.map_slug` copies (plain TEXT — nothing cascades), a
`map_redirects` row, and any existing redirects chained onto the old slug are
flattened to the new one. Then Redis — the only store the heatmap serves from —
is rebuilt under the new slug and the old slug's keys (`ev:`, `vote_rev:`,
`bd:`/`bagg:`) are purged.

Against **prod**, run it through the bastion tunnels (Postgres on local
`:5433`, Redis on `:6380` — see `docs/gcp-deployment.md`), passing
`DATABASE_URL`/`REDIS_HOST`/`REDIS_PORT` inline for the one command; never
repoint `server/.env`. Notes:

- Running Flask instances hold in-process map caches (30–60s TTL); the old
  slug may serve its old config for up to a minute after the rename.
- Anything keyed by slug resets client-side on first visit to the new slug
  (IndexedDB vote cache, `localStorage` passcode tokens) — self-healing.
- `/previews/<new-slug>.png` is missing until the next daily screenshot job.

## Redirect inventory

**Source of truth** for every redirect/rewrite the system performs. If you add
one, add a row here.

| What | Mechanism | Where | Query params |
|------|-----------|-------|--------------|
| `donate.cityedit.org` → donorbox | nginx `return 301` | `deploy/nginx-cloudrun.conf` | dropped |
| `feedback.cityedit.org` → feedback page | nginx `try_files` rewrite (not a redirect) | `deploy/nginx-cloudrun.conf` | n/a |
| Canonical-subdomain redirect (`/m/nyc-bikes` → `bikepaths.cityedit.org`) | client `location.replace` after map-config fetch | `App.tsx` + `themes.ts subdomainRedirectUrl` | preserved (+ re-attached `src`) |
| Retired-slug redirects (renamed maps) | client `location.replace` on `MapConfig.redirect` | DB `map_redirects` table + `App.tsx` + `map/runtime.ts slugRedirectUrl` | preserved, `append_query` merged |
| Staging: subdomain redirect disabled | `APP_ENV=staging` → `staging: true` on map config | `app.py` / `App.tsx` | n/a |

Current `map_redirects` rows (query the DB for the live list:
`SELECT * FROM map_redirects;`):

| From | To | Appends | Why |
|------|----|---------|-----|
| `nyc-intersections` | `nyc-proposals` | `src=qr-poster` | 2026-07 QR poster campaign was printed without a `src` tag; the redirect retro-tags those scans (chain-flattened from `nyc-crossings` on the 2026-07-30 rename) |
| `nyc-crossings` | `nyc-proposals` | — | 2026-07-30: map broadened from dangerous intersections to all NYC proposals |
