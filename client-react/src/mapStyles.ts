// ==========================================================================
// Map Styles
// The visual identity of a map: a two-color (base + accent) representation plus
// the basemap tiles and heatmap ramp that read well on that basemap.
//
// Keyed by a map's `style` id (see map/runtime.ts MapConfig.style and
// themes.ts themeFromMap — the resolved theme's `id` is the style key). Preset
// maps use bikepaths/walkways/trees; user-created maps fall back to `default`.
// Adding a new look = add an entry here and reference it from a map's style.
// ==========================================================================

export type Basemap = "dark" | "light";

/**
 * Cross-section color stops for the vote heatmap. The renderer strokes each
 * stop at a different width so the gradient runs ACROSS the line (halo on the
 * outside, peak in the core). Dark basemaps blend additively (screen/lighter);
 * the light basemap blends via multiply so heat darkens the map.
 */
export interface HeatRamp {
  halo: string; // outer glow (widest, faintest)
  warm: string; // dominant mid color
  hot: string;  // core, kicks in past mid intensity
  peak: string; // brightest, only the hottest edges
}

export interface MapStyle {
  id: string;
  basemap: Basemap; // drives the [data-basemap] CSS palette + tile/background choice
  base: string;     // two-color representation — primary surface / map background
  accent: string;   // two-color representation — accent / heat identity
  selection: string; // hover/selection outline (white on dark, dark on light)

  // Raster basemap tiles (CARTO). {s} subdomain + {r} retina placeholders.
  tileUrl: string;
  tileAttribution: string;
  tileMaxZoom: number;

  // Heatmap rendering.
  heat: HeatRamp;
  heatBlend: "screen" | "multiply";         // canvas element mix-blend-mode
  heatComposite: "lighter" | "source-over"; // in-canvas globalCompositeOperation
}

const CARTO_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

const TILE_DARK =
  "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png";
// Positron — a near-greyscale light basemap. Minimal coloring so the green
// heat + selection do the talking (Voyager's colored parks were too busy).
const TILE_LIGHT =
  "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png";

// Dark base + warm yellow-brown orange. Shared by walkways and the neutral
// default so user maps get a sensible look out of the box.
const DARK_WARM: Omit<MapStyle, "id"> = {
  basemap: "dark",
  base: "#0d0d0d",
  accent: "#E0A23A",
  selection: "#FFFFFF",
  tileUrl: TILE_DARK,
  tileAttribution: CARTO_ATTRIBUTION,
  tileMaxZoom: 21,
  heat: {
    halo: "rgb(140, 78, 24)",
    warm: "rgb(214, 142, 42)",
    hot: "rgb(245, 200, 96)",
    peak: "rgb(255, 242, 212)",
  },
  heatBlend: "screen",
  heatComposite: "lighter",
};

// Build a dark-basemap style from an accent + heat ramp. All dark themes share
// the same near-black base, tiles, white selection, and additive screen blend.
function darkStyle(id: string, accent: string, heat: HeatRamp): MapStyle {
  return {
    id,
    basemap: "dark",
    base: "#0d0d0d",
    accent,
    selection: "#FFFFFF",
    tileUrl: TILE_DARK,
    tileAttribution: CARTO_ATTRIBUTION,
    tileMaxZoom: 21,
    heat,
    heatBlend: "screen",
    heatComposite: "lighter",
  };
}

// Build a light-basemap style from an accent + heat ramp. Light themes share the
// warm paper base + greyscale tiles and darken via multiply (additive blending
// would wash out on a light map), with a near-black selection for contrast.
function lightStyle(id: string, accent: string, heat: HeatRamp): MapStyle {
  return {
    id,
    basemap: "light",
    base: "#F4F5F0",
    accent,
    selection: "#1f2320",
    tileUrl: TILE_LIGHT,
    tileAttribution: CARTO_ATTRIBUTION,
    tileMaxZoom: 21,
    heat,
    heatBlend: "multiply",
    heatComposite: "source-over",
  };
}

