import { describe, it, expect } from "vitest";
import { PIN_BAND, pinZIndexOffset } from "./pinStacking";

/** What Leaflet adds on top of our offset: `Marker._setPos` sets
 *  `zIndex = layerPoint.y + zIndexOffset`. Southern = larger y = on top. */
const leafletZIndex = (offset: number, layerPointY: number) => offset + layerPointY;

/** A settled point proposal (a square) and a settled corridor (a diamond) —
 *  the two kinds, in the only state most pins are ever in. */
const point = (over: Partial<Parameters<typeof pinZIndexOffset>[0]> = {}) =>
  pinZIndexOffset({ fanned: false, matchedWaypoint: false, active: false, ...over });
const corridor = (over: Partial<Parameters<typeof pinZIndexOffset>[0]> = {}) =>
  pinZIndexOffset({ fanned: false, matchedWaypoint: false, active: false, ...over });

describe("proposal pin stacking", () => {
  // The requirement: of two overlapping pins, the lower-latitude (southern,
  // larger screen y) one draws on top. Leaflet does this by default; the job
  // here is to not override it. A pin's vote count used to be added to its
  // offset, which is exactly such an override — and a large one.
  it("lets latitude decide between two pins in the same band", () => {
    expect(point()).toBe(point());
    expect(leafletZIndex(point(), 620)).toBeGreaterThan(leafletZIndex(point(), 600));
  });

  // The cross-kind bug. A corridor used to sit in a band of its own (300k
  // uncovered, 400k covered) above every point pin, so a southern square could
  // never cover a northern diamond — latitude worked WITHIN each kind and not
  // at all between them. Both kinds now take the same band for the same state,
  // which is what puts them on one scale.
  it("orders a corridor against a point pin by latitude, not by kind", () => {
    expect(corridor()).toBe(point());
    // Point further south → point in front.
    expect(leafletZIndex(point(), 700)).toBeGreaterThan(leafletZIndex(corridor(), 500));
    // Swap the latitudes → the order flips.
    expect(leafletZIndex(corridor(), 700)).toBeGreaterThan(leafletZIndex(point(), 500));
  });

  // The regression this file exists for. Any per-marker term — votes, score,
  // rank, or a constant for one kind — added to the offset swamps y, because y
  // is a screen coordinate (a few hundred among pins that overlap) while those
  // quantities run to five figures.
  it("adds nothing per-proposal and nothing per-kind", () => {
    const offsets = new Set<number>();
    for (const active of [true, false]) {
      for (const matchedWaypoint of [true, false]) {
        for (const fanned of [true, false]) {
          offsets.add(pinZIndexOffset({ fanned, matchedWaypoint, active }));
        }
      }
    }
    // Eight combinations, but only the four bands they map onto.
    expect([...offsets].sort((a, b) => a - b)).toEqual([
      PIN_BAND.browse, PIN_BAND.active, PIN_BAND.matchedWaypoint, PIN_BAND.fanned,
    ]);
    // And the function has no way to be told what kind of pin it is looking at.
    expect(pinZIndexOffset.length).toBe(1);
  });

  // Bands are the part latitude may NOT overturn: a pin doing a job stays on
  // top of a pin that is merely nearer the viewer. The gap has to clear the
  // largest y difference two OVERLAPPING pins can have, which is the height of
  // a pin — everything else is far enough away to be invisible.
  it("keeps a band above the one below it for any two overlapping pins", () => {
    const bands = Object.values(PIN_BAND).sort((a, b) => a - b);
    const PIN_HEIGHT_PX = 42;
    for (let i = 0; i < bands.length - 1; i++) {
      expect(bands[i + 1] - bands[i]).toBeGreaterThan(PIN_HEIGHT_PX);
      // The southernmost possible overlapping pin in the lower band still loses
      // to the northernmost in the higher one.
      expect(leafletZIndex(bands[i], 1000 + PIN_HEIGHT_PX))
        .toBeLessThan(leafletZIndex(bands[i + 1], 1000));
    }
  });

  it("puts a fanned-out pin above everything, whichever kind it is", () => {
    expect(point({ fanned: true, matchedWaypoint: true, active: true })).toBe(PIN_BAND.fanned);
    expect(corridor({ fanned: true, active: true })).toBe(PIN_BAND.fanned);
    expect(corridor({ fanned: true })).toBeGreaterThan(point({ matchedWaypoint: true }));
  });

  // A matched waypoint carries the [×] badge, and a covered corridor is the one
  // the route summary is talking about. Those are jobs, so they outrank browse
  // pins of EITHER kind — but they are states, not kinds.
  it("lifts the pin being acted on, of either kind", () => {
    expect(point({ matchedWaypoint: true })).toBeGreaterThan(corridor());
    expect(corridor({ active: true })).toBeGreaterThan(point());
    expect(point({ active: true })).toBe(corridor({ active: true }));
  });
});
