/**
 * Little line icons for the place-label layer.
 *
 * These are drawn at ~8-10 CSS px, which is the constraint behind every decision
 * here. Each glyph is built on a 16×16 grid with a 1.5 stroke — the same
 * thin-line language as the vote-type icons in public/icons (32-grid, 1.2
 * stroke), scaled so the strokes hold together at a third of the size. Anything
 * with more than three or four primitives turns to mush at this scale, so the
 * set is deliberately coarser than the OSM tags feeding it: a pub and a bar
 * share a glass, a college and a university share a mortarboard.
 *
 * Icons are rasterised at mount and registered with `map.addImage`, rather than
 * shipped as a sprite sheet. That keeps them themeable — each glyph is baked in
 * its category's colour for the ACTIVE basemap, so there is no sprite to
 * regenerate when a palette changes and no second asset pipeline to keep in
 * sync with mapStyles.ts.
 *
 * `kind` values must match the icon slugs in server/build_place_labels.py's
 * KINDS table; tests/unit/test_place_labels.py asserts the two stay in step.
 */

import maplibregl from "maplibre-gl";
import { POI_COLORS, type Basemap } from "../../mapStyles";
import { dlog, dwarn } from "../../utils/debugLog";

/** Rendered size in CSS pixels, before the layer's zoom-dependent icon-size
 *  (so a mark is ~8-10px on screen). Small enough to read as an annotation on
 *  the map and never as a pin competing with the top-proposal markers. */
const ICON_PX = 11;

/** Every glyph's category (which is what colours it) and its SVG body. Paths
 *  are stroked unless they opt into `fill` — see toSvg. */
interface IconDef {
  cat: keyof typeof POI_COLORS.dark;
  body: string;
  /** Fill the shape instead of stroking it (used where an outline would be
   *  too fine to survive rasterisation, e.g. the aircraft). */
  filled?: boolean;
}

