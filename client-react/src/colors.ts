// ==========================================================================
// Centralized Color Definitions
// All colors used in the application should be defined here.
//
// 2025 Color Palette inspired by Pantone & W3Schools 2025 trends:
// https://www.w3schools.com/Colors/colors_2025.asp
// ==========================================================================

// Marker colors (start/end pins, header dots)
export const COLOR_START = "#2E5283"; // Deja Vu Blue - bold 2025 blue
export const COLOR_END = "#DC343B"; // Poppy Red - vibrant 2025 red

// Desire path overlay - blue like walking dots
export const DESIRE_PATH = {
  fill: "#5B8FBF", // Light blue (matches walk core)
  stroke: "#5B8FBF",
};

// Route mode colors
export const ROUTE_COLORS = {
  walk: {
    glow: "#98DDDF", // Limpet Shell - soft teal glow
    edge: "#3B6EA5", // Brighter dark blue outline
    core: "#5B8FBF", // Lighter blue fill
  },
  bike: {
    glow: "#98DDDF", // Soft teal glow (matches walk)
    middle: "#3B6EA5", // Blue
    core: "#3B6EA5", // Blue core
  },
  drive: {
    glow: "#B8B0A8", // Warmer, more emphatic glow
    asphalt: "#343148", // Eclipse - deep purple-gray
    centerLine: "#E3BD33", // Misted Marigold - bold yellow
  },
  desire: {
    glow: "#F5E6A3", // Soft gold glow
    middle: "#E3BD33", // Misted Marigold gold
    core: "#D4A017", // Deep gold core
  },
  splitDesire: {
    glow: "#F5E6A3",    // Soft gold glow
    middle: "#E3BD33",  // Misted Marigold gold
    core: "#D4A017",    // Deep gold core
  },
};

// UI colors (for reference - primary definitions in CSS)
export const UI = {
  paper: "#F4F5F0", // Bright White - 2025
  ink: "#343148", // Eclipse - deep text
};

// Hex heatmap gradient (for H3 hex visualization)
// Gold gradient to match desire paths
export const HEX_HEATMAP = {
  light: { r: 245, g: 215, b: 110 },  // #F5D76E light gold
  dark: { r: 212, g: 160, b: 23 },    // #D4A017 deep gold
};

// Voted paths color (aggregated community votes)
// Using teal to differentiate from gold desire paths
export const VOTED_PATHS_COLOR = "#3FB8AF"; // Teal/cyan
