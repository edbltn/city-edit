import { describe, it, expect } from "vitest";
import type { Selection } from "./types";
import { deriveStart, deriveEnd, deriveMids, deriveMidIds } from "./selectors";

const wp = (lat: number, lng: number, id: string, extra: Partial<{ voteEdgeId: number; address: string }> = {}) => ({
  coords: { lat, lng },
  id,
  ...extra,
});

const empty: Selection = { waypoints: [], voteType: "" };
const point: Selection = { waypoints: [wp(1, 1, "a", { voteEdgeId: 5, address: "Home" })], voteType: "" };
const route: Selection = { waypoints: [wp(1, 1, "a"), wp(2, 2, "b")], voteType: "" };
const mids: Selection = {
  waypoints: [wp(1, 1, "a"), wp(1.5, 1.5, "m1"), wp(1.7, 1.7, "m2"), wp(2, 2, "b")],
  voteType: "",
};

describe("deriveStart", () => {
  it("is empty for an empty selection", () => {
    expect(deriveStart(empty).coords).toBeNull();
  });
  it("carries coords, address, voteEdgeId of waypoint 0", () => {
    const s = deriveStart(point);
    expect(s.coords).toEqual({ lat: 1, lng: 1 });
    expect(s.address).toBe("Home");
    expect(s.voteEdgeId).toBe(5);
  });
});

describe("deriveEnd", () => {
  it("is empty for a lone point (no end until 2+ waypoints)", () => {
    expect(deriveEnd(point).coords).toBeNull();
  });
  it("is the last waypoint for a route", () => {
    expect(deriveEnd(route).coords).toEqual({ lat: 2, lng: 2 });
    expect(deriveEnd(mids).coords).toEqual({ lat: 2, lng: 2 });
  });
});

describe("deriveMids / deriveMidIds", () => {
  it("are empty without interior points", () => {
    expect(deriveMids(route)).toEqual([]);
    expect(deriveMidIds(point)).toEqual([]);
  });
  it("return the interior coords and their ids in order", () => {
    expect(deriveMids(mids)).toEqual([
      { lat: 1.5, lng: 1.5 },
      { lat: 1.7, lng: 1.7 },
    ]);
    expect(deriveMidIds(mids)).toEqual(["m1", "m2"]);
  });
});
