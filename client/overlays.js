import { DESIRE_PATH, COLOR_END, ROUTE_COLORS } from "./colors.js";

// All modes use gold to represent desire paths
const MODE_COLORS = {
  walk: ROUTE_COLORS.desire.core,   // Gold
  bike: ROUTE_COLORS.desire.core,   // Gold
  drive: ROUTE_COLORS.desire.core   // Gold
};

const defaultLineStyle = {
  color: DESIRE_PATH.fill,  // fallback color
  weight: 1.5,              // base thin stroke
  opacity: 0.08,            // very subtle by default
  lineCap: "butt",          // no rounded ends (prevents overlap at nodes)
  lineJoin: "bevel"         // no rounded joins
};

// Calculate opacity based on vote count (linear scale)
// Starts very subtle, becomes more visible with more votes
function getOpacityForVotes(votes) {
  const minOpacity = 0.08;
  const maxOpacity = 0.7;
  const votesForMax = 20;  // votes needed to reach max opacity
  return Math.min(minOpacity + (votes / votesForMax) * (maxOpacity - minOpacity), maxOpacity);
}

// Calculate weight based on vote count (linear scale)
function getWeightForVotes(votes) {
  const minWeight = 1.5;
  const maxWeight = 5;
  const votesForMax = 20;  // votes needed to reach max weight
  return Math.min(minWeight + (votes / votesForMax) * (maxWeight - minWeight), maxWeight);
}

const defaultPointStyle = {
  fillColor: DESIRE_PATH.fill,
  color: DESIRE_PATH.stroke,
  weight: 0,
  fillOpacity: 0.16
};

// Get radius based on zoom level
function getRadiusForZoom(zoom) {
  // Scale from 0.5 at zoom 10 to 3 at zoom 18
  return Math.max(0.5, Math.min(3, (zoom - 10) * 0.3 + 0.5));
}

// Keeps Leaflet layers in sync with mapState.overlays
export function createOverlayManager(map) {
  const layersById = new Map();
  const dataHashById = new Map();

  function hashData(data) {
    return JSON.stringify(data);
  }

  // Update circle marker radii on zoom
  function updateRadii() {
    const radius = getRadiusForZoom(map.getZoom());
    for (const layer of layersById.values()) {
      layer.eachLayer((sublayer) => {
        if (sublayer.setRadius) {
          sublayer.setRadius(radius);
        }
      });
    }
  }

  map.on("zoomend", updateRadii);

  function upsertGeoJsonLayer(id, overlay) {
    const newHash = hashData(overlay.data);
    const oldHash = dataHashById.get(id);

    // Skip update if data hasn't changed
    if (oldHash === newHash) {
      return;
    }

    // Remove existing layer
    const existingLayer = layersById.get(id);
    if (existingLayer) {
      map.removeLayer(existingLayer);
    }

    // Create new layer with per-feature styling
    const layer = L.geoJSON(overlay.data, {
      pointToLayer: (feature, latlng) => {
        const radius = getRadiusForZoom(map.getZoom());
        return L.circleMarker(latlng, {
          ...defaultPointStyle,
          radius: radius
        });
      },
      style: (feature) => {
        // Don't apply line styles to points
        if (feature.geometry?.type === "Point") {
          return {};
        }

        // Get mode-specific color
        const mode = feature.properties?.mode;
        const modeColor = MODE_COLORS[mode] || DESIRE_PATH.fill;

        // Get vote count for dynamic styling
        const votes = feature.properties?.votes || 1;

        const baseStyle = {
          ...defaultLineStyle,
          color: modeColor,
          opacity: getOpacityForVotes(votes),
          weight: getWeightForVotes(votes),
          ...overlay.options?.style
        };

        return baseStyle;
      }
    });

    layer.addTo(map);
    layersById.set(id, layer);
    dataHashById.set(id, newHash);
  }

  function removeLayer(id) {
    const layer = layersById.get(id);
    if (!layer) return;
    map.removeLayer(layer);
    layersById.delete(id);
    dataHashById.delete(id);
  }

  function applyMapState(mapState) {
    const desiredIds = new Set(Object.keys(mapState.overlays || {}));

    // remove overlays no longer present
    for (const existingId of layersById.keys()) {
      if (!desiredIds.has(existingId)) removeLayer(existingId);
    }

    // upsert overlays
    for (const [id, overlay] of Object.entries(mapState.overlays || {})) {
      if (overlay.type === "geojson") upsertGeoJsonLayer(id, overlay);
      // later: markers, polylines, heatmaps, etc.
    }
  }

  return { applyMapState };
}
