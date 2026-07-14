---
name: verify
description: Drive the City Edit dev app to verify a client/server change end-to-end (launch stack, pick a repro map, click markers, read debug console).
---

# Verify a City Edit change in the running app

## Launch / health check

Usually already running. Check, don't restart blindly:

```bash
curl -s -o /dev/null -w "vite:%{http_code} " http://localhost:3000/
curl -s http://localhost:5001/api/maps | head -c 200   # Flask (no /api/health route)
redis-cli -p 6379 ping
```

If down: Redis `redis-server`; Flask `cd server && SKIP_PREWARM=1 ./env/bin/python app.py`
(restart Flask after any `server/*.py` edit — no auto-reload); Vite `cd client-react && npm run dev`
(hot-reloads client edits, no restart needed).

## Pick a repro map

`curl -s localhost:5001/api/maps` lists slugs. Useful local states (2026-07):

- `chicago-bikes` — 20 RBTP diamonds, **0 PBTP squares** (all-route-vote map)
- `nyc-bikes` — mixed: ~8 squares + 20 diamonds (Lyft import; ~20s topology load, be patient)
- `e-bikes-3` / `new-york-e-bikes` — station networks (every station is a marker)

Count marker kinds in-page:
```js
document.querySelectorAll('.vote-type-indicator').length        // all pins
document.querySelectorAll('.vote-type-indicator.is-diamond').length  // RBTP diamonds
```

## Drive it (Claude-in-Chrome)

- Open `http://localhost:3000/m/<slug>?tab=<name>` — tags the tab `[dbg:<name>]` and enables all
  debug channels. Read console with pattern `\[(topo|votes|cast|store|blocks|proposals|maplibre|ws)\]`.
- `window.cityedit.dumpState()` = one-call health check (topology sizes, maplibreLoaded).
- Coordinate mapping: screenshots are scaled — scale = screenshotWidth / window.innerWidth
  (get innerWidth via javascript_tool). DOM `getBoundingClientRect()` coords are CSS px; multiply
  by that scale before clicking.
- Hidden/occluded Chrome window freezes rAF → MapLibre never fires `load` (`maplibreLoaded:false`,
  no heatmap). Leaflet markers + clicks still work; bring the window forward for heatmap checks.

## Gotchas

- javascript_tool responses get BLOCKED if the returned string pattern-matches cookies/query
  strings (e.g. className dumps, `x,y | x,y` strings). Return JSON arrays of numbers / short
  scalars instead.
- Cluster fan-out ("explode") is TRANSIENT: snaps back after `SPREAD_DURATION_MS` (2.2s) unless
  the cursor hovers a fanned icon. Probe state (`.leaflet-container.votes-spreading`, markers with
  `style.zIndex >= 500000` = fanned band) in the SAME batch as the click, no waits in between.
- Vote/heat state: served only from Redis; DB-only edits are invisible until rebuild (see memory).
