import { describe, it, expect } from "vitest";
import { CURATED, isCurated, openerFor } from "./phrasebook";
import { THEMES } from "../themes";

/** Every vote type the shipped presets can put on a wall — THEMES holds the
 *  three default vote sets the Propose-a-Map picker offers (Walkways, Bikes,
 *  Trees), which is the vocabulary every wall is built from now that there are
 *  no type-less sentences to fall back on. */
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
});

describe("the voice", () => {
  const all = Object.values(CURATED);

  it("leaves every sentence unfinished", () => {
    expect(all.filter((s) => !s.endsWith("…"))).toEqual([]);
  });

  it("keeps every sentence short enough for a phone", () => {
    expect(all.filter((s) => s.length > 78)).toEqual([]);
  });

  it("speaks in the first person, every line", () => {
    // The rule the wall lives or dies by: an impersonal caption ("Walking this
    // stretch is miserable") and a second-person instruction ("You cross this on
    // faith") both describe somebody else's street.
    expect(all.filter((s) => /\b(you|your|yours|yourself)\b/i.test(s))).toEqual([]);
    expect(all.filter((s) => !/\b(I|my|me|us|we|our)\b/i.test(s))).toEqual([]);
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
    expect(openerFor("Add ferry landing")).toBe("I wish there were a ferry landing here…");
    expect(openerFor("Add awning")).toBe("I wish there were an awning here…");
    // Labels that already carry their own article are left alone.
    expect(openerFor("Add a parklet")).toBe("I wish there were a parklet here…");
  });

  it("turns a Fix into a complaint", () => {
    expect(openerFor("Fix drainage grate"))
      .toBe("I've been waiting for someone to fix the drainage grate here…");
  });

  it("turns a volunteering label into an offer, without doubling the place", () => {
    expect(openerFor("Run a paint day along here")).toBe("I could run a paint day here…");
  });

  it("keeps a stranger's own words rather than inventing grammar", () => {
    expect(openerFor("Pigeon situation")).toBe("I'd say pigeon situation, right here…");
  });

  it("prefers the curated line whenever there is one", () => {
    expect(openerFor("Add bench")).toBe(CURATED["Add bench"]);
  });
});
