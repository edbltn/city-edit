import { describe, it, expect } from "vitest";
import { buildTiles } from "./tiles";
import { GENERIC, openerFor } from "./phrasebook";
import type { MapConfig, MapVoteType } from "../map/runtime";

function mapWith(voteTypes: MapVoteType[]): MapConfig {
  return {
    slug: "test", name: "Test", cityId: "nyc", mode: "walkways", style: "walkways",
    symbol: "", allowSuggestions: true, requiresPasscode: false, voteTypes,
  };
}

const vt = (label: string, pointType: "route" | "point"): MapVoteType =>
  ({ label, pointType, icon: "walkways" });

describe("the wall comes from the map, not from this repo", () => {
  it("offers one slip per authored vote type", () => {
    const tiles = buildTiles(mapWith([vt("Add crosswalk", "route"), vt("Add bench", "point")]));
    const typed = tiles.filter((t) => t.voteType);
    expect(typed.map((t) => t.voteType)).toEqual(["Add crosswalk", "Add bench"]);
    expect(typed[0].text).toBe(openerFor("Add crosswalk"));
    expect(typed[0].pointType).toBe("route");
  });

  it("interleaves the two families so neither owns the top of the wall", () => {
    const tiles = buildTiles(mapWith([
      vt("Add bench", "point"), vt("Add tree", "point"), vt("Add bus shelter", "point"),
      vt("Add crosswalk", "route"), vt("Add bike lane", "route"),
    ]));
    expect(tiles.filter((t) => t.voteType).map((t) => t.pointType))
      .toEqual(["point", "route", "point", "route", "point"]);
  });

  it("shows a shared sentence once", () => {
    // "Add Citi Bike station" and "Add Citibike station" are the same complaint,
    // and two identical slips read as a rendering bug.
    const tiles = buildTiles(mapWith([
      vt("Add Citi Bike station", "point"), vt("Add Citibike station", "point"),
    ]));
    expect(tiles.filter((t) => t.voteType).length).toBe(1);
  });
});

describe("generic sentences", () => {
  it("ride alongside a map that has its own types", () => {
    const tiles = buildTiles(mapWith([vt("Add crosswalk", "route")]));
    const generic = tiles.filter((t) => !t.voteType);
    expect(generic.length).toBeGreaterThan(0);
    expect(generic.length).toBeLessThan(GENERIC.length);
    // A generic slip commits to no vote type and no kind — the flow asks for an
    // end anyway and lets it be skipped.
    expect(generic[0].pointType).toBeNull();
  });

  it("carry the whole wall for a map that authored none", () => {
    // nyc-ebike-charging authors no vote types at all; the wall must still work.
    const tiles = buildTiles(mapWith([]));
    expect(tiles.length).toBe(GENERIC.length);
    expect(tiles.every((t) => t.voteType === null)).toBe(true);
  });

  it("still produces a wall with no map at all", () => {
    expect(buildTiles(null).length).toBeGreaterThan(0);
  });
});

describe("size", () => {
  it("stays a wall rather than a scroll", () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      vt(`Add thing ${i}`, i % 2 ? "route" : "point"));
    expect(buildTiles(mapWith(many)).length).toBeLessThanOrEqual(32);
  });
});
