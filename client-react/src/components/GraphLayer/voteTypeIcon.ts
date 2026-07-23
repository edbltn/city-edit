import { iconForLabel, iconSrc } from "../../themes";

/**
 * Deterministic hash -> HSL -> hex. Same string always produces the same color.
 * Saturation/lightness chosen to read on the dark base map.
 */
export function hashLabelToColor(label: string): string {
  let h = 0;
  for (let i = 0; i < label.length; i++) {
    h = (h * 31 + label.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  return hslToHex(hue, 65, 60);
}

function hslToHex(h: number, s: number, l: number): string {
  s /= 100;
  l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const v = l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
    return Math.round(v * 255).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

export const VOTE_TYPE_ICON_SIZE: [number, number] = [28, 28];

/**
 * Marker HTML for a vote-type indicator, styled to match the app's terminal
 * aesthetic. Renders the matching icon image, or a hex-colored disc as
 * fallback. Rendered inside a MapMarker element (formerly a Leaflet divIcon).
 */
export function voteTypeIconHtml(label: string): string {
  const icon = iconForLabel(label);
  return icon
    ? `<div class="vote-type-indicator"><img class="vote-type-indicator-icon" src="${iconSrc(icon)}" alt="" /></div>`
    : `<div class="vote-type-indicator"><span class="vote-type-indicator-disc" style="background:${hashLabelToColor(label)}"></span></div>`;
}
