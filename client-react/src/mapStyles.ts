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
  // The NEGATIVE arm: block heat is the top proposal's vote differential
  // (up − down), so a net-against block ramps the other way — into cold.
  cold: string;     // mildly negative (any net-against differential)
  coldDeep: string; // overwhelmingly negative — the "almost blue" floor
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

  // The labels half of the same CARTO cartography, as a transparent overlay.
  // Drawn ABOVE the vote heat — see labelOpacity.
  labelTileUrl: string;
  // How loudly the labels speak. Heat is the product and labels are orientation
  // furniture, so they're deliberately held back below full strength. The light
  // basemap's slate text is much heavier on paper than the dark basemap's grey
  // on near-black, so it's pulled down further.
  labelOpacity: number;

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

// Labels-only companions to the two basemaps above: the SAME CARTO cartography
// split into its text half, served as transparent PNGs. Keeping the basemap
// label-free and re-adding the text as a separate layer is what lets the labels
// sit on top of the vote heat instead of underneath it — a street name buried
// under a translucent hot block is unreadable, and it's the heat that has to
// stay the loudest thing on the map.
//
// Coverage (verified against CARTO's own tiles): street names, neighborhood and
// borough names, water, parks and a handful of named landmarks. NOT businesses
// — those come from the separate POI layer built off our own OSM extract.
const TILE_DARK_LABELS =
  "https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png";
const TILE_LIGHT_LABELS =
  "https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png";

