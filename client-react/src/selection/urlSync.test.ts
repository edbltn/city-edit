// ==========================================================================
// Deep links that exist in the world
// ==========================================================================
// Some of the URLs below are printed on paper — QR posters and stickers already
// stuck to poles — and cannot be reissued. These tests are the contract that
// they keep resolving: each one is parsed for its selection, framed by
// computeInitialMapView, and then normalised by the URL mirror, and all three
// steps have to agree.

import { describe, it, expect } from "vitest";
import { selectionFromParams } from "./serialize";
import { canonicalSearch, CONSUMED_PARAM_KEYS } from "./urlSync";
import { computeInitialMapView } from "../utils/mapViewState";

/** Resolve a URL the way the app does at boot: selection first, then camera. */
function boot(search: string, opts?: { stationNetwork?: boolean }) {
  const params = new URLSearchParams(search);
  const parsed = selectionFromParams(params, opts);
  return {
    points: parsed?.waypoints.map((w) => w.coords) ?? [],
    forced: parsed?.waypoints.map((w) => w.forcedProposalId) ?? [],
    voteType: parsed?.voteType ?? null,
    view: computeInitialMapView(params),
    /** What the address bar reads after the mirror's first pass. */
    normalised: new URLSearchParams(
      canonicalSearch(search, {
        waypoints: (parsed?.waypoints ?? []).map((w) => ({
          coords: w.coords,
          forcedCorridor: w.forcedProposalId ? { proposalId: w.forcedProposalId } : null,
        })),
        voteType: parsed?.voteType,
      })
    ),
  };
}

describe("legacy deep links (printed, cannot be reissued)", () => {
  it("resolves a legacy start-only QR poster link and centers on it", () => {
    const r = boot("slat=40.75&slng=-73.99&vt=Add%20bike%20lane&src=qr-poster");
    expect(r.points).toEqual([{ lat: 40.75, lng: -73.99 }]);
    expect(r.voteType).toBe("Add bike lane");
    expect(r.view.lat).toBeCloseTo(40.75, 6);
    expect(r.view.lng).toBeCloseTo(-73.99, 6);
    expect(r.view.zoom).toBe(17);
  });

  it("resolves a legacy start+end route link", () => {
    const r = boot("slat=40.70&slng=-74.01&elat=40.73&elng=-73.99&vt=Walk");
    expect(r.points).toEqual([
      { lat: 40.7, lng: -74.01 },
      { lat: 40.73, lng: -73.99 },
    ]);
    expect(r.voteType).toBe("Walk");
  });

  it("normalises a legacy link to the canonical w list without losing anything", () => {
    const r = boot("slat=40.70&slng=-74.01&elat=40.73&elng=-73.99&vt=Walk");
    expect(r.normalised.get("w")).toBe("40.700000,-74.010000;40.730000,-73.990000");
    expect(r.normalised.get("vt")).toBe("Walk");
    // Respelled, not doubled up.
    for (const k of ["slat", "slng", "elat", "elng"]) {
      expect(r.normalised.has(k)).toBe(false);
    }
  });

  it("keeps src through normalisation so the MAPLOAD label survives", () => {
    // sourceTag captures and strips ?src= itself; the mirror can run first, and
    // must not eat the tag before the beacon has read it.
    const r = boot("slat=40.75&slng=-73.99&src=qr-poster");
    expect(r.normalised.get("src")).toBe("qr-poster");
  });

  it("keeps the sticker's stk code and every unrelated param", () => {
    const r = boot("w=40.75,-73.99&vt=Add%20tree&src=stk-fix-this&stk=K4M9X&map=nyc-walkways&style=trees");
    expect(r.normalised.get("stk")).toBe("K4M9X");
    expect(r.normalised.get("src")).toBe("stk-fix-this");
    expect(r.normalised.get("map")).toBe("nyc-walkways");
    expect(r.normalised.get("style")).toBe("trees");
  });

  it("a legacy end-only link degrades to a single point rather than nothing", () => {
    const r = boot("elat=40.73&elng=-73.99");
    expect(r.points).toEqual([{ lat: 40.73, lng: -73.99 }]);
  });

  it("ignores legacy params when the canonical w is present", () => {
    const r = boot("w=41.0,-73.0&slat=40.7&slng=-74.0");
    expect(r.points).toEqual([{ lat: 41, lng: -73 }]);
  });
});

describe("current deep links", () => {
  it("resolves a sticker scan link: selection, camera, campaign tag", () => {
    const r = boot("w=40.712800,-74.006000&vt=Add%20tree&z=18&lat=40.71280&lng=-74.00600&src=stk-a&stk=K4M9X");
    expect(r.points).toEqual([{ lat: 40.7128, lng: -74.006 }]);
    expect(r.voteType).toBe("Add tree");
    // Explicit camera params win over the selection-derived framing.
    expect(r.view.zoom).toBe(18);
    expect(r.view.lat).toBeCloseTo(40.7128, 5);
  });

  it("drops the camera params once consumed, keeping the URL about the selection", () => {
    const r = boot("w=40.7128,-74.006&z=18&lat=40.71280&lng=-74.00600");
    expect(r.normalised.get("z")).toBeNull();
    expect(r.normalised.get("lat")).toBeNull();
    expect(r.normalised.get("lng")).toBeNull();
    expect(r.normalised.get("w")).toBe("40.712800,-74.006000");
  });

  it("frames a multi-waypoint link on the whole chain, not just the start", () => {
    const r = boot("w=40.70,-74.02;40.71,-74.00;40.72,-73.98");
    expect(r.points).toHaveLength(3);
    expect(r.view.lat).toBeCloseTo(40.71, 5);
    expect(r.view.lng).toBeCloseTo(-74.0, 5);
    expect(r.view.zoom).toBeLessThan(17); // zoomed out to fit the route
  });

  it("preserves a forced corridor through normalisation", () => {
    const r = boot("w=40.70,-74.02,fdeadbeef;40.72,-73.98");
    expect(r.forced).toEqual(["deadbeef", null]);
    expect(r.normalised.get("w")).toBe("40.700000,-74.020000,fdeadbeef;40.720000,-73.980000");
  });

  it("collapses a route link to one point on a station network", () => {
    const r = boot("w=40.70,-74.02;40.72,-73.98", { stationNetwork: true });
    expect(r.points).toEqual([{ lat: 40.7, lng: -74.02 }]);
  });

  it("leaves an empty selection with an empty query string", () => {
    expect(canonicalSearch("", { waypoints: [] })).toBe("");
    expect(canonicalSearch("z=15&lat=40.7&lng=-74", { waypoints: [] })).toBe("");
  });
});

describe("param vocabulary", () => {
  it("consumes exactly the selection + camera params", () => {
    expect([...CONSUMED_PARAM_KEYS].sort()).toEqual(
      ["elat", "elng", "lat", "lng", "slat", "slng", "vt", "w", "z"].sort()
    );
  });

  it("is idempotent — mirroring an already-canonical URL changes nothing", () => {
    const once = canonicalSearch("w=40.70,-74.02&map=x", {
      waypoints: [{ coords: { lat: 40.7, lng: -74.02 } }],
    });
    const twice = canonicalSearch(once, {
      waypoints: [{ coords: { lat: 40.7, lng: -74.02 } }],
    });
    expect(twice).toBe(once);
  });
});
