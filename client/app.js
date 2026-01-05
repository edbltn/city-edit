import { CONFIG, COLOR_START, COLOR_END } from "./config.js?v=4";
import { createOverlayManager } from "./overlays.js?v=3";
import { connectMapStateWebSocket } from "./ws.js?v=3";
import { createCommuteInputModal } from "./commute-input.js?v=4";
import { createRouteLayer, createDesirePathLayer } from "./route-styles.js?v=3";

const bounds = L.latLngBounds(
  [CONFIG.nycBounds.sw.lat, CONFIG.nycBounds.sw.lon],
  [CONFIG.nycBounds.ne.lat, CONFIG.nycBounds.ne.lon]
);

const map = L.map("map", {
  preferCanvas: CONFIG.preferCanvas,
  maxBounds: bounds,
  maxBoundsViscosity: 1.0,
  zoomControl: false
}).setView([CONFIG.initialView.lat, CONFIG.initialView.lon], CONFIG.initialView.zoom);

map.setMinZoom(CONFIG.minZoom);

L.control.zoom({
  position: 'bottomright'
}).addTo(map);

L.tileLayer(CONFIG.tileUrlTemplate, {
  subdomains: CONFIG.tileSubdomains,
  maxZoom: CONFIG.maxZoom,
  attribution: CONFIG.tileAttribution
}).addTo(map);

// Create a pane for the user's route so it appears above vote overlays
map.createPane("routePane");
map.getPane("routePane").style.zIndex = 450;

// Create a separate pane for desire path (below main route)
map.createPane("desirePathPane");
map.getPane("desirePathPane").style.zIndex = 440;

const overlays = createOverlayManager(map);

// Track latest revision for overlay updates
let latestRevision = 0;

// WebSocket connection with send capability
const wsConnection = connectMapStateWebSocket({
  url: CONFIG.wsUrl,
  onState: (state) => {
    if (!state || typeof state.revision !== "number") return;
    if (state.revision <= latestRevision) return;
    latestRevision = state.revision;
    overlays.applyMapState(state);
  },
  onConnect: () => {
    // Send initial mode filter on connection
    wsConnection.send({ type: "set_mode", mode: commuteInput.state.selectedMode });
  }
});

// Initialize commute input modal
const commuteInput = createCommuteInputModal(map);
commuteInput.init();

// Mode selector dropdown elements
const modeSelector = document.getElementById("mode-selector");
const modeBtn = document.getElementById("mode-btn");
const modeOptions = document.querySelectorAll(".mode-option");

// Map click state
let state = {
  start: { coords: null, marker: null, timestamp: null },
  end: { coords: null, marker: null, timestamp: null },
  routeLayer: null,
  desirePathLayer: null,
  routeRequestId: 0
};

// Legend element
const routeLegend = document.getElementById("route-legend");

// Custom marker icons - Classic photographic pin style
const createCustomIcon = (color) => {
  return L.divIcon({
    className: 'custom-marker',
    html: `<div class="pin-container">
      <div class="pin-head" style="background: ${color};"></div>
      <div class="pin-needle"></div>
      <div class="pin-shadow"></div>
    </div>`,
    iconSize: [30, 40],
    iconAnchor: [15, 40]
  });
};

const startIcon = createCustomIcon(COLOR_START);
const endIcon = createCustomIcon(COLOR_END);

const clearBtn = document.getElementById("clear-btn");
const startInput = document.getElementById("start-coords");
const endInput = document.getElementById("end-coords");
const calculatingIndicator = document.getElementById("calculating-indicator");

// Helper to clear route layers and legend
const clearRoute = () => {
  if (state.routeLayer) {
    map.removeLayer(state.routeLayer);
    state.routeLayer = null;
  }
  if (state.desirePathLayer) {
    map.removeLayer(state.desirePathLayer);
    state.desirePathLayer = null;
  }
  // Hide legend
  if (routeLegend) {
    routeLegend.classList.remove("active");
  }
};

// Helper to bring markers to front
const bringMarkersToFront = () => {
  if (state.start.marker) {
    state.start.marker.setZIndexOffset(1000);
  }
  if (state.end.marker) {
    state.end.marker.setZIndexOffset(1000);
  }
};

