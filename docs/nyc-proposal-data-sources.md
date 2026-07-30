# NYC Street-Level Change Proposals — Data Sources & Scrape Recipes

*Researched and verified 2026-07-30. Companion script: [`tools/nyc_proposals/fetch_latest.py`](../tools/nyc_proposals/fetch_latest.py) (stdlib-only, run it any time for the latest changes).*

City Edit's proposals are crowdsourced; this documents where the **city's own**
street-change proposals live, so we can compare/overlay official plans against
our vote heat. Three sources, in order of usefulness:

| Source | What it is | Freshness | Machine-readable? |
|---|---|---|---|
| [nycdotprojects.info](https://nycdotprojects.info/) | DOT "Projects & Initiatives": **proposed** redesigns, CB presentations, feedback maps | Days (sitemap updated daily) | Sitemap XML + HTML scrape |
| [NYC Open Data — VZV SIPs](https://data.cityofnewyork.us/Transportation/VZV_Street-Improvement-Projects-SIPs-Corridor/wqhs-q6wd) | **Implemented** Street Improvement Projects, geocoded corridors + intersections | ~Monthly | Full SODA API + GeoJSON |
| [nyc.gov DOT yearly project pages](https://www.nyc.gov/html/dot/html/about/current-projects.shtml) | Per-year index of projects by borough w/ presentation links | Rolling | HTML scrape (`<h3>` headings) |

The pipeline that makes sense for us: **nycdotprojects.info tells you what's
being proposed** (earliest signal, community-board stage); **the SIPs datasets
tell you what actually got built** (with geometry we can snap to our graph).

---

## 1. nycdotprojects.info — DOT Projects & Initiatives (primary)

Drupal 9/10 site run by DOT's Public Engagement Group. Every active street
redesign gets a project page, plus dated updates, event announcements
(community-board presentations), and Mapbox-based "feedback maps" where the
public drops pins — DOT's own crowdsourcing, structurally similar to ours.

### What's scrapeable

- **`https://nycdotprojects.info/sitemap.xml`** — the whole site (~900 URLs)
  with `<lastmod>` timestamps. Actively maintained (entries from yesterday as
  of writing). **This is the scrape entry point: diff `lastmod` against your
  last run, fetch only changed pages.**
- Content types are distinguishable by path prefix:
  | Path | Content |
  |---|---|
  | `/project/<slug>` and bare `/<slug>` | Project homepage (title, borough, description, presentation PDFs) |
  | `/project-updates/<slug>` | Dated update — meeting announcements, "we presented to CB3", rulemaking notices |
  | `/project-event/<slug>` | Event (street ambassadors, workshops) |
  | `/project-feedback-map/<slug>` | Interactive feedback map (pin data is **not** public — POST-only backend) |
  | `/content/<slug>` | Landing pages / surveys (e.g. one-way conversion studies) |
- Project pages have clean `<title>` and `<meta name="description">`; CB
  presentation decks are linked as PDFs (hosted under
  `nycdotprojects.info/sites/default/files/…`).

### Dead ends (verified, don't retry)

- `rss.xml` exists and returns 200 with `application/rss+xml` — but it's an
  **empty** Drupal views feed (no `<item>`s). Useless.
- JSON:API (`/jsonapi`) is **disabled** (404).
- Feedback-map pin submissions are not exposed anywhere public.

### Recipe

```bash
# Everything changed since a date (titles, descriptions, PDF links):
python3 tools/nyc_proposals/fetch_latest.py --since 2026-07-01

# Fast check — URLs + lastmod only, no page fetches:
python3 tools/nyc_proposals/fetch_latest.py --no-pages --limit 100
```

The script keeps `state.json` next to itself so a bare invocation means "since
last run". It rate-limits to 1 req/s and sends a contact UA — keep both; this
is a small city-run site. robots.txt is standard Drupal (content allowed).

---

## 2. NYC Open Data — VZV Street Improvement Projects (SIPs)

Vision Zero's record of **completed** operational (non-capital) safety
projects since 2009: signal timing, markings, concrete islands, road diets.
Two Socrata assets, updated roughly monthly (~996 corridors, ~424
intersections as of writing; latest `end_date` 2026-05-29).

### ⚠️ The map-UID trap

The catalog/search results give you the **map asset** UIDs — querying those
via SODA returns rows of empty `{}` objects, and the geospatial export
returns an empty FeatureCollection. You must query the **underlying data
view** (found via `modifyingViewUid` in `/api/views/<map-uid>.json`):

| Dataset | Map asset (public page) | **Data view (query this)** | Fields |
|---|---|---|---|
| SIPs Corridors | `wqhs-q6wd` | **`if4c-w48d`** | `the_geom` (MultiLineString), `pjct_name`, `sip_year`, `end_date`, `shape_leng` |
| SIPs Intersections | `79sh-heg3` | **`shr7-eqdc`** | `the_geom` (Point), `pjct_name`, `sip_year`, `end_date`, `long`, `lat`, `x`, `y` |

### Recipe

```bash
# New corridor projects completed since a date (SoQL):
curl -s "https://data.cityofnewyork.us/resource/if4c-w48d.json?\
\$where=end_date%20%3E=%20'2026-01-01'&\$order=end_date%20DESC"

# Full GeoJSON (for snapping onto our graph):
curl -s "https://data.cityofnewyork.us/resource/if4c-w48d.geojson?\$limit=2000"

# Freshness probe without downloading anything:
curl -s "https://data.cityofnewyork.us/resource/if4c-w48d.json?\
\$select=max(end_date),count(*)"
```

No API key needed at our volumes (anonymous SODA is throttled but generous;
add an app token via `X-App-Token` header only if we ever poll aggressively).
`rowsUpdatedAt` in `/api/views/if4c-w48d.json` is the cheap "did anything
change" check (epoch seconds).

Related Socrata datasets worth knowing about (same access pattern):
[Bicycle Routes](https://data.cityofnewyork.us/Transportation/Bicycle-Routes/7vsa-caz7)
(has lane install/upgrade dates — effectively a changelog of bike-network
changes), VZV Priority Corridors/Intersections/Zones (where DOT is *likely*
to propose next), and DOT In-house Street Resurfacing.

---

## 3. nyc.gov DOT yearly project pages

`https://www.nyc.gov/html/dot/html/about/projects-<YYYY>.shtml` (2007→2025 so
far; the current year appears once projects are presented —
`current-projects.shtml` is the stable index page). Each page lists projects
as `<h3>` headings ("Bedford Avenue Slip Lane Closure", "Tenth Avenue, 14th
Street to 52nd Street") grouped by borough, each followed by a short
description and links to the presentation (usually a PDF on nyc.gov
`/html/dot/downloads/pdf/…` or a nycdotprojects.info page).

Scrape: fetch the year page, `re.findall(r'<h3[^>]*>(.*?)</h3>', html)` for
project names, take the sibling paragraph + links for detail. Low churn —
useful as an annual sweep / backfill, not a polling target. There's no feed;
diff the page HTML to detect additions.

---

## Watch-list ideas (not yet wired up)

- **Community-board agendas** (each CB posts PDF agendas; DOT items appear
  there *before* nycdotprojects.info sometimes) — very heterogeneous, 59
  boards, probably not worth scraping until we care about a specific CB.
- **NYC DOT press releases** (`nyc.gov/html/dot/html/pr/pr.shtml`) — announce
  major projects; HTML list, easy to diff.
- **The City Record** (procurement notices reveal capital street projects
  early) — structured on NYC Open Data as "City Record Online".
