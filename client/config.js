// Re-export all colors for convenient access
export * from "./colors.js";

// Detect environment: use relative paths in production, localhost in dev
const isLocalDev = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";

export const CONFIG = {
  // Initial camera
  initialView: { lat: 40.7128, lon: -74.0060, zoom: 11 },

  // Pan limits (strict geographic bounds)
  nycBounds: {
    sw: { lat: 40.4774, lon: -74.2591 },
    ne: { lat: 40.9176, lon: -73.7004 }
  },

  // Zoom limits
  minZoom: 10,
  maxZoom: 20,

  // Tiles - CartoDB Positron No Labels
  tileUrlTemplate: "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
  tileAttribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  tileSubdomains: "abcd",

  // Leaflet behaviors
  preferCanvas: true,

  // API & Socket URLs - auto-detect based on environment
  apiUrl: isLocalDev ? "http://localhost:5001/api" : "/api",
  wsUrl: isLocalDev ? "ws://localhost:5001/ws" : `${wsProtocol}//${window.location.host}/ws`
};
