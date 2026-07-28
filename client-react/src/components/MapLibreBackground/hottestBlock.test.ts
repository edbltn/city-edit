import { describe, it, expect } from "vitest";
import { hottestBlockId } from "./hottestBlock";

describe("hottestBlockId", () => {
  it("returns null for no features", () => {
    expect(hottestBlockId([])).toBeNull();
  });

  it("returns the only feature's id", () => {
    expect(hottestBlockId([{ id: 7, state: {} }])).toBe(7);
  });

  it("picks the hotter block over render order", () => {
    // The cool overlap renders on top (first in query results) — the hot
    // block underneath must still win the hit.
    const feats = [
      { id: 1, state: { heat: 0 } },
      { id: 2, state: { heat: 0.8 } },
      { id: 3, state: { heat: 0.4 } },
    ];
    expect(hottestBlockId(feats)).toBe(2);
  });

  it("treats missing heat state as zero", () => {
    expect(hottestBlockId([{ id: 1 }, { id: 2, state: { heat: 0.1 } }])).toBe(2);
  });

  it("keeps render order on ties (all-zero heat)", () => {
    const feats = [
      { id: 5, state: {} },
      { id: 6, state: { heat: 0 } },
    ];
    expect(hottestBlockId(feats)).toBe(5);
  });

  it("ranks negative (cold) heat by magnitude", () => {
    // A strongly net-against block is just as visible as a hot one — it must
    // beat a zero-heat overlap.
    const feats = [
      { id: 1, state: { heat: 0 } },
      { id: 2, state: { heat: -0.9 } },
      { id: 3, state: { heat: 0.5 } },
    ];
    expect(hottestBlockId(feats)).toBe(2);
  });

  it("ignores features without a numeric id", () => {
    const feats = [
      { id: "nope", state: { heat: 1 } },
      { id: undefined, state: { heat: 1 } },
      { id: 4, state: { heat: 0.2 } },
    ];
    expect(hottestBlockId(feats)).toBe(4);
  });
});
