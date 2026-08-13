import { describe, it, expect } from "vitest";
import {
  createArrivalAnimator,
  sparkEnvelope,
  cohortGain,
  HEAT_FADE_MS,
  SPARK_MS,
  MAX_CONCURRENT,
  type ArrivalAnimator,
} from "./arrivalAnimation";

/** A manual clock + frame pump, so the curves are tested at exact times rather
 *  than at whatever the machine's rAF happened to deliver. */
function harness(reducedMotion = false) {
  let t = 0;
  let pending: (() => void) | null = null;
  let suspended = false;
  const writes: Array<{ id: number; heat: number; arrive: number }> = [];

  const animator: ArrivalAnimator = createArrivalAnimator({
    suspended: () => suspended,
    write: (id, heat, arrive) => writes.push({ id, heat, arrive }),
    now: () => t,
    requestFrame: (cb) => {
      pending = cb;
      return 1;
    },
    cancelFrame: () => {
      pending = null;
    },
    reducedMotion: () => reducedMotion,
  });

  return {
    animator,
    writes,
    suspend(v: boolean) {
      suspended = v;
    },
    /** Advance the clock and run the frame the animator scheduled (if any). */
    frame(dt: number) {
      t += dt;
      const cb = pending;
      pending = null;
      cb?.();
    },
    hasPendingFrame: () => pending !== null,
    /** Every value written for a block, in order. */
    forId(id: number) {
      return writes.filter((w) => w.id === id);
    },
    last(id: number) {
      const own = writes.filter((w) => w.id === id);
      return own[own.length - 1];
    },
  };
}

