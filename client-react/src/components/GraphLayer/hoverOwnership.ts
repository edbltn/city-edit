// ==========================================================================
// Indicator hover ownership (docs/three-layer-model.md §2.4)
// ==========================================================================
// A hovered top-proposal icon owns transient map state: it suppresses the graph
// hover beneath it, lights its corridor's WHOLE block-edge union, and opens its
// hover card. All three are set by the marker's `mouseover` and cleared by its
// `mouseout` — and a `mouseout` is not something the app can count on arriving.
//
// Leaflet delivers marker hover through DOM events that must BUBBLE to the map
// container. `L.DivIcon.createIcon` reuses the wrapper element but rewrites its
// `innerHTML`, so a re-render that hands a hovered icon a new icon object
// destroys the child node the browser is tracking as the hover target; the
// eventual `mouseout` is dispatched on that detached node, never reaches the
// map, and the marker's release never runs. The corridor then stays lit in
// every later block-select broadcast — the "frozen highlight" after dragging a
// start/end waypoint, which re-renders those icons constantly (the drop-target
// ring recomputes per drag frame, and coverage/passthrough recompute at the
// drop).
//
// So ownership is reconciled positively instead: the pointer sitting on the
// bare map is proof that no icon owns the hover, whatever the DOM did or did
// not deliver. Pure (no React/DOM) so the rule is unit-testable.

/** CSS selector for "the pointer is on a marker that may own the hover" — any
 *  Leaflet marker icon: a proposal icon, or a waypoint kite (which lights its
 *  matched proposal's card through the same `overIndicator` flag). */
export const HOVER_OWNER_SELECTOR = ".leaflet-marker-icon";

/** The transient state a hovered indicator owns. */
export interface IndicatorHoverState {
  /** An icon claims the map's hover, suppressing the graph hover beneath it. */
  overIndicator: boolean;
  /** A hovered corridor's whole block-edge union is lit (blocks + canvas). */
  routeHighlight: boolean;
  /** A route-proposal hover card is open. */
  hoverCard: boolean;
}

/** True when any indicator-owned state is currently held. */
export function holdsIndicatorHover(held: IndicatorHoverState): boolean {
  return held.overIndicator || held.routeHighlight || held.hoverCard;
}

/**
 * Should the indicator-owned hover be released?
 *
 * `overMarkerIcon` — the pointer is over an element inside some Leaflet marker
 * icon (test the event target with `HOVER_OWNER_SELECTOR`). While it is, the
 * owner may legitimately still be hovered, so nothing is released; the moment
 * the pointer is on the bare map with state still held, that state is stale by
 * definition and gets dropped.
 */
export function shouldReleaseIndicatorHover(
  overMarkerIcon: boolean,
  held: IndicatorHoverState,
): boolean {
  if (overMarkerIcon) return false;
  return holdsIndicatorHover(held);
}
