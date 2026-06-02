import L from "leaflet";
import { iconForLabel, iconSrc } from "../../themes";
import { hashLabelToColor, suggestionGlyphSvg } from "../../utils/suggestionIcon";

/**
 * Builds a Leaflet divIcon styled to match the app's terminal aesthetic.
 * Renders the matching themed icon image, or a colorized "suggestion" sparkle
 * for custom vote types that have no themed icon.
 */
export function makeVoteTypeIcon(label: string): L.DivIcon {
  const icon = iconForLabel(label);
  const html = icon
    ? `<div class="vote-type-indicator"><img class="vote-type-indicator-icon" src="${iconSrc(icon)}" alt="" /></div>`
    : `<div class="vote-type-indicator">${suggestionGlyphSvg(hashLabelToColor(label))}</div>`;

  return L.divIcon({
    html,
    className: "vote-type-indicator-wrapper",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}
