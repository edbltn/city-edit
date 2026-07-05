import L from "leaflet";
import { iconForLabel, iconSrc } from "../../themes";
import { hashLabelToColor, suggestionGlyphSvg } from "../../utils/suggestionIcon";

/** Visual modifiers for an indicator. `selected` adds the pinned double border;
 *  `tint` recolors it teal (start) / red (end) when the indicator coincides with
 *  a start/end waypoint; `square` drops the locating divot for fanned-out icons. */
export interface VoteTypeIconOptions {
  selected?: boolean;
  tint?: "start" | "end" | null;
  /** Render the plain square without the downward divot. Used for fanned-out
   *  (spread) icons: the divot points at a precise location, but a spread icon
   *  sits in an arbitrary grid cell — not on its real spot — so it shouldn't. */
  square?: boolean;
  /** Render a DIAMOND container (same single icon area, rotated 45°) instead of
   *  the square — the visual marker for a ROUTE proposal vs a point proposal. Its
   *  bottom vertex is the anchor tip, so it points down at the location like the
   *  pin's tail. Takes precedence over `square`. */
  diamond?: boolean;
  /** Make the icon click-through (pointer-events:none). Used when the proposal is
   *  a linked waypoint: it's a fixed visual, and the kite RouteMarker underneath
   *  receives the drag/click. */
  passthrough?: boolean;
  /** Render a remove [×] badge pinned to the icon's top-right corner — shown when
   *  this proposal IS a route waypoint (start/end/mid). It lives INSIDE the icon,
   *  so it scales with `--indicator-scale`, follows the icon when the cluster
   *  fans out, and stays centered on the corner with zero parallel positioning.
   *  The edge id is stamped (`data-x-edge`) so one delegated click handler can
   *  resolve which waypoint to remove. Stays clickable even when `passthrough`
   *  (the badge is pointer-events:auto; the rest of the icon is not). */
  removeEdge?: number | null;
  /** "Heat" of this proposal, 0–1, normalized against the hottest visible
   *  proposal's vote count. Rendered as INK, not glow (CSS off the `--heat` /
   *  `--heat-color` custom properties below): the pin's paper fill warms toward
   *  the ramp color and its outline burns hotter and slightly thicker — crisp
   *  at every heat. 0 (or omitted) renders the plain pin. */
  heat?: number;
  /** The tint color for `heat`, sampled from the active map's heat ramp at the
   *  same intensity the heat field uses — so a pin matches the hue the heat
   *  would paint under it. Ignored when `heat` is falsy. */
  heatColor?: string;
}

// The pin is one atomic SVG shape (square + downward tail), drawn in a 34×42
// viewBox so a 2px ring has room. Geometry (user units = px at scale 1):
//   square  : (3,3)→(31,31)               — 28px box
//   tail tip: (17,36)                      — the Leaflet anchor, on the location
//                                            (a wide, shallow tail reads blunt)
//   inner   : (6,6) 22×22 square           — the inner border, set out a touch
//                                            so it doesn't crowd the icon
// Idle = a single hairline outline. selected/start/end add the inner square and
// thicken the outline (CSS in globals.css drives colors + widths per state).
const SVG_W = 34;
const SVG_H = 42;
const OUTER_PATH = "M3,3 H31 V31 H23 L17,36 L11,31 H3 Z";
// Inner border: a true 3px inward offset (contour) of the outer pin — every edge
// moved in by the same perpendicular distance, so the apex angle is preserved
// and the gap to the outer reads as a constant width. The tail tip therefore
// rises by gap/sin(halfAngle) (≈3.9) and the base narrows to match, rather than
// being a naively-scaled (and visibly sharper) V. Shown only in pin states.
const INNER_PATH = "M6,6 H28 V28 H21.9 L17,32.1 L12.1,28 H6 Z";
// Square variants (no tail) for fanned-out icons: the anchor/viewBox stay
// identical so dropping the divot doesn't shift the icon; it just loses its point.
const SQUARE_PATH = "M3,3 H31 V31 H3 Z";
const INNER_SQUARE_PATH = "M6,6 H28 V28 H6 Z";
// Diamond variant (route proposals): the square rotated 45° within the same box,
// its bottom vertex pulled down to the tail tip so the kite points at the spot.
// Vertices: top (17,3) · right (31,17) · bottom-tip (17,36) · left (3,17).
const DIAMOND_PATH = "M17,3 L31,17 L17,36 L3,17 Z";
const INNER_DIAMOND_PATH = "M17,9 L26,17 L17,30 L8,17 Z";
const TIP: [number, number] = [17, 36];

