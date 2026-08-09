import { describe, it, expect, beforeEach } from "vitest";
import {
  getHiddenVoteTypes,
  isVoteTypeVisible,
  setVoteTypeVisible,
  toggleVoteTypeVisible,
  ensureVoteTypeVisible,
  showAllVoteTypes,
  showOnlyVoteTypes,
  legendVisibilityMask,
  subscribeVoteTypeFilter,
  getVoteTypeFilterVersion,
} from "./voteTypeFilter";

describe("voteTypeFilter", () => {
  beforeEach(() => {
    showAllVoteTypes();
  });

  it("shows everything by default", () => {
    expect(getHiddenVoteTypes().size).toBe(0);
    expect(isVoteTypeVisible("Add a bike lane")).toBe(true);
    // A type nobody has heard of is visible too — the store holds HIDDEN
    // labels, so new vote types never need opting in.
    expect(isVoteTypeVisible("Brand new suggestion")).toBe(true);
  });

  it("hides and re-shows a single label", () => {
    setVoteTypeVisible("Trees", false);
    expect(isVoteTypeVisible("Trees")).toBe(false);
    expect(isVoteTypeVisible("Benches")).toBe(true);
    toggleVoteTypeVisible("Trees");
    expect(isVoteTypeVisible("Trees")).toBe(true);
  });

  it("ensureVoteTypeVisible un-hides (the cast path's guarantee)", () => {
    setVoteTypeVisible("Trees", false);
    ensureVoteTypeVisible("Trees");
    expect(isVoteTypeVisible("Trees")).toBe(true);
  });

  it("showOnlyVoteTypes isolates the kept labels", () => {
    showOnlyVoteTypes(["A", "B", "C"], ["B"]);
    expect(isVoteTypeVisible("A")).toBe(false);
    expect(isVoteTypeVisible("B")).toBe(true);
    expect(isVoteTypeVisible("C")).toBe(false);
    showAllVoteTypes();
    expect(getHiddenVoteTypes().size).toBe(0);
  });

  it("showOnlyVoteTypes with no keeps hides them all", () => {
    showOnlyVoteTypes(["A", "B"]);
    expect(getHiddenVoteTypes().size).toBe(2);
  });

  it("notifies subscribers only on a real change", () => {
    let calls = 0;
    const stop = subscribeVoteTypeFilter(() => { calls++; });
    setVoteTypeVisible("A", false);
    expect(calls).toBe(1);
    setVoteTypeVisible("A", false); // already hidden — no-op
    expect(calls).toBe(1);
    setVoteTypeVisible("A", true);
    expect(calls).toBe(2);
    stop();
    setVoteTypeVisible("A", false);
    expect(calls).toBe(2);
  });

  it("bumps the version on each change (the derived-array cache key)", () => {
    const before = getVoteTypeFilterVersion();
    setVoteTypeVisible("A", false);
    expect(getVoteTypeFilterVersion()).toBe(before + 1);
    setVoteTypeVisible("A", false);
    expect(getVoteTypeFilterVersion()).toBe(before + 1);
  });

  describe("legendVisibilityMask", () => {
    it("is null when nothing is hidden — the no-filter fast path", () => {
      expect(legendVisibilityMask(["A", "B"])).toBeNull();
    });

    it("is null for an absent or empty legend", () => {
      setVoteTypeVisible("A", false);
      expect(legendVisibilityMask(undefined)).toBeNull();
      expect(legendVisibilityMask([])).toBeNull();
    });

    it("marks hidden legend indices 0 and the rest 1", () => {
      setVoteTypeVisible("B", false);
      const mask = legendVisibilityMask(["A", "B", "C"]);
      expect(mask).not.toBeNull();
      expect([...mask!]).toEqual([1, 0, 1]);
    });
  });
});
