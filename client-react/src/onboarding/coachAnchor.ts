// ==========================================================================
// Where the coach points — measured, never guessed
// ==========================================================================
// The first-run coach used to be one strip at the bottom of the map saying
// "Tap the map where that stretch starts." A strip at the bottom of the screen
// cannot point, so the sentence had to describe the control instead of
// indicating it, and the control it described is at the TOP of the screen in a
// bar the reader had not looked at yet. Now the coach is a callout hung off the
// real element, with an arrow tip on it.
//
// That turns the whole problem into one question — WHERE IS THAT ELEMENT RIGHT
// NOW — and this module is the answer. Nothing here is a constant, because
// every constant would be wrong at some width:
//
//   · The chrome has TWO MODES. `data-chrome="float"` (the default) lays the
//     bar out as a 6-column grid of floating plates; `banner` is a stacked bar
//     in normal flow. Start and End are in completely different places in the
//     two, and the mode is chosen per device, not per breakpoint.
//   · Inside float there are three grids — >1080px, 580–1080px, and <580px —
//     and the Start/End plate moves grid area in each.
//   · THE ANCHOR CAN BE ABSENT. `.topbar-legend` is `display: none` in short
//     landscape, and the whole legend is never rendered at all on a point-only
//     map (a station map, or a theme whose inputMode is "point"). The cast
//     plate is `display: none` in float mode until a selection exists, and
//     `visibility: hidden` (`hidden-reserve`) when a cast is not yet possible.
//     A callout with an arrow tip pointing at nothing is worse than the strip
//     it replaced, so "absent" is a first-class answer here and the caller
//     falls back to the bottom strip when it comes back.
//
// So: ask the DOM, watch it, and re-ask on anything that could have moved it.
// The observers are the same set `useLogoAnchor` settled on for the same reason
// — resize, a ResizeObserver on the element, a MutationObserver over the whole
// bar (class and attribute changes included, because that is how the fields
// switch into their typing state), and `document.fonts.ready`, since the bar's
// own height changes when the mono face lands.
//
// One extra fact this reports that the logo hook did not need: whether an
// ADDRESS SEARCH IS OPEN in the legend. The flow has to stand down while
// somebody is typing an address — see the note on `typing` below.
// ==========================================================================

import { useEffect, useState } from "react";

/** Which piece of chrome the coach is pointing at. Matches `data-coach` in
 *  TopBar.tsx — the only coupling between this flow and the bar. */
export type CoachTarget = "start" | "end" | "cast";

export interface AnchorBox {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface CoachAnchor {
  /** The element's live viewport rect, or null when it is not on screen. */
  box: AnchorBox | null;
  /**
   * An address search is open in the legend.
   *
   * This is the "atomic" case the coach must not interrupt: somebody clicks the
   * Start field, types a street, waits for the geocoder, arrows down the list
   * and presses Enter. That is one intention with four steps in it, and a
   * callout with an arrow tip parked over the suggestion list — which opens
   * from the same plate — is in the way of the very control it is pointing at.
   * So the coach goes quiet for the whole of it and comes back on the other
   * side, by which time the point has landed and the step has already moved on.
   *
   * Read from the DOM rather than from TopBar's own `typingField` state on
   * purpose: it is the same observer that is already watching the bar, it needs
   * no prop drilled through a component another workstream is editing, and the
   * class it keys on (`.legend-tool.typing`) is the one the float chrome's own
   * stacking rules key on too, so it cannot quietly stop meaning this.
   */
  typing: boolean;
}

const EMPTY: CoachAnchor = { box: null, typing: false };

/** On screen, taking up space, and actually painted. Three different ways to
 *  be missing and all three are in play here: `display:none` (the legend in
 *  short landscape, the cast plate before a selection), a zero rect, and
 *  `visibility:hidden` (`hidden-reserve`, which keeps its layout box). */
function liveRect(el: HTMLElement): AnchorBox | null {
  if (el.offsetParent === null) return null;
  if (getComputedStyle(el).visibility === "hidden") return null;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return null;
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

function same(a: AnchorBox | null, b: AnchorBox | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  // Sub-pixel jitter from a backdrop-filter repaint is not a move. Rounding to
  // whole pixels keeps a resize from re-rendering the callout every frame.
  return (
    Math.round(a.top) === Math.round(b.top) &&
    Math.round(a.left) === Math.round(b.left) &&
    Math.round(a.width) === Math.round(b.width) &&
    Math.round(a.height) === Math.round(b.height)
  );
}

export function useCoachAnchor(target: CoachTarget | null): CoachAnchor {
  const [anchor, setAnchor] = useState<CoachAnchor>(EMPTY);

  useEffect(() => {
    if (!target) {
      setAnchor(EMPTY);
      return;
    }

    let current: HTMLElement | null = null;
    let ro: ResizeObserver | null = null;
    let pending = 0;

    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-coach="${target}"]`);
      // Both renderings of a field carry the attribute — the resting button and
      // the typing div are different elements in the same slot — so the element
      // itself is replaced, not just moved, every time somebody opens a search.
      if (el !== current) {
        current = el;
        ro?.disconnect();
        ro = null;
        if (el) {
          ro = new ResizeObserver(schedule);
          ro.observe(el);
        }
      }
      const box = el ? liveRect(el) : null;
      const typing = !!document.querySelector(".legend-tool.typing");
      setAnchor((prev) =>
        same(prev.box, box) && prev.typing === typing ? prev : { box, typing }
      );
    };

    // Coalesce: a single width change fires resize, the ResizeObserver and a
    // pile of mutations, and each one would otherwise measure the same layout.
    //
    // A TIMEOUT, NOT requestAnimationFrame, and this is not a style choice. rAF
    // does not run in a hidden or fully occluded window — Chrome stops the frame
    // clock — so a coach coalesced on it freezes at whatever it last measured.
    // The case that actually broke: the visitor clicks the Start field, the
    // field swaps to its typing state, the MutationObserver fires, and the
    // callback never runs, so the coach stays sitting over the suggestion list
    // it is supposed to stand aside for. There is nothing to align to a frame
    // here anyway — this reads layout and sets state, and the render that
    // follows is React's to schedule.
    const schedule = () => {
      if (pending) return;
      pending = window.setTimeout(() => {
        pending = 0;
        measure();
      }, 0);
    };

    measure();
    window.addEventListener("resize", schedule);
    // The float chrome scrolls nothing, but the banner bar is in normal flow
    // and a short viewport can put it under the fold.
    window.addEventListener("scroll", schedule, true);
    const mo = new MutationObserver(schedule);
    const topbar = document.querySelector(".topbar");
    if (topbar) {
      mo.observe(topbar, { childList: true, subtree: true, attributes: true });
    }
    document.fonts?.ready.then(schedule).catch(() => {});

    return () => {
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule, true);
      mo.disconnect();
      ro?.disconnect();
      if (pending) clearTimeout(pending);
    };
  }, [target]);

  return anchor;
}
