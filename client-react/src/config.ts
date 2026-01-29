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

  // Pan limits (strict geographic bounds)
  nycBounds: {
    sw: { lat: 40.4774, lon: -74.2591 },
    ne: { lat: 40.9176, lon: -73.7004 },
  },

  // Zoom limits
  minZoom: 10,
  maxZoom: 20,

  // Basemap options
  basemaps: {
    light: {
      name: "Light",
      url: "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
    lightLabels: {
      name: "Light + Labels",
      url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
    dark: {
      name: "Dark",
      url: "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    },
    satellite: {
      name: "Satellite",
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      attribution: '&copy; Esri',
    },
    osm: {
      name: "OpenStreetMap",
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  defaultBasemap: "light" as const,
  tileSubdomains: "abcd",

  // Leaflet behaviors - use SVG for better event handling on individual paths
  preferCanvas: false,

  // API & Socket URLs - auto-detect based on environment
  apiUrl: isLocalDev ? "http://localhost:5001/api" : "/api",
  wsUrl: isLocalDev
    ? "ws://localhost:5001/ws"
    : `${wsProtocol}//${typeof window !== "undefined" ? window.location.host : ""}/ws`,
};
