// ==========================================================================
// "Is this your first time here?" — and why the obvious answer is wrong
// ==========================================================================
// The obvious implementation is a localStorage flag. It is also the one thing
// this app has already proved does not work.
//
// `voter_id` is a UUID in localStorage, and on 2026-08-13 prod logs recorded one
// iPhone producing SIX of them in three minutes across six page loads (see
// server/vote_identity.py, which exists because of that day). Private Browsing,
// "Block All Cookies", Safari's 7-day ITP eviction, and the canonical-subdomain
// hop (cityedit.org → walkways.cityedit.org crosses an ORIGIN, and localStorage
// is per-origin) each mint a fresh one. A flag written beside it fragments the
// same way — which means a first-run experience keyed on it would greet the same
// real person again, and again, and again. That is strictly worse than never
// onboarding anybody: the returning visitor is the one who most needs the app to
// get out of their way.
//
// So the key is the same one the vote system already counts people by:
//
//   THE KEY        vote_identity.counting_identity() — the IP hash, falling back
//                  to the device (server/vote_identity.py). Answered from
//                  Postgres, which is durable across Redis flushes and deploys.
//
// WHAT THE QUESTION ASKED OF THAT KEY IS — and what it used to be. This probe
// first shipped asking "has anybody at this counting identity ever cast a VOTE?"
// That is the wrong question, and backwards for the population it matters most
// to: every long-time lurker who reads maps and never votes was handed a welcome
// wall on their fiftieth visit. The wall is a WELCOME, so the question is
//
//   "HAS THIS PERSON EVER OPENED A MAP BEFORE?"
//
//   THE ENDPOINT   GET /api/visitor → { hasVisited, by }
//
// which the server answers and, in the same breath, records — see the endpoint's
// own note. Two things about that recording matter enough to say here, because
// either one silently breaks the whole feature:
//
//   · THE CURRENT LOAD MUST NOT COUNT. The server reads before it writes and
//     returns the answer from BEFORE this open, or nobody would ever see a wall:
//     every visitor's first probe would find their own visit already recorded.
//   · THE SUPPRESSANT IS WRITTEN WHEN THE WALL IS SHOWN, not when it is finished
//     with (components/Onboarding/Onboarding.tsx). A reload is a second open, and
//     without that write the visitor who reloads gets it twice.
//
// (The [MAPLOAD] beacon already reports map opens, and it is not what answers
// this: it is a log line for a monitoring metric, with nothing queryable behind
// it, and it is posted AFTER the load — so a first-run probe racing it would get
// a different answer depending on which request landed first.)
//
// localStorage still appears here, in exactly one role: a SUPPRESSANT. It can
// stop the flow, never start it. Losing it costs one network round-trip, never a
// repeat performance.
//
// What happens when the key changes — stated plainly, because it does change:
//
//   · Storage cleared, same network      → server says visited → suppressed.
//   · New network, storage intact        → local mark suppresses. Fixed.
//   · New network AND storage cleared    → they see it again. This is the
//                                          residual case and it is why the wall
//                                          is one tap to dismiss, never claims
//                                          "welcome back", and never blocks the
//                                          map underneath it.
//   · Shared NAT (office, household)     → a genuinely new colleague of someone
//                                          who has opened a map is NOT onboarded.
//                                          A false negative, deliberately
//                                          preferred to a false positive, and
//                                          recoverable: "What needs fixing" in
//                                          How it Works opens the wall on demand.
//
// The same trade the vote counter made, for the same reason and in the same
// direction. Under-showing a first run to somebody behind a busy IP is a much
// smaller lie than showing it forever to somebody whose phone won't hold a UUID.
// ==========================================================================

import { CONFIG } from "../config";
import { dlog } from "../utils/debugLog";
import { getVoterId } from "../utils/voterIdentity";

/** Written the moment the wall is shown, and again when the flow ends.
 *  Suppresses only. */
const SUPPRESS_KEY = "cityedit.onboarded";

/** The server's answer, held for the length of the visit. A scan is TWO document
 *  loads (/s/<code> then the map) and a map switch is a third; asking once per
 *  visit keeps the answer — and therefore the flow — consistent across them, and
 *  keeps one visit from recording itself as several opens. */
const ANSWER_KEY = "cityedit.visitedBefore";

function readStore(store: Storage | undefined, key: string): string | null {
  try {
    return store?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

function writeStore(store: Storage | undefined, key: string, value: string): void {
  try {
    store?.setItem(key, value);
  } catch {
    /* private mode — the session and the server still carry the answer */
  }
}

/** Has this browser been told not to show the flow again? */
export function isSuppressed(): boolean {
  if (typeof window === "undefined") return true;
  return (
    readStore(window.localStorage, SUPPRESS_KEY) === "1" ||
    readStore(window.sessionStorage, SUPPRESS_KEY) === "1"
  );
}

/** Stop offering the flow. Both stores: localStorage for the next visit,
 *  sessionStorage so a browser that refuses persistent storage still gets a
 *  quiet remainder of THIS visit. */
export function suppress(): void {
  if (typeof window === "undefined") return;
  writeStore(window.localStorage, SUPPRESS_KEY, "1");
  writeStore(window.sessionStorage, SUPPRESS_KEY, "1");
}

/** Clear the mark — "What needs fixing" from How it Works, and the tests. */
export function unsuppress(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(SUPPRESS_KEY);
    window.sessionStorage.removeItem(SUPPRESS_KEY);
    window.sessionStorage.removeItem(ANSWER_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Has the person behind this request opened a map before this one?
 *
 * Fails CLOSED — an unreachable or erroring API answers `true`. A blip must not
 * turn into a wall in front of every returning visitor on the site; the cost of
 * the opposite mistake is that a genuine newcomer sees the map without its
 * welcome, and the map is still the thing they came for.
 */
export async function hasVisitedBefore(): Promise<boolean> {
  const cached = readStore(window.sessionStorage, ANSWER_KEY);
  if (cached === "1" || cached === "0") return cached === "1";

  try {
    const url =
      `${CONFIG.apiUrl}/visitor?voter_id=${encodeURIComponent(getVoterId())}`;
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) return true;
    const body = (await res.json()) as { hasVisited?: boolean; by?: string | null };
    const visited = body.hasVisited !== false;
    dlog("onboard", `visitor: hasVisited=${visited} by=${body.by ?? "—"}`);
    writeStore(window.sessionStorage, ANSWER_KEY, visited ? "1" : "0");
    return visited;
  } catch {
    dlog("onboard", "visitor probe failed — assuming a returning visitor");
    return true;
  }
}