// Dark basemaps get the labels at FULL strength. CARTO's dark label tiles are
// light grey with a black halo baked in — dialling them back sounds tasteful in
// isolation, but on a busy map (Williamsburg on nyc-bikes) the heat blocks are
// saturated blue/green and anything under ~1.0 washes the street names out
// completely. There is no halo knob on a raster overlay, so opacity is the only
// contrast control and it has to stay pinned.
const LABEL_OPACITY_DARK = 1.0;
// Light basemaps can afford restraint: the slate text is already high-contrast
// on near-white paper, and these styles blend heat by MULTIPLY, so a hot block
// darkens the paper *around* the text rather than lighting up behind it.
const LABEL_OPACITY_LIGHT = 0.75;

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
  labelTileUrl: TILE_DARK_LABELS,
  labelOpacity: LABEL_OPACITY_DARK,
  // Hue runs purple → red-orange → amber → gold so intensity reads as a color
  // shift, not just brightness. Peak is gold (not near-white) to keep glow tame.
  // Negative arm dives through indigo to icy blue — the opposite temperature.
  heat: {
    halo: "rgb(96, 56, 120)",
    warm: "rgb(196, 96, 56)",
    hot: "rgb(232, 154, 54)",
    peak: "rgb(250, 214, 120)",
    cold: "rgb(74, 84, 190)",
    coldDeep: "rgb(92, 118, 250)",
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
    labelTileUrl: TILE_DARK_LABELS,
    labelOpacity: LABEL_OPACITY_DARK,
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
    labelTileUrl: TILE_LIGHT_LABELS,
    labelOpacity: LABEL_OPACITY_LIGHT,
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
  // Negative arm: the green field freezes — steel blue down to a vivid
  // near-pure blue, so an overwhelmingly net-against block reads almost blue.
  bikepaths: darkStyle("bikepaths", "#2BE06B", {
    halo: "rgb(20, 88, 124)",
    warm: "rgb(36, 168, 96)",
    hot: "rgb(146, 210, 70)",
    peak: "rgb(244, 206, 96)",
    cold: "rgb(38, 108, 205)",
    coldDeep: "rgb(70, 105, 255)",
  }),

  // Walkways — dark basemap, warm yellow-brown orange.
  walkways: { id: "walkways", ...DARK_WARM },

  // Transit & mobility — dark basemap, signal blue that glows like a transit map.
  // Hue runs indigo → blue → cyan → mint so intensity climbs the cool spectrum.
  // Positive heat already climbs the cool blues, so the negative arm swings to
  // violet/magenta — unmistakably "against", never confusable with busy-cool.
  transit: darkStyle("transit", "#3B8EE0", {
    halo: "rgb(60, 44, 132)",
    warm: "rgb(50, 118, 206)",
    hot: "rgb(58, 192, 196)",
    peak: "rgb(176, 240, 206)",
    cold: "rgb(122, 58, 196)",
    coldDeep: "rgb(178, 74, 235)",
  }),

  // Waterfront & blue infrastructure — dark basemap, harbor teal/cyan.
  // Hue runs deep blue → teal → green → pale yellow so hotspots warm up.
  // Teal positives; the negative arm goes indigo → violet (blue alone would
  // read as more water).
  waterfront: darkStyle("waterfront", "#22C9C9", {
    halo: "rgb(28, 72, 142)",
    warm: "rgb(34, 168, 174)",
    hot: "rgb(118, 214, 128)",
    peak: "rgb(228, 232, 140)",
    cold: "rgb(104, 64, 200)",
    coldDeep: "rgb(158, 80, 240)",
  }),

  // Trees / parks & greening — light basemap, leaf green.
  // Light basemap (multiply): pale chartreuse → green → emerald → deep blue, so
  // intensity both darkens and shifts hue toward blue.
  // Multiply basemap: the negative arm tints toward slate → strong blue (a
  // multiply blue darkens the paper coolly, the inverse of the leafy warmth).
  trees: lightStyle("trees", "#5FA052", {
    halo: "rgb(206, 222, 152)",
    warm: "rgb(122, 182, 84)",
    hot: "rgb(44, 138, 98)",
    peak: "rgb(22, 74, 112)",
    cold: "rgb(132, 150, 214)",
    coldDeep: "rgb(62, 82, 190)",
  }),

  // Streets & public space — light basemap, terracotta (brick / paving).
  // Light basemap (multiply): pale sand → terracotta → brick red → deep plum.
  terracotta: lightStyle("terracotta", "#C0674A", {
    halo: "rgb(238, 214, 176)",
    warm: "rgb(208, 138, 90)",
    hot: "rgb(176, 72, 70)",
    peak: "rgb(92, 32, 82)",
    cold: "rgb(138, 152, 208)",
    coldDeep: "rgb(56, 84, 186)",
  }),

  // Culture & community — light basemap, plum/violet.
  // Light basemap (multiply): pale rose → orchid → violet → indigo.
  // Violet positives; the negative arm cools to steel/teal-blue so it can't be
  // mistaken for a deeper violet.
  plum: lightStyle("plum", "#8E5AA8", {
    halo: "rgb(234, 202, 204)",
    warm: "rgb(182, 122, 184)",
    hot: "rgb(120, 72, 162)",
    peak: "rgb(52, 40, 112)",
    cold: "rgb(118, 158, 202)",
    coldDeep: "rgb(42, 104, 176)",
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

/**
 * Place-label text colors, keyed by the `cat` property each POI carries from
 * build_place_labels.py. One palette per basemap rather than one per style:
 * color coding only pays off if it's learnable, and re-hueing the categories
 * for each of the eight map styles would mean re-learning them on every map.
 *
 * Retail is deliberately the most neutral of the four. It is by far the largest
 * category, so tinting every deli and bank would turn the deep zooms into
 * confetti — the color is there to make transit and civic anchors findable at a
 * glance, not to decorate.
 *
 * There is no green here: parks and squares are labelled by the CARTO raster
 * underneath, in its own green, and the builder drops them for that reason.
 */
export const POI_COLORS: Record<Basemap, Record<string, string>> = {
  dark: {
    transit: "#7FB2FF",
    civic: "#E3BE79",
    culture: "#C49BDE",
    retail: "#BFBAB2",
  },
  light: {
    transit: "#2C5FB4",
    civic: "#8F5E12",
    culture: "#743C93",
    retail: "#5A554D",
  },
};

/** Resolve a style id to its MapStyle, falling back to the default. */
export function getMapStyle(id: string): MapStyle {
  return MAP_STYLES[id] ?? DEFAULT_MAP_STYLE;
}

/**
 * Expand a style's tile template into the 4 retina (@2x) subdomain URLs that
 * MapLibre's raster source expects (it has no {s}/{r} placeholder support).
 */
export function maplibreRasterTiles(style: MapStyle): string[] {
  return expandTileTemplate(style.tileUrl);
}

/** Same expansion for the labels-only overlay tiles. */
export function maplibreLabelTiles(style: MapStyle): string[] {
  return expandTileTemplate(style.labelTileUrl);
}

function expandTileTemplate(template: string): string[] {
  return ["a", "b", "c", "d"].map((s) =>
    template.replace("{s}", s).replace("{r}", "@2x"),
  );
}

/**
 * The incandescent tip of a ramp — the color reserved for the very hottest
 * intensities, ABOVE `peak`. Votes are heavy-tailed (log-normalized heat piles
 * up near 1.0 on busy maps), and a ramp that ends at `peak` renders that whole
 * tail as one flat color. The tip extends the ramp past `peak`: toward
 * white-hot on dark basemaps (additive blending — hotter = brighter) and
 * toward near-black ink on light ones (multiply — hotter = darker), so the
 * hottest corridor visibly outburns a merely-busy one.
 */
export function heatTip(heat: HeatRamp, basemap: Basemap): string {
  const [r, g, b] = parseRgb(heat.peak);
  const [tr, tg, tb] = basemap === "light" ? [10, 12, 18] : [255, 252, 242];
  const mix = 0.68;
  return `rgb(${Math.round(r + (tr - r) * mix)}, ${Math.round(g + (tg - g) * mix)}, ${Math.round(b + (tb - b) * mix)})`;
}

/**
 * Build the CSS `linear-gradient(...)` for the heatmap legend swatch from a
 * style's ramp, so the legend always matches the map. The left quarter is the
 * negative arm (net-against differentials); zero sits at 25%.
 */
export function heatGradientCss(heat: HeatRamp, basemap: Basemap): string {
  return `linear-gradient(to right, ${heat.coldDeep}, ${heat.cold} 18%, ${heat.halo} 25%, ${heat.warm} 51%, ${heat.hot} 74%, ${heat.peak} 89%, ${heatTip(heat, basemap)})`;
}

// A heat ramp expanded to positioned RGB stops, ready for interpolation. The
// stop positions (0 / 0.35 / 0.65 / 0.85 / 1) mirror heatGradientCss so the
// legend, the canvas heatmap, and the proposal pins all read the same color at
// a given intensity. Built once per style and sampled many times.
export interface HeatRampStop {
  pos: number;
  rgb: [number, number, number];
}

function parseRgb(s: string): [number, number, number] {
  const m = s.match(/\d+/g);
  return m ? [Number(m[0]), Number(m[1]), Number(m[2])] : [0, 0, 0];
}

/** Parse `#rrggbb` or `rgb(r, g, b)` into RGB components. Accent colors are
 *  authored as hex; ramp colors as rgb() — the pin ramp needs both. */
function parseColor(s: string): [number, number, number] {
  const hex = s.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }
  return parseRgb(s);
}

function mixRgb(
  a: [number, number, number],
  b: [number, number, number],
  f: number,
): [number, number, number] {
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

/** Ramp position of `peak` — the upper extreme of the NAMED ramp colors. The
 *  proposal pins cap their color spectrum here (a pin ring should wear the
 *  ramp's top color, not blaze); only the heatmap itself runs past it into the
 *  incandescent tip. */
export const HEAT_PEAK_POS = 0.85;

/** Expand a HeatRamp into the positioned stops `sampleHeatRamp` interpolates.
 *  `peak` sits at HEAT_PEAK_POS (not 1) with the incandescent tip above it, so
 *  the log-crushed top of a busy map's vote distribution still resolves into
 *  visibly different colors instead of one flat band. */
export function buildHeatRampStops(heat: HeatRamp, basemap: Basemap): HeatRampStop[] {
  return [
    { pos: 0, rgb: parseRgb(heat.halo) },
    { pos: 0.35, rgb: parseRgb(heat.warm) },
    { pos: 0.65, rgb: parseRgb(heat.hot) },
    { pos: HEAT_PEAK_POS, rgb: parseRgb(heat.peak) },
    { pos: 1, rgb: parseRgb(heatTip(heat, basemap)) },
  ];
}

/**
 * The ramp the TOP-PROPOSAL PINS sample for their glow ring + fill tint.
 *
 * Dark basemaps: the heat ramp itself — its additive colors read vivid against
 * the dark paper, so a pin genuinely glows the hue the heatmap paints.
 *
 * Light basemaps: the multiply ramps are engineered to DARKEN the basemap —
 * pale sand at the cold end, near-black plum/indigo at peak. Painted flat on a
 * pin ring they read as greys and tans, never as the map's identity color. So
 * light styles anchor the pin spectrum to the style's ACCENT instead: a pale
 * accent tint at the lowest rank, climbing to the pure accent on the hottest
 * pin (sampled at HEAT_PEAK_POS), with an ink-deepened accent above it for the
 * unused tip. Rank still reads as color depth, and every pin wears the theme.
 */
export function buildPinRampStops(style: MapStyle): HeatRampStop[] {
  if (style.basemap !== "light") return buildHeatRampStops(style.heat, style.basemap);
  const accent = parseColor(style.accent);
  const paper = parseColor(style.base);
  const ink = parseColor(style.selection);
  return [
    { pos: 0, rgb: mixRgb(accent, paper, 0.55) },
    { pos: HEAT_PEAK_POS, rgb: accent },
    { pos: 1, rgb: mixRgb(accent, ink, 0.3) },
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
