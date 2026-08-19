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

// ── What each step leaves LIVE ─────────────────────────────────────────────

/**
 * A callout the coach can have on screen. These are exactly the `data-coach`
 * attributes TopBar puts on its own controls, because a mark is always ON one.
 *
 * `votetype` is the odd one out: it is never a STEP, it is the second mark the
 * cast step puts up beside the first.
 */
export type CoachMark = "start" | "end" | "cast" | "votetype";

/** Something the lock may leave live: a control by its `data-coach` name, or
 *  the map and everything drawn on it. */
export type CoachLive = "map" | CoachMark;

/**
 * The marks a step puts on screen — and so, because the lock is derived from
 * what is showing rather than announced by a flag, the list the lock is a
 * function of.
 *
 * `cast` is the only step with two, and they are dismissed INDEPENDENTLY: one ×
 * takes its own mark down and leaves the other standing. That is why "am I
 * locked" cannot be a boolean any step sets — it is "is anything still
 * showing", recomputed from the marks on every render. Three stale-visual-state
 * bugs in one week (a frozen hover, a stranded proposal highlight, a sticky
 * Start/End highlight) were all the same shape: a flag that got set and had a
 * path out that did not clear it. A derived lock has no such path.
 */
export function marksDuring(step: OnboardingStep): readonly CoachMark[] {
  switch (step) {
    case "start":
      return ["start"];
    case "end":
      return ["end"];
    case "cast":
      return ["cast", "votetype"];
    // The wall covers the screen itself and `done` is a report, not an ask.
    default:
      return [];
  }
}

/**
 * WHAT THIS STEP LEAVES LIVE. Everything else is inert while its marks are up —
 * not merely faded: faded-but-clickable and lit-but-dead are both lies, and the
 * second is the worse one.
 *
 * The rule this encodes has now been re-derived three times, twice as a bug:
 *
 *   ANYTHING THE CURRENT STEP ASKS THE PERSON TO USE STAYS BRIGHT AND LIVE.
 *   Locking is for what is genuinely not part of this step. If that leaves a
 *   step with almost nothing locked, that is the correct outcome.
 *
 * So it is A LIST PER STEP rather than a pile of special cases: each step says
 * what it is asking for and the lock is the complement, which means adding a
 * step is adding a line here rather than finding every place that exempts
 * something. Two of the four entries are not obvious:
 *
 *   · `start` and `end` keep the map because their instruction IS a tap on it.
 *   · `cast` keeps the map TOO, and that is the interesting one. Both points are
 *     placed, so the map has nothing left to ask for — but somebody looking at
 *     the corridor the app drew and wanting it down the other side of the
 *     street is doing the right thing at exactly the right moment. The step
 *     asks "is this what you meant?", and changing the answer is part of the
 *     question. It keeps the vote-type picker for the same reason: the second
 *     mark points at it and says the vote is still theirs to change.
 *
 * Dimming the control you are pointing at reads as "this is disabled" at the
 * moment it needs to read as "use this" — and the failure has a nastier second
 * half, because locking the map is a full-screen scrim. Take only the COLOUR
 * out of a scrim and you are left with an invisible sheet still swallowing
 * every click, which is strictly worse than a visible grey: the map then LOOKS
 * available and is not. So the scrim is never restyled per step, it is NOT
 * RENDERED unless this list omits the map.
 *
 * `wall` and `done` lock nothing, and need no entry to say so: neither puts a
 * mark on screen (`marksDuring`), and the lock is a function of the marks that
 * are showing, so their empty list is never read.
 */
export function liveDuring(step: OnboardingStep): readonly CoachLive[] {
  switch (step) {
    case "start":
      return ["map", "start"];
    case "end":
      return ["map", "end"];
    case "cast":
      return ["map", "cast", "votetype"];
    default:
      return [];
  }
}

/** Whether the scrim is rendered at all — the map half of `liveDuring`, named
 *  because that is the half with teeth. */
export function stepUsesTheMap(step: OnboardingStep): boolean {
  return liveDuring(step).includes("map");
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
