import { describe, it, expect } from "vitest";
import { PLACE_LABEL_TEXT_SIZE, countZoomCurves } from "./placeLabelStyle";

/** Evaluate the expression the way MapLibre would, for one feature at one zoom. */
function sizeAt(zoom: number, kind: string): number {
  const expr = PLACE_LABEL_TEXT_SIZE as unknown as unknown[];
  const stops: Array<[number, number]> = [];
  for (let i = 3; i < expr.length; i += 2) {
    const match = expr[i + 1] as unknown[];
    // ["match", ["get","kind"], "subway", <hit>, <miss>]
    stops.push([expr[i] as number, kind === match[2] ? (match[3] as number) : (match[4] as number)]);
  }
  if (zoom <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (zoom >= last[0]) return last[1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [z0, v0] = stops[i];
    const [z1, v1] = stops[i + 1];
    if (zoom >= z0 && zoom <= z1) return v0 + ((v1 - v0) * (zoom - z0)) / (z1 - z0);
  }
  throw new Error("unreachable");
}

describe("place label text-size", () => {
  // The regression this file exists for. A `match` wrapping two zoom ramps
  // validates fine to the eye and to TypeScript, but MapLibre rejects the whole
  // LAYER for it — as a non-fatal style error, so the only symptom is that every
  // place label silently stops rendering.
  it("contains exactly one zoom-based curve", () => {
    expect(countZoomCurves(PLACE_LABEL_TEXT_SIZE)).toBe(1);
  });

  it("flags the shape that broke the layer (a match over two zoom ramps)", () => {
    const broken = [
      "match", ["get", "kind"],
      "subway", ["interpolate", ["linear"], ["zoom"], 11, 9.6, 18, 11.5],
      ["interpolate", ["linear"], ["zoom"], 11, 8.6, 18, 10.5],
    ];
    expect(countZoomCurves(broken)).toBe(2);
  });

  it("does not count a non-zoom interpolate", () => {
    const byProperty = ["interpolate", ["linear"], ["get", "minz"], 11, 1, 18, 2];
    expect(countZoomCurves(byProperty)).toBe(0);
  });

  it("sizes subway stops above everything else at every zoom", () => {
    for (const zoom of [10, 11, 13, 15, 16.5, 18, 20]) {
      expect(sizeAt(zoom, "subway")).toBeGreaterThan(sizeAt(zoom, "cafe"));
    }
  });

  it("keeps the rest of transit at the base size", () => {
    for (const kind of ["bus", "ferry", "rail", "airport"]) {
      expect(sizeAt(15, kind)).toBe(sizeAt(15, "cafe"));
    }
  });

  it("stays a restrained bump — under 1.5px, so labels stay quieter than the heat", () => {
    for (const zoom of [11, 15, 18]) {
      const delta = sizeAt(zoom, "subway") - sizeAt(zoom, "cafe");
      expect(delta).toBeGreaterThan(0.5);
      expect(delta).toBeLessThanOrEqual(1.5);
    }
  });
});
