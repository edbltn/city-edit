import { useCallback, useEffect, useMemo, useState } from "react";
import { useMap, useRoute } from "../../context";
import { pointTypeForLabel } from "../../themes";
import { fitPoints } from "../../utils/mapViewState";
import { dlog } from "../../utils/debugLog";
import { hasPendingSticker } from "../../sticker";
import { AddressSearch } from "../AddressSearch";
import { MapNotice } from "../MapNotice";
import { OpenerWall } from "./OpenerWall";
import { openerFor } from "../../onboarding/phrasebook";
import { buildTiles, type OpenerTile } from "../../onboarding/tiles";
import {
  endIsOptional, onboardingStep, requiresEnd, shouldOnboard,
  type OnboardingFacts,
} from "../../onboarding/state";
import { hasVisitedBefore, isSuppressed, suppress } from "../../onboarding/firstRun";
import { setOnboardingActive, useOnboardingRequests } from "../../onboarding/active";
import type { LatLng } from "../../types";
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
// The strip is a MapNotice: the app has exactly one place it speaks from, and a
// first-run coach is not special enough to earn a second. That also inherits the
// rule the notice was built for — the strip is transparent to the pointer and
// sits UNDER the map's cards, so a coach can never be why a proposal underneath
// it won't take a tap.
// ==========================================================================

/** How long the closing line stays up after the first vote lands. */
const DONE_LINGER_MS = 7000;

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
    start, end, routeData, splitDesirePaths, pointType, isCalculating,
    isCalculatingSplit, hasVoted, requestedVoteType, voteType,
    setVoteType, setStartPoint, setEndPoint, setActiveTool, castVote,
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
  // Which step's address field is open, rather than a bare boolean: the field
  // closes by itself when the flow moves under it, with no effect to keep in
  // step with the step.
  const [searchingFor, setSearchingFor] = useState<"start" | "end" | null>(null);
  const [finished, setFinished] = useState(false);
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

  // The cast control's own condition, spelled the same way the topbar spells it:
  // a routed path, a split path, or a single point in point mode.
  const canCast =
    !!routeData || splitDesirePaths.length > 0 ||
    (pointType === "point" && !!start.coords);
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

  // A cast finishes the flow. Suppress — this and the dismissal are the only two
  // writes — then let the closing line stand for a few seconds and leave.
  useEffect(() => {
    if (!active || step !== "done") return;
    dlog("onboard", "first vote landed — flow complete");
    suppress();
    const t = setTimeout(() => setFinished(true), DONE_LINGER_MS);
    return () => clearTimeout(t);
  }, [active, step]);

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

  function handleAddress(coords: LatLng, address: string) {
    if (step === "end") {
      setEndPoint(coords, address);
      setActiveTool("start");
      fitPoints([start.coords, coords]);
    } else {
      setStartPoint(coords, address);
      fitPoints([coords, end.coords]);
    }
    setSearchingFor(null);
  }

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

  return (
    <MapNotice tone="notice" anchor aria-label="Getting started">
      <div className="onboard-coach map-notice-body">
        <div className="onboard-lines">
          {sentence && <p className="onboard-sentence">{sentence}</p>}
          <p className="onboard-ask">{ask(step, facts, voteType, calculating)}</p>
        </div>

        {searchingFor === step ? (
          <div className="onboard-search">
            <AddressSearch
              onSelect={handleAddress}
              onClose={() => setSearchingFor(null)}
              placeholder={step === "end" ? "Search the other end…" : "Search an address…"}
              accentColor={step === "end" ? "var(--color-end)" : "var(--color-start)"}
            />
          </div>
        ) : (
          <div className="onboard-actions map-notice-actions">
            {(step === "start" || step === "end") && (
              <button
                type="button"
                className="map-notice-btn"
                onClick={() => setSearchingFor(step === "end" ? "end" : "start")}
              >
                Search instead
              </button>
            )}
            {step === "end" && optionalEnd && (
              <button
                type="button"
                className="map-notice-btn"
                onClick={() => setEndSkipped(true)}
              >
                It's one spot
              </button>
            )}
            {step === "cast" && (
              <>
                <button
                  type="button"
                  className="map-notice-btn onboard-cast onboard-cast-down"
                  onClick={() => castVote(-1)}
                  disabled={!canCast}
                  title={`Vote against ${voteType} here`}
                >
                  − Not here
                </button>
                <button
                  type="button"
                  className="map-notice-btn map-notice-btn-primary onboard-cast onboard-cast-up"
                  onClick={() => castVote(1)}
                  disabled={!canCast}
                  title={`Vote for ${voteType} here`}
                >
                  + Yes, here
                </button>
              </>
            )}
          </div>
        )}

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
    case "start":
      if (facts.isStationNetwork) return "Tap the station on the map.";
      if (requiresEnd(facts)) return "Tap the map where that stretch starts.";
      return "Tap the map on the spot you mean.";
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
      // resolve one. Say so, and say where to change it: this is the only place
      // the app tells a newcomer that the Proposal picker is theirs to move.
      const tail = facts.pointType === null ? " — or pick another above." : "";
      return facts.hasEnd
        ? `“${effectiveVoteType}”, all along this stretch?${tail}`
        : `“${effectiveVoteType}”, right here?${tail}`;
    }
    case "done":
      return "Cast. It's on the map — and it's yours to take back any time.";
    default:
      return "";
  }
}
