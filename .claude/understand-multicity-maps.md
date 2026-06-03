# Understand — Multi-city + user-proposed maps (session tutor checklist)

Scope: this coding session — turning the single-city (NYC) app into a multi-city
platform with user-proposed "maps", plus a clean vote data model and UI.

Legend: [ ] not yet · [~] explained, not verified · [x] verified (restated + quiz)

## Pillar A — The data model: cities, maps ("modes"), vote-type lists
- [ ] Problem: why "single-city, theme-by-subdomain" didn't scale
- [ ] What a **city** is (curated config) vs a **map/mode** (DB row) vs a **vote-type list**
- [ ] Why "mode" == the map (slug), and why the old 4-bit `mode`/`packed_key` became redundant
- [ ] Why maps are addressed by slug (`/m/<slug>`) with presets still on subdomains

## Pillar B — Per-city graph + OSRM (the routing engine)
- [ ] Problem: one global NYC graph in memory; OSRM serves one dataset/process
- [ ] GraphRegistry (lazy + LRU) and OsrmRegistry (one container per city)
- [ ] How a request resolves slug → map → city → (graph, OSRM)

## Pillar C — Vote storage: Redis + Postgres
- [ ] Why Redis (live counts) AND Postgres (durable) — roles of each
- [ ] Per-map Redis namespacing (`ev:<slug>`) and why it lets the packed key stay
- [ ] The clean `edge_votes` schema: entity, vote_type, map(mode), user (IP + device)
- [ ] Why dedup-by-device; the in-place 62k-row migration
- [ ] SQL consolidation into database.py

## Pillar D — Client: theme-derived-from-map, slug routing
- [ ] Why `ThemeContext` now derives from the resolved map (not the subdomain)
- [ ] The bootstrap that fetches map config and rebinds CONFIG before render
- [ ] Searchable, vote-ranked mode switcher

## Pillar E — The UX fixes (and the "why" behind each)
- [ ] Modal viewport-clamping; deep-link load-timing race + fix
- [ ] SF bbox "clustered north" diagnosis; Chicago centering

(We'll focus where you choose; not all pillars must be done in one sitting.)
