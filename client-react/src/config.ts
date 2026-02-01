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
  initialView: { lat: 40.7128, lon: -74.006, zoom: 11 },

  // Pan limits (expanded geographic bounds for more scroll room)
  nycBounds: {
    sw: { lat: 40.3, lon: -74.4 },
    ne: { lat: 41.1, lon: -73.5 },
  },

  // Routing limits (mapped region bounds)
  mappedBounds: {
    sw: { lat: 40.70121, lon: -74.03069 },
    ne: { lat: 40.87043, lon: -73.90752 },
  },

  // Zoom limits
  minZoom: 10,
  maxZoom: 18,

  // Tiles - CartoDB Positron No Labels
  tileUrlTemplate:
    "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
  tileAttribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  tileSubdomains: "abcd",

  // Leaflet behaviors
  preferCanvas: true,

  // API & Socket URLs - auto-detect based on environment
  apiUrl: isLocalDev ? "http://localhost:5001/api" : "/api",
  wsUrl: isLocalDev
    ? "ws://localhost:5001/ws"
    : `${wsProtocol}//${typeof window !== "undefined" ? window.location.host : ""}/ws`,
};
