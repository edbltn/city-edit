/**
 * The audience glyph: one figure for a single other viewer, two for several.
 *
 * Inline SVG rather than an <img src="/icons/…">, because an <img> cannot
 * inherit `currentColor` — and this glyph has to sit in the strip's muted ink
 * and flip with the light/dark basemap like every other mark on it. The two
 * .svg files that draw the same shape for the vote-type icons
 * (public/icons/community.svg, public/icons/tactical.svg) are hand-mirrored
 * from these paths; the repo already keeps mirrored pairs this way
 * (foot_profile.py ↔ osrm/foot.lua). Change one, change the others.
 *
 * Outline only, no fill: filled figures at 13px read as blobs, and the strip's
 * whole visual argument is that it is drawn in hairlines like the wordmark.
 */

interface Props {
  /** How many OTHER viewers the sentence is about — picks the figure count. */
  others: number;
  size?: number;
}

export function AudienceIcon({ others, size = 13 }: Props) {
  const common = {
    viewBox: "0 0 24 24",
    width: size,
    height: size,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    style: { display: "block" as const, flex: "0 0 auto" as const },
  };

  // One other person: a single figure, centred.
  if (others === 1) {
    return (
      <svg {...common}>
        <circle cx="12" cy="8" r="3.2" />
        <path d="M5.5 20 C5.5 14.6, 8.4 12.6, 12 12.6 C15.6 12.6, 18.5 14.6, 18.5 20" />
      </svg>
    );
  }

  // Several: a front figure and one behind it. Two, not three — three figures
  // inside 13px lose their gaps and turn into a smudge, and the count is
  // already carried exactly by the marks beside it.
  return (
    <svg {...common}>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3 20 C3 15.2, 5.6 13.2, 9 13.2 C12.4 13.2, 15 15.2, 15 20" />
      <circle cx="17.2" cy="9.6" r="2.4" />
      <path d="M21 20 C21 16.4, 19.4 14.9, 17.2 14.9 C16.5 14.9, 15.9 15.05, 15.4 15.3" />
    </svg>
  );
}
