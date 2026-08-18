import { describe, it, expect } from "vitest";
import {
  endIsOptional, onboardingStep, requiresEnd, shouldOnboard,
  type OnboardingFacts,
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

describe("a generic sentence asks for an end but takes no for an answer", () => {
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
    hasVotedBefore: false,
    suppressed: false,
    pendingSticker: false,
    dismissed: false,
  };

  it("runs for somebody with no vote to their counting identity", () => {
    expect(shouldOnboard(trigger)).toBe(true);
  });

  it("stays away from anyone who has voted before", () => {
    expect(shouldOnboard({ ...trigger, hasVotedBefore: true })).toBe(false);
  });

  it("stays away from a browser that waved it off", () => {
    expect(shouldOnboard({ ...trigger, suppressed: true })).toBe(false);
  });

  it("runs for an unlinked sticker scan even for a veteran voter", () => {
    // That visit's first vote is what pins the code for everyone who scans it
    // afterwards — and the veteran is the person most likely to pin it right.
    expect(
      shouldOnboard({ ...trigger, hasVotedBefore: true, suppressed: true, pendingSticker: true })
    ).toBe(true);
  });

  it("takes a dismissal as final, sticker or not", () => {
    expect(shouldOnboard({ ...trigger, pendingSticker: true, dismissed: true })).toBe(false);
  });
});
