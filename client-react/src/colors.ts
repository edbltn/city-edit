// ==========================================================================
// Centralized Color Definitions
// All colors used in the application should be defined here.
//
// 2025 Color Palette inspired by Pantone & W3Schools 2025 trends:
// https://www.w3schools.com/Colors/colors_2025.asp
// ==========================================================================

// Marker colors (start/end pins, header dots)
export const COLOR_START = "#00C4D4"; // Bright cyan-teal — pops against orange heatmap
export const COLOR_END = "#DC343B"; // Poppy Red - vibrant 2025 red

// GIS-style selection highlight (the QGIS/ArcGIS "selected features" yellow).
// Used for the corridor of edges sharing a selected top proposal's vote type.
export const SELECTION_YELLOW = "#FFE816";

// Desire path overlay - green like bike paths
export const DESIRE_PATH = {
  fill: "#3CB371",
  stroke: "#3CB371",
};

// Route mode colors
export const ROUTE_COLORS = {
  walk: {
    glow: "#98E0B0",
    edge: "#2E8B57",
    core: "#3CB371",
  },
  bike: {
    glow: "#98E0B0",
    middle: "#2E8B57",
    core: "#2E8B57",
  },
  bikeDesire: {
    glow: "#98E0B0",
    edge: "#2E8B57",
    core: "#3CB371",
  },
  drive: {
    glow: "#B8B0A8", // Warmer, more emphatic glow
    asphalt: "#343148", // Eclipse - deep purple-gray
    centerLine: "#E07070", // Warm red accent
  },
  desire: {
    glow: "rgba(255, 255, 255, 0.15)", // Soft white glow
    middle: "#FFFFFF", // White selection boundary
    core: "#FFFFFF",
  },
  splitDesire: {
    glow: "rgba(255, 255, 255, 0.15)",
    middle: "#FFFFFF",
    core: "#FFFFFF",
  },
};

// UI colors (for reference - primary definitions in CSS)
export const UI = {
  paper: "#F4F5F0", // Bright White - 2025
  ink: "#343148", // Eclipse - deep text
};

