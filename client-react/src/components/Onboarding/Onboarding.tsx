import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useMap, useRoute } from "../../context";
import { pointTypeForLabel } from "../../themes";
import { dlog } from "../../utils/debugLog";
import { hasPendingSticker } from "../../sticker";
import { MapNotice } from "../MapNotice";
import { CoachCallout } from "./CoachCallout";
import { OpenerWall } from "./OpenerWall";
import { openerFor } from "../../onboarding/phrasebook";
import { buildTiles, type OpenerTile } from "../../onboarding/tiles";
import {
  endIsOptional, onboardingStep, requiresEnd, shouldOnboard,
  type OnboardingFacts,
} from "../../onboarding/state";
import { hasVisitedBefore, isSuppressed, suppress } from "../../onboarding/firstRun";
import { setOnboardingActive, useOnboardingRequests } from "../../onboarding/active";
import { useCoachAnchor, type CoachTarget } from "../../onboarding/coachAnchor";
import "./Onboarding.css";

// ==========================================================================
// First run — one sentence, one place, one vote
// ==========================================================================
// Two ways in, one flow:
//
//   · The map OPEN itself, the first time anybody at this counting identity has
//     opened one → the wall opens over the map, and picking a sentence starts
//     the flow at its first blank. Once ever, on any map, and never behind a
//     menu.
//
//     WHAT ENDS IT IS A DECISION, NOT A SIGHTING. The suppressant used to go
//     down the moment the wall was on screen; it now waits for the visitor to
//     do one of the two things that mean they are done with it — cast their
//     first vote, or say "just take me to the map". Seeing a wall and walking
//     away is not being onboarded, and marking it as such spent somebody's one
//     first run on a screen they never read. What that costs is spelled out at
//     the effect below, because a reload is the case it changes.
//   · The visit came from a scan of a sticker nobody has voted from yet → the
//     sentence was chosen by the object in their hand and travelled here in the
//     URL (`?vt=`), so the wall is skipped and the flow starts already picked.
//     Nothing in the sticker flow changed to make that work; it already writes
//     the vote type into the canonical selection, which is where this reads it.
//
// After that it is not a tour. There is no script being replayed over the app:
// each step is derived from the same Selection the map, the topbar and the URL
// are derived from (onboarding/state.ts), so anything the user does by other
// means — the legend, the back button, a shared link — moves the flow with it.
// The only bit of state that belongs to the flow is "the optional end was
// declined", and it holds no fact the selection also holds.
//
// WHERE THE COACH SPEAKS FROM, and why it moved. It used to be a MapNotice — the
// one bottom strip the app says everything from — on the reasoning that a
// first-run coach is not special enough to earn a second place to speak. That
// was right about the strip and wrong about the coach. A strip at the BOTTOM of
// the screen cannot point, so every step had to DESCRIBE its control instead of
// indicating it ("Tap the map where that stretch starts"), and two of the three
// controls it was describing are in a bar at the TOP of the screen that a
// first-timer has not looked at yet. The instruction and the thing it was about
// were as far apart as the screen allows.
//
// So the steps are now CALLOUTS ANCHORED TO THE REAL CONTROL, with an arrow tip
// on them, and the rest of the chrome greys out behind them so there is exactly
// one lit thing on the screen at a time (coachAnchor.ts finds the control,
// calloutPlacement.ts decides where the box goes, Onboarding.css does the
// greying). One thing kept the strip, and deliberately: the CLOSING LINE. It is
// not an instruction, it has no control to point at, and it is the one moment
// the flow is reporting rather than asking.
//
// Three consequences worth stating, because each is a thing the strip used to do:
//
//   · THE COACH NO LONGER CARRIES ITS OWN CONTROLS. It had an address search and
//     its own +/− pair, because a strip that cannot point has to bring the
//     controls to the reader. Pointing at the real ones is strictly better —
//     what somebody learns in the first run is then the app they will use — so
//     the copies are gone.
//   · IT STANDS DOWN WHILE SOMEBODY IS TYPING AN ADDRESS. Clicking the Start
//     field opens a search inside that same plate; a callout hanging off the
//     plate would be over the suggestion list. See `typing` in coachAnchor.ts.
//   · IT FALLS BACK TO THE STRIP WHEN THERE IS NOTHING TO POINT AT. The legend
//     is `display:none` in short landscape and absent entirely on a point-only
//     map. An arrow tip pointing at nothing is worse than the strip it replaced.
// ==========================================================================

