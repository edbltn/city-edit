// ==========================================================================
// Route Layer Styling
// Creates multi-layer route visualizations based on transport mode.
// ==========================================================================

import { ROUTE_COLORS } from "./colors.js";

/**
 * Create route layers based on mode.
 * Returns a LayerGroup that can contain multiple overlapping layers.
 */
export function createRouteLayer(geometry, mode, pane) {
  const layers = [];

  if (mode === "walk") {
    const colors = ROUTE_COLORS.walk;
    // Light blue outer glow
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.glow,
        weight: 12,
        opacity: 0.4,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Dark blue edge/outline layer
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.edge,
        weight: 8,
        opacity: 0.9,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Light blue fill dots on top
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.core,
        weight: 5,
        opacity: 1,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round"
      }
    }));
  } else if (mode === "bike") {
    const colors = ROUTE_COLORS.bike;
    // Outer glow layer
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.glow,
        weight: 8,
        opacity: 0.3,
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Middle glow
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.middle,
        weight: 6,
        opacity: 0.5,
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Core line
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.core,
        weight: 4,
        opacity: 1,
        lineCap: "round",
        lineJoin: "round"
      }
    }));
  } else if (mode === "drive") {
    const colors = ROUTE_COLORS.drive;
    // White outer glow
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.glow,
        weight: 12,
        opacity: 0.4,
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Dark asphalt base
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.asphalt,
        weight: 7,
        opacity: 0.95,
        lineCap: "round",
        lineJoin: "round"
      }
    }));
    // Yellow center dashed line
    layers.push(L.geoJSON(geometry, {
      pane,
      style: {
        color: colors.centerLine,
        weight: 1.5,
        opacity: 0.9,
        dashArray: "6, 12",
        lineCap: "butt",
        lineJoin: "round"
      }
    }));
  }

  return L.layerGroup(layers);
}

/**
 * Create multi-segment route layer for Roam mode.
 * Each segment is rendered with its own mode styling.
 *
 * @param {Object} routeData - Full route response with geometry and segments
 * @param {string} pane - Leaflet pane name
 * @returns {L.LayerGroup} Layer group containing all segment layers
 */
export function createRoamRouteLayer(routeData, pane) {
  const layers = [];
  const segments = routeData.segments || [];

  // If no segments, fall back to bike styling for the whole geometry
  if (segments.length === 0 && routeData.geometry) {
    return createRouteLayer(routeData.geometry, "bike", pane);
  }

  // Render each segment with its mode's style
  for (const segment of segments) {
    const segmentMode = segment.mode || "bike";
    const coords = segment.coordinates || [];

    if (coords.length < 2) continue;

    // Create GeoJSON geometry from segment coordinates
    const segmentGeometry = {
      type: "LineString",
      coordinates: coords
    };

    // Skip portal segments (they're just transitions)
    if (segmentMode === "portal") continue;

    // Use the mode-specific styling
    const segmentLayer = createRouteLayer(segmentGeometry, segmentMode, pane);
    segmentLayer.eachLayer((layer) => layers.push(layer));
  }

  return L.layerGroup(layers);
}

/**
 * Create desire path layer - a glowing gold line showing where people would
 * prefer to go (walk route) vs where infrastructure forces them (mode route).
 *
 * @param {Object} geometry - GeoJSON LineString geometry
 * @param {string} pane - Leaflet pane name
 * @returns {L.LayerGroup} Layer group with desire path styling
 */
export function createDesirePathLayer(geometry, pane) {
  if (!geometry) return L.layerGroup([]);

  const colors = ROUTE_COLORS.desire;
  const layers = [];

  // Outer glow layer
  layers.push(L.geoJSON(geometry, {
    pane,
    style: {
      color: colors.glow,
      weight: 8,
      opacity: 0.3,
      lineCap: "round",
      lineJoin: "round"
    }
  }));

  // Middle glow
  layers.push(L.geoJSON(geometry, {
    pane,
    style: {
      color: colors.middle,
      weight: 6,
      opacity: 0.5,
      lineCap: "round",
      lineJoin: "round"
    }
  }));

  // Core line
  layers.push(L.geoJSON(geometry, {
    pane,
    style: {
      color: colors.core,
      weight: 4,
      opacity: 1,
      lineCap: "round",
      lineJoin: "round"
    }
  }));

  return L.layerGroup(layers);
}
