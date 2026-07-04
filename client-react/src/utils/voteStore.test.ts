import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  getVote, setVote, setVotes, coverage, blockCoverage, myVotesInBlocks,
  reconcileEdge, setVoteTypeMap, subscribeVotes, getVotesVersion, _resetVoteStore,
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

// The whole point of the store: ONE change signal that every view shares. This
// is what keeps the top-bar banner and the proposal modal from drifting (the
// sync bug). If a mutation stops notifying, these break — not a real component.
describe("voteStore — change notification (the sync contract)", () => {
  it("notifies subscribers and bumps the version on a real change", () => {
    setVoteTypeMap(TYPES);
    const listener = vi.fn();
    const unsubscribe = subscribeVotes(listener);
    const v0 = getVotesVersion();

    setVote(MODE, 5, "Add bike lane", 1);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(getVotesVersion()).toBe(v0 + 1);

    unsubscribe();
    setVote(MODE, 5, "Add bike lane", -1);
    expect(listener).toHaveBeenCalledTimes(1); // no longer subscribed
  });

  it("does NOT notify when a write changes nothing (stable snapshot)", () => {
    setVoteTypeMap(TYPES);
    setVote(MODE, 5, "Add bike lane", 1);
    const listener = vi.fn();
    subscribeVotes(listener);
    const v0 = getVotesVersion();

    setVote(MODE, 5, "Add bike lane", 1); // same direction → no-op
    expect(listener).not.toHaveBeenCalled();
    expect(getVotesVersion()).toBe(v0);
  });

  it("notifies once for a multi-edge batch, not once per edge", () => {
    setVoteTypeMap(TYPES);
    const listener = vi.fn();
    subscribeVotes(listener);

    setVotes(MODE, [1, 2, 3], "Add trees", 1);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("notifies on reconcileEdge only when the server's truth differs", () => {
    setVoteTypeMap(TYPES);
    setVote(MODE, 9, "Add bike lane", 1);
    const listener = vi.fn();
    subscribeVotes(listener);

    reconcileEdge(MODE, 9, { "Add bike lane": 1 }); // matches local → no-op
    expect(listener).not.toHaveBeenCalled();

    reconcileEdge(MODE, 9, { "Add bike lane": -1 }); // differs → notify
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe("voteStore — blockCoverage (block-scoped button state)", () => {
  const label = "Add bike lane";

  beforeEach(() => {
    setVoteTypeMap(TYPES);
  });

  it("is none/none with no votes anywhere", () => {
    expect(blockCoverage(MODE, [[1], [2]], label)).toEqual({ up: "none", down: "none" });
  });

  it("is all when every block holds the direction on ≥1 edge", () => {
    setVote(MODE, 1, label, 1);
    setVote(MODE, 5, label, 1); // block [4, 5] held via edge 5 only
    expect(blockCoverage(MODE, [[1], [4, 5]], label)).toEqual({ up: "all", down: "none" });
  });

  it("is some when only a strict subset of blocks holds the direction", () => {
    setVote(MODE, 1, label, 1);
    expect(blockCoverage(MODE, [[1], [2]], label)).toEqual({ up: "some", down: "none" });
  });

  it("tracks the two directions independently across blocks", () => {
    setVote(MODE, 1, label, 1);
    setVote(MODE, 2, label, -1);
    expect(blockCoverage(MODE, [[1], [2]], label)).toEqual({ up: "some", down: "some" });
    setVote(MODE, 3, label, -1);
    expect(blockCoverage(MODE, [[1, 3], [2]], label)).toEqual({ up: "some", down: "all" });
  });

  it("scopes coverage to the given blocks and vote type", () => {
    setVote(MODE, 9, label, 1); // outside every block
    setVote(MODE, 1, "Add trees", 1); // other type
    expect(blockCoverage(MODE, [[1], [2]], label)).toEqual({ up: "none", down: "none" });
  });

  it("is none/none for an empty block list (never 'all')", () => {
    expect(blockCoverage(MODE, [], label)).toEqual({ up: "none", down: "none" });
  });
});

describe("voteStore — myVotesInBlocks", () => {
  const label = "Add bike lane";

  beforeEach(() => {
    setVoteTypeMap(TYPES);
  });

  it("returns every voted edge in the blocks with its direction", () => {
    setVote(MODE, 1, label, 1);
    setVote(MODE, 5, label, -1);
    setVote(MODE, 9, label, 1); // outside the blocks — excluded
    setVote(MODE, 2, "Add trees", 1); // other type — excluded
    const mine = myVotesInBlocks(MODE, [[1, 2], [4, 5]], label);
    expect(mine).toEqual(new Map([[1, 1], [5, -1]]));
  });

  it("dedupes an edge shared by two blocks", () => {
    setVote(MODE, 3, label, 1);
    const mine = myVotesInBlocks(MODE, [[3], [3, 4]], label);
    expect([...mine.entries()]).toEqual([[3, 1]]);
  });

  it("is empty when I hold no votes in the blocks", () => {
    expect(myVotesInBlocks(MODE, [[1], [2]], label).size).toBe(0);
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
