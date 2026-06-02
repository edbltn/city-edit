import { describe, it, expect, beforeEach } from "vitest";
import { planVoteChange } from "./castVote";
import { setVote, setVotes, setVoteTypeMap, _resetVoteStore } from "./voteStore";

const MODE = "walkways";
const LABEL = "Add bike lane";

beforeEach(() => {
  _resetVoteStore();
  setVoteTypeMap({ "10": LABEL });
});

describe("planVoteChange — the multi-select cast rule", () => {
  it("casts on every edge when none is voted yet", () => {
    const plan = planVoteChange(MODE, [1, 2, 3], LABEL, 1);
    expect(plan.targetDir).toBe(1);
    expect(plan.changedEdges.sort()).toEqual([1, 2, 3]);
    expect(plan.groups).toEqual([{ edges: [1, 2, 3], prevDir: 0, newDir: 1 }]);
  });

  it("leaves already-cast edges untouched and only votes/reverses the rest", () => {
    setVote(MODE, 1, LABEL, 1); // already up — must be left alone
    setVote(MODE, 2, LABEL, -1); // down — must be reversed to up
    // edge 3 unvoted — must get a fresh up
    const plan = planVoteChange(MODE, [1, 2, 3], LABEL, 1);
    expect(plan.targetDir).toBe(1);
    expect(plan.changedEdges.sort()).toEqual([2, 3]); // edge 1 NOT touched
    expect(plan.groups).toContainEqual({ edges: [3], prevDir: 0, newDir: 1 });
    expect(plan.groups).toContainEqual({ edges: [2], prevDir: -1, newDir: 1 });
  });

  it("toggles OFF (removes) when every edge already holds the direction", () => {
    setVotes(MODE, [1, 2, 3], LABEL, 1);
    const plan = planVoteChange(MODE, [1, 2, 3], LABEL, 1);
    expect(plan.targetDir).toBe(0);
    expect(plan.changedEdges.sort()).toEqual([1, 2, 3]);
    expect(plan.groups).toEqual([{ edges: [1, 2, 3], prevDir: 1, newDir: 0 }]);
  });

  it("does NOT toggle off when coverage is partial (some unvoted remain)", () => {
    setVote(MODE, 1, LABEL, 1);
    // edge 2 unvoted → this is a cast, not a removal
    const plan = planVoteChange(MODE, [1, 2], LABEL, 1);
    expect(plan.targetDir).toBe(1);
    expect(plan.changedEdges).toEqual([2]);
  });

  it("downvotes a whole stretch (the new route-against gesture)", () => {
    const plan = planVoteChange(MODE, [4, 5], LABEL, -1);
    expect(plan.targetDir).toBe(-1);
    expect(plan.groups).toEqual([{ edges: [4, 5], prevDir: 0, newDir: -1 }]);
  });
});
