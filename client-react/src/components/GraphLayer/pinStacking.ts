/**
 * Where a proposal pin sits in the Leaflet marker stack.
 *
 * ONE scale for both kinds. Point proposals (squares) and route proposals
 * (diamonds) are both Leaflet markers, both anchored on their tail tip, and they
 * go through the SAME function here — because the moment either kind carries a
 * base offset the other does not, latitude stops meaning anything between them.
 * That is not a subtle failure: bands are 100k apart and screen y reaches a few
 * hundred, so a per-kind band makes EVERY member of one kind draw in front of
 * EVERY member of the other, whatever their latitudes. Corridors used to sit in
 * bands of their own (300k uncovered, 400k covered) on the rule that "a corridor
 * is never buried under point pins", and that rule is what this file deleted:
 * the two kinds are peers on the map, so they are peers in the stack.
 *
 * Two rules remain, and the split between them is the whole design:
 *
 *   1. STATE decides the band — never KIND. A pin that is fanned out, or
 *      carrying a route role, or holding the open modal, is doing a JOB, and a
 *      pin doing a job must not be buried by a pin that is merely present.
 *      Every band below is a state both kinds can be in (or, for
 *      `matchedWaypoint`, one that simply never arises for a corridor) — none of
 *      them is "is this a corridor".
 *   2. LATITUDE decides the order inside a band — and it is Leaflet, not this
 *      module, that applies it. `Marker._setPos` sets `zIndex = layerPoint.y +
 *      zIndexOffset`, and layer-point y grows southward, so of two overlapping
 *      pins the SOUTHERN one paints on top by default. That is the near/far
 *      reading every map with a tilted pin uses, it is free, and because both
 *      kinds anchor on their tip, y is the same measurement for a square and a
 *      diamond.
 *
 * Which means the offsets below carry NO per-marker term. They used to: a pin
 * was lifted by its own vote count (capped at 50k) and a corridor by its score
 * (capped at 48k), so where two pins overlapped the better-supported one won.
 * That is a defensible rule on its own, but it is not the one asked for, and it
 * is not one the eye can read — nothing on the map says which of two overlapping
 * pins has more votes, so the stacking looked arbitrary. Worse, y only ever
 * reaches a few thousand, so a term in the tens of thousands did not merely
 * outrank latitude, it erased it. Removing it is what hands the order back to
 * Leaflet; the state bands are untouched.
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
 *
 * Read the list for what is NOT in it: there is no band for "corridor" and none
 * for "point". Every rung is a state.
 */
export const PIN_BAND = {
  /** Fanned out by the spread. The exploded cluster is the disambiguation UI,
   *  so it reads as one group on top of EVERYTHING — including, for a fanned
   *  square, the corridors it was overlapping a moment ago. */
  fanned: 500_000,
  /** A MATCHED waypoint (start/end/mid). It carries the [×] badge, so it must
   *  sit above any overlapping sibling — else the upper icon conceals its
   *  badge. Only point pins are ever matched to a waypoint; a corridor simply
   *  never asks for this band rather than being excluded from it. */
  matchedWaypoint: 200_000,
  /** The one being acted on: a point proposal holding the open modal, or a
   *  corridor covered by the current selection. Same band, because it is the
   *  same statement — this is the proposal in play. */
  active: 100_000,
  /** Everything else, of either kind. */
  browse: 1_000,
} as const;

/**
 * Stacking offset for a proposal pin — squares and diamonds alike.
 *
 * Deliberately kind-blind: it takes no parameter saying which family the pin
 * belongs to, so there is nowhere for a per-kind lift to be reintroduced.
 */
export function pinZIndexOffset(state: {
  /** Displaced by the cluster fan-out. */
  fanned: boolean;
  /** Matched to a route waypoint (start/end/mid). Corridors pass false. */
  matchedWaypoint: boolean;
  /** Holding the open modal (point), or covered by the selection (corridor). */
  active: boolean;
}): number {
  if (state.fanned) return PIN_BAND.fanned;
  if (state.matchedWaypoint) return PIN_BAND.matchedWaypoint;
  if (state.active) return PIN_BAND.active;
  return PIN_BAND.browse;
}
