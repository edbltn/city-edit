// ==========================================================================
// Configuration
// ==========================================================================

// Re-export all colors for convenient access
export * from "./colors";

// Detect environment: when running the Vite dev server (any port — including
// incremented ports for parallel worktrees) talk to Flask directly on :5001.
// In Docker/production (built bundle served by nginx) use relative paths.
const isLocalDev =
  typeof window !== "undefined" && import.meta.env.DEV;

const wsProtocol =
  typeof window !== "undefined" && window.location.protocol === "https:"
    ? "wss:"
    : "ws:";

export const CONFIG = {
  // Active map slug (set at bootstrap from the URL; empty = legacy single-map mode)
  mapSlug: "",

  // Initial camera
  initialView: { lat: 40.7580, lon: -73.9855, zoom: 14 },

  // Pan limits (generous padding around mapped region)
  nycBounds: {
    sw: { lat: 40.400, lon: -74.350 },
    ne: { lat: 41.000, lon: -73.600 },
  },

  // Routing limits (all 5 boroughs)
  mappedBounds: {
    sw: { lat: 40.4774, lon: -74.2591 },
    ne: { lat: 40.9176, lon: -73.7004 },
  },

  // Votable region for station networks (e.g. ebikes). These are NYC-only and
  // their stations cluster in/around Manhattan, so the votable area — and thus
  // the boundary scrim and pan limits — is scoped to Manhattan rather than all
  // five boroughs. Sized to enclose every station in ebike_stations.json with a
  // little padding. Applied over mappedBounds by applyMap for non-streets maps.
  stationNetworkBounds: {
    sw: { lat: 40.64, lon: -74.07 },
    ne: { lat: 40.88, lon: -73.90 },
  },

  // Zoom limits. Max is intentionally deep so short edges can be zoomed until
  // they exceed the node hit radius and become selectable (per-city value from
  // the server overrides this at bootstrap; the raster basemap upscales past
  // its native zoom, the canvas graph stays crisp at any zoom).
  minZoom: 10,
  maxZoom: 21,

  // Tiles - CartoDB DarkMatter No Labels
  tileUrlTemplate:
    "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
  tileAttribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  tileSubdomains: "abcd",

  // PMTiles — graph overlay tiles (built from OSM walk graph). Per-city path,
  // served by Flask in dev and aliased to osm_data by nginx in prod. This is the
  // default for the bootstrap (nyc); applyCityConfig() rebinds it to the active
  // city's tilesPath once the map config loads.
  graphTilesUrl: isLocalDev
    ? `${import.meta.env.VITE_API_BASE || "http://localhost:5001"}/api/tiles/nyc/graph.pmtiles`
    : "/api/tiles/nyc/graph.pmtiles",

  // Block polygons (one per street segment) — the primary heat display when a
  // city has them. Same per-city tiles dir as the graph; availability is
  // detected from the /api/graph-votes response carrying block_votes.
  blockTilesUrl: isLocalDev
    ? `${import.meta.env.VITE_API_BASE || "http://localhost:5001"}/api/tiles/nyc/blocks.pmtiles`
    : "/api/tiles/nyc/blocks.pmtiles",

  // Leaflet behaviors
  preferCanvas: true,

  // API & Socket URLs - auto-detect based on environment. VITE_API_BASE /
  // VITE_WS_BASE override the dev defaults so a worktree stack can point at its
  // own Flask on a non-default port without colliding with the main dev server.
  apiUrl: import.meta.env.VITE_API_BASE
    ? `${import.meta.env.VITE_API_BASE}/api`
    : (isLocalDev ? "http://localhost:5001/api" : "/api"),
  wsUrl: import.meta.env.VITE_WS_BASE
    ? `${import.meta.env.VITE_WS_BASE}/ws`
    : (isLocalDev
      ? "ws://localhost:5001/ws"
      : `${wsProtocol}//${typeof window !== "undefined" ? window.location.host : ""}/ws`),
};

// Public city shape returned by the API (matches server cities.City.to_public()).
export interface CityConfig {
  id: string;
  name: string;
  bounds: { sw: { lat: number; lon: number }; ne: { lat: number; lon: number } };
  center: { lat: number; lon: number };
  defaultZoom: number;
  minZoom: number;
  maxZoom: number;
  tilesPath: string;
}

/**
 * Rebind the camera, bounds, zoom, and graph-tile URL to a given city. Called
 * once at bootstrap (before the map subtree renders) so all CONFIG consumers
 * read the active city's values. Pan limits get generous padding around the bbox.
 */
export function applyCityConfig(city: CityConfig): void {
  const { bounds, center, defaultZoom, minZoom, maxZoom, tilesPath } = city;
  CONFIG.initialView = { lat: center.lat, lon: center.lon, zoom: defaultZoom };
  CONFIG.mappedBounds = { sw: { ...bounds.sw }, ne: { ...bounds.ne } };
  CONFIG.nycBounds = {
    sw: { lat: bounds.sw.lat - 0.1, lon: bounds.sw.lon - 0.15 },
    ne: { lat: bounds.ne.lat + 0.1, lon: bounds.ne.lon + 0.15 },
  };
  CONFIG.minZoom = minZoom;
  CONFIG.maxZoom = maxZoom;
  if (tilesPath) {
    CONFIG.graphTilesUrl = isLocalDev ? `http://localhost:5001${tilesPath}` : tilesPath;
    const blocksPath = tilesPath.replace("graph.pmtiles", "blocks.pmtiles");
    CONFIG.blockTilesUrl = isLocalDev ? `http://localhost:5001${blocksPath}` : blocksPath;
  }
}
