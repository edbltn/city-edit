// ==========================================================================
// Where a first-timer is in the flow — derived, never stored
// ==========================================================================
// The canonical Selection already knows everything about where somebody is: how
// many waypoints they have placed, which vote type they asked for, whether the
// current target is already voted. So the onboarding step is a PROJECTION of
// that, exactly like `selectionPhase` and `pointType` are. Nothing here is
// state; every input is read from RouteContext on the render it is used.
//
// This matters more than it looks. The obvious way to build a first-run flow is
// a little state machine of its own — step 1, step 2, step 3 — and the moment it
// exists it can disagree with the map: the user drops a second pin from the
// legend, or hits the browser back button (this app has real selection history),
// or opens a shared link, and the coach is still saying "tap the map to start"
// over a finished route. Deriving costs one function and cannot drift.
//
// Two flags are genuinely the flow's own, and neither carries any truth the
// selection also holds:
//   · `picked`     — a wall tile was chosen. Every tile names a vote type, so
//                    this is itself derived: the label is in the Selection, and
//                    in the URL.
//   · `endSkipped` — the user declined an end that was only worth asking for.
//                    "Stop asking", not "there is no end": place one anyway and
//                    the route appears, because the selection is still the truth.
// ==========================================================================

/** The step the coach is on. `wall` is the tiled opener screen. */
export type OnboardingStep = "wall" | "start" | "end" | "cast" | "done";

export interface OnboardingFacts {
  /** A sentence has been chosen (⇒ the selection carries its vote-type label). */
  picked: boolean;
  /** The chosen type's kind, or null when nothing on the map, the map's own set
   *  or the presets knows which kind that label is. */
  pointType: "route" | "point" | null;
  /** The map votes on fixed stations (ebikes): one point, never a route. */
  isStationNetwork: boolean;
  hasStart: boolean;
  hasEnd: boolean;
  /** The optional end was declined (generic sentences only). */
  endSkipped: boolean;
  /** This selection is already cast in the direction the control would apply. */
  hasVoted: boolean;
}

/**
 * Whether this flow must collect an end point before it may offer a cast.
 *
 * Route-kind vote types are the reason this exists. A route vote on a single
 * point is not a small version of a corridor — it is a vote that can never
 * become a route proposal, and worse, casting one silently retargets: with a
 * lone waypoint the selection's point type is "point", so the effective vote
 * type resolves AWAY from the route label the user chose and lands on the map's
 * default point type instead. Offering the cast button early would record a vote
 * for something the user never picked.
 */
export function requiresEnd(facts: OnboardingFacts): boolean {
  if (facts.isStationNetwork) return false;
  return facts.pointType === "route";
}

/** Whether an end point is worth asking for but fine to skip — a label of
 *  unknown kind, which might be a corridor and might be a spot. */
export function endIsOptional(facts: OnboardingFacts): boolean {
  return !facts.isStationNetwork && facts.pointType === null;
}

export function onboardingStep(facts: OnboardingFacts): OnboardingStep {
  if (facts.hasVoted) return "done";
  if (!facts.picked) return "wall";
  if (!facts.hasStart) return "start";
  if (!facts.hasEnd && (requiresEnd(facts) || (endIsOptional(facts) && !facts.endSkipped))) {
    return "end";
  }
  return "cast";
}

// ── Who sees this at all ───────────────────────────────────────────────────

export interface TriggerFacts {
  /** Server's answer for this visitor's COUNTING identity: had they opened a map
   *  before this one? See firstRun.ts for why it is not a localStorage flag, and
   *  why the question is opens rather than votes. */
  hasVisitedBefore: boolean;
  /** A local "don't show me again" mark. Only ever suppresses. */
  suppressed: boolean;
  /** This visit arrived from a scan of a sticker that nobody has voted from yet
   *  (`?stk=` — sticker/pending.ts). */
  pendingSticker: boolean;
  /** The visitor dismissed the flow during this visit. */
  dismissed: boolean;
}

/**
 * Two triggers, one rule each:
 *
 *   MAP OPEN    Nobody at this counting identity has opened a map before this
 *               one, and this browser hasn't been told to stop asking. It fires
 *               on the open itself, for any map — not from behind a menu. What
 *               makes it once ever rather than once per map is the server's
 *               record of the open (firstRun.ts); the local suppressant is
 *               written only once the visitor has DECIDED — cast a first vote,
 *               or asked to be taken to the map — so a wall that was shown and
 *               ignored does not count as having been onboarded.
 *
 *   UNLINKED QR A scan of a sticker that is still unbound. That visit is being
 *               asked to do something extra — its first vote is what pins the
 *               sticker for everyone who scans it afterwards — and it arrives
 *               with the sentence already chosen, so the coach is two steps.
 *               It runs even for a veteran voter, which is the point: the
 *               veteran is the person most likely to bind it correctly.
 */
export function shouldOnboard(facts: TriggerFacts): boolean {
  if (facts.dismissed) return false;
  if (facts.pendingSticker) return true;
  return !facts.hasVisitedBefore && !facts.suppressed;
}
