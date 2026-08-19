// ==========================================================================
// Putting the callout somewhere it can actually point from
// ==========================================================================
// Pure geometry, separated from the component for one reason: the thing that
// goes wrong with an anchored callout is never "does it render", it is "does it
// still point at the right control at 390px" — and that is a question about
// four numbers, which a test can ask far more thoroughly than a person dragging
// a window edge can.
//
// THE SHAPE IS HORIZONTAL: a wide, short box with the pointer on a VERTICAL
// edge, sitting beside what it describes — `[ bla bla bla ]▶`. A box under a
// control is a caption; a box beside it with a tip touching it is a label, and
// the whole point of moving off the bottom strip was to label rather than
// caption. Left of the anchor is preferred, because that is the shape as drawn
// and because every anchor in the bar has map to its left at the widths where
// this fits at all.
//
// Two rules do all the work, and they pull against each other:
//
//   THE BOX STAYS ON SCREEN. It is clamped into the viewport with a margin. A
//   callout that runs off an edge is worse than one that is not centred,
//   because part of its sentence is gone.
//
//   THE TIP STAYS ON THE CONTROL. So when the clamp moves the box, the tip does
//   NOT move with it — it slides along the box's edge to stay over the anchor's
//   centre. This is the part a naive implementation gets wrong: it centres the
//   tip on the BOX, and then the moment anything clamps, the tip points
//   confidently at whatever happens to be next to the control.
//
// AND WHEN HORIZONTAL DOES NOT FIT, IT GOES BACK UNDER. Stated plainly because
// it is a real case, not a theoretical one: at 390px the Start/End plate is
// nearly the full width of the screen, so there is no room for a legible box on
// either side of it. A 40px-wide callout obeying the shape rule is worse than a
// readable one under the control, so below the horizontal floor the box flips
// to a vertical tip. Which orientation is in play is decided from the ANCHOR
// AND THE VIEWPORT ALONE — never from the box's own size — so the caller can
// ask for it before the box exists and apply it as a max-width.
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

/** Which edge of the box the tip grows out of — named for where the box sits
 *  relative to the anchor, so `left` means "box on the left, tip on its right". */
export type CalloutSide = "left" | "right" | "above" | "below";

export interface CalloutFit {
  side: CalloutSide | null;
  /** Null side = vertical, decided later because it needs the box's height. */
  maxWidth: number;
}

/** A box already on screen that this one must not land on top of. */
export interface AvoidRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface CalloutPlacement {
  /** Viewport coordinates for `position: fixed`. */
  top: number;
  left: number;
  side: CalloutSide;
  /**
   * The tip's centre, in pixels from the box's own leading edge — from the LEFT
   * edge for `above`/`below`, from the TOP edge for `left`/`right`. One number
   * and one CSS custom property for all four sides.
   */
  arrow: number;
  maxWidth: number;
}

/** Clear of the viewport edge. */
const MARGIN = 8;
/** Between the anchor's edge and the box — the tip lives in here. */
const GAP = 10;
/** How close the tip's centre may get to a corner of the box. */
const ARROW_INSET = 16;
/** Wide enough for the ask on one or two lines at desktop type. */
const PREFERRED_WIDTH = 340;
/** Below this a side-by-side box stops being readable and we go back under. */
const MIN_HORIZONTAL_WIDTH = 168;
/** The vertical fallback may be narrower — it has the whole width to play with. */
const MIN_VERTICAL_WIDTH = 160;

function clamp(value: number, low: number, high: number): number {
  // Guard the inverted range: at a viewport narrower than the box, `high` lands
  // below `low` and an unguarded clamp returns the larger of the two, throwing
  // the box off the LEFT edge — the one place there is never a scrollbar to
  // reveal it.
  if (high < low) return low;
  return Math.min(Math.max(value, low), high);
}

/**
 * Orientation and width, from the anchor and the viewport only.
 *
 * Deliberately independent of the box's own size, because the caller needs this
 * BEFORE the box has one: the width it is allowed decides how the ask wraps,
 * which decides the height, which decides where a vertical box can go. Asking
 * the other way round is the circular version.
 */
export function calloutFit(
  anchor: AnchorBox,
  viewport: Viewport,
  prefer: "left" | "right" = "left"
): CalloutFit {
  const room = {
    left: anchor.left - GAP - MARGIN,
    right: viewport.width - (anchor.left + anchor.width) - GAP - MARGIN,
  };
  const other = prefer === "left" ? "right" : "left";

  // `prefer` is how the cast step keeps its two marks apart WITHOUT any
  // collision arithmetic: the one on the vote-type picker takes the left, the
  // one on the −/+ pair takes the right, and since the picker is left of the
  // pair in every layout the bar has, the two boxes cannot meet. Choosing the
  // side up front also means the width is chosen for the side that will
  // actually be used — flipping afterwards would leave a box sized for the
  // roomier side trying to fit in the narrower one.
  if (room[prefer] >= MIN_HORIZONTAL_WIDTH) {
    return { side: prefer, maxWidth: Math.min(PREFERRED_WIDTH, room[prefer]) };
  }
  if (room[other] >= MIN_HORIZONTAL_WIDTH) {
    return { side: other, maxWidth: Math.min(PREFERRED_WIDTH, room[other]) };
  }
  return {
    side: null,
    maxWidth: Math.min(
      PREFERRED_WIDTH,
      Math.max(MIN_VERTICAL_WIDTH, viewport.width - MARGIN * 2)
    ),
  };
}

