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
  // Bike desire path - green
  bikeDesire: {
    glow: "#98E0B0", // Soft green glow
    edge: "#2E8B57", // Sea green outline
    core: "#3CB371", // Medium sea green fill
  },
  drive: {
    glow: "#B8B0A8", // Warmer, more emphatic glow
    asphalt: "#343148", // Eclipse - deep purple-gray
    centerLine: "#E3BD33", // Misted Marigold - bold yellow
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