describe("sparkEnvelope", () => {
  it("is silent outside its life", () => {
    expect(sparkEnvelope(-0.1)).toBe(0);
    expect(sparkEnvelope(0)).toBe(0);
    expect(sparkEnvelope(1)).toBe(0);
    expect(sparkEnvelope(2)).toBe(0);
  });

  it("attacks fast and decays slowly", () => {
    // Peak lands in the first sixth of the life…
    expect(sparkEnvelope(0.16)).toBeCloseTo(1, 6);
    // …and the tail is still visible well past the midpoint.
    expect(sparkEnvelope(0.08)).toBeGreaterThan(0.3);
    expect(sparkEnvelope(0.5)).toBeGreaterThan(0.1);
    expect(sparkEnvelope(0.9)).toBeLessThan(0.05);
  });

  it("never overshoots 1 — the paint layer multiplies this into an opacity", () => {
    for (let t = 0; t <= 1; t += 0.01) {
      expect(sparkEnvelope(t)).toBeLessThanOrEqual(1);
      expect(sparkEnvelope(t)).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("cohortGain", () => {
  it("leaves a normal vote (a corridor of a few blocks) at full strength", () => {
    expect(cohortGain(1)).toBe(1);
    expect(cohortGain(12)).toBe(1);
  });

  it("damps monotonically as the cohort grows, and never reaches zero", () => {
    const gains = [20, 50, 100, 140, 400].map(cohortGain);
    for (let i = 1; i < gains.length; i++) {
      expect(gains[i]).toBeLessThanOrEqual(gains[i - 1]);
    }
    expect(gains[gains.length - 1]).toBeGreaterThan(0.3);
  });
});

describe("createArrivalAnimator", () => {
  it("fades a newly lit block in from zero and lands exactly on target", () => {
    const h = harness();
    h.animator.arrive([{ id: 7, from: 0, to: 0.8 }]);

    h.frame(HEAT_FADE_MS / 2);
    const mid = h.last(7);
    expect(mid.heat).toBeGreaterThan(0);
    expect(mid.heat).toBeLessThan(0.8);

    // Past both clocks the block is committed to its exact target with no spark
    // left, and the animator stops asking for frames.
    h.frame(SPARK_MS);
    expect(h.last(7)).toEqual({ id: 7, heat: 0.8, arrive: 0 });
    expect(h.animator.size).toBe(0);
    expect(h.hasPendingFrame()).toBe(false);
  });

  it("moves heat monotonically toward the target", () => {
    const h = harness();
    h.animator.arrive([{ id: 3, from: 0.2, to: 0.9 }]);
    for (let i = 0; i < Math.ceil(SPARK_MS / 16) + 2; i++) h.frame(16);
    const heats = h.forId(3).map((w) => w.heat);
    for (let i = 1; i < heats.length; i++) {
      expect(heats[i]).toBeGreaterThanOrEqual(heats[i - 1]);
    }
    expect(heats[heats.length - 1]).toBeCloseTo(0.9, 6);
  });

  it("sparks a block gaining heat but not one merely cooling", () => {
    const h = harness();
    h.animator.arrive([
      { id: 1, from: 0, to: 0.5 },   // support landing
      { id: 2, from: 0.5, to: 0 },   // a vote withdrawn
    ]);
    h.frame(SPARK_MS * 0.16);
    expect(h.last(1).arrive).toBeGreaterThan(0.5);
    expect(h.last(2).arrive).toBe(0);
    // …but the cooling block still fades rather than snapping.
    expect(h.last(2).heat).toBeGreaterThan(0);
    expect(h.last(2).heat).toBeLessThan(0.5);
  });

  it("sparks the cold arm too — opposition arriving is also news", () => {
    const h = harness();
    h.animator.arrive([{ id: 9, from: 0, to: -0.6 }]);
    h.frame(SPARK_MS * 0.16);
    expect(h.last(9).arrive).toBeGreaterThan(0.5);
    expect(h.last(9).heat).toBeLessThan(0);
  });

  it("writes at most once per animating block per frame", () => {
    const h = harness();
    h.animator.arrive([
      { id: 1, from: 0, to: 0.4 },
      { id: 2, from: 0, to: 0.4 },
      { id: 3, from: 0, to: 0.4 },
    ]);
    const before = h.writes.length;
    h.frame(16);
    expect(h.writes.length - before).toBe(3);
  });

  it("keeps ONE spark when a block is re-hit mid-episode (no strobe)", () => {
    const h = harness();
    h.animator.arrive([{ id: 4, from: 0, to: 0.4 }]);
    h.frame(SPARK_MS * 0.16);
    const peak = h.last(4).arrive;
    expect(peak).toBeCloseTo(1, 2);

    // A second vote on the same block, halfway through the first spark. The
    // envelope must keep DECAYING from where it was — restarting the clock is
    // exactly the flicker this rule exists to prevent.
    h.frame(SPARK_MS * 0.34);
    const decayed = h.last(4).arrive;
    h.animator.arrive([{ id: 4, from: 0.4, to: 0.8 }]);
    h.frame(16);
    expect(h.last(4).arrive).toBeLessThan(decayed);
    // …while the heat still re-targets to the new value.
    h.frame(HEAT_FADE_MS + SPARK_MS);
    expect(h.last(4).heat).toBeCloseTo(0.8, 6);
  });

  it("re-targets from the LIVE value, never jumping backward", () => {
    const h = harness();
    h.animator.arrive([{ id: 5, from: 0, to: 1 }]);
    h.frame(HEAT_FADE_MS / 2);
    const live = h.last(5).heat;

    // Second cohort claims the block started at 0 (that is what the diff
    // bookkeeping holds), but it is visibly at `live` — continue from there.
    h.animator.arrive([{ id: 5, from: 0, to: 1 }]);
    h.frame(16);
    expect(h.last(5).heat).toBeGreaterThanOrEqual(live);
  });

  it("paints instantly when a burst exceeds the concurrency cap", () => {
    const h = harness();
    const flood = Array.from({ length: MAX_CONCURRENT + 1 }, (_, i) => ({
      id: i, from: 0, to: 0.5,
    }));
    h.animator.arrive(flood);
    expect(h.writes).toHaveLength(flood.length);
    expect(h.writes.every((w) => w.heat === 0.5 && w.arrive === 0)).toBe(true);
    expect(h.animator.size).toBe(0);
    expect(h.hasPendingFrame()).toBe(false);
  });

  it("counts in-flight blocks toward the cap, and settles them on the way", () => {
    const h = harness();
    h.animator.arrive([{ id: 1, from: 0, to: 0.5 }]);
    h.frame(16);
    h.animator.arrive(Array.from({ length: MAX_CONCURRENT, }, (_, i) => ({
      id: i + 100, from: 0, to: 0.5,
    })));
    // The in-flight block was landed on its target rather than left mid-tween.
    expect(h.last(1)).toEqual({ id: 1, heat: 0.5, arrive: 0 });
    expect(h.animator.size).toBe(0);
  });

  it("paints instantly while suspended — a vote landing mid zoom-ride", () => {
    const h = harness();
    h.suspend(true);
    h.animator.arrive([{ id: 8, from: 0, to: 0.6 }]);
    expect(h.writes).toEqual([{ id: 8, heat: 0.6, arrive: 0 }]);
    expect(h.hasPendingFrame()).toBe(false);

    // …and animates again once the ride is over.
    h.suspend(false);
    h.animator.arrive([{ id: 8, from: 0.6, to: 0.9 }]);
    h.frame(16);
    expect(h.last(8).heat).toBeLessThan(0.9);
    expect(h.last(8).arrive).toBeGreaterThan(0);
  });

  it("paints instantly under prefers-reduced-motion", () => {
    const h = harness(true);
    h.animator.arrive([{ id: 2, from: 0, to: 0.7 }]);
    expect(h.writes).toEqual([{ id: 2, heat: 0.7, arrive: 0 }]);
    expect(h.hasPendingFrame()).toBe(false);
  });

  it("settle() lands everything at once and stops the loop", () => {
    const h = harness();
    h.animator.arrive([
      { id: 1, from: 0, to: 0.5 },
      { id: 2, from: 0, to: -0.5 },
    ]);
    h.frame(16);
    h.animator.settle();
    expect(h.last(1)).toEqual({ id: 1, heat: 0.5, arrive: 0 });
    expect(h.last(2)).toEqual({ id: 2, heat: -0.5, arrive: 0 });
    expect(h.animator.size).toBe(0);
    expect(h.hasPendingFrame()).toBe(false);
  });

  it("reset() drops in-flight work without writing", () => {
    const h = harness();
    h.animator.arrive([{ id: 1, from: 0, to: 0.5 }]);
    h.frame(16);
    const before = h.writes.length;
    h.animator.reset();
    expect(h.writes.length).toBe(before);
    expect(h.animator.size).toBe(0);
    expect(h.hasPendingFrame()).toBe(false);
  });

  it("ignores an empty cohort", () => {
    const h = harness();
    h.animator.arrive([]);
    expect(h.writes).toHaveLength(0);
    expect(h.hasPendingFrame()).toBe(false);
  });
});