/**
 * The flow, remounted whenever somebody asks for it back.
 *
 * "Start a sentence" (How it Works) has to undo everything this component has
 * concluded about the visit — the dismissal, the finished flag, the sentence
 * already chosen. Resetting six pieces of state from an effect is the version of
 * that which drifts; keying the mount on the request counter makes React do it,
 * and makes "reopened" mean exactly "a fresh flow".
 */
export function Onboarding() {
  const requests = useOnboardingRequests();
  return <OnboardingFlow key={requests} openedByHand={requests > 0} />;
}

function OnboardingFlow({ openedByHand }: { openedByHand: boolean }) {
  const map = useMap();
  const {
    start, end, isCalculating, isCalculatingSplit, hasVoted,
    requestedVoteType, voteType, setVoteType, setActiveTool,
  } = useRoute();

  // A scan of a still-unbound sticker. Read once: the code is spent by the first
  // cast, and this must not change under the flow half way through it.
  const pendingSticker = useMemo(() => hasPendingSticker(), []);

  // Every slip on the wall is one of the map's own vote types, so a map that
  // authors none has no wall to show (onboarding/tiles.ts). Checked BEFORE the
  // probe rather than after it, and that ordering is the point: the probe is
  // also what records the open, so probing on a map that cannot show a wall
  // would spend this visitor's one first run on nothing. A sticker scan is the
  // exception — it arrives with the sentence already chosen, so it never needed
  // the wall in the first place.
  const tiles = useMemo(() => buildTiles(map), [map]);
  const hasWall = tiles.length > 0;

  // null = the visitor probe hasn't answered yet. Nothing renders until it has:
  // a wall that appears and then vanishes is worse than one that arrives late.
  // Two answers need no probe at all — a browser already told to stop (the
  // suppressant is local and free, and the probe is a request on the load path),
  // and a flow the user just asked for by hand.
  //
  // Read ONCE, in a lazy initializer, because the flow writes the suppressant to
  // itself when it finishes: a live isSuppressed() here would see the write the
  // first cast makes and tear the closing line off the screen before it is read.
  const [wasSuppressed] = useState(() => !openedByHand && isSuppressed());
  const [visitedBefore, setVisitedBefore] = useState<boolean | null>(() => {
    if (openedByHand) return false;
    return isSuppressed() && !hasPendingSticker() ? true : null;
  });
  const [dismissed, setDismissed] = useState(false);
  const [endSkipped, setEndSkipped] = useState(false);
  const [finished, setFinished] = useState(false);
  // The flow has reported. See the one-way-door effect for what it is for.
  const [reachedDone, setReachedDone] = useState(false);
  // A hand-opened flow reopens the WALL even though a sentence (and possibly a
  // whole selection) is already in play, because choosing a different sentence
  // is the reason to ask for it. Cleared by the pick.
  const [forcedWall, setForcedWall] = useState(openedByHand);

  // A link that already carries a selection (a shared proposal, or a sticker that
  // knows where it is) has made the choice the wall exists to make. A lazy
  // initializer, so it is the state of the world on the FIRST render and nothing
  // later can move it.
  const [arrivedWithSelection] = useState(() => !!start.coords);

  useEffect(() => {
    if (visitedBefore !== null || !(hasWall || pendingSticker)) return;
    let cancelled = false;
    hasVisitedBefore().then((visited) => {
      if (!cancelled) setVisitedBefore(visited);
    });
    return () => { cancelled = true; };
  }, [visitedBefore, hasWall, pendingSticker]);

  const active =
    (hasWall || pendingSticker) &&
    visitedBefore !== null &&
    !finished &&
    shouldOnboard({
      hasVisitedBefore: visitedBefore,
      // A hand-opened flow overrules both keys: the person in front of the
      // screen has just said they want it.
      suppressed: wasSuppressed,
      pendingSticker,
      dismissed,
    });

  // ── The step, derived ─────────────────────────────────────────────────────
  const isStationNetwork = (map?.network ?? "streets") !== "streets";
  const pickedLabel = requestedVoteType || null;
  const pickedKind = pickedLabel
    ? pointTypeForLabel(pickedLabel, map?.voteTypes, map?.searchVoteTypes)
    : null;

  const facts: OnboardingFacts = {
    picked: !forcedWall && (!!pickedLabel || arrivedWithSelection),
    pointType: pickedKind,
    isStationNetwork,
    hasStart: !!start.coords,
    hasEnd: !!end.coords,
    endSkipped,
    hasVoted,
  };
  const step = onboardingStep(facts);

  // Which control this step is about. `wall` covers the screen and `done` is a
  // report rather than an instruction, so neither of them points at anything.
  const target: CoachTarget | null =
    step === "start" ? "start" : step === "end" ? "end" : step === "cast" ? "cast" : null;
  const anchor = useCoachAnchor(active ? target : null);
  // Anchored = there is a live control to hang the box off AND nobody is in the
  // middle of typing an address into it. Either failure falls back to the strip
  // (or, for typing, to silence) rather than to a tip pointing at nothing.
  const anchored = !!anchor.box && !anchor.typing;

  const calculating = isCalculating || isCalculatingSplit;

  // ── Effects that DRIVE the model (never shadow it) ────────────────────────

  // Publish, so the event banner can stand aside — one bottom-centre slot, and
  // a first-timer being asked to do one thing should not also be invited to a
  // walk next week (onboarding/active.ts).
  useEffect(() => {
    setOnboardingActive(active);
    return () => setOnboardingActive(false);
  }, [active]);

  // Reaching the end step arms the end tool, because on this map a bare tap is
  // a NEW start — sticky-start is the default and it wipes the route. Arming it
  // here is the same call the legend's End button makes; the tool disarms itself
  // when the point lands (useMapClick), so this fires once per arrival.
  useEffect(() => {
    if (active && step === "end") setActiveTool("end");
  }, [active, step, setActiveTool]);

  // NOTHING SUPPRESSES HERE. The mark goes down in exactly two places — the
  // `done` effect below (a vote was cast) and `handleDismiss` ("just take me to
  // the map", the coach's ×, Escape). Showing the wall writes nothing.
  //
  // What that means for a RELOAD, which is the case it changes:
  //
  //   · Reloaded with a sentence already picked → the flow resumes where it was
  //     and no wall appears. The pick is in the canonical selection, so it is in
  //     the URL (`?vt=`), so `picked` is true on the first render after the
  //     reload and the step derives to start/end/cast. This is the common case
  //     and it needs nothing from local storage at all.
  //   · Reloaded ON the wall, nothing picked → the wall comes back, which is now
  //     the intended answer: they have not decided anything yet. It comes back
  //     because the visitor probe's answer is cached in sessionStorage for the
  //     length of the visit (firstRun.ts, ANSWER_KEY) — the reload does not
  //     re-ask the server, which would say "visited" and hide it.
  //
  // The residual, stated rather than papered over: close the tab and come back
  // later and there is no wall, because /api/visitor recorded the first open and
  // sessionStorage is gone. Only the LOCAL mark moved; the server still counts a
  // sighting as a visit. Making the whole thing wait for a decision would mean
  // the endpoint recording on the cast instead of on the open, which is a
  // different change to a different layer.

  // Grey the chrome down to the one thing this step is about.
  //
  // A body attribute rather than a class on each control, and the greying rules
  // live in Onboarding.css keyed off it (`body[data-coach-focus="start"] …`).
  // That is the whole coupling: this flow never touches another component's DOM,
  // and the bar never learns that a coach exists beyond the three `data-coach`
  // attributes naming its own controls.
  //
  // It cannot be done with opacity on a CONTAINER, which is the obvious version
  // and the wrong one: opacity makes a group, and nothing inside a faded group
  // can be brought back to full — so fading `.topbar` and un-fading the Start
  // field inside it is not expressible. The rules therefore fade the bar's
  // controls INDIVIDUALLY, each one excluded by `:not([data-coach="…"])`.
  useEffect(() => {
    if (!active || !target || !anchored) return;
    document.body.setAttribute("data-coach-focus", target);
    return () => document.body.removeAttribute("data-coach-focus");
  }, [active, target, anchored]);

  // A cast finishes the flow. Suppress — this and the dismissal are the only two
  // writes — and then the closing line STAYS.
  //
  // It used to be pulled off the screen by a seven-second timer. Nothing about a
  // report needs a stopwatch, and the half of that line worth reading is the
  // half that arrives second: the first vote somebody ever casts is exactly when
  // they want to know it can be taken back, and seven seconds is not long enough
  // to place a vote, look at what it did to the map, and then read.
  //
  // What replaces the timer is the fact the line reports. The step is DERIVED
  // from `hasVoted` (onboarding/state.ts), so the closing line stands while — and
  // only while — the vote it describes is standing. Press the same button again
  // and `hasVoted` goes false on that press's own tick; move the selection
  // somewhere else and it goes false too. Either way the step leaves `done` and
  // the door below shuts. There is no reachable state in which this line is on
  // screen claiming a vote is on the map that isn't.
  useEffect(() => {
    if (!active || step !== "done") return;
    dlog("onboard", "first vote landed — flow complete");
    suppress();
  }, [active, step]);

  useEffect(() => {
    if (active && step === "done") setReachedDone(true);
  }, [active, step]);

  // THE ONE-WAY DOOR: leaving `done` ends the flow, it never walks back to an
  // instruction. Deriving the step is what makes this necessary as well as easy.
  // Take the vote back, or hit Clear, and the same derivation that correctly
  // retires the closing line would then hand back `cast` or `start` — so
  // somebody who has already voted gets told to tap the map again, with the
  // chrome greyed out around it and an arrow pointing at a control they have
  // used. The timer used to hide that by having usually fired first. Ending
  // instead is the honest version: they have voted, so the first run is over,
  // whatever they do to the vote afterwards.
  useEffect(() => {
    if (reachedDone && step !== "done") {
      dlog("onboard", "closing line retired — the vote it reported is gone");
      setFinished(true);
    }
  }, [reachedDone, step]);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handlePick = useCallback((tile: OpenerTile) => {
    dlog("onboard", `picked "${tile.text}"`, tile.voteType);
    setForcedWall(false);
    // Straight into the canonical selection — which also writes it to the URL,
    // so a reload or a shared link resumes at the same sentence. Every tile
    // carries a vote type now, so there is no other branch to take.
    setVoteType(tile.voteType);
  }, [setVoteType]);

  // The other half of "a decision, not a sighting": "just take me to the map",
  // the coach's ×, and Escape all land here, and all of them are the visitor
  // saying they are done being asked. That is worth marking; being shown the
  // wall is not.
  const handleDismiss = useCallback(() => {
    dlog("onboard", "dismissed — not offering it again");
    suppress();
    setDismissed(true);
  }, []);

  if (!active) return null;

  if (step === "wall") {
    // Nothing to put on it — a sticker scan onto a map that authors no vote
    // types. Never an empty wall.
    if (!hasWall) return null;
    // A selection that arrived in the URL counts as a choice already made, so
    // the wall never opens over somebody's shared link.
    return (
      <OpenerWall map={map} tiles={tiles} onPick={handlePick} onDismiss={handleDismiss} />
    );
  }

  // The sentence the flow is carrying: the phrasebook's line for whichever label
  // the selection holds — a tile pick, a sticker's vote type, or a deep link's
  // `vt=`. One lookup, so a sticker scan and a wall pick read identically.
  const sentence = pickedLabel ? openerFor(pickedLabel) : null;
  const optionalEnd = endIsOptional(facts);
  const line = ask(step, facts, voteType, calculating);

  // The one decision the chrome cannot make for us: an end point the flow was
  // only GUESSING might exist, on a label whose kind is unknown. Everything else
  // the coach used to carry — an address search, its own +/− pair — is now the
  // real control with an arrow pointing at it.
  const actions =
    step === "end" && optionalEnd ? (
      <button type="button" className="coach-callout-btn" onClick={() => setEndSkipped(true)}>
        It's one spot
      </button>
    ) : null;

  // Somebody is typing an address into the very field we would be pointing at.
  // Say nothing at all until they are done — see `typing` in coachAnchor.ts.
  if (target && anchor.typing) return null;

  if (target && anchor.box) {
    return (
      <>
        {/* Only the cast step takes the map away. Placing a point IS a map
            gesture, so greying the map out during `start` or `end` would grey
            out the thing being asked for; by `cast` the points are placed and
            the only move left is in the bar. The scrim sits one rung under the
            chrome, so it covers the map, its furniture and any open proposal
            card, while the bar stays above it and is greyed by the rules
            instead — which is what leaves the +/− the single lit thing. */}
        {step === "cast" && createPortal(
          <div className="coach-scrim" aria-hidden="true" />,
          document.body
        )}
        <CoachCallout
          anchor={anchor.box}
          sentence={sentence}
          ask={line}
          actions={actions}
          onDismiss={handleDismiss}
        />
      </>
    );
  }

  // No control to point at — a point-only map (no legend at all), short
  // landscape (the legend is display:none), or a cast that is not yet possible
  // so its plate is still reserved-but-hidden. And the closing line, which is a
  // report rather than an instruction and never had a control. The strip is
  // still the right answer for all of them.
  return (
    <MapNotice tone="notice" anchor aria-label="Getting started">
      <div className="onboard-coach map-notice-body">
        <div className="onboard-lines">
          {sentence && <p className="onboard-sentence">{sentence}</p>}
          <p className="onboard-ask">{line}</p>
        </div>

        {actions && <div className="onboard-actions map-notice-actions">{actions}</div>}

        <button
          type="button"
          className="map-notice-close onboard-close"
          onClick={handleDismiss}
          aria-label="Close getting started"
        >
          ×
        </button>
      </div>
    </MapNotice>
  );
}

