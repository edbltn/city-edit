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
// Teal gradient - cool and distinct from blue walking routes
export const HEX_HEATMAP = {
  light: { r: 128, g: 203, b: 196 },  // #80CBC4 soft teal
  dark: { r: 0, g: 77, b: 64 },       // #004D40 deep forest teal
};
