// ==========================================================================
// StickerGate — the screen a scanned sticker lands on
// ==========================================================================
// This is the whole app for a fraction of a second, or for one tap.
//
// The sticker in the visitor's hand knows what it wants (a vote type) but not
// where it is. Only two things can happen here, and the good one is invisible:
//
//   resolved    Someone already voted from this sticker, so it is pinned to a
//               place. Redirect straight there. The visitor never sees this
//               component — the splash is still up when the navigation fires.
//   unresolved  We do not know which pole this is on. Ask once, for the one
//               thing only this person can supply.
//
// The ask is deliberately bare. Someone standing at a kerb with their phone out
// has already been persuaded — by the sticker — and every extra sentence here is
// a chance to change their mind. So: the line they just read, echoed back so the
// screen obviously belongs to the object in their hand, what the tap will do,
// and the button. Nothing about the project, no sign-up, no explainer.
//
// Refusing is a first-class outcome, not an error. The map still opens with the
// vote type ready and the pin left to them; the sticker simply stays unresolved
// for the next person, which is the correct thing to record — see link.ts.

import { useCallback, useEffect, useState } from "react";
import {
  fetchStickerTarget, type StickerTarget,
} from "../../sticker/api";
import { stickerFallbackUrl, stickerMapUrl } from "../../sticker/link";
import { dlog } from "../../utils/debugLog";
import "./StickerGate.css";

/** Long enough for a cold GPS fix at street level, short enough that a phone
 *  with location switched off does not look like a hung page. */
const FIX_TIMEOUT_MS = 12_000;

type Phase =
  | { at: "loading" }
  | { at: "ask"; target: StickerTarget }
  | { at: "locating"; target: StickerTarget }
  | { at: "denied"; target: StickerTarget; reason: string }
  | { at: "unknown" };

function refusalReason(err: GeolocationPositionError): string {
  if (err.code === err.PERMISSION_DENIED) return "Location permission was declined.";
  if (err.code === err.TIMEOUT) return "Couldn't get a fix in time.";
  return "This device couldn't work out where it is.";
}

export function StickerGate({ code }: { code: string }) {
  const [phase, setPhase] = useState<Phase>({ at: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const target = await fetchStickerTarget(code);
      if (cancelled) return;
      if (!target) {
        setPhase({ at: "unknown" });
        return;
      }
      // Already pinned: this visitor is not the one who has to answer the
      // question, so do not put the question in front of them.
      if (target.location) {
        dlog("sticker", `${code} resolved → straight to the vote`, target.location);
        window.location.replace(stickerMapUrl(target, target.location));
        return;
      }
      dlog("sticker", `${code} unresolved → asking for location`);
      setPhase({ at: "ask", target });
    })();
    return () => { cancelled = true; };
  }, [code]);

  const share = useCallback((target: StickerTarget) => {
    if (!navigator.geolocation) {
      setPhase({ at: "denied", target, reason: "This browser has no location support." });
      return;
    }
    setPhase({ at: "locating", target });
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const point = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        dlog("sticker", `${target.code} located`, point, `±${Math.round(pos.coords.accuracy)}m`);
        // `pending`: the code rides on to the map so the first vote can pin the
        // sticker here for everyone who scans it after this.
        window.location.replace(stickerMapUrl(target, point, { pending: true }));
      },
      (err) => {
        dlog("sticker", `${target.code} location refused:`, err.code, err.message);
        setPhase({ at: "denied", target, reason: refusalReason(err) });
      },
      { enableHighAccuracy: true, timeout: FIX_TIMEOUT_MS, maximumAge: 0 },
    );
  }, []);

  if (phase.at === "loading") {
    return (
      <div className="sticker-gate">
        <span className="spinner sticker-gate-spinner" aria-hidden />
      </div>
    );
  }

  if (phase.at === "unknown") {
    return (
      <div className="sticker-gate">
        <div className="sticker-gate-panel">
          <p className="sticker-gate-note">
            That code isn't one of ours — or it hasn't been switched on yet.
          </p>
          <a className="sticker-gate-secondary" href="/m/nyc-proposals">
            Open the map anyway
          </a>
        </div>
      </div>
    );
  }

  const { target } = phase;

  return (
    <div className="sticker-gate">
      <div className="sticker-gate-panel">
        {/* The line off the sticker, set the way the sticker sets it. */}
        <h1 className="sticker-gate-line">{target.headline.toUpperCase()}</h1>
        <div className="sticker-gate-rule" aria-hidden />

        {phase.at === "denied" ? (
          <>
            <p className="sticker-gate-note">{phase.reason}</p>
            <p className="sticker-gate-note">
              Open the map and drop the pin on the spot yourself.
            </p>
            <a className="sticker-gate-button" href={stickerFallbackUrl(target)}>
              Open the map
            </a>
            <button
              type="button"
              className="sticker-gate-secondary"
              onClick={() => share(target)}
            >
              Try location again
            </button>
          </>
        ) : (
          <>
            <p className="sticker-gate-note">
              Share your location and this becomes a vote for{" "}
              <b>{target.voteType.toLowerCase()}</b> right where you're standing.
            </p>
            <button
              type="button"
              className="sticker-gate-button"
              onClick={() => share(target)}
              disabled={phase.at === "locating"}
            >
              {phase.at === "locating" ? "Finding you…" : "Share my location"}
            </button>
            <a className="sticker-gate-secondary" href={stickerFallbackUrl(target)}>
              Skip — I'll place it myself
            </a>
          </>
        )}
      </div>
    </div>
  );
}
