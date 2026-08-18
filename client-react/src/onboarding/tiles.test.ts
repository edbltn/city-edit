import { describe, it, expect } from "vitest";
import { buildTiles } from "./tiles";
import { openerFor } from "./phrasebook";
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

describe("a map with no vote types of its own", () => {
  it("gets no wall at all, rather than a wall of sentences that cast nothing", () => {
    // nyc-ebike-charging authors zero vote types. The earlier design appended
    // twelve type-less openers here; every slip now has to be a vote type, so an
    // empty list is the honest answer and the flow checks for it before it opens
    // (components/Onboarding/Onboarding.tsx) rather than rendering an empty wall.
    expect(buildTiles(mapWith([]))).toEqual([]);
    expect(buildTiles(null)).toEqual([]);
  });

  it("shows a fully custom set anyway, overlapping no preset", () => {
    // The presets are the vocabulary the CURATED sentences are written for, not
    // a whitelist the wall is filtered by: a custom type is a real vote type,
    // and its slip casts a real vote. It gets a derived sentence, not silence.
    const tiles = buildTiles(mapWith([vt("Add ferry landing", "point")]));
    expect(tiles.map((t) => t.voteType)).toEqual(["Add ferry landing"]);
    expect(tiles[0].text).toBe(openerFor("Add ferry landing"));
  });
});

describe("size", () => {
  it("stays a wall rather than a scroll", () => {
    const many = Array.from({ length: 60 }, (_, i) =>
      vt(`Add thing ${i}`, i % 2 ? "route" : "point"));
    expect(buildTiles(mapWith(many)).length).toBeLessThanOrEqual(32);
  });
});
