import { describe, it, expect } from "vitest";
import { CURATED, GENERIC, isCurated, openerFor } from "./phrasebook";
import { THEMES } from "../themes";

/** Every vote type the shipped presets can put on a wall. */
const PRESET_LABELS = [
  ...new Set(Object.values(THEMES).flatMap((t) => t.suggestions.map((s) => s.label))),
];

describe("coverage", () => {
  it("has a hand-written sentence for every preset vote type", () => {
    // The derivation below is a safety net for labels authored after this file,
    // not a substitute for writing one. If this fails, a preset gained a vote
    // type and its sentence is missing — write it, don't delete the test.
    const missing = PRESET_LABELS.filter((label) => !isCurated(label));
    expect(missing).toEqual([]);
  });

  it("offers enough generic sentences to carry a map that authored none", () => {
    expect(GENERIC.length).toBeGreaterThanOrEqual(8);
  });
});

describe("the voice", () => {
  const all = [...Object.values(CURATED), ...GENERIC];

  it("leaves every sentence unfinished", () => {
    expect(all.filter((s) => !s.endsWith("…"))).toEqual([]);
  });

  it("keeps every sentence short enough for a phone", () => {
    expect(all.filter((s) => s.length > 78)).toEqual([]);
  });

  it("never speaks in the imperative of a work order", () => {
    // "Add a bench here…" is the label with a full stop; the whole point is that
    // it should sound like a person, so no sentence may open with the label's
    // own verb.
    const workOrder = /^(Add|Fix|Improve|Widen|Install|Create|Repave) /;
    expect(all.filter((s) => workOrder.test(s))).toEqual([]);
  });
});

describe("derivation, for labels written after this file", () => {
  it("turns an Add into a wish, with the right article", () => {
    expect(openerFor("Add ferry landing")).toBe("There should be a ferry landing here…");
    expect(openerFor("Add awning")).toBe("There should be an awning here…");
    // Labels that already carry their own article are left alone.
    expect(openerFor("Add a parklet")).toBe("There should be a parklet here…");
  });

  it("turns a Fix into a complaint", () => {
    expect(openerFor("Fix drainage grate")).toBe("The drainage grate here is broken…");
  });

  it("turns a volunteering label into an offer, without doubling the place", () => {
    expect(openerFor("Run a paint day along here")).toBe("I could run a paint day here…");
  });

  it("keeps a stranger's own words rather than inventing grammar", () => {
    expect(openerFor("Pigeon situation")).toBe("Pigeon situation, right here…");
  });

  it("prefers the curated line whenever there is one", () => {
    expect(openerFor("Add bench")).toBe(CURATED["Add bench"]);
  });
});
