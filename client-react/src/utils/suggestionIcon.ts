// ==========================================================================
// Suggestion icon
//
// Custom vote types (user suggestions) have no themed icon. Instead of a flat
// color block, render a generic "sparkle" glyph stroked in a deterministic
// per-label color. The same color logic is shared by map markers, the proposal
// modal, and the vote-type selector so a given label always looks the same.
// ==========================================================================

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

/**
 * A generic "suggestion" sparkle (four-point star), stroked in `color`. Returns
 * raw SVG markup so it can be inlined both in HTML strings (Leaflet divIcons)
 * and in React via dangerouslySetInnerHTML. Matches the thin-stroke aesthetic
 * of the themed icons in /icons/.
 */
export function suggestionGlyphSvg(color: string, size = 20): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" ` +
    `viewBox="0 0 32 32" fill="none" stroke="${color}" stroke-width="1.6" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `<path d="M16 4 L19.5 12.5 L28 16 L19.5 19.5 L16 28 L12.5 19.5 L4 16 L12.5 12.5 Z"/>` +
    `</svg>`
  );
}

/** Convenience: the sparkle glyph colored from a vote-type label. */
export function suggestionGlyphForLabel(label: string, size = 20): string {
  return suggestionGlyphSvg(hashLabelToColor(label), size);
}