function hits(p: CalloutPlacement, size: CalloutSize, avoid: AvoidRect[]): boolean {
  return avoid.some((a) => !(
    p.left + size.width <= a.left ||
    p.left >= a.left + a.width ||
    p.top + size.height <= a.top ||
    p.top >= a.top + a.height
  ));
}

function placeHorizontal(
  anchor: AnchorBox,
  size: CalloutSize,
  viewport: Viewport,
  side: "left" | "right",
  maxWidth: number
): CalloutPlacement {
  const left = clamp(
    side === "left"
      ? anchor.left - GAP - size.width
      : anchor.left + anchor.width + GAP,
    MARGIN,
    viewport.width - size.width - MARGIN
  );
  const centreY = anchor.top + anchor.height / 2;
  const top = clamp(
    centreY - size.height / 2,
    MARGIN,
    Math.max(MARGIN, viewport.height - size.height - MARGIN)
  );
  const arrow = clamp(
    centreY - top,
    ARROW_INSET,
    Math.max(ARROW_INSET, size.height - ARROW_INSET)
  );
  return { top, left, side, arrow, maxWidth };
}

function placeVertical(
  anchor: AnchorBox,
  size: CalloutSize,
  viewport: Viewport,
  side: "above" | "below",
  maxWidth: number
): CalloutPlacement {
  const top = clamp(
    side === "below" ? anchor.top + anchor.height + GAP : anchor.top - GAP - size.height,
    MARGIN,
    Math.max(MARGIN, viewport.height - size.height - MARGIN)
  );
  const centreX = anchor.left + anchor.width / 2;
  const left = clamp(
    centreX - size.width / 2,
    MARGIN,
    viewport.width - size.width - MARGIN
  );
  const arrow = clamp(
    centreX - left,
    ARROW_INSET,
    Math.max(ARROW_INSET, size.width - ARROW_INSET)
  );
  return { top, left, side, arrow, maxWidth };
}

/**
 * `avoid` is what keeps the cast step's TWO marks off each other — and, just as
 * importantly, off the controls the other mark is pointing at.
 *
 * The vote-type picker and the −/+ pair are neighbours in the bar: adjacent
 * columns on a desktop, stacked rows on a phone. Two boxes both taking their
 * preferred side overlap, and two overlapping callouts read as one broken one.
 * Worse, at 390px the box on the −/+ pair lands squarely on the PICKER, which
 * is the one control this step is actively inviting the reader to use. A
 * callout that covers the thing it just told you to go and press is the same
 * failure as greying it out, only harder to notice.
 *
 * So the sides are tried in order — preferred, its opposite, below, above — and
 * the first one that clears every avoided rect wins. Trying sides rather than
 * NUDGING is deliberate: a nudge moves the box off its own control and the tip
 * has to stretch or lie, whereas every side in that list is still a box
 * pointing straight at it. If nothing clears, the preferred placement stands —
 * an overlap is better than a box in the wrong place entirely, and saying so
 * here is better than a silent fifth fallback.
 */
export function placeCallout(
  anchor: AnchorBox,
  size: CalloutSize,
  viewport: Viewport,
  avoid?: AvoidRect | AvoidRect[],
  prefer: "left" | "right" = "left"
): CalloutPlacement {
  const fit = calloutFit(anchor, viewport, prefer);
  const maxWidth = fit.maxWidth;
  const blockers = !avoid ? [] : Array.isArray(avoid) ? avoid : [avoid];

  const candidates: CalloutPlacement[] = [];
  if (fit.side === "left" || fit.side === "right") {
    const other = fit.side === "left" ? "right" : "left";
    candidates.push(placeHorizontal(anchor, size, viewport, fit.side, maxWidth));
    const roomOther =
      other === "left"
        ? anchor.left - GAP - MARGIN
        : viewport.width - (anchor.left + anchor.width) - GAP - MARGIN;
    if (roomOther >= size.width) {
      candidates.push(placeHorizontal(anchor, size, viewport, other, maxWidth));
    }
  }
  const below = anchor.top + anchor.height + GAP;
  const above = anchor.top - GAP - size.height;
  if (below + size.height <= viewport.height - MARGIN) {
    candidates.push(placeVertical(anchor, size, viewport, "below", maxWidth));
  }
  if (above >= MARGIN) {
    candidates.push(placeVertical(anchor, size, viewport, "above", maxWidth));
  }

  if (candidates.length) {
    const clear = candidates.find((c) => !hits(c, size, blockers));
    if (clear) return clear;
    if (!blockers.length) return candidates[0];
    return candidates[0];
  }

  // Nothing fitted anywhere — a control taller than the viewport minus the box,
  // which short landscape can produce. Below, clamped, and let the reader see
  // something rather than nothing.
  return placeVertical(anchor, size, viewport, "below", maxWidth);
}
