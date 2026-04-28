import L from "leaflet";

/**
 * Extracts the first emoji grapheme from a label string.
 * Returns null if the leading grapheme isn't an Extended_Pictographic codepoint.
 *
 * Uses Intl.Segmenter for grapheme awareness so ZWJ sequences and presentation
 * selectors stay together (e.g. "🛠️" = U+1F6E0 + U+FE0F).
 */
const segmenter = typeof Intl !== "undefined" && "Segmenter" in Intl
  ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
  : null;

const EMOJI_RE = /\p{Extended_Pictographic}/u;

export function extractFirstEmoji(label: string): string | null {
  if (!label) return null;

  if (segmenter) {
    const first = segmenter.segment(label.trim())[Symbol.iterator]().next().value;
    if (!first) return null;
    return EMOJI_RE.test(first.segment) ? first.segment : null;
  }

  // Fallback: best-effort first character match
  const trimmed = label.trim();
  return EMOJI_RE.test(trimmed[0] ?? "") ? trimmed[0] : null;
}

/**
 * Deterministic hash → HSL → hex. Same string always produces the same color.
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
 * Builds a Leaflet divIcon styled to match the app's terminal aesthetic.
 * Either renders the first emoji from the label, or a hex-colored disc.
 */
export function makeVoteTypeIcon(label: string): L.DivIcon {
  const emoji = extractFirstEmoji(label);
  const html = emoji
    ? `<div class="vote-type-indicator"><span class="vote-type-indicator-glyph">${emoji}</span></div>`
    : `<div class="vote-type-indicator"><span class="vote-type-indicator-disc" style="background:${hashLabelToColor(label)}"></span></div>`;

  return L.divIcon({
    html,
    className: "vote-type-indicator-wrapper",
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}
