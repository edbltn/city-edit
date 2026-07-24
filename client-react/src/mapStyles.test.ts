import { describe, it, expect } from "vitest";
import {
  getMapStyle,
  maplibreRasterTiles,
  heatGradientCss,
  MAP_STYLES,
  DEFAULT_MAP_STYLE,
  type MapStyle,
} from "./mapStyles";

describe("getMapStyle", () => {
  it("returns the named style for a known id", () => {
    const style = getMapStyle("bikepaths");
    expect(style.id).toBe("bikepaths");
    expect(style.basemap).toBe("dark");
    expect(style.accent).toBe("#2BE06B");
  });

  it("returns the named style for walkways", () => {
    const style = getMapStyle("walkways");
    expect(style.id).toBe("walkways");
    expect(style.basemap).toBe("dark");
    expect(style.accent).toBe("#E0A23A");
  });

  it("returns the named style for trees", () => {
    const style = getMapStyle("trees");
    expect(style.id).toBe("trees");
    expect(style.basemap).toBe("light");
    expect(style.accent).toBe("#5FA052");
  });

  it("falls back to default for unknown style id", () => {
    const style = getMapStyle("nonexistent");
    expect(style.id).toBe("default");
    expect(style).toEqual(DEFAULT_MAP_STYLE);
  });

  it("falls back to default for empty string", () => {
    const style = getMapStyle("");
    expect(style).toEqual(DEFAULT_MAP_STYLE);
  });
});

describe("maplibreRasterTiles", () => {
  it("expands tile URL template with all 4 subdomains", () => {
    const style = MAP_STYLES.bikepaths;
    const tiles = maplibreRasterTiles(style);
    expect(tiles).toHaveLength(4);
    expect(tiles[0]).toContain("a.basemaps.cartocdn.com");
    expect(tiles[1]).toContain("b.basemaps.cartocdn.com");
    expect(tiles[2]).toContain("c.basemaps.cartocdn.com");
    expect(tiles[3]).toContain("d.basemaps.cartocdn.com");
  });

  it("replaces {s} subdomain and {r} retina placeholders", () => {
    const style = MAP_STYLES.walkways;
    const tiles = maplibreRasterTiles(style);
    tiles.forEach((url) => {
      expect(url).not.toContain("{s}");
      expect(url).not.toContain("{r}");
      expect(url).toContain("@2x");
    });
  });

  it("produces valid URLs for the dark basemap", () => {
    const style = MAP_STYLES.bikepaths;
    const tiles = maplibreRasterTiles(style);
    tiles.forEach((url) => {
      expect(url).toMatch(/^https:\/\/[a-d]\.basemaps\.cartocdn\.com\/dark_nolabels\/\{z\}\/\{x\}\/\{y\}@2x\.png$/);
    });
  });

  it("produces valid URLs for the light basemap", () => {
    const style = MAP_STYLES.trees;
    const tiles = maplibreRasterTiles(style);
    tiles.forEach((url) => {
      expect(url).toMatch(/^https:\/\/[a-d]\.basemaps\.cartocdn\.com\/light_nolabels\/\{z\}\/\{x\}\/\{y\}@2x\.png$/);
    });
  });
});

describe("heatGradientCss", () => {
  it("generates a valid linear-gradient CSS function", () => {
    const css = heatGradientCss(MAP_STYLES.bikepaths.heat);
    expect(css).toMatch(/^linear-gradient\(to right,/);
    expect(css).toContain("rgb(");
  });

  it("includes all four heat stops: halo, warm, hot, peak", () => {
    const heat = MAP_STYLES.bikepaths.heat;
    const css = heatGradientCss(heat);
    expect(css).toContain(heat.halo);
    expect(css).toContain(heat.warm);
    expect(css).toContain(heat.hot);
    expect(css).toContain(heat.peak);
  });

  it("includes all heat stops in the gradient", () => {
    const heat = MAP_STYLES.bikepaths.heat;
    const css = heatGradientCss(heat);
    // Should include halo, warm, hot, peak in the gradient
    expect(css).toContain(heat.halo);
    expect(css).toContain(heat.warm);
    expect(css).toContain(heat.hot);
    expect(css).toContain(heat.peak);
  });

  it("works with different heat ramps (bikepaths)", () => {
    const heat = MAP_STYLES.bikepaths.heat;
    const css = heatGradientCss(heat);
    // Verify the gradient includes all the right colors
    expect(css).toContain(heat.halo);
    expect(css).toContain(heat.warm);
    expect(css).toContain(heat.hot);
    expect(css).toContain(heat.peak);
  });

  it("works with different heat ramps (trees)", () => {
    const heat = MAP_STYLES.trees.heat;
    const css = heatGradientCss(heat);
    // Verify the gradient includes all the right colors
    expect(css).toContain(heat.halo);
    expect(css).toContain(heat.warm);
    expect(css).toContain(heat.hot);
    expect(css).toContain(heat.peak);
  });
});