export const MAP_STYLES: Record<string, MapStyle> = {
  // Bikes — dark basemap, bright bike-lane green. Additive blending lets the
  // green stack toward a hot lime/white core at busy intersections.
  bikepaths: darkStyle("bikepaths", "#2BE06B", {
    halo: "rgb(16, 110, 46)",
    warm: "rgb(38, 190, 92)",
    hot: "rgb(150, 245, 130)",
    peak: "rgb(225, 255, 215)",
  }),

  // Walkways — dark basemap, warm yellow-brown orange.
  walkways: { id: "walkways", ...DARK_WARM },

  // Transit & mobility — dark basemap, signal blue that glows like a transit map.
  transit: darkStyle("transit", "#3B8EE0", {
    halo: "rgb(24, 70, 140)",
    warm: "rgb(48, 122, 214)",
    hot: "rgb(110, 184, 245)",
    peak: "rgb(212, 234, 255)",
  }),

  // Waterfront & blue infrastructure — dark basemap, harbor teal/cyan.
  waterfront: darkStyle("waterfront", "#22C9C9", {
    halo: "rgb(16, 110, 116)",
    warm: "rgb(38, 178, 184)",
    hot: "rgb(120, 228, 232)",
    peak: "rgb(212, 252, 252)",
  }),

  // Trees / parks & greening — light basemap, leaf green.
  trees: lightStyle("trees", "#5FA052", {
    halo: "rgb(198, 224, 188)",
    warm: "rgb(132, 190, 104)",
    hot: "rgb(78, 150, 58)",
    peak: "rgb(40, 102, 34)",
  }),

  // Streets & public space — light basemap, terracotta (brick / paving).
  terracotta: lightStyle("terracotta", "#C0674A", {
    halo: "rgb(234, 208, 198)",
    warm: "rgb(206, 146, 122)",
    hot: "rgb(178, 94, 68)",
    peak: "rgb(120, 56, 38)",
  }),

  // Neutral default for user-created maps without a preset style.
  default: { id: "default", ...DARK_WARM },
};

export const DEFAULT_MAP_STYLE = MAP_STYLES.default;

/**
 * Themes a proposer can pick for a new map (must mirror _VALID_MAP_STYLES in
 * server/app.py). A small, curated set: four dark looks plus two light ones in
 * hues that read as urbanist (amber streets, bike green, transit blue, harbor
 * teal, park green, terracotta public space). `walkways` is omitted because
 * it's visually identical to `default`; each id keys into MAP_STYLES.
 */
export const SELECTABLE_MAP_STYLES: { id: string; label: string }[] = [
  { id: "default", label: "Streets (amber)" },
  { id: "bikepaths", label: "Bikes (green)" },
  { id: "transit", label: "Transit (blue)" },
  { id: "waterfront", label: "Waterfront (teal)" },
  { id: "trees", label: "Parks (green)" },
  { id: "terracotta", label: "Public space (terracotta)" },
];

/** Resolve a style id to its MapStyle, falling back to the default. */
export function getMapStyle(id: string): MapStyle {
  return MAP_STYLES[id] ?? DEFAULT_MAP_STYLE;
}

/**
 * Expand a style's tile template into the 4 retina (@2x) subdomain URLs that
 * MapLibre's raster source expects (it has no {s}/{r} placeholder support).
 */
export function maplibreRasterTiles(style: MapStyle): string[] {
  return ["a", "b", "c", "d"].map((s) =>
    style.tileUrl.replace("{s}", s).replace("{r}", "@2x"),
  );
}

/**
 * Build the CSS `linear-gradient(...)` for the heatmap legend swatch from a
 * style's ramp, so the legend always matches the map.
 */
export function heatGradientCss(heat: HeatRamp): string {
  return `linear-gradient(to right, ${heat.halo}, ${heat.warm} 40%, ${heat.hot} 75%, ${heat.peak})`;
}
