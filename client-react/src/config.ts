// ==========================================================================
// Configuration
// ==========================================================================

// Re-export all colors for convenient access
export * from "./colors";

// Detect environment: when running the Vite dev server (any port — including
// incremented ports for parallel worktrees) talk to Flask directly on :5001.
// In Docker/production (built bundle served by nginx) use relative paths.
// VITE_LOCAL_API=1 forces the local-dev URLs in a production build, so
// `vite build && vite preview` can run against local Flask for perf testing.
const isLocalDev =
  typeof window !== "undefined" &&
  (import.meta.env.DEV || import.meta.env.VITE_LOCAL_API === "1");

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

  // PMTiles — graph overlay tiles (built from OSM walk graph)
  graphTilesUrl: isLocalDev
    ? "http://localhost:5001/api/tiles/graph.pmtiles"
    : "/tiles/graph.pmtiles",

  // API & Socket URLs - auto-detect based on environment
  apiUrl: isLocalDev ? "http://localhost:5001/api" : "/api",
  wsUrl: isLocalDev
    ? "ws://localhost:5001/ws"
    : `${wsProtocol}//${typeof window !== "undefined" ? window.location.host : ""}/ws`,
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
  }
}
