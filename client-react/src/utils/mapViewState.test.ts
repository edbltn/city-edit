import { describe, it, expect } from "vitest";
import { computeInitialMapView } from "./mapViewState";
import { CONFIG } from "../config";

const view = (search: string) => computeInitialMapView(new URLSearchParams(search));

describe("computeInitialMapView", () => {
  it("uses the default view with no params", () => {
    const v = view("");
    expect(v.lat).toBe(CONFIG.initialView.lat);
    expect(v.zoom).toBe(CONFIG.initialView.zoom);
  });

  it("honors explicit camera params", () => {
    expect(view("z=15&lat=40.5&lng=-73.9")).toEqual({ lat: 40.5, lng: -73.9, zoom: 15 });
  });

  it("centers on a single deep-linked waypoint at street zoom", () => {
    const v = view("w=40.869434,-73.826116&vt=Improve+sidewalk");
    expect(v.lat).toBeCloseTo(40.869434, 6);
    expect(v.lng).toBeCloseTo(-73.826116, 6);
    expect(v.zoom).toBe(17);
  });

  it("fits a multi-waypoint selection to its bounding box", () => {
    const v = view("w=40.700000,-74.000000;40.720000,-73.980000");
    expect(v.lat).toBeCloseTo(40.71, 6);
    expect(v.lng).toBeCloseTo(-73.99, 6);
    expect(v.zoom).toBeLessThan(17);
    expect(v.zoom).toBeGreaterThanOrEqual(13);
  });

  it("centers on legacy slat/slng deep links too", () => {
    const v = view("slat=40.75&slng=-73.99");
    expect(v.lat).toBeCloseTo(40.75, 6);
    expect(v.zoom).toBe(17);
  });

  it("explicit camera params beat the selection", () => {
    expect(view("z=12&lat=40.6&lng=-74.1&w=40.869434,-73.826116")).toEqual({
      lat: 40.6,
      lng: -74.1,
      zoom: 12,
    });
  });

  it("vt-only links keep the default view", () => {
    const v = view("vt=Improve+sidewalk");
    expect(v.lat).toBe(CONFIG.initialView.lat);
    expect(v.zoom).toBe(CONFIG.initialView.zoom);
  });

  it("waypoints with forced-corridor tokens still center", () => {
    const v = view("w=40.700000,-74.000000,fabc123;40.720000,-73.980000");
    expect(v.lat).toBeCloseTo(40.71, 6);
  });
});
