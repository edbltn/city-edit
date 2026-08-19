import { describe, it, expect } from "vitest";
import { PIN_BAND, pointPinZIndexOffset, routePinZIndexOffset } from "./pinStacking";

/** What Leaflet adds on top of our offset: `Marker._setPos` sets
 *  `zIndex = layerPoint.y + zIndexOffset`. Southern = larger y = on top. */
const leafletZIndex = (offset: number, layerPointY: number) => offset + layerPointY;

describe("proposal pin stacking", () => {
  // The requirement: of two overlapping pins, the lower-latitude (southern,
  // larger screen y) one draws on top. Leaflet does this by default; the job
  // here is to not override it. A pin's vote count used to be added to its
  // offset, which is exactly such an override — and a large one.
  it("lets latitude decide between two pins in the same band", () => {
    const north = pointPinZIndexOffset({ fanned: false, matchedWaypoint: false, selected: false });
    const south = pointPinZIndexOffset({ fanned: false, matchedWaypoint: false, selected: false });
    // Same state, so the same offset — the ordering can only come from y.
    expect(north).toBe(south);
    expect(leafletZIndex(south, 620)).toBeGreaterThan(leafletZIndex(north, 600));
  });

  it("gives corridors the same treatment", () => {
    const a = routePinZIndexOffset({ fanned: false, covered: false });
    const b = routePinZIndexOffset({ fanned: false, covered: false });
    expect(a).toBe(b);
    expect(leafletZIndex(b, 900)).toBeGreaterThan(leafletZIndex(a, 100));
  });

  // The regression this file exists for. Any per-proposal term — votes, score,
  // rank — added to the offset swamps y, because y is a screen coordinate (a
  // few hundred among pins that overlap) and vote counts run to five figures.
  it("adds nothing per-proposal, so no proposal can outrank geography", () => {
    const offsets = new Set<number>();
    for (const selected of [true, false]) {
      for (const matchedWaypoint of [true, false]) {
        for (const fanned of [true, false]) {
          offsets.add(pointPinZIndexOffset({ fanned, matchedWaypoint, selected }));
        }
      }
    }
    // Eight combinations, but only the four bands they map onto.
    expect([...offsets].sort((a, b) => a - b)).toEqual([
      PIN_BAND.browse, PIN_BAND.selected, PIN_BAND.matchedWaypoint, PIN_BAND.fanned,
    ]);
    expect([...offsets].every((o) => Object.values(PIN_BAND).includes(o as never))).toBe(true);
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

  it("puts a fanned-out pin above everything, both families", () => {
    const fannedPoint = pointPinZIndexOffset({ fanned: true, matchedWaypoint: true, selected: true });
    const fannedRoute = routePinZIndexOffset({ fanned: true, covered: true });
    expect(fannedPoint).toBe(PIN_BAND.fanned);
    expect(fannedRoute).toBe(PIN_BAND.fanned);
    expect(fannedPoint).toBeGreaterThan(routePinZIndexOffset({ fanned: false, covered: true }));
  });

  it("keeps every corridor above every settled point pin", () => {
    const corridor = routePinZIndexOffset({ fanned: false, covered: false });
    for (const selected of [true, false]) {
      for (const matchedWaypoint of [true, false]) {
        expect(pointPinZIndexOffset({ fanned: false, matchedWaypoint, selected }))
          .toBeLessThan(corridor);
      }
    }
  });
});
