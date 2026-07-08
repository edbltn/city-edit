// Press-vs-tap timing — the single source of truth for every "click vs
// click-and-drag" differentiator in the app, so they all feel identical.
//
// A pointer press that's RELEASED within TAP_MAX_MS is a tap (a click); a press
// held longer is a deliberate drag. This is time-based on purpose: it doesn't
// matter how far the pointer wandered, only whether the gesture was a quick poke
// or a sustained grab. That makes a slightly-imperfect click still read as a
// click, and avoids a tiny hand-tremor turning a click into a drag.
//
// Used by:
//   - RouteMarker  — a draggable kite (start/end/mid). Leaflet also flags any
//                    real drag via `dragstart`, so there it's belt-and-suspenders.
//   - usePathDrag  — dragging the route polyline to insert a mid. The polyline is
//                    not a Leaflet draggable, so timing is the ONLY signal here.

export const TAP_MAX_MS = 300;

// A press that never strayed beyond this many pixels is ALSO a tap, no matter
// how long it was held. Time alone turned a slow, deliberate click into a
// zero-distance drag-and-drop — on the route path that "dropped a mid where
// pressed", and with proposal pins being sticky drop targets it kept threading
// corridors the user only meant to select. Drift is measured as the MAX
// displacement during the gesture (never the net): a real drag that returns to
// its origin — e.g. pulling a mid out and dropping it back on its proposal to
// link it — must still count as a drag.
export const TAP_MAX_DRIFT_PX = 8;

/**
 * True if a press that began at `pressStartMs` (a `Date.now()` timestamp) and is
 * ending now counts as a tap — i.e. it was released within TAP_MAX_MS. A longer
 * press is a drag — unless it never drifted past TAP_MAX_DRIFT_PX; callers that
 * track movement pass their max drift so an unmoved hold reads as a tap too.
 */
export function isTap(pressStartMs: number, maxDriftPx = Infinity): boolean {
  return Date.now() - pressStartMs < TAP_MAX_MS || maxDriftPx < TAP_MAX_DRIFT_PX;
}
