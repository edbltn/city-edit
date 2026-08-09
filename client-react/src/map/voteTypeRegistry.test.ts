import { describe, it, expect, beforeEach } from "vitest";
import {
  buildVoteTypeLegend,
  publishVoteTypeNets,
  registerVoteTypeLabel,
  getVoteTypeRegistryVersion,
  subscribeVoteTypeRegistry,
  resetVoteTypeRegistry,
} from "./voteTypeRegistry";
import type { MapConfig } from "./runtime";

const cfg = (over: Partial<MapConfig> = {}): MapConfig => ({
  slug: "test-map",
  name: "Test",
  cityId: "nyc",
  mode: "walk",
  style: "walkways",
  symbol: "",
  allowSuggestions: true,
  requiresPasscode: false,
  voteTypes: [
    { label: "Add a bike lane", icon: "", pointType: "route" },
    { label: "Plant a tree", icon: "", pointType: "point" },
  ],
  ...over,
});

describe("voteTypeRegistry", () => {
  beforeEach(() => {
    resetVoteTypeRegistry();
  });

  it("lists every authored type even at zero votes", () => {
    const rows = buildVoteTypeLegend(cfg(), "point");
    expect(rows.map((r) => r.label)).toEqual(["Plant a tree", "Add a bike lane"]);
    expect(rows.every((r) => r.net === 0 && !r.onMap)).toBe(true);
  });

  it("puts castable rows first, keeping discovery order within each half", () => {
    const routeMode = buildVoteTypeLegend(cfg(), "route");
    expect(routeMode.map((r) => [r.label, r.castable])).toEqual([
      ["Add a bike lane", true],
      ["Plant a tree", false],
    ]);
  });

  it("stamps live net support onto the rows", () => {
    publishVoteTypeNets(new Map([["Plant a tree", 412], ["Add a bike lane", -30]]));
    const byLabel = Object.fromEntries(
      buildVoteTypeLegend(cfg(), "point").map((r) => [r.label, r])
    );
    expect(byLabel["Plant a tree"]).toMatchObject({ net: 412, onMap: true });
    // Negative nets survive: a counter-voted type paints cold, and the legend
    // has to say so.
    expect(byLabel["Add a bike lane"]).toMatchObject({ net: -30, onMap: true });
  });

  it("lists an unauthored label once it carries votes", () => {
    publishVoteTypeNets(new Map([["Ban cars here", 7]]));
    const rows = buildVoteTypeLegend(cfg(), "point");
    expect(rows.map((r) => r.label)).toContain("Ban cars here");
  });

  it("does NOT list a voted-once search type with no live votes", () => {
    // searchVoteTypes is the server's load-time snapshot; without a live net
    // the type isn't drawn, so it stays out of the legend (it remains findable
    // by search — see VoteTypeSelector's searchOnlyRows).
    const rows = buildVoteTypeLegend(
      cfg({ searchVoteTypes: [{ label: "Old idea", pointType: "point" }] }), "point");
    expect(rows.map((r) => r.label)).not.toContain("Old idea");
  });

  it("lists a label cast this session immediately, before any votes land", () => {
    registerVoteTypeLabel("Fresh idea");
    const rows = buildVoteTypeLegend(cfg(), "point");
    expect(rows.map((r) => r.label)).toContain("Fresh idea");
  });

  it("makes every type castable on a station network, whatever its kind", () => {
    const rows = buildVoteTypeLegend(cfg({ network: "ebikes" }), "point");
    expect(rows.every((r) => r.castable)).toBe(true);
  });

  it("keeps unknown-kind labels castable in both modes", () => {
    publishVoteTypeNets(new Map([["Legacy label", 3]]));
    for (const mode of ["route", "point"] as const) {
      const row = buildVoteTypeLegend(cfg(), mode).find((r) => r.label === "Legacy label");
      expect(row?.castable).toBe(true);
    }
  });

  it("notifies subscribers only when the nets actually move", () => {
    let calls = 0;
    const stop = subscribeVoteTypeRegistry(() => { calls++; });
    publishVoteTypeNets(new Map([["A", 1]]));
    expect(calls).toBe(1);
    publishVoteTypeNets(new Map([["A", 1]]));
    expect(calls).toBe(1);
    publishVoteTypeNets(new Map([["A", 2]]));
    expect(calls).toBe(2);
    stop();
  });

  it("registers a label once", () => {
    const before = getVoteTypeRegistryVersion();
    registerVoteTypeLabel("Once");
    registerVoteTypeLabel("Once");
    expect(getVoteTypeRegistryVersion()).toBe(before + 1);
  });
});
