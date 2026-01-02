// Re-export all colors for convenient access
export * from "./colors.js";

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

  // API & Socket URLs
  apiUrl: "http://localhost:5001/api",
  wsUrl: "ws://localhost:5001/ws"
};
