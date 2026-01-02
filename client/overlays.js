import { DESIRE_PATH, COLOR_END } from "./colors.js";

const defaultLineStyle = {
  color: COLOR_END,     // red default for desire paths
  weight: 3,            // stroke width in pixels
  opacity: 0.85,        // overall stroke opacity
  lineCap: "round",
  lineJoin: "round"
};

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

        const baseStyle = {
          ...defaultLineStyle,
          ...overlay.options?.style
        };

        // Use per-feature opacity if available
        if (feature.properties?.opacity !== undefined) {
          baseStyle.opacity = feature.properties.opacity;
        }

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