/**
 * Builds a Leaflet divIcon styled to match the app's terminal aesthetic.
 * Renders the matching themed icon image, or a colorized "suggestion" sparkle
 * for custom vote types that have no themed icon. Pass the active map's
 * `voteTypes` so a custom vote-type set resolves to its own icons. When
 * `selected`, the indicator gets a double border so the pinned top proposal
 * reads as the active one; `tint` recolors it for a coincident start/end point.
 */
export function makeVoteTypeIcon(
  label: string,
  voteTypes?: readonly { label: string; icon: string }[],
  opts: VoteTypeIconOptions = {}
): L.DivIcon {
  const icon = iconForLabel(label, voteTypes);
  const cls = ["vote-type-indicator"];
  if (opts.selected) cls.push("is-selected");
  if (opts.tint === "start") cls.push("is-start");
  else if (opts.tint === "end") cls.push("is-end");
  if (opts.diamond) cls.push("is-diamond");

  const glyph = icon
    ? `<img class="vote-type-indicator-icon" src="${iconSrc(icon)}" alt="" />`
    : suggestionGlyphSvg(hashLabelToColor(label));

  // Diamond (route) takes precedence over the square (fanned-out) variant.
  const shape = opts.diamond ? DIAMOND_PATH : opts.square ? SQUARE_PATH : OUTER_PATH;
  const innerShape = opts.diamond
    ? INNER_DIAMOND_PATH
    : opts.square ? INNER_SQUARE_PATH : INNER_PATH;
  // Remove badge: a sibling of the pin/icon inside the scaled .vote-type-indicator,
  // so CSS pins it to the square's top-right corner (SVG 31,3) and it inherits the
  // icon's scale + fanned-out position. data-x-edge lets the delegated handler in
  // GraphLayer resolve which waypoint to drop.
  const removeBadge = opts.removeEdge != null
    ? `<span class="vote-type-indicator-x" data-x-edge="${opts.removeEdge}" role="button" aria-label="Remove from route">×</span>`
    : "";
  // Heat: hand the intensity + color to CSS as custom properties on the
  // scaled .vote-type-indicator. CSS color-mixes the border and a drop-shadow
  // glow by `--heat`, so a hotter proposal both glows brighter and burns its
  // outline toward the ramp color. Omitted entirely at heat 0 → the plain pin.
  const heat = opts.heat && opts.heatColor ? opts.heat : 0;
  const heatStyle = heat > 0
    ? ` style="--heat:${heat.toFixed(3)};--heat-color:${opts.heatColor}"`
    : "";
  const html =
    `<div class="${cls.join(" ")}"${heatStyle}>` +
      `<svg class="vote-type-indicator-pin" viewBox="0 0 ${SVG_W} ${SVG_H}" width="${SVG_W}" height="${SVG_H}">` +
        `<path class="vote-type-indicator-fill" d="${shape}" />` +
        `<path class="vote-type-indicator-outer" d="${shape}" />` +
        `<path class="vote-type-indicator-inner" d="${innerShape}" />` +
      `</svg>` +
      `<div class="vote-type-indicator-iconwrap">${glyph}</div>` +
      removeBadge +
    `</div>`;

  return L.divIcon({
    html,
    className: "vote-type-indicator-wrapper" + (opts.passthrough ? " passthrough" : ""),
    iconSize: [SVG_W, SVG_H],
    iconAnchor: TIP,
  });
}
