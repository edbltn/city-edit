import { describe, it, expect } from "vitest";
import {
  iconSrc,
  iconForLabel,
  mapHref,
  themeFromMap,
  type ThemeNavState,
} from "./themes";

describe("iconSrc", () => {
  it("returns the icon SVG path for a given icon name", () => {
    const path = iconSrc("bikes");
    expect(path).toBe("/icons/bikes.svg");
  });

  it("works with different icon names", () => {
    expect(iconSrc("trees")).toBe("/icons/trees.svg");
    expect(iconSrc("walkways")).toBe("/icons/walkways.svg");
    expect(iconSrc("safety")).toBe("/icons/safety.svg");
    expect(iconSrc("parks")).toBe("/icons/parks.svg");
  });

  it("handles icon names with hyphens", () => {
    expect(iconSrc("traffic-reduction")).toBe("/icons/traffic-reduction.svg");
    expect(iconSrc("public-space")).toBe("/icons/public-space.svg");
  });

  it("produces consistent paths", () => {
    const icon = "custom-icon";
    expect(iconSrc(icon)).toBe(iconSrc(icon));
  });
});

describe("iconForLabel", () => {
  it("returns the icon for a preset suggestion label", () => {
    const icon = iconForLabel("Improve bike lane");
    expect(icon).toBe("bikes");
  });

  it("finds icons from the bikepaths theme", () => {
    expect(iconForLabel("Add bike parking")).toBe("bikes");
    expect(iconForLabel("Add protected bike lane")).toBe("safety");
  });

  it("finds icons from the trees theme", () => {
    expect(iconForLabel("Add tree")).toBe("trees");
    expect(iconForLabel("Create a tree pit")).toBe("trees");
  });

  it("finds icons from the walkways theme", () => {
    expect(iconForLabel("Improve sidewalk")).toBe("walkways");
    expect(iconForLabel("Add crosswalk")).toBe("pedestrian-streets");
  });

  it("returns null for unknown labels", () => {
    expect(iconForLabel("Unknown custom label")).toBeNull();
    expect(iconForLabel("")).toBeNull();
  });

  it("is case-sensitive (exact label match required)", () => {
    expect(iconForLabel("improve bike lane")).toBeNull();
    expect(iconForLabel("Improve Bike Lane")).toBeNull();
  });
});

describe("mapHref", () => {
  it("returns a path for a map slug", () => {
    const href = mapHref("nyc-bikepaths");
    expect(href).toBe("/m/nyc-bikepaths");
  });

  it("includes zoom in query string when provided", () => {
    const href = mapHref("nyc-bikepaths", { zoom: 15 });
    expect(href).toContain("z=15");
    expect(href).toContain("/m/nyc-bikepaths?");
  });

  it("includes center coordinates when provided", () => {
    const state: ThemeNavState = { center: { lat: 40.7128, lng: -74.0060 } };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("lat=40.71280");
    expect(href).toContain("lng=-74.00600");
  });

  it("includes start point when provided", () => {
    const state: ThemeNavState = { start: { lat: 40.7000, lng: -74.0100 } };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("slat=40.70000");
    expect(href).toContain("slng=-74.01000");
  });

  it("includes end point when provided", () => {
    const state: ThemeNavState = { end: { lat: 40.7300, lng: -73.9900 } };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("elat=40.73000");
    expect(href).toContain("elng=-73.99000");
  });

  it("includes vote type when provided", () => {
    const state: ThemeNavState = { vt: "tree-planting" };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("vt=tree-planting");
  });

  it("combines multiple state properties in the query string", () => {
    const state: ThemeNavState = {
      zoom: 14,
      center: { lat: 40.7000, lng: -74.0000 },
      start: { lat: 40.6900, lng: -74.0100 },
      end: { lat: 40.7100, lng: -73.9900 },
      vt: "bike-lane",
    };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("z=14");
    expect(href).toContain("lat=40.70000");
    expect(href).toContain("lng=-74.00000");
    expect(href).toContain("slat=40.69000");
    expect(href).toContain("slng=-74.01000");
    expect(href).toContain("elat=40.71000");
    expect(href).toContain("elng=-73.99000");
    expect(href).toContain("vt=bike-lane");
  });

  it("handles null state gracefully (no query string)", () => {
    const href = mapHref("nyc-bikepaths", undefined);
    expect(href).toBe("/m/nyc-bikepaths");
  });

  it("truncates coordinates to 5 decimal places", () => {
    const state: ThemeNavState = { center: { lat: 40.712891234567, lng: -74.006012345 } };
    const href = mapHref("nyc-bikepaths", state);
    expect(href).toContain("lat=40.71289");
    expect(href).not.toContain("lat=40.712891");
  });
});

describe("themeFromMap", () => {
  it("builds a theme from a preset map (bikepaths)", () => {
    const map = {
      slug: "nyc-bikepaths",
      name: "NYC Bikepaths",
      subtitle: "Vote for better cycling",
      mode: "bikepaths",
      style: "bikepaths",
    };
    const theme = themeFromMap(map);
    expect(theme.id).toBe("bikepaths");
    expect(theme.name).toBe("NYC Bikepaths");
    expect(theme.tagline).toBe("Vote for better cycling"); // subtitle takes precedence
    expect(theme.mode).toBe("bikepaths");
    expect(theme.inputMode).toBe("both");
  });

  it("falls back to walkways theme defaults for unknown style", () => {
    const map = {
      slug: "my-custom-map",
      name: "Custom Map",
      mode: "custom-mode",
    };
    const theme = themeFromMap(map);
    expect(theme.id).toBe("my-custom-map");
    expect(theme.name).toBe("Custom Map");
    expect(theme.mode).toBe("custom-mode");
    expect(theme.locationLabel).toBe("Start"); // default
    expect(theme.symbol).toBe("mapping"); // default
  });

  it("uses custom vote types when provided", () => {
    const map = {
      slug: "test-map",
      name: "Test",
      voteTypes: [
        { label: "Custom Vote", icon: "custom-icon", pointType: "route" as const },
      ],
    };
    const theme = themeFromMap(map);
    expect(theme.suggestions).toHaveLength(1);
    expect(theme.suggestions[0].label).toBe("Custom Vote");
  });

  it("uses the first vote type's icon as symbol when no style symbol exists", () => {
    const map = {
      slug: "test",
      name: "Test",
      voteTypes: [
        { label: "Custom", icon: "star", pointType: "point" as const },
      ],
    };
    const theme = themeFromMap(map);
    expect(theme.symbol).toBe("star");
  });

  it("prefers subtitle over style tagline when both exist", () => {
    const map = {
      slug: "test",
      name: "Test",
      subtitle: "Custom subtitle",
      style: "bikepaths",
    };
    const theme = themeFromMap(map);
    expect(theme.tagline).toBe("Custom subtitle");
  });

  it("sets subdomain from the map", () => {
    const map = {
      slug: "test",
      name: "Test",
      subdomain: "bikepaths",
    };
    const theme = themeFromMap(map);
    expect(theme.subdomain).toBe("bikepaths");
  });
});
