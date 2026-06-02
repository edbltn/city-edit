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
// Voyager renders parks/green space in soft greens — a natural fit for a light,
// foliage-forward map.
const TILE_LIGHT =
  "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_nolabels/{z}/{x}/{y}{r}.png";

// Dark base + warm yellow-brown orange. Shared by walkways and the neutral
// default so user maps get a sensible look out of the box.
const DARK_WARM: Omit<MapStyle, "id"> = {
  basemap: "dark",
  base: "#0d0d0d",
  accent: "#E0A23A",
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

export const MAP_STYLES: Record<string, MapStyle> = {
  // Bikes — dark basemap, bright bike-lane green. Additive blending lets the
  // green stack toward a hot lime/white core at busy intersections.
  bikepaths: {
    id: "bikepaths",
    basemap: "dark",
    base: "#0d0d0d",
    accent: "#2BE06B",
    tileUrl: TILE_DARK,
    tileAttribution: CARTO_ATTRIBUTION,
    tileMaxZoom: 21,
    heat: {
      halo: "rgb(16, 110, 46)",
      warm: "rgb(38, 190, 92)",
      hot: "rgb(150, 245, 130)",
      peak: "rgb(225, 255, 215)",
    },
    heatBlend: "screen",
    heatComposite: "lighter",
  },

  // Walkways — dark basemap, warm yellow-brown orange.
  walkways: { id: "walkways", ...DARK_WARM },

  // Trees — light basemap, lighter green. Multiply blending darkens the light
  // map toward green where votes concentrate (additive would wash out on light).
  trees: {
    id: "trees",
    basemap: "light",
    base: "#F4F5F0",
    accent: "#5FA052",
    tileUrl: TILE_LIGHT,
    tileAttribution: CARTO_ATTRIBUTION,
    tileMaxZoom: 21,
    heat: {
      halo: "rgb(198, 224, 188)",
      warm: "rgb(132, 190, 104)",
      hot: "rgb(78, 150, 58)",
      peak: "rgb(40, 102, 34)",
    },
    heatBlend: "multiply",
    heatComposite: "source-over",
  },

  // Neutral default for user-created maps without a preset style.
  default: { id: "default", ...DARK_WARM },
};

export const DEFAULT_MAP_STYLE = MAP_STYLES.default;

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
