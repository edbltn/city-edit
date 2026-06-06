import { describe, it, expect } from "vitest";
import type { Selection } from "./types";
import { EMPTY_SELECTION, selectionPhase } from "./types";
import {
  setStart,
  setEnd,
  insertMid,
  updateAt,
  removeAt,
  clearWaypoints,
  setVoteType,
  fullIndexOf,
} from "./reducer";

// Deterministic id generator for tests.
function counter() {
  let n = 0;
  return () => `id-${++n}`;
}

const ll = (lat: number, lng: number) => ({ lat, lng });

function sel(...pairs: [number, number][]): Selection {
  let s = EMPTY_SELECTION;
  const id = counter();
  pairs.forEach(([lat, lng], i) => {
    s = i === 0 ? setStart(s, { coords: ll(lat, lng) }, id) : setEnd(s, { coords: ll(lat, lng) }, id);
  });
  return s;
}

describe("setStart", () => {
  it("appends into an empty selection", () => {
    const s = setStart(EMPTY_SELECTION, { coords: ll(1, 2) }, counter());
    expect(s.waypoints).toHaveLength(1);
    expect(s.waypoints[0].coords).toEqual(ll(1, 2));
    expect(s.waypoints[0].id).toBe("id-1");
  });

  it("replaces index 0 in place, preserving mids + end and the slot id", () => {
    let s = sel([0, 0], [2, 2]); // start+end, ids id-1,id-2
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "mid"); // [start, mid, end]
    const startId = s.waypoints[0].id;
    const moved = setStart(s, { coords: ll(9, 9) }, () => "NEW");
    expect(moved.waypoints).toHaveLength(3);
    expect(moved.waypoints[0].coords).toEqual(ll(9, 9));
    expect(moved.waypoints[0].id).toBe(startId); // reused, not "NEW"
    expect(moved.waypoints[1].coords).toEqual(ll(1, 1)); // mid intact
    expect(moved.waypoints[2].coords).toEqual(ll(2, 2)); // end intact
  });

  it("carries voteEdgeId and address", () => {
    const s = setStart(EMPTY_SELECTION, { coords: ll(1, 2), voteEdgeId: 42, address: "X" }, counter());
    expect(s.waypoints[0].voteEdgeId).toBe(42);
    expect(s.waypoints[0].address).toBe("X");
  });
});

describe("setEnd", () => {
  it("appends as the second point when only a start exists", () => {
    let s = setStart(EMPTY_SELECTION, { coords: ll(0, 0) }, counter());
    s = setEnd(s, { coords: ll(2, 2) }, counter());
    expect(s.waypoints.map((w) => w.coords)).toEqual([ll(0, 0), ll(2, 2)]);
  });

  it("replaces the last point when an end already exists, keeping mids", () => {
    let s = sel([0, 0], [2, 2]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "mid"); // [s, mid, e]
    const endId = s.waypoints[2].id;
    s = setEnd(s, { coords: ll(5, 5) }, () => "NEW");
    expect(s.waypoints).toHaveLength(3);
    expect(s.waypoints[1].coords).toEqual(ll(1, 1)); // mid untouched
    expect(s.waypoints[2].coords).toEqual(ll(5, 5));
    expect(s.waypoints[2].id).toBe(endId);
  });
});

describe("insertMid (segment → full index)", () => {
  it("segment 0 lands a mid at full index 1 (after start)", () => {
    let s = sel([0, 0], [3, 3]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "m1");
    expect(s.waypoints.map((w) => w.coords)).toEqual([ll(0, 0), ll(1, 1), ll(3, 3)]);
  });

  it("inserts into the correct later segment", () => {
    let s = sel([0, 0], [3, 3]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "m1"); // [0,1,3]
    s = insertMid(s, 1, { coords: ll(2, 2) }, () => "m2"); // segment between mid(1) and end → [0,1,2,3]
    expect(s.waypoints.map((w) => w.coords)).toEqual([ll(0, 0), ll(1, 1), ll(2, 2), ll(3, 3)]);
  });

  it("is a no-op with no end to insert before", () => {
    const s = setStart(EMPTY_SELECTION, { coords: ll(0, 0) }, counter());
    expect(insertMid(s, 0, { coords: ll(1, 1) }, () => "x")).toBe(s);
  });

  it("is a no-op for an out-of-range segment", () => {
    const s = sel([0, 0], [3, 3]);
    expect(insertMid(s, 5, { coords: ll(1, 1) }, () => "x")).toBe(s);
  });
});

