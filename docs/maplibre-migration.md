# Leaflet → MapLibre migration handoff

**Status: COMPLETE. All 4 phases done on `perf-interim` — MapLibre owns the camera, Leaflet is deleted.**

## Phase 4 outcome (2026-07-22)

- `maplibregl.Map` is the single interactive map (`MapCanvas`, formerly
  MapLibreBackground). leaflet / react-leaflet / @types/leaflet removed.
- `src/map/facade.ts` — `MapFacade` wraps the ML map with the Leaflet-style
  API the code was written against. **Zooms at the facade boundary are
  Leaflet-style (ML native + 1)**: CONFIG zooms, `?z=` share links,
  `INDICATOR_MIN_ZOOM`, mapViewState all unchanged. Provided to the map
  subtree via `src/map/MapFacadeContext.tsx`.
- `src/components/MapMarker/` — React wrapper over `maplibregl.Marker`
  reusing the DivIcon HTML (kites, pins, vote-type icons). Marker clicks also
  fire a map click in ML (unlike Leaflet) — MapView's click handler ignores
  clicks originating on `.maplibregl-marker`.
- Drag trails + waypoint connectors → `util-lines` GL source; desire-path
  dimming during drag → paint properties (`setDesirePathDimmed`).
- `usePathDrag` uses pure-math hit-testing (10px radius, module-level
  hover/drag claim so split paths don't double-claim); drag-pan suppressed
  via the ML event's `preventDefault()`.
- No-WebGL fallbacks deleted (canvas heat/rings, Leaflet SVG routes, raster
  TileLayer, BoundaryLayer) — GL failure now shows a notice. GraphLayer lost
  ~800 lines of canvas renderer; Flatbush hit-testing kept as planned.
- Verified via `perf/smoke-phase4.mjs` (Playwright): click-to-place + pinned
  card, hover cards, route solve, drag-to-insert waypoint (ghost + trail),
  marker drag + snap + recalc, vote cast → GL heat, deep links, zoom control.
- **Known headless artifact (pre-existing, NOT a phase-4 regression)**:
  headless Chromium sometimes composites the GL canvas with the bottom band
  unpainted (viewport-minus-topbar height). Reproduced identically on the
  phase-3 commit; real browsers unaffected. Affects perf/screenshot harness
  captures only.
- Perf victory-lap rerun (`perf/measure.mjs`) still TODO — its
  instrumentation may assume the old canvas renderer.

Decision: drop the two-map sandwich (MapLibre GL basemap underneath + transparent
Leaflet on top for interaction) and converge on MapLibre-only. Rationale: the
survey UX heading toward block-face painting needs `queryRenderedFeatures` +
feature-state on PMTiles block polygons, and the sandwich taxes every feature
with camera-sync bugs.

## Done (verified in browser 2026-07-22)

| Commit | What |
|---|---|
| `9a257fa` | MapLibre = primary basemap; camera-sync zoom fix; GPU vote heatmap |
| `561b435` | Hover/pinned selection rings as GL layers |
| `8da01bd` | Route, desire-path, boundary visuals as GL layers |

Architecture of the shim (all new modules small and single-purpose):

- `src/map/maplibreStatus.ts` — status singleton (`pending|ready|failed`), `isMapLibreReady()`, `useMapLibreLive()` hook. **Every migrated visual falls back to its legacy Leaflet/canvas path when WebGL is unavailable** — don't break that.
- `src/map/maplibreInstance.ts` — live `maplibregl.Map` registry (`getMapLibreMap`/`onMapLibreMap`); the instance is recreated on map-style change, subscribers re-prime their sources.
- `src/map/maplibreOverlays.ts` — keyed feature registry → GeoJSON sources `route-path`, `desire-path`. Components push geometry while live.
- `src/components/GraphLayer/maplibreHeat.ts` — voted edges → `graph-live` source (sparse; full network stays PMTiles). Norm/tHot/tPeak baked as properties.
- `src/components/GraphLayer/maplibreHighlight.ts` — pinned/hover ring features → `graph-highlight` source.
- `src/components/MapLibreBackground/MapLibreBackground.tsx` — owns the style: all sources + layers (boundary scrim, heat halo/warm/hot/peak, highlight rings, desire rings, route dashes), camera sync from Leaflet.

### Gotchas already learned (do not relearn)

- **Zoom offset**: MapLibre zoom = Leaflet zoom **− 1** (256px vs 512px tile
  worlds). `ML_ZOOM_OFFSET` in MapLibreBackground. In phase 4, when MapLibre
  becomes the camera owner, everything Leaflet-zoom-denominated must convert:
  `mapViewState` persisted zooms, `CONFIG.initialView.zoom`, `INDICATOR_MIN_ZOOM`
  (13, Leaflet terms) in GraphLayer, min/max zoom clamps.
- **Dash arrays**: Leaflet `dashArray` is px; MapLibre `line-dasharray` is
  multiples of line-width.
- **No canvas blend modes in MapLibre** (screen/multiply/lighter) — heat look is
  approximated with stacked translucent strokes; accepted by Brook.
- **Keep Flatbush hit-testing** (GraphLayer). It provides nearest-edge-anywhere
  fallback + node-over-edge priority that `queryRenderedFeatures` can't. It's
  camera-independent (works from lat/lng + container points).
- Ring geometry equivalence: 7px stroke with 4px hole = `line-width: 1.5` +
  `line-gap-width: 4`; node ring = circle radius 3.5 + stroke 1.5.

## Phase 4: flip camera ownership, delete Leaflet

Goal: `maplibregl.Map` becomes the single interactive map; delete react-leaflet,
leaflet, the camera sync, and the canvas fallbacks (or keep canvas fallback only
if we still care about no-WebGL clients — decide with Brook; raster tiles were
already GL-gated, so probably delete).

Suggested order (each step keeps the app running):

1. **Inventory Leaflet touchpoints**: `grep -rl "react-leaflet\|from \"leaflet\"" src/`.
   Main ones: MapView (MapContainer, panes, ZoomControl), GraphLayer (map events,
   `latLngToContainerPoint`, canvas fallback, react-leaflet `Marker` for
   indicators), RouteLayer (interactive hit-line + ghost marker), RouteMarker,
   WaypointMarker, GhostPin, WaypointConnectors, hooks/useMapClick,
   hooks/usePathDrag, utils/mapViewState, AddressSearch (map.flyTo?), ProposeMap.
2. **Make MapLibre interactive** (`interactive: true`), remove the Leaflet camera
   sync, and give MapView a thin adapter exposing the handful of Leaflet map
   methods the code actually uses (`getZoom`, `getCenter`, `latLngToContainerPoint`
   → `map.project`, `containerPointToLatLng` → `map.unproject`, `on/off` for
   `move|zoom|click|mousemove`) so GraphLayer/hooks port mechanically. Remember
   the ±1 zoom conversion at this boundary if the adapter presents Leaflet-style zooms.
3. **Markers**: react-leaflet `Marker` → `maplibregl.Marker({element})` with the
   same DivIcon DOM (kite icons, vote-type icons are plain HTML/CSS). Drag =
   marker `dragstart/drag/dragend`. WaypointConnectors reads marker positions per
   frame — port to a GL line source updated on drag events.
4. **usePathDrag / useMapClick**: swap Leaflet mouse events for MapLibre
   `mousedown/mousemove/mouseup` + `map.dragPan.disable()` during path drag. The
   fat invisible hit-line becomes either a GL layer + `queryRenderedFeatures`
   with a px tolerance, or keep pure-math hit-testing against the geometry
   (Flatbush pattern) — prefer the latter for consistency.
5. **MapView teardown**: replace MapContainer with a plain div hosting
   MapLibreBackground (renamed → MapCanvas?); ZoomControl → maplibre
   NavigationControl or keep the custom buttons calling `zoomIn/zoomOut`;
   mapViewState persists center/zoom (convert stored Leaflet zooms once).
6. **Delete**: leaflet, react-leaflet, @types/leaflet from package.json; canvas
   heat/ring code paths in GraphLayer (~800 lines) if dropping no-WebGL support;
   `maplibreStatus` fallback branches; leaflet CSS import; `ml-base` CSS hack.
7. **Verify**: `npx tsc --noEmit`, `npx vitest run`, then in-browser: route
   solve + drag-to-insert waypoint, marker drag, point vote, hover cards, top-
   proposal indicators + spread interaction, zoom animations, theme/style switch
   (map recreation re-primes sources), mobile touch (pinch zoom, tap vote),
   deep-link with pinned point, city switch (nyc/sf/chicago bboxes).

## Perf context

`perf/` has a Puppeteer harness (`measure.mjs`, `compare.mjs`, seeded votes)
with baseline vs optimized results from the panning investigation — rerun it
after phase 4 for the victory lap numbers.

## Dev environment

redis+postgres via `docker compose up -d redis postgres`; flask via
`cd server && source env/bin/activate && python app.py`; client via
`cd client-react && npm run dev` → http://localhost:3000. OSRM containers not
set up locally (routing works via the python/rustworkx fallback).

## Bigger picture (why this migration)

Survey redesign: votes should happen at the scale improvements actually happen.
NYC data says: block **face** is the atom (75% of projects differ by street
side), the votable proposal is a **span of ~2–13 contiguous blocks** with ONE
treatment from a constrained menu (NACTO All-Ages-&-Abilities matrix), and
**intersections are separate votable units**. Analysis + reference PDFs in
`/Users/brook/Documents/temp/bike-lane-unit-analysis/`. The GL foundation is
what makes the block-face painting UI feasible (PMTiles blocks + feature-state
+ queryRenderedFeatures).
