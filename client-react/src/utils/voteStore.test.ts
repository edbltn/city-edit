import { describe, it, expect, beforeEach } from "vitest";
import {
  getVote, setVote, setVotes, coverage, reconcileEdge, setVoteTypeMap, _resetVoteStore,
} from "./voteStore";

const MODE = "walkways";
// Two known vote types (ids assigned by the server, learned via setVoteTypeMap).
const TYPES = { "10": "Add bike lane", "11": "Add trees" };

beforeEach(() => {
  _resetVoteStore();
});

describe("voteStore — direction get/set/clear", () => {
  it("stores and reads a direction, and clears with 0", () => {
    setVoteTypeMap(TYPES);
    expect(getVote(MODE, 5, "Add bike lane")).toBe(0);
    setVote(MODE, 5, "Add bike lane", 1);
    expect(getVote(MODE, 5, "Add bike lane")).toBe(1);
    setVote(MODE, 5, "Add bike lane", -1);
    expect(getVote(MODE, 5, "Add bike lane")).toBe(-1);
    setVote(MODE, 5, "Add bike lane", 0);
    expect(getVote(MODE, 5, "Add bike lane")).toBe(0);
  });

  it("keeps directions isolated by mode, edge, and vote type", () => {
    setVoteTypeMap(TYPES);
    setVote(MODE, 5, "Add bike lane", 1);
    expect(getVote("bikepaths", 5, "Add bike lane")).toBe(0); // other mode
    expect(getVote(MODE, 6, "Add bike lane")).toBe(0); // other edge
    expect(getVote(MODE, 5, "Add trees")).toBe(0); // other type
  });
});

describe("voteStore — unknown-label fallback + migration", () => {
  it("records a vote on a brand-new label, then migrates to the packed store once its id is learned", () => {
    // Label not yet known to the server → stored in the fallback layer.
    setVote(MODE, 7, "Add benches", 1);
    expect(getVote(MODE, 7, "Add benches", )).toBe(1);
    // Server assigns id 12 for it; the entry migrates and stays correct.
    setVoteTypeMap({ "12": "Add benches" });
    expect(getVote(MODE, 7, "Add benches")).toBe(1);
    // And clearing still works after migration.
    setVote(MODE, 7, "Add benches", 0);
    expect(getVote(MODE, 7, "Add benches")).toBe(0);
  });
});

describe("voteStore — coverage (multi-select availability)", () => {
  it("classifies edges into atTarget / opposite / unvoted for a direction", () => {
    setVoteTypeMap(TYPES);
    const label = "Add bike lane";
    setVote(MODE, 1, label, 1); // already up
    setVote(MODE, 2, label, -1); // currently down (opposite of an up cast)
    // edge 3 unvoted
    const cov = coverage(MODE, [1, 2, 3], label, 1);
    expect(cov.atTarget).toEqual([1]);
    expect(cov.opposite).toEqual([2]);
    expect(cov.unvoted).toEqual([3]);
  });

  it("reports full coverage when every edge already holds the direction", () => {
    setVoteTypeMap(TYPES);
    const label = "Add trees";
    setVotes(MODE, [1, 2, 3], label, 1);
    const cov = coverage(MODE, [1, 2, 3], label, 1);
    expect(cov.unvoted).toHaveLength(0);
    expect(cov.opposite).toHaveLength(0);
    expect(cov.atTarget).toEqual([1, 2, 3]);
  });
});

describe("voteStore — reconcileEdge (server authoritative)", () => {
  it("overwrites local direction with the server's per-label truth", () => {
    setVoteTypeMap(TYPES);
    setVote(MODE, 9, "Add bike lane", 1);
    reconcileEdge(MODE, 9, { "Add bike lane": -1, "Add trees": 1 });
    expect(getVote(MODE, 9, "Add bike lane")).toBe(-1);
    expect(getVote(MODE, 9, "Add trees")).toBe(1);
  });
});
