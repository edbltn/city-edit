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

  const glyph = icon
    ? `<img class="vote-type-indicator-icon" src="${iconSrc(icon)}" alt="" />`
    : suggestionGlyphSvg(hashLabelToColor(label));

  const shape = opts.square ? SQUARE_PATH : OUTER_PATH;
  const innerShape = opts.square ? INNER_SQUARE_PATH : INNER_PATH;
  // Remove badge: a sibling of the pin/icon inside the scaled .vote-type-indicator,
  // so CSS pins it to the square's top-right corner (SVG 31,3) and it inherits the
  // icon's scale + fanned-out position. data-x-edge lets the delegated handler in
  // GraphLayer resolve which waypoint to drop.
  const removeBadge = opts.removeEdge != null
    ? `<span class="vote-type-indicator-x" data-x-edge="${opts.removeEdge}" role="button" aria-label="Remove from route">×</span>`
    : "";
  const html =
    `<div class="${cls.join(" ")}">` +
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
