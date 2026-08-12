import { describe, it, expect } from "vitest";
import type { Selection } from "./types";
import { selectionToParams, selectionFromParams } from "./serialize";

const wp = (lat: number, lng: number, id: string) => ({ coords: { lat, lng }, id });
/** Expected ParsedWaypoint shape. */
const pw = (lat: number, lng: number, forcedProposalId: string | null = null) => ({
  coords: { lat, lng },
  forcedProposalId,
});

describe("selectionToParams", () => {
  it("encodes an ordered waypoint list + vote type at 6dp", () => {
    const sel: Selection = {
      waypoints: [wp(40.7128, -74.006, "a"), wp(40.73, -73.99, "b")],
      voteType: "Add bike lane",
    };
    const p = selectionToParams(sel);
    expect(p.get("w")).toBe("40.712800,-74.006000;40.730000,-73.990000");
    expect(p.get("vt")).toBe("Add bike lane");
  });

  it("omits w for an empty selection and omits vt when blank", () => {
    const p = selectionToParams({ waypoints: [], voteType: "" });
    expect(p.get("w")).toBeNull();
    expect(p.get("vt")).toBeNull();
  });

  it("round-trips coordinates (within 6dp) and order", () => {
    const sel: Selection = {
      waypoints: [wp(40.712812, -74.006015, "a"), wp(40.659999, -73.5, "b"), wp(41.0, -72.123456, "c")],
      voteType: "Trees",
    };
    const parsed = selectionFromParams(selectionToParams(sel));
    expect(parsed).not.toBeNull();
    expect(parsed!.voteType).toBe("Trees");
    expect(parsed!.waypoints).toHaveLength(3);
    parsed!.waypoints.forEach((c, i) => {
      expect(c.coords.lat).toBeCloseTo(sel.waypoints[i].coords.lat, 6);
      expect(c.coords.lng).toBeCloseTo(sel.waypoints[i].coords.lng, 6);
    });
  });

  it("encodes a forced-corridor marker and round-trips its proposal id", () => {
    const sel: Selection = {
      waypoints: [
        { ...wp(40.7, -74.0, "a"), forcedCorridor: { proposalId: "1a2b3c4d", edgeIds: [3, 4] } },
        wp(40.8, -73.9, "b"),
      ],
      voteType: "",
    };
    const p = selectionToParams(sel);
    expect(p.get("w")).toBe("40.700000,-74.000000,f1a2b3c4d;40.800000,-73.900000");
    const parsed = selectionFromParams(p);
    // Only the id survives the URL — the edge snapshot is session-only.
    expect(parsed!.waypoints).toEqual([
      pw(40.7, -74.0, "1a2b3c4d"),
      pw(40.8, -73.9),
    ]);
  });
});

// The whole point of the `w` list: arity IS the meaning. One pair is a point,
// two are start/end, three or more make the middle ones waypoints. Nothing in
// the URL says which — the reader derives it from the length.
describe("selection arity round-trips", () => {
  const cases: [string, number][] = [
    ["a single point", 1],
    ["a start/end route", 2],
    ["a route with one mid", 3],
    ["a route with several mids", 6],
  ];

  for (const [name, n] of cases) {
    it(`round-trips ${name} (${n} pair${n === 1 ? "" : "s"})`, () => {
      const sel: Selection = {
        waypoints: Array.from({ length: n }, (_, i) =>
          wp(40.7 + i / 1000, -74 + i / 1000, `w${i}`)
        ),
        voteType: "",
      };
      const parsed = selectionFromParams(selectionToParams(sel))!;
      expect(parsed.waypoints).toHaveLength(n);
      parsed.waypoints.forEach((c, i) => {
        expect(c.coords.lat).toBeCloseTo(sel.waypoints[i].coords.lat, 6);
        expect(c.coords.lng).toBeCloseTo(sel.waypoints[i].coords.lng, 6);
      });
      // Order is the encoding — reversing the list must not round-trip equal.
      const reversed = selectionToParams({ waypoints: [...sel.waypoints].reverse() });
      if (n > 1) expect(reversed.get("w")).not.toBe(selectionToParams(sel).get("w"));
    });
  }

  it("keeps mids distinguishable from endpoints only by position", () => {
    const p = new URLSearchParams("w=40.70,-74.00;40.71,-73.99;40.72,-73.98");
    const parsed = selectionFromParams(p)!;
    expect(parsed.waypoints[0].coords).toEqual({ lat: 40.7, lng: -74.0 });
    expect(parsed.waypoints[parsed.waypoints.length - 1].coords).toEqual({
      lat: 40.72,
      lng: -73.98,
    });
    expect(parsed.waypoints.slice(1, -1)).toHaveLength(1);
  });

  it("carries a forced corridor on any mid, not just the first waypoint", () => {
    const p = new URLSearchParams("w=40.7,-74;40.71,-73.99,fabc123;40.72,-73.98");
    expect(selectionFromParams(p)!.waypoints).toEqual([
      pw(40.7, -74),
      pw(40.71, -73.99, "abc123"),
      pw(40.72, -73.98),
    ]);
  });
});

describe("selectionFromParams", () => {
  it("returns null when there is nothing to restore", () => {
    expect(selectionFromParams(new URLSearchParams(""))).toBeNull();
    expect(selectionFromParams(new URLSearchParams("z=15&lat=40.7&lng=-74"))).toBeNull();
  });

  it("parses the w list and skips malformed pairs", () => {
    const p = new URLSearchParams("w=40.7,-74;garbage;41.0,-73.5,extra;42,-72");
    const parsed = selectionFromParams(p);
    // "garbage" (no comma) and "41.0,-73.5,extra" (third field isn't a valid
    // forced-corridor marker) are dropped.
    expect(parsed!.waypoints).toEqual([pw(40.7, -74), pw(42, -72)]);
  });

  it("accepts a forced-corridor marker as the third field", () => {
    const p = new URLSearchParams("w=40.7,-74,fdeadbeef;40.8,-73.9");
    expect(selectionFromParams(p)!.waypoints).toEqual([
      pw(40.7, -74, "deadbeef"),
      pw(40.8, -73.9),
    ]);
  });

  it("reads legacy slat/slng/elat/elng links", () => {
    const p = new URLSearchParams("slat=40.7&slng=-74.0&elat=40.8&elng=-73.9&vt=Walk");
    const parsed = selectionFromParams(p);
    expect(parsed!.waypoints).toEqual([pw(40.7, -74.0), pw(40.8, -73.9)]);
    expect(parsed!.voteType).toBe("Walk");
  });

  it("legacy start-only link yields a single point", () => {
    const p = new URLSearchParams("slat=40.7&slng=-74.0");
    expect(selectionFromParams(p)!.waypoints).toEqual([pw(40.7, -74.0)]);
  });

  it("prefers w over legacy params when both are present", () => {
    const p = new URLSearchParams("w=1,2&slat=9&slng=9");
    expect(selectionFromParams(p)!.waypoints).toEqual([pw(1, 2)]);
  });

  it("a vt-only link restores with no waypoints", () => {
    const parsed = selectionFromParams(new URLSearchParams("vt=Trees"));
    expect(parsed).toEqual({ waypoints: [], voteType: "Trees" });
  });

  it("collapses to a single start on a station network", () => {
    const p = new URLSearchParams("w=40.7,-74;40.8,-73.9;40.9,-73.8");
    const parsed = selectionFromParams(p, { stationNetwork: true });
    expect(parsed!.waypoints).toEqual([pw(40.7, -74)]);
  });
});
