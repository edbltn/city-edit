/**
 * Where a proposal pin sits in the Leaflet marker stack.
 *
 * Two rules, and the split between them is the whole design:
 *
 *   1. STATE decides the band. A pin that is fanned out, or carrying a route
 *      role, or holding the open modal, is doing a JOB, and a pin doing a job
 *      must not be buried by a pin that is merely present. Bands are far apart
 *      so nothing can bleed from one into the next.
 *   2. LATITUDE decides the order inside a band — and it is Leaflet, not this
 *      module, that applies it. `Marker._setPos` sets `zIndex = layerPoint.y +
 *      zIndexOffset`, and layer-point y grows southward, so of two overlapping
 *      pins the SOUTHERN one paints on top by default. That is the near/far
 *      reading every map with a tilted pin uses, and it is free.
 *
 * Which means the offsets below carry NO in-band term. They used to: a pin was
 * lifted by its own vote count (capped at 50k) and a corridor by its score
 * (capped at 48k), so where two pins overlapped the better-supported one won.
 * That is a defensible rule on its own, but it is not the one asked for, and it
 * is not one the eye can read — nothing on the map says which of two
 * overlapping pins has more votes, so the stacking looked arbitrary. Worse, y
 * only ever reaches a few thousand, so a term in the tens of thousands did not
 * merely outrank latitude, it erased it. Removing it is what hands the order
 * back to Leaflet; the bands are untouched.
 *
 * Bands and screen distance: y is a layer point, not a viewport coordinate, so
 * two pins far enough apart can differ by more than the 100k band gap and
 * invert. "Far enough apart" is 100,000 pixels — hundreds of screens — and two
 * pins that far apart cannot overlap, so an inversion there is not something
 * anyone can see. The invariant that matters is the local one: pins close
 * enough to overlap have near-identical y, so their bands hold.
 */

/**
 * The bands, highest first. 100k apart — far enough that the y term (a few
 * thousand at most among pins that can actually overlap) can never carry one
 * pin into its neighbour's band.
 */
export const PIN_BAND = {
  /** Fanned out by the spread. The exploded cluster is the disambiguation UI,
   *  so it reads as one group on top of EVERYTHING, route diamonds included. */
  fanned: 500_000,
  /** A route diamond covered by the selection. */
  routeCovered: 400_000,
  /** A route diamond, uncovered. Above every settled point icon, so a corridor
   *  is never buried under point pins. */
  routeUncovered: 300_000,
  /** A MATCHED waypoint (start/end/mid). It carries the [×] badge, so it must
   *  sit above any overlapping non-matched sibling — else the upper icon
   *  conceals its badge. */
  matchedWaypoint: 200_000,
  /** The selected (open-modal) proposal. */
  selected: 100_000,
  /** Everything else. */
  browse: 1_000,
} as const;

/** Stacking offset for a point proposal's pin (the squares). */
export function pointPinZIndexOffset(state: {
  /** Displaced by the cluster fan-out. */
  fanned: boolean;
  /** Matched to a route waypoint (start/end/mid). */
  matchedWaypoint: boolean;
  /** Holding the open proposal modal. */
  selected: boolean;
}): number {
  if (state.fanned) return PIN_BAND.fanned;
  if (state.matchedWaypoint) return PIN_BAND.matchedWaypoint;
  if (state.selected) return PIN_BAND.selected;
  return PIN_BAND.browse;
}

/** Stacking offset for a route proposal's pin (the diamonds). */
export function routePinZIndexOffset(state: {
  /** Displaced by the cluster fan-out. */
  fanned: boolean;
  /** Its corridor is covered by the current selection. */
  covered: boolean;
}): number {
  if (state.fanned) return PIN_BAND.fanned;
  return state.covered ? PIN_BAND.routeCovered : PIN_BAND.routeUncovered;
}
