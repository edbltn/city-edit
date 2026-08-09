import { describe, it, expect } from "vitest";
import { makeHeatNormalization } from "./blockHeat";

describe("makeHeatNormalization", () => {
  it("maps zero to zero — a cancelled block carries no signal", () => {
    // Signed zero: the negative arm returns -0, which compares equal to 0
    // everywhere it is used (paint, and the diff test in MapLibreBackground).
    expect(makeHeatNormalization(50, 10).heatOf(0)).toBeCloseTo(0, 10);
  });

  it("maps the positive ceiling to exactly 1", () => {
    expect(makeHeatNormalization(50, 10).heatOf(50)).toBeCloseTo(1, 10);
  });

  it("maps the negative ceiling to exactly -1", () => {
    expect(makeHeatNormalization(50, 10).heatOf(-10)).toBeCloseTo(-1, 10);
  });

  it("makes a single vote visible rather than zero", () => {
    const h = makeHeatNormalization(50, 10).heatOf(1);
    expect(h).toBeGreaterThan(0);
    expect(h).toBeLessThan(0.2);
  });

  it("normalizes the arms separately, so opposition reaches deep cold", () => {
    // Same magnitude, very different ceilings: -5 against a ceiling of 10 is
    // much colder than +5 is warm against a ceiling of 50.
    const { heatOf } = makeHeatNormalization(50, 10);
    expect(Math.abs(heatOf(-5))).toBeGreaterThan(heatOf(5));
  });

  it("is compressive — 10x the votes is far less than 10x the heat", () => {
    const { heatOf } = makeHeatNormalization(1000, 10);
    expect(heatOf(100)).toBeLessThan(heatOf(10) * 3);
  });

  it("floors both ceilings at 1 so a quiet map cannot saturate or divide by zero", () => {
    const { heatOf } = makeHeatNormalization(0, 0);
    expect(Number.isFinite(heatOf(1))).toBe(true);
    expect(heatOf(1)).toBeCloseTo(1, 10);
  });

  it("changes denomKey when a ceiling moves, and only then", () => {
    expect(makeHeatNormalization(50, 10).denomKey)
      .toBe(makeHeatNormalization(50, 10).denomKey);
    expect(makeHeatNormalization(50, 10).denomKey)
      .not.toBe(makeHeatNormalization(51, 10).denomKey);
  });
});
