import { CONFIG } from "./config.js";
import { createOverlayManager } from "./overlays.js";
import { connectMapStateWebSocket } from "./ws.js";

const statusEl = document.getElementById("status");

const bounds = L.latLngBounds(
  [CONFIG.manhattanBounds.sw.lat, CONFIG.manhattanBounds.sw.lon],
  [CONFIG.manhattanBounds.ne.lat, CONFIG.manhattanBounds.ne.lon]
);

const map = L.map("map", {
  preferCanvas: CONFIG.preferCanvas,
  maxBounds: bounds,
  maxBoundsViscosity: CONFIG.maxBoundsViscosity
}).setView([CONFIG.initialView.lat, CONFIG.initialView.lon], CONFIG.initialView.zoom);

map.setMinZoom(CONFIG.minZoom);

L.tileLayer(CONFIG.tileUrlTemplate, {
  subdomains: CONFIG.tileSubdomains,
  maxZoom: CONFIG.maxZoom,
  attribution: CONFIG.tileAttribution
}).addTo(map);

const overlays = createOverlayManager(map);

// Fallback demo state (so you always see *something*)
let latestRevision = 0;
const demoState = {
  revision: 1,
  overlays: {
    demo_line: {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [{
          type: "Feature",
          properties: {},
          geometry: {
            type: "LineString",
            coordinates: [
              [-73.9772, 40.7712],
              [-73.9715, 40.7818],
              [-73.9650, 40.7910]
            ]
          }
        }]
      }
    }
  }
};

overlays.applyMapState(demoState);

connectMapStateWebSocket({
  url: CONFIG.wsUrl,
  onStatus: (txt) => (statusEl.textContent = `Step 4: ${txt}`),
  onState: (state) => {
    if (!state || typeof state.revision !== "number") return;
    if (state.revision <= latestRevision) return; // ignore stale
    latestRevision = state.revision;
    overlays.applyMapState(state);
  }
});

