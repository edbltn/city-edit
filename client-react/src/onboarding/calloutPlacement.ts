// ==========================================================================
// Putting the callout somewhere it can actually point from
// ==========================================================================
// Pure geometry, separated from the component for one reason: the thing that
// goes wrong with an anchored callout is never "does it render", it is "does it
// still point at the right control at 380px" — and that is a question about
// four numbers, which a test can ask far more thoroughly than a person dragging
// a window edge can.
//
// Two rules do all the work, and they pull against each other:
//
//   THE BOX STAYS ON SCREEN. It is clamped into the viewport with a margin. A
//   callout that runs off the right edge is worse than one that is not centred,
//   because half its sentence is gone.
//
//   THE TIP STAYS ON THE CONTROL. So when the clamp moves the box, the arrow
//   does NOT move with it — it slides along the box's edge to stay over the
//   anchor's centre. This is the part a naive implementation gets wrong: it
//   centres the arrow on the BOX, and then at narrow widths the arrow points
//   confidently at whatever happens to be next to the control.
//
// The arrow keeps a minimum inset from the box's corners, because a tip growing
// out of a corner reads as a rendering fault rather than as a pointer. When the
// anchor is so far into a corner that even the inset cannot reach it, the arrow
// stops at the inset and the box is as close as it can get — the honest failure,
// and the only one available once the box is against the viewport edge.
//
// Vertically it is a simple flip: below the anchor by default, above it when
// there is no room below AND there is room above. Below is the default because
// every anchor the coach uses is in the top bar, so below is where the map is.
// ==========================================================================

import type { AnchorBox } from "./coachAnchor";

export interface Viewport {
  width: number;
  height: number;
}

export interface CalloutSize {
  width: number;
  height: number;
}

export interface CalloutPlacement {
  /** Viewport coordinates for `position: fixed`. */
  top: number;
  left: number;
  /** Which side of the box the tip grows out of. */
  side: "above" | "below";
  /** The tip's centre, in pixels from the box's own left edge. */
  arrowLeft: number;
  /** What the box is allowed to be, before it is measured. */
  maxWidth: number;
}

/** Clear of the viewport edge. */
const MARGIN = 8;
/** Between the anchor's edge and the box — the tip lives in here. */
const GAP = 10;
/** How close the tip's centre may get to a corner of the box. */
const ARROW_INSET = 16;
/** Wide enough for two lines of the ask at desktop type; narrower is fine. */
const PREFERRED_WIDTH = 300;

function clamp(value: number, low: number, high: number): number {
  // Guard the inverted range: at a viewport narrower than the box, `high` lands
  // below `low` and an unguarded clamp returns the larger of the two, throwing
  // the box off the LEFT edge — the one place there is never a scrollbar to
  // reveal it.
  if (high < low) return low;
  return Math.min(Math.max(value, low), high);
}

/** The widest the box may be here. Read before the box exists, so it can be
 *  applied as a CSS max-width and measured afterwards. */
export function calloutMaxWidth(viewportWidth: number): number {
  return Math.min(PREFERRED_WIDTH, Math.max(160, viewportWidth - MARGIN * 2));
}

export function placeCallout(
  anchor: AnchorBox,
  size: CalloutSize,
  viewport: Viewport
): CalloutPlacement {
  const maxWidth = calloutMaxWidth(viewport.width);

  const below = anchor.top + anchor.height + GAP;
  const above = anchor.top - GAP - size.height;
  const fitsBelow = below + size.height <= viewport.height - MARGIN;
  const fitsAbove = above >= MARGIN;
  // Below unless it does not fit and above does. When NEITHER fits — a control
  // taller than the viewport minus the box, which short landscape can produce —
  // stay below and let the clamp deal with it, because below is where the map
  // is and an overlapping callout above the bar covers the control itself.
  const side: "above" | "below" = !fitsBelow && fitsAbove ? "above" : "below";

  const top = clamp(
    side === "below" ? below : above,
    MARGIN,
    Math.max(MARGIN, viewport.height - size.height - MARGIN)
  );

  const anchorCentre = anchor.left + anchor.width / 2;
  const left = clamp(
    anchorCentre - size.width / 2,
    MARGIN,
    viewport.width - size.width - MARGIN
  );

  const arrowLeft = clamp(
    anchorCentre - left,
    ARROW_INSET,
    Math.max(ARROW_INSET, size.width - ARROW_INSET)
  );

  return { top, left, side, arrowLeft, maxWidth };
}
