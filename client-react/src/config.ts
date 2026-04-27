// ==========================================================================
// Configuration
// ==========================================================================

// Re-export all colors for convenient access
export * from "./colors";

// Detect environment: use direct Flask URLs only when running Vite dev server (port 3000)
// When running in Docker (port 8080), use relative paths through nginx
const isLocalDev =
  typeof window !== "undefined" &&
  (window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1") &&
  window.location.port === "3000";

const wsProtocol =
  typeof window !== "undefined" && window.location.protocol === "https:"
    ? "wss:"
    : "ws:";

export const CONFIG = {
  // Initial camera
  initialView: { lat: 40.7580, lon: -73.9855, zoom: 14 },

  // Pan limits (generous padding around mapped region)
  nycBounds: {
    sw: { lat: 40.550, lon: -74.200 },
    ne: { lat: 41.000, lon: -73.750 },
  },

  // Routing limits (mapped region bounds)
  mappedBounds: {
    sw: { lat: 40.70121, lon: -74.03069 },
    ne: { lat: 40.87043, lon: -73.90752 },
  },

  // Zoom limits
  minZoom: 12,
  maxZoom: 18,

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

  // Leaflet behaviors
  preferCanvas: true,

  // API & Socket URLs - auto-detect based on environment
  apiUrl: isLocalDev ? "http://localhost:5001/api" : "/api",
  wsUrl: isLocalDev
    ? "ws://localhost:5001/ws"
    : `${wsProtocol}//${typeof window !== "undefined" ? window.location.host : ""}/ws`,
};
