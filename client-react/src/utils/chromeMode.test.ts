// ==========================================================================
// The floating-chrome seam
// ==========================================================================
// The experiment is meant to be backed out by one constant and compared by one
// URL param. These pin both halves of that promise: that the default is the
// only thing deciding the shipped look, and that a malformed override can never
// leave the app in some third state.

import { describe, it, expect } from "vitest";
import { chromeModeFrom, DEFAULT_CHROME } from "./chromeMode";

describe("chromeMode", () => {
  it("ships the default when the URL says nothing", () => {
    expect(chromeModeFrom("")).toBe(DEFAULT_CHROME);
    expect(chromeModeFrom("?map=nyc-proposals&tab=dbg")).toBe(DEFAULT_CHROME);
  });

  it("lets a URL ask for either treatment", () => {
    expect(chromeModeFrom("?chrome=banner")).toBe("banner");
    expect(chromeModeFrom("?chrome=float")).toBe("float");
  });

  it("keeps the override alongside the params it will share a URL with", () => {
    // The real comparison link carries a selection and a debug tab too.
    const search = "?w=40.76,-73.96&vt=12&tab=chrome&chrome=banner";
    expect(chromeModeFrom(search)).toBe("banner");
  });

  it("falls back rather than inventing a third mode", () => {
    for (const bad of ["?chrome=", "?chrome=Banner", "?chrome=none", "?chrome=1"]) {
      expect(chromeModeFrom(bad)).toBe(DEFAULT_CHROME);
    }
  });

  it("accepts an already-parsed URLSearchParams", () => {
    expect(chromeModeFrom(new URLSearchParams("chrome=banner"))).toBe("banner");
  });
});
