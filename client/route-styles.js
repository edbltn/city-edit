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