const ICONS: Record<string, IconDef> = {
  // ── Transit ───────────────────────────────────────────────────────────────
  // A metro car read head-on: body, window band, two lamps. This is the one
  // glyph that has to be unmistakable — 496 of NYC's stations are named after
  // the street above them, so without a mark "5th Avenue" in transit blue is
  // indistinguishable from the basemap's own "5th Avenue" street label.
  subway: {
    cat: "transit",
    body: `<rect x="4" y="2.4" width="8" height="11.2" rx="2.4"/>
           <line x1="5.2" y1="7.2" x2="10.8" y2="7.2"/>
           <circle cx="6.4" cy="10.6" r="0.9" fill="currentColor" stroke="none"/>
           <circle cx="9.6" cy="10.6" r="0.9" fill="currentColor" stroke="none"/>`,
  },
  // Track rather than a train, so it never reads as a second subway mark.
  rail: {
    cat: "transit",
    body: `<line x1="5.8" y1="2" x2="5.8" y2="14"/>
           <line x1="10.2" y1="2" x2="10.2" y2="14"/>
           <line x1="3.8" y1="4.6" x2="12.2" y2="4.6"/>
           <line x1="3.8" y1="8" x2="12.2" y2="8"/>
           <line x1="3.8" y1="11.4" x2="12.2" y2="11.4"/>`,
  },
  ferry: {
    cat: "transit",
    body: `<path d="M2.6 10.2 C4 13.4, 12 13.4, 13.4 10.2 Z"/>
           <line x1="8" y1="10.2" x2="8" y2="3"/>
           <path d="M8 3.4 L12 5.4 L8 7"/>`,
  },
  bus: {
    cat: "transit",
    body: `<rect x="3" y="2.6" width="10" height="9" rx="2"/>
           <line x1="4.2" y1="7" x2="11.8" y2="7"/>
           <circle cx="5.4" cy="13.2" r="1.1"/>
           <circle cx="10.6" cy="13.2" r="1.1"/>`,
  },
  airport: {
    cat: "transit",
    filled: true,
    body: `<path d="M8 1.6 C8.75 1.6 9.1 2.5 9.1 3.6 L9.1 6.5 L14.3 9.7 L14.3 11.2
                    L9.1 9.7 L9.1 12.3 L10.8 13.6 L10.8 14.4 L8 13.6 L5.2 14.4
                    L5.2 13.6 L6.9 12.3 L6.9 9.7 L1.7 11.2 L1.7 9.7 L6.9 6.5
                    L6.9 3.6 C6.9 2.5 7.25 1.6 8 1.6 Z"/>`,
  },

  // ── Civic ─────────────────────────────────────────────────────────────────
  school: {
    cat: "civic",
    body: `<path d="M1.6 6 L8 3 L14.4 6 L8 9 Z"/>
           <path d="M4.6 7.4 L4.6 11 C4.6 12.6, 11.4 12.6, 11.4 11 L11.4 7.4"/>`,
  },
  library: {
    cat: "civic",
    body: `<path d="M8 4.6 C6.5 3.3, 4 3.3, 2 4.1 L2 11.9 C4 11.1, 6.5 11.1, 8 12.4
                    C9.5 11.1, 12 11.1, 14 11.9 L14 4.1 C12 3.3, 9.5 3.3, 8 4.6 Z"/>
           <line x1="8" y1="4.6" x2="8" y2="12.4"/>`,
  },
  // Just the cross. Anything more at this size loses the one thing that makes
  // it instantly legible.
  hospital: {
    cat: "civic",
    body: `<line x1="8" y1="3.4" x2="8" y2="12.6"/>
           <line x1="3.4" y1="8" x2="12.6" y2="8"/>`,
  },
  government: {
    cat: "civic",
    body: `<path d="M2 6 L8 2.6 L14 6"/>
           <line x1="3.4" y1="6.6" x2="3.4" y2="12"/>
           <line x1="6.5" y1="6.6" x2="6.5" y2="12"/>
           <line x1="9.5" y1="6.6" x2="9.5" y2="12"/>
           <line x1="12.6" y1="6.6" x2="12.6" y2="12"/>
           <line x1="1.8" y1="13.2" x2="14.2" y2="13.2"/>`,
  },
  // Deliberately non-denominational: a pitched hall with an arched door and a
  // finial, which reads as a place of worship without picking one.
  worship: {
    cat: "civic",
    body: `<path d="M4 13.4 L4 7 L8 3.6 L12 7 L12 13.4"/>
           <line x1="8" y1="3.6" x2="8" y2="1.8"/>
           <path d="M6.7 13.4 L6.7 10.4 C6.7 9.2, 9.3 9.2, 9.3 10.4 L9.3 13.4"/>`,
  },
  emergency: {
    cat: "civic",
    body: `<path d="M8 1.8 L13.2 3.9 L13.2 8.1 C13.2 11.2, 10.8 13.5, 8 14.2
                    C5.2 13.5, 2.8 11.2, 2.8 8.1 L2.8 3.9 Z"/>`,
  },
  post: {
    cat: "civic",
    body: `<rect x="2" y="4" width="12" height="8" rx="1.3"/>
           <path d="M2.5 4.7 L8 8.8 L13.5 4.7"/>`,
  },

  // ── Culture ───────────────────────────────────────────────────────────────
  museum: {
    cat: "culture",
    body: `<rect x="2" y="3" width="12" height="10" rx="1.3"/>
           <path d="M3.6 11 L6.6 7.6 L8.9 10 L10.6 8.3 L12.4 11"/>`,
  },
  // A ticket stub. Drawn as stage curtains — a rail with two sweeps and a
  // centre split — it came out looking like a Greek letter.
  theatre: {
    cat: "culture",
    body: `<path d="M1.8 5.2 L14.2 5.2 L14.2 7 C13.1 7, 13.1 9, 14.2 9 L14.2 10.8
                    L1.8 10.8 L1.8 9 C2.9 9, 2.9 7, 1.8 7 Z"/>
           <line x1="8" y1="6.6" x2="8" y2="9.4"/>`,
  },
  cinema: {
    cat: "culture",
    body: `<rect x="2" y="3.4" width="12" height="9.2" rx="1.5"/>
           <path d="M6.7 6.2 L10.5 8 L6.7 9.8 Z" fill="currentColor" stroke="none"/>`,
  },
  // A pitch from above, not a bowl in plan: two concentric ellipses read
  // unmistakably as an eye.
  stadium: {
    cat: "culture",
    body: `<rect x="1.6" y="3.8" width="12.8" height="8.4" rx="1.2"/>
           <line x1="8" y1="3.8" x2="8" y2="12.2"/>
           <path d="M1.6 6.4 L3.7 6.4 L3.7 9.6 L1.6 9.6"/>
           <path d="M14.4 6.4 L12.3 6.4 L12.3 9.6 L14.4 9.6"/>`,
  },
  attraction: {
    cat: "culture",
    body: `<path d="M8 1.9 L9.85 6 L14.3 6.5 L11 9.55 L11.9 14 L8 11.8
                    L4.1 14 L5 9.55 L1.7 6.5 L6.15 6 Z"/>`,
  },
  monument: {
    cat: "culture",
    body: `<path d="M8 1.8 L10.1 5.6 L10.1 11.6 L5.9 11.6 L5.9 5.6 Z"/>
           <line x1="4" y1="13.3" x2="12" y2="13.3"/>`,
  },

  // ── Retail, food & lodging ────────────────────────────────────────────────
  restaurant: {
    cat: "retail",
    body: `<path d="M3.9 2.4 L3.9 5.6 C3.9 6.8, 7.1 6.8, 7.1 5.6 L7.1 2.4"/>
           <line x1="5.5" y1="6.4" x2="5.5" y2="13.6"/>
           <path d="M11.2 2.4 C12.7 4, 12.7 7.2, 11.2 8.4 L11.2 13.6"/>`,
  },
  cafe: {
    cat: "retail",
    body: `<path d="M3 6.2 L3 10.4 C3 12.2, 4.4 13.4, 6.2 13.4 L8.8 13.4
                    C10.6 13.4, 12 12.2, 12 10.4 L12 6.2 Z"/>
           <path d="M12 7.6 C13.9 7.6, 13.9 10.4, 12 10.4"/>
           <path d="M6.1 4.2 C6.1 3.2, 7.1 3.2, 7.1 2.2"/>
           <path d="M8.9 4.2 C8.9 3.2, 9.9 3.2, 9.9 2.2"/>`,
  },
  bar: {
    cat: "retail",
    body: `<path d="M3 3.4 L13 3.4 L8 9.2 Z"/>
           <line x1="8" y1="9.2" x2="8" y2="13.2"/>
           <line x1="5.5" y1="13.2" x2="10.5" y2="13.2"/>`,
  },
  shop: {
    cat: "retail",
    body: `<path d="M3.3 5.6 L12.7 5.6 L11.9 13.5 L4.1 13.5 Z"/>
           <path d="M6 5.6 L6 4 C6 2.6, 10 2.6, 10 4 L10 5.6"/>`,
  },
  // A stall awning, so it never reads as a second shopping bag.
  market: {
    cat: "retail",
    body: `<path d="M1.8 6.4 L3.3 3 L12.7 3 L14.2 6.4 Z"/>
           <path d="M3.1 6.4 L3.1 13.3 L12.9 13.3 L12.9 6.4"/>`,
  },
  hotel: {
    cat: "retail",
    body: `<line x1="2" y1="5.4" x2="2" y2="13"/>
           <path d="M2 9 C2 7.5, 3.4 6.7, 5 6.7 L12 6.7 C13.4 6.7, 14 7.7, 14 9 L14 13"/>
           <line x1="2" y1="10.6" x2="14" y2="10.6"/>`,
  },
  // An elongated capsule, not a squarish one: drawn compact it came out as a
  // circle with a diagonal through it, which is the international "no entry".
  pharmacy: {
    cat: "retail",
    body: `<g transform="rotate(-45 8 8)">
             <rect x="1.6" y="5.6" width="12.8" height="4.8" rx="2.4"/>
             <line x1="8" y1="5.6" x2="8" y2="10.4"/>
           </g>`,
  },
  bank: {
    cat: "retail",
    body: `<ellipse cx="8" cy="4.6" rx="5" ry="2"/>
           <path d="M3 4.6 L3 11.4 C3 12.5, 5.2 13.4, 8 13.4 C10.8 13.4, 13 12.5, 13 11.4 L13 4.6"/>
           <path d="M3 8 C3 9.1, 5.2 10, 8 10 C10.8 10, 13 9.1, 13 8"/>`,
  },
  sports: {
    cat: "retail",
    body: `<circle cx="8" cy="8" r="5.6"/>
           <path d="M2.9 5.7 C6 7.2, 10 7.2, 13.1 5.7"/>
           <path d="M3.4 11.2 C6 9.7, 10 9.7, 12.6 11.2"/>`,
  },
};

