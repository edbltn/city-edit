# City Edit — Frontend

The City Edit single-page app: a Leaflet map with a canvas heatmap overlay where
users route between two points and vote on the streets and crossings that should
change. React + TypeScript + Vite.

## Commands

```bash
npm install
npm run dev       # dev server with HMR at http://localhost:3000
npm run build     # type-check (tsc -b) + production build to dist/
npm run preview   # serve the production build locally
npm run lint      # eslint
npm test          # vitest (unit tests, colocated as *.test.ts)
```

The dev server proxies the API/WebSocket to the backend; run the Flask server
(or the Docker stack) alongside it. See the root [README](../README.md).

## Structure

```
src/
  components/   UI + map layers (MapView, GraphLayer, RouteLayer, TopBar,
                Landing, ProposeMap, VoteTypeSelector, ...)
  context/      React contexts — the app's state backbone (see below)
  hooks/        e.g. useRouteCalculation (POST /api/routes)
  map/          runtime.ts (which map to load) + themes.ts (slug/link helpers)
  selection/    point/route selection state
  utils/        voteKey, voteStore, castVote, shareLink, graphCache
  types/        shared TS types
  constants/    app constants
  styles/       global CSS
```

## Key contexts (`src/context/`)

| Context | Responsibility |
|---------|----------------|
| `MapContext` | The Leaflet map instance and viewport. |
| `RouteContext` | Start/end/waypoint selection, route calculation, and the single vote-cast path (`castVotes`). |
| `GraphSnapContext` | Snaps a clicked `[lat,lng]` to the nearest graph edge — the same resolution used for hover and for casting a vote. |
| `HeatmapContext` | Drives the canvas heatmap rendering of aggregate votes. |
| `ThemeContext` | Derives the active theme from the loaded map (`style` column), with a preset fallback. |
| `WebSocketContext` | Holds the `/ws` connection; receives authoritative vote-count broadcasts. |
| `GhostPinContext` | The hover/preview pin state. |

## How it connects to the backend

- **Which map loads** (slug / subdomain / apex) is resolved client-side with no
  router library — see [docs/url-routing.md](../docs/url-routing.md).
- **Voting** — identity, the packed-integer codec, optimistic apply, and server
  reconciliation are documented in
  [docs/voting-architecture.md](../docs/voting-architecture.md) (the source of
  truth). Note `src/utils/voteKey.ts` is a **byte-for-byte mirror** of the
  server's `vote_store.py` codec; a parity test (`voteKey.test.ts` ↔
  `test_vote_codec.py`) guards against drift. Don't edit one side without the
  other.

## Tooling notes

Built on Vite with `@vitejs/plugin-react`. The mapping stack is Leaflet +
MapLibre GL (basemap) with `flatbush` for spatial indexing. ESLint config is in
`eslint.config.js`.
</content>
