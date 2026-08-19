import { describe, it, expect } from "vitest";
import {
  endIsOptional, liveDuring, marksDuring, onboardingStep, requiresEnd, shouldOnboard,
  stepUsesTheMap, type OnboardingFacts,
} from "./state";

const BASE: OnboardingFacts = {
  picked: true,
  pointType: "point",
  isStationNetwork: false,
  hasStart: false,
  hasEnd: false,
  endSkipped: false,
  hasVoted: false,
};

const facts = (over: Partial<OnboardingFacts>): OnboardingFacts => ({ ...BASE, ...over });

describe("the step is a projection of the selection", () => {
  it("opens on the wall until a sentence is chosen", () => {
    expect(onboardingStep(facts({ picked: false }))).toBe("wall");
  });

  it("asks for a start, then offers the cast, for a point-kind sentence", () => {
    expect(onboardingStep(facts({}))).toBe("start");
    expect(onboardingStep(facts({ hasStart: true }))).toBe("cast");
  });

  it("finishes the moment the selection is cast", () => {
    expect(onboardingStep(facts({ hasStart: true, hasVoted: true }))).toBe("done");
    // Even mid-flow: somebody who casts from the topbar has finished the flow,
    // and the coach must not keep asking for the point they already voted on.
    expect(onboardingStep(facts({ picked: false, hasVoted: true }))).toBe("done");
  });
});

describe("a route-kind sentence must reach a corridor before it can cast", () => {
  const route = facts({ pointType: "route", hasStart: true });

  it("holds the cast back until both ends exist", () => {
    expect(requiresEnd(route)).toBe(true);
    expect(onboardingStep(route)).toBe("end");
    expect(onboardingStep({ ...route, hasEnd: true })).toBe("cast");
  });

  it("does not let the end be skipped", () => {
    // A route vote on one point can never become a route proposal, and casting
    // it would silently retarget: with a lone waypoint the selection's point
    // type is "point", so the effective vote type resolves away from the route
    // label the user chose.
    expect(endIsOptional(route)).toBe(false);
    expect(onboardingStep({ ...route, endSkipped: true })).toBe("end");
  });
});

describe("a label of unknown kind asks for an end but takes no for an answer", () => {
  const generic = facts({ pointType: null, hasStart: true });

  it("asks", () => {
    expect(endIsOptional(generic)).toBe(true);
    expect(onboardingStep(generic)).toBe("end");
  });

  it("moves on when declined", () => {
    expect(onboardingStep({ ...generic, endSkipped: true })).toBe("cast");
  });

  it("moves on when the end is placed anyway", () => {
    expect(onboardingStep({ ...generic, hasEnd: true })).toBe("cast");
  });
});

describe("station maps vote on one fixed point", () => {
  // An ebikes map's types may be authored as either kind; it never routes.
  const station = facts({ isStationNetwork: true, pointType: "route", hasStart: true });

  it("never asks for an end, whatever kind the type was authored as", () => {
    expect(requiresEnd(station)).toBe(false);
    expect(endIsOptional(station)).toBe(false);
    expect(onboardingStep(station)).toBe("cast");
  });
});

describe("who sees the flow", () => {
  const trigger = {
    hasVisitedBefore: false,
    suppressed: false,
    pendingSticker: false,
    dismissed: false,
  };

  it("runs on the first map this counting identity has ever opened", () => {
    expect(shouldOnboard(trigger)).toBe(true);
  });

  it("stays away from anyone who has opened a map before — voted or not", () => {
    // The point of the key change: a lurker who reads maps and never votes is a
    // returning visitor, and the old question said they were a newcomer.
    expect(shouldOnboard({ ...trigger, hasVisitedBefore: true })).toBe(false);
  });

  it("stays away from a browser that waved it off", () => {
    expect(shouldOnboard({ ...trigger, suppressed: true })).toBe(false);
  });

  it("runs for an unlinked sticker scan even for a veteran voter", () => {
    // That visit's first vote is what pins the code for everyone who scans it
    // afterwards — and the veteran is the person most likely to pin it right.
    expect(
      shouldOnboard({ ...trigger, hasVisitedBefore: true, suppressed: true, pendingSticker: true })
    ).toBe(true);
  });

  it("takes a dismissal as final, sticker or not", () => {
    expect(shouldOnboard({ ...trigger, pendingSticker: true, dismissed: true })).toBe(false);
  });
});

describe("what each step leaves live", () => {
  // The rule, stated once: anything the step is asking the person to USE stays
  // bright and live, and everything else is inert — not merely faded.
  it("keeps the map on the steps that ask for a tap on it", () => {
    expect(liveDuring("start")).toContain("map");
    expect(liveDuring("end")).toContain("map");
    expect(stepUsesTheMap("start")).toBe(true);
    expect(stepUsesTheMap("end")).toBe(true);
  });

  it("keeps the map at the cast step too — the route may still be wrong", () => {
    // Both points are placed, so the map has nothing left to ASK for; it is kept
    // because somebody who wants the corridor down the other side of the street
    // is doing the right thing at exactly the right moment. This is the one
    // entry that is an exception to "the step's subject is in the bar".
    expect(stepUsesTheMap("cast")).toBe(true);
  });

  it("lights the step's own control, and nothing else in the bar", () => {
    expect(liveDuring("start")).toEqual(["map", "start"]);
    expect(liveDuring("end")).toEqual(["map", "end"]);
  });

  it("lights the picker as well as the −/+ pair at the cast step", () => {
    // The second mark points at the picker and says the vote is still yours to
    // change; inviting somebody to use a control that is locked is worse than
    // not inviting them.
    expect(liveDuring("cast")).toEqual(["map", "cast", "votetype"]);
  });

  it("locks nothing on the steps that put up no mark", () => {
    // `wall` covers the screen itself and `done` is a report. Neither shows a
    // mark, and the lock is a function of the marks that are showing, so these
    // lists are never read — asserted anyway, because a stray "map" here would
    // be the difference between no scrim and a scrim over everything.
    expect(marksDuring("wall")).toEqual([]);
    expect(marksDuring("done")).toEqual([]);
    expect(stepUsesTheMap("wall")).toBe(false);
    expect(stepUsesTheMap("done")).toBe(false);
  });
});

describe("the marks a step puts up", () => {
  it("is one per step, except the cast step, which is two", () => {
    expect(marksDuring("start")).toEqual(["start"]);
    expect(marksDuring("end")).toEqual(["end"]);
    expect(marksDuring("cast")).toEqual(["cast", "votetype"]);
  });

  it("names only controls the same step leaves live", () => {
    // A mark on a locked control would be an arrow pointing at something that
    // does not respond. The two lists are written separately, so this is the
    // invariant that keeps them honest.
    for (const step of ["wall", "start", "end", "cast", "done"] as const) {
      const live = liveDuring(step);
      for (const mark of marksDuring(step)) {
        expect(live).toContain(mark);
      }
    }
  });
});
