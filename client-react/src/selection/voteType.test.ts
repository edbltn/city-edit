import { describe, it, expect } from "vitest";
import { resolveEffectiveVoteType } from "./voteType";

const base = {
  validLabels: ["Add bike lane", "Widen sidewalk"],
  allowSuggestions: false,
  topLabelOnPath: null as string | null,
  themeDefault: "Add bike lane",
};

describe("resolveEffectiveVoteType", () => {
  it("keeps the requested label when it's in the map's set", () => {
    expect(resolveEffectiveVoteType({ ...base, requested: "Widen sidewalk" })).toBe("Widen sidewalk");
  });

  it("keeps any requested label on a suggestions-friendly map", () => {
    expect(
      resolveEffectiveVoteType({ ...base, allowSuggestions: true, requested: "Anything goes" })
    ).toBe("Anything goes");
  });

  it("falls back to the most-voted path label when the requested one is invalid", () => {
    expect(
      resolveEffectiveVoteType({
        ...base,
        requested: "Bogus from old link",
        topLabelOnPath: "Widen sidewalk",
      })
    ).toBe("Widen sidewalk");
  });

  it("ignores a path label that is itself invalid for the map", () => {
    expect(
      resolveEffectiveVoteType({
        ...base,
        requested: "Bogus",
        topLabelOnPath: "Also not in set",
      })
    ).toBe("Add bike lane"); // theme default
  });

  it("falls through to the theme default when nothing else is valid", () => {
    expect(resolveEffectiveVoteType({ ...base, requested: "" })).toBe("Add bike lane");
  });

  it("an empty requested label is never treated as valid", () => {
    expect(
      resolveEffectiveVoteType({ ...base, allowSuggestions: true, requested: "", themeDefault: "Default" })
    ).toBe("Default");
  });
});