/** What the coach is asking for right now. */
function ask(
  step: string,
  facts: OnboardingFacts,
  effectiveVoteType: string,
  calculating: boolean
): string {
  switch (step) {
    // The box now hangs off the field these fill in, so each line says what the
    // gesture is and lets the arrow say where the result lands. "This field
    // fills in" is the half a first-timer cannot guess: the map and the bar
    // look like separate things until one visibly answers the other.
    case "start":
      if (facts.isStationNetwork) return "Tap the station on the map. This fills in.";
      if (requiresEnd(facts)) return "Tap the map where that stretch starts. This fills in.";
      return "Tap the map on the spot you mean. This fills in.";
    case "end":
      if (requiresEnd(facts)) return "Now tap the far end of it.";
      return "Tap the far end too, if it's a stretch rather than a spot.";
    case "cast": {
      if (calculating) return "Working out the route…";
      // The label is quoted rather than folded into the sentence: vote types are
      // written as work orders ("Add crosswalk", "Fix curb cut") and no wording
      // makes every one of them read as a verb phrase in the middle of a
      // question. Quoted, it also shows exactly what the vote will be recorded
      // as — including when a label of unknown kind let the map's default
      // resolve one.
      //
      // The old line ended "— or pick another above", pointing at the vote-type
      // picker. That was written for a strip with nothing lit and nothing dimmed;
      // the picker is now one of the greyed controls, so an instruction to use it
      // contradicts the screen. The quoted label still says what will be recorded.
      const question = facts.hasEnd
        ? `“${effectiveVoteType}”, all along this stretch?`
        : `“${effectiveVoteType}”, right here?`;
      return `${question} + for yes, − for no.`;
    }
    case "done":
      // The second sentence is a PROMISE ABOUT A CONTROL, so it was checked
      // against the control before it was written. Pressing +/− when every
      // touched block already holds your vote in that direction plans
      // `targetDir: 0` — an unvote — and the button already says so in its own
      // title ("Remove your vote for"). See planBlockVote/voteButtonState in
      // utils/castVote.ts and their tests. "The button" is unambiguous here
      // because the coach was pointing at that exact pair one step ago.
      return "Cast! It's on the map. Hit the button again if you need to take back your vote.";
    default:
      return "";
  }
}
