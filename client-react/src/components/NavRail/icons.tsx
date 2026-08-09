/**
 * Nav glyph set — the icons for the secondary/meta nav rail.
 *
 * Drawn to one skeleton so the five marks read as a family: a 24×24 grid,
 * 1.75px strokes, `square` caps and `miter` joins (no rounded corners), and
 * every glyph built from a rectangle on that grid. That's the same drafting
 * language as CheckIcon and the crossword-grid logo — an off-the-shelf icon
 * pack (rounded caps, 2px strokes) reads as a foreign body next to them.
 *
 * The heart is the deliberate exception: it's the only curve-led mark in the
 * set, which is what makes Donate the one glyph that catches the eye.
 *
 * All inherit color via currentColor and are aria-hidden — the surrounding
 * button carries the accessible name.
 */

interface IconProps {
  className?: string;
}

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "square",
  strokeLinejoin: "miter",
  "aria-hidden": true,
  focusable: false,
} as const;

/** How it Works — a "?" set in the logo's lettered box. */
export function IconQuestion({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="3.5" y="3.5" width="17" height="17" />
      <path d="M9.4 9.6 A2.6 2.6 0 1 1 12 12.2 L12 13.7" />
      <rect x="11.1" y="15.9" width="1.8" height="1.8" fill="currentColor" stroke="none" />
    </svg>
  );
}

/** About — an "i" in the matching box, so the two info marks read as a pair. */
export function IconInfo({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="3.5" y="3.5" width="17" height="17" />
      <rect x="11.1" y="6.5" width="1.8" height="1.8" fill="currentColor" stroke="none" />
      <path d="M12 10.6 L12 17.4" />
    </svg>
  );
}

/**
 * Blog — a laid-out article: image block, two column rules, one full measure.
 * The image block is filled rather than stroked: at the 15–18px this renders
 * at, a 6×5 stroked box is all outline and silts up into a grey smudge.
 */
export function IconArticle({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="2.5" y="4.5" width="19" height="15" />
      <rect x="5.5" y="7.6" width="6.4" height="5.2" fill="currentColor" stroke="none" />
      <path d="M15.1 8.5 L18.6 8.5" />
      <path d="M15.1 11.9 L18.6 11.9" />
      <path d="M5.5 16.3 L18.6 16.3" />
    </svg>
  );
}

/** Feedback — a square speech bubble; the tail is mitered, not rounded. */
export function IconBubble({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M3 4.5 L21 4.5 L21 15.5 L11 15.5 L7 19.5 L7 15.5 L3 15.5 Z" />
    </svg>
  );
}

/**
 * Shop — a tote. The handle is a squared arch rather than the usual half-round
 * one, which is what keeps it in this set's drafting language instead of
 * looking like it came from a checkout page.
 */
export function IconTote({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="3.5" y="8.5" width="17" height="11.5" />
      <path d="M8.4 8.5 L8.4 4.5 L15.6 4.5 L15.6 8.5" />
    </svg>
  );
}

/** Donate — the one curve in the set, so it reads first. */
export function IconHeart({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M12 20.4 L4.4 12.8 A5 5 0 0 1 12 7.5 A5 5 0 0 1 19.6 12.8 Z" />
    </svg>
  );
}