describe("updateAt", () => {
  it("moves a waypoint, keeps id, clears voteEdgeId + address", () => {
    let s = setStart(EMPTY_SELECTION, { coords: ll(0, 0), voteEdgeId: 7, address: "A" }, counter());
    const id = s.waypoints[0].id;
    s = updateAt(s, 0, ll(9, 9));
    expect(s.waypoints[0].coords).toEqual(ll(9, 9));
    expect(s.waypoints[0].id).toBe(id);
    expect(s.waypoints[0].voteEdgeId).toBeNull();
    expect(s.waypoints[0].address).toBeNull();
  });

  it("is a no-op out of range", () => {
    const s = sel([0, 0], [2, 2]);
    expect(updateAt(s, 9, ll(1, 1))).toBe(s);
  });
});

describe("removeAt (rebalancing)", () => {
  it("removing the start promotes the next point to start", () => {
    let s = sel([0, 0], [3, 3]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "m"); // [0,1,3]
    const r = removeAt(s, 0);
    expect(r.waypoints.map((w) => w.coords)).toEqual([ll(1, 1), ll(3, 3)]);
  });

  it("removing the end promotes the prior point to end", () => {
    let s = sel([0, 0], [3, 3]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "m"); // [0,1,3]
    const r = removeAt(s, 2);
    expect(r.waypoints.map((w) => w.coords)).toEqual([ll(0, 0), ll(1, 1)]);
  });

  it("removing the lone start empties the selection", () => {
    const s = setStart(EMPTY_SELECTION, { coords: ll(0, 0) }, counter());
    expect(removeAt(s, 0).waypoints).toHaveLength(0);
  });

  it("a 2-point route minus one collapses to a single point (no end)", () => {
    const s = sel([0, 0], [3, 3]);
    const r = removeAt(s, 1);
    expect(r.waypoints).toHaveLength(1);
    expect(r.waypoints[0].coords).toEqual(ll(0, 0));
  });
});

describe("clearWaypoints / setVoteType / identity", () => {
  it("clearWaypoints keeps voteType, drops points, returns same ref when already empty", () => {
    const s = setVoteType(EMPTY_SELECTION, "Bike lane");
    expect(clearWaypoints(s)).toBe(s); // already empty → no churn
    const withPts = sel([0, 0], [1, 1]);
    expect(clearWaypoints(withPts).waypoints).toHaveLength(0);
  });

  it("setVoteType returns the same ref when unchanged (avoids re-render churn)", () => {
    const s = setVoteType(EMPTY_SELECTION, "X");
    expect(setVoteType(s, "X")).toBe(s);
    expect(setVoteType(s, "Y").voteType).toBe("Y");
  });
});

describe("fullIndexOf", () => {
  it("maps start/end/ghost addressing to full indices", () => {
    let s = sel([0, 0], [3, 3]);
    s = insertMid(s, 0, { coords: ll(1, 1) }, () => "a");
    s = insertMid(s, 1, { coords: ll(2, 2) }, () => "b"); // [start, a, b, end]
    expect(fullIndexOf(s, "start")).toBe(0);
    expect(fullIndexOf(s, "end")).toBe(3);
    expect(fullIndexOf(s, 0)).toBe(1); // ghost 0 → full 1
    expect(fullIndexOf(s, 1)).toBe(2); // ghost 1 → full 2
  });
});

describe("selectionPhase", () => {
  it("classifies the four states", () => {
    expect(selectionPhase(EMPTY_SELECTION, false)).toBe("empty");
    expect(selectionPhase(sel([0, 0]), false)).toBe("point");
    expect(selectionPhase(sel([0, 0], [1, 1]), false)).toBe("route");
    let mids = sel([0, 0], [3, 3]);
    mids = insertMid(mids, 0, { coords: ll(1, 1) }, () => "m");
    expect(selectionPhase(mids, false)).toBe("route-mids");
  });

  it("a station network is always 'point' even with multiple coords", () => {
    expect(selectionPhase(sel([0, 0], [1, 1]), true)).toBe("point");
  });
});