// Helper to calculate and display route
const calculateRoute = async () => {
  if (!state.start.coords || !state.end.coords) return;

  // Increment request ID and capture it for this request
  state.routeRequestId++;
  const requestId = state.routeRequestId;

  // Capture the start/end coords and mode at time of request
  const requestStart = { lat: state.start.coords.lat, lng: state.start.coords.lng };
  const requestEnd = { lat: state.end.coords.lat, lng: state.end.coords.lng };
  const requestMode = commuteInput.state.selectedMode;

  // Show calculating indicator
  calculatingIndicator.classList.add("active");

  try {
    // Use ORS-based routing with desire path computation
    const response = await fetch(`${CONFIG.apiUrl}/routes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        start: [requestStart.lat, requestStart.lng],
        end: [requestEnd.lat, requestEnd.lng],
        mode: requestMode
      })
    });

    if (!response.ok) {
      throw new Error(`Route calculation failed: ${response.statusText}`);
    }

    const data = await response.json();

    // Ignore response if a newer request was made
    if (requestId !== state.routeRequestId) {
      return;
    }

    // Verify start/end still match what we requested
    const startMatches = state.start.coords &&
      state.start.coords.lat === requestStart.lat &&
      state.start.coords.lng === requestStart.lng;
    const endMatches = state.end.coords &&
      state.end.coords.lat === requestEnd.lat &&
      state.end.coords.lng === requestEnd.lng;

    if (!startMatches || !endMatches) {
      return;
    }

    // Remove existing route layers
    clearRoute();

    // Extract route data from new API format
    const routeData = data.route;
    const desirePathData = data.desire_path;

    // Create desire path layer first (so it appears below main route)
    if (desirePathData && desirePathData.geometry && requestMode !== "walk") {
      state.desirePathLayer = createDesirePathLayer(desirePathData.geometry, "desirePathPane");
      state.desirePathLayer.addTo(map);
    }

    // Create main route layer
    if (routeData && routeData.geometry) {
      state.routeLayer = createRouteLayer(routeData.geometry, requestMode, "routePane");
      state.routeLayer.addTo(map);
    }

    // Show legend for bike/drive modes (not walk)
    if (routeLegend && requestMode !== "walk" && desirePathData) {
      routeLegend.classList.add("active");
      // Update legend labels based on mode
      const modeLabel = routeLegend.querySelector(".legend-mode-label");
      const modeLine = routeLegend.querySelector(".legend-line-mode");
      if (modeLabel) {
        const modeNames = { bike: "Bike", drive: "Drive" };
        modeLabel.textContent = `${modeNames[requestMode] || requestMode} path`;
      }
      if (modeLine) {
        modeLine.style.background = requestMode === "bike" ? "#00A651" : "#343148";
      }
    }

    // Ensure markers stay on top of route
    bringMarkersToFront();

    // Voting now happens server-side when route is calculated

  } catch (error) {
    console.error("Route calculation failed:", error.message);
  } finally {
    // Hide calculating indicator only if this is still the current request
    if (requestId === state.routeRequestId) {
      calculatingIndicator.classList.remove("active");
    }
  }
};

// Mode selector dropdown event handlers
if (modeBtn && modeSelector) {
  modeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    modeSelector.classList.toggle("active");
  });

  document.addEventListener("click", () => {
    modeSelector.classList.remove("active");
  });

  modeOptions.forEach((option) => {
    option.addEventListener("click", () => {
      const mode = option.dataset.mode;
      const icon = option.querySelector(".mode-icon").textContent;
      const label = option.querySelector(".mode-label").textContent;

      // Update button
      modeBtn.querySelector(".mode-icon").textContent = icon;
      modeBtn.querySelector(".mode-label").textContent = label;

      // Update selected state
      modeOptions.forEach((opt) => opt.classList.remove("selected"));
      option.classList.add("selected");

      // Update state in commuteInput
      commuteInput.state.selectedMode = mode;

      // Send mode filter to server
      wsConnection.send({ type: "set_mode", mode: mode });

      // Close dropdown
      modeSelector.classList.remove("active");

      // Re-calculate route if both points are set
      if (state.start.coords && state.end.coords) {
        calculateRoute();
      }
    });
  });
}

// Helper to update a point (start or end)
const updatePoint = (which, lat, lng) => {
  const point = state[which];
  const display = which === 'start' ? startInput : endInput;
  const icon = which === 'start' ? startIcon : endIcon;

  // Update coords and timestamp
  point.coords = { lat, lng };
  point.timestamp = Date.now();

  // Update display
  display.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  display.classList.remove('empty');

  // Update marker
  if (point.marker) {
    map.removeLayer(point.marker);
  }
  point.marker = L.marker([lat, lng], {
    icon,
    title: which,
    zIndexOffset: 1000
  }).addTo(map);

  // Clear existing route when points change
  clearRoute();

  // Auto-calculate route when both points are set
  if (state.start.coords && state.end.coords) {
    calculateRoute();
  }
};

// Helper to clear a point
const clearPoint = (which) => {
  const point = state[which];
  const display = which === 'start' ? startInput : endInput;
  const placeholder = which === 'start' ? 'Click map to set start' : 'Click map to set end';

  point.coords = null;
  point.timestamp = null;

  if (point.marker) {
    map.removeLayer(point.marker);
    point.marker = null;
  }

  display.textContent = placeholder;
  display.classList.add('empty');
};

// Map click handler
map.on("click", (e) => {
  // If no start, set start
  if (!state.start.coords) {
    updatePoint('start', e.latlng.lat, e.latlng.lng);
    return;
  }

  // If start exists but no end, set end
  if (!state.end.coords) {
    updatePoint('end', e.latlng.lat, e.latlng.lng);
    return;
  }

  // Both exist: previous end becomes start, new click becomes end
  // Move end marker to start position
  const prevEnd = state.end.coords;

  // Update start with previous end coords
  updatePoint('start', prevEnd.lat, prevEnd.lng);

  // Update end with new click
  updatePoint('end', e.latlng.lat, e.latlng.lng);
});

// Clear button handler
clearBtn.addEventListener("click", () => {
  clearPoint('start');
  clearPoint('end');
  clearRoute();
});

// Logo click handler - refresh page
const logoTitle = document.querySelector(".topbar h1");
if (logoTitle) {
  logoTitle.addEventListener("click", () => {
    window.location.reload();
  });
}
