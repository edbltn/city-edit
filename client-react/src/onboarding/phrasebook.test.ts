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

  it("leaves every sentence open", () => {
    // Usually a trailing "…", finished by pointing at the map. A line that is
    // already a whole thought ("There is too much litter in this area") may stop
    // without one — but nothing closes with a full stop, which is the shape of a
    // caption rather than something somebody said.
    expect(all.filter((s) => /[.!?]$/.test(s))).toEqual([]);
  });

  it("keeps every sentence short enough for a phone", () => {
    expect(all.filter((s) => s.length > 78)).toEqual([]);
  });

  it("never volunteers the reader for a job", () => {
    // THE rule. A tile names what is wrong, never a shift the reader is being
    // signed up for: "I'd put flowers where this bed is bare dirt…" reads as an
    // offer of labour, "This unused patch of land needs flowers…" is the same
    // vote type as a need. Only the labour verbs are banned in the "I'd …"
    // opening — "I'd ride this route if it had a lane…" is a use of the thing
    // being asked for, which is a need and stays.
    const offer =
      /^I(?:'d| would| could| can)\s+(chalk|paint|repaint|put|plant|water|weed|shovel|sweep|clear|tend|adopt|install|host|start|run|organi[sz]e|lead|swap|help|look after|give|send|write|grow|build|make)\b/i;
    expect(all.filter((s) => offer.test(s))).toEqual([]);
  });

  it("never speaks to the reader about their own street", () => {
    // A second-person instruction ("You cross this on faith") describes somebody
    // else's street back at them.
    expect(all.filter((s) => /\b(you|your|yours|yourself)\b/i.test(s))).toEqual([]);
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

  it("turns a volunteering label into a need, without doubling the place", () => {
    // Not "I could run a paint day here…": the derived tier obeys the same
    // no-offer-of-labour rule the curated one does.
    expect(openerFor("Run a paint day along here"))
      .toBe("Somebody needs to run a paint day here…");
  });

  it("keeps a stranger's own words rather than inventing grammar", () => {
    expect(openerFor("Pigeon situation")).toBe("I'd say pigeon situation, right here…");
  });

  it("prefers the curated line whenever there is one", () => {
    expect(openerFor("Add bench")).toBe(CURATED["Add bench"]);
  });
});
