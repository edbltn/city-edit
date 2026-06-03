import L from "leaflet";
import { iconForLabel, iconSrc } from "../../themes";
import { hashLabelToColor, suggestionGlyphSvg } from "../../utils/suggestionIcon";

/**
 * Builds a Leaflet divIcon styled to match the app's terminal aesthetic.
 * Renders the matching themed icon image, or a colorized "suggestion" sparkle
 * for custom vote types that have no themed icon. Pass the active map's
 * `voteTypes` so a custom vote-type set resolves to its own icons. When
 * `selected`, the indicator gets a highlighted ring so the pinned top proposal
 * reads as the active one.
 */
export function makeVoteTypeIcon(
  label: string,
  voteTypes?: readonly { label: string; icon: string }[],
  selected = false
): L.DivIcon {
  const icon = iconForLabel(label, voteTypes);
  const cls = selected
    ? "vote-type-indicator is-selected"
    : "vote-type-indicator";
  const html = icon
    ? `<div class="${cls}"><img class="vote-type-indicator-icon" src="${iconSrc(icon)}" alt="" /></div>`
    : `<div class="${cls}">${suggestionGlyphSvg(hashLabelToColor(label))}</div>`;

  return L.divIcon({
    html,
    className: "vote-type-indicator-wrapper",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}
