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
  // Hue runs purple → red-orange → amber → gold so intensity reads as a color
  // shift, not just brightness. Peak is gold (not near-white) to keep glow tame.
  heat: {
    halo: "rgb(96, 56, 120)",
    warm: "rgb(196, 96, 56)",
    hot: "rgb(232, 154, 54)",
    peak: "rgb(250, 214, 120)",
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
  // Hue runs blue-teal → green → yellow-green → gold so hotspots glow warm
  // against a cool low-traffic field.
  bikepaths: darkStyle("bikepaths", "#2BE06B", {
    halo: "rgb(20, 88, 124)",
    warm: "rgb(36, 168, 96)",
    hot: "rgb(146, 210, 70)",
    peak: "rgb(244, 206, 96)",
  }),

  // Walkways — dark basemap, warm yellow-brown orange.
  walkways: { id: "walkways", ...DARK_WARM },

  // Transit & mobility — dark basemap, signal blue that glows like a transit map.
  // Hue runs indigo → blue → cyan → mint so intensity climbs the cool spectrum.
  transit: darkStyle("transit", "#3B8EE0", {
    halo: "rgb(60, 44, 132)",
    warm: "rgb(50, 118, 206)",
    hot: "rgb(58, 192, 196)",
    peak: "rgb(176, 240, 206)",
  }),

  // Waterfront & blue infrastructure — dark basemap, harbor teal/cyan.
  // Hue runs deep blue → teal → green → pale yellow so hotspots warm up.
  waterfront: darkStyle("waterfront", "#22C9C9", {
    halo: "rgb(28, 72, 142)",
    warm: "rgb(34, 168, 174)",
    hot: "rgb(118, 214, 128)",
    peak: "rgb(228, 232, 140)",
  }),

  // Trees / parks & greening — light basemap, leaf green.
  // Light basemap (multiply): pale chartreuse → green → emerald → deep blue, so
  // intensity both darkens and shifts hue toward blue.
  trees: lightStyle("trees", "#5FA052", {
    halo: "rgb(206, 222, 152)",
    warm: "rgb(122, 182, 84)",
    hot: "rgb(44, 138, 98)",
    peak: "rgb(22, 74, 112)",
  }),

  // Streets & public space — light basemap, terracotta (brick / paving).
  // Light basemap (multiply): pale sand → terracotta → brick red → deep plum.
  terracotta: lightStyle("terracotta", "#C0674A", {
    halo: "rgb(238, 214, 176)",
    warm: "rgb(208, 138, 90)",
    hot: "rgb(176, 72, 70)",
    peak: "rgb(92, 32, 82)",
  }),

  // Culture & community — light basemap, plum/violet.
  // Light basemap (multiply): pale rose → orchid → violet → indigo.
  plum: lightStyle("plum", "#8E5AA8", {
    halo: "rgb(234, 202, 204)",
    warm: "rgb(182, 122, 184)",
    hot: "rgb(120, 72, 162)",
    peak: "rgb(52, 40, 112)",
  }),

  // Neutral default for user-created maps without a preset style.
  default: { id: "default", ...DARK_WARM },
};

export const DEFAULT_MAP_STYLE = MAP_STYLES.default;

/**
 * Themes a proposer can pick for a new map (must mirror _VALID_MAP_STYLES in
 * server/app.py). A small, curated set: four dark looks plus three light ones in
 * hues that read as urbanist (amber streets, bike green, transit blue, harbor
 * teal, park green, terracotta public space, plum culture). `walkways` is omitted
 * because it's visually identical to `default`; each id keys into MAP_STYLES.
 */
export const SELECTABLE_MAP_STYLES: { id: string; label: string }[] = [
  { id: "default", label: "Streets (amber)" },
  { id: "bikepaths", label: "Bikes (green)" },
  { id: "transit", label: "Transit (blue)" },
  { id: "waterfront", label: "Waterfront (teal)" },
  { id: "trees", label: "Parks (green)" },
  { id: "terracotta", label: "Public space (terracotta)" },
  { id: "plum", label: "Culture (purple)" },
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

// A heat ramp expanded to positioned RGB stops, ready for interpolation. The
// stop positions (0 / 0.4 / 0.75 / 1) mirror heatGradientCss so the legend, the
// canvas heatmap, and the proposal pins all read the same color at a given
// intensity. Built once per style and sampled many times.
export interface HeatRampStop {
  pos: number;
  rgb: [number, number, number];
}

function parseRgb(s: string): [number, number, number] {
  const m = s.match(/\d+/g);
  return m ? [Number(m[0]), Number(m[1]), Number(m[2])] : [0, 0, 0];
}

/** Expand a HeatRamp into the positioned stops `sampleHeatRamp` interpolates. */
export function buildHeatRampStops(heat: HeatRamp): HeatRampStop[] {
  return [
    { pos: 0, rgb: parseRgb(heat.halo) },
    { pos: 0.4, rgb: parseRgb(heat.warm) },
    { pos: 0.75, rgb: parseRgb(heat.hot) },
    { pos: 1, rgb: parseRgb(heat.peak) },
  ];
}

/**
 * Linearly interpolate the ramp at intensity `t` (0–1, clamped) and return an
 * `rgb(...)` string. Shared by the canvas heatmap and the top-proposal pins so a
 * pin glows the same hue the heatmap would paint at that vote count.
 */
export function sampleHeatRamp(stops: HeatRampStop[], t: number): string {
  let hi = 1;
  while (hi < stops.length - 1 && t > stops[hi].pos) hi++;
  const lo = stops[hi - 1];
  const up = stops[hi];
  const span = up.pos - lo.pos || 1;
  const f = Math.min(1, Math.max(0, (t - lo.pos) / span));
  const r = Math.round(lo.rgb[0] + (up.rgb[0] - lo.rgb[0]) * f);
  const g = Math.round(lo.rgb[1] + (up.rgb[1] - lo.rgb[1]) * f);
  const b = Math.round(lo.rgb[2] + (up.rgb[2] - lo.rgb[2]) * f);
  return `rgb(${r}, ${g}, ${b})`;
}