/** Drawn when a tile carries a `kind` this build doesn't know — an older client
 *  against a newer tileset. A quiet dot beats MapLibre's missing-image warning
 *  storm and a label with nothing anchoring it. */
const FALLBACK_ICON = "poi-dot";

function toSvg(def: IconDef, color: string): string {
  const paint = def.filled
    ? `fill="${color}" stroke="none"`
    : `fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"`;
  // currentColor in a body (used for the few solid accents inside outlined
  // glyphs) resolves against the wrapper's color property.
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" color="${color}" ${paint}>${def.body}</svg>`;
}

async function rasterize(svg: string, sizePx: number, ratio: number): Promise<ImageData> {
  const px = Math.round(sizePx * ratio);
  const url = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  const img = new Image(px, px);
  img.src = url;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = px;
  canvas.height = px;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("2d context unavailable");
  ctx.drawImage(img, 0, 0, px, px);
  return ctx.getImageData(0, 0, px, px);
}

/**
 * Rasterise every glyph in the active basemap's palette and register it with
 * the map. Resolves once all images are available, so the caller can add the
 * symbol layer without MapLibre ever asking for an image that isn't there yet.
 */
export async function registerPlaceIcons(
  map: maplibregl.Map,
  basemap: Basemap,
): Promise<void> {
  const palette = POI_COLORS[basemap];
  // Rasterise at 2× regardless of the display's DPR: the images are cached on
  // the map for the session, and a crisp icon on a 1× screen costs 4 tiny
  // canvases' worth of memory.
  const ratio = 2;

  const entries = Object.entries(ICONS);
  await Promise.all(
    entries.map(async ([name, def]) => {
      if (map.hasImage(name)) return;
      try {
        const data = await rasterize(toSvg(def, palette[def.cat]), ICON_PX, ratio);
        map.addImage(name, data, { pixelRatio: ratio });
      } catch (err) {
        dwarn("maplibre", `place icon "${name}" failed to rasterize`, err);
      }
    }),
  );

  if (!map.hasImage(FALLBACK_ICON)) {
    try {
      const dot = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
        <circle cx="8" cy="8" r="2.2" fill="${palette.retail}"/></svg>`;
      map.addImage(FALLBACK_ICON, await rasterize(dot, ICON_PX, ratio), { pixelRatio: ratio });
    } catch (err) {
      dwarn("maplibre", "place icon fallback failed to rasterize", err);
    }
  }

  dlog("maplibre", `place icons registered: ${entries.length} + fallback`);
}

/** `icon-image` expression: the feature's kind, or the dot when unknown. */
export const placeIconExpression: maplibregl.ExpressionSpecification = [
  "coalesce",
  ["image", ["get", "kind"]],
  ["image", FALLBACK_ICON],
];

/** Exported for the drift test between this set and the builder's KINDS table. */
export const PLACE_ICON_NAMES = Object.keys(ICONS);
