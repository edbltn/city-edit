import { useCallback, useState } from "react";
import { getCurrentMap } from "../../map/runtime";
import { MapNotice } from "../MapNotice";
import { useOnboardingActive } from "../../onboarding/active";

// ==========================================================================
// Event banner — a one-off promo strip for an upcoming City Edit meetup.
//
// Self-contained on purpose: retiring the campaign is `rm -r EventBanner/`
// plus the two lines that mount it (components/index.ts, App.tsx). Everything
// the campaign knows — copy, link, audience, dismissal key — lives here.
//
// Dismissal is DURABLE (localStorage, not sessionStorage like the vote-type
// filter): a promo you've already waved off should stay gone across visits,
// and the key carries CAMPAIGN_ID so the next event starts from a clean slate
// instead of inheriting this one's dismissals.
// ==========================================================================

/** Bump when the banner advertises a different event — it re-arms dismissals. */
const CAMPAIGN_ID = "tactical-urbanism-adventure-1";

const EVENT_URL = "https://luma.com/3mvlme31";

/** City ids (MapConfig.cityId) this event is relevant to — it's an NYC walk. */
const AUDIENCE_CITIES = new Set(["nyc"]);

/**
 * The banner sunsets itself the moment the event is over, so nobody is invited
 * to a walk that already happened if the deploy that removes this component
 * lags behind the date. Midnight ending Aug 22 2026, New York time (EDT, -04:00)
 * — the offset is explicit because the cutoff is the event's local midnight, not
 * the visitor's.
 */
const EVENT_ENDS_AT = Date.parse("2026-08-23T00:00:00-04:00");

const STORAGE_KEY = `cityedit_banner_dismissed:${CAMPAIGN_ID}`;

function wasDismissed(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false; // private mode: show it, just don't remember the dismissal
  }
}

function rememberDismissal(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    /* ignore */
  }
}

export function EventBanner() {
  const [dismissed, setDismissed] = useState(wasDismissed);
  // A first-timer's coach owns the bottom slot while it is up. This banner is
  // durably dismissible and the event is a week out, so it waits for their
  // second visit rather than sharing the strip with "tap the map to start".
  const onboarding = useOnboardingActive();

  const inAudience = AUDIENCE_CITIES.has(getCurrentMap()?.cityId ?? "");
  const visible =
    !dismissed && !onboarding && inAudience && Date.now() < EVENT_ENDS_AT;

  const dismiss = useCallback(() => {
    rememberDismissal();
    setDismissed(true);
  }, []);

  // Deliberately no Escape-to-dismiss: unlike the error toast this dismissal is
  // permanent, and Escape is already the app's "close the modal / drop the
  // selection" key — one stray press would retire the banner for good.
  if (!visible) return null;

  return (
    <MapNotice tone="notice" anchor aria-label="Upcoming event">
      <span className="map-notice-message">
        Join us for our first Tactical Urbanism Adventure on Aug. 22!
      </span>
      <a
        className="map-notice-link"
        href={EVENT_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        RSVP
      </a>
      <button
        className="map-notice-close"
        onClick={dismiss}
        aria-label="Dismiss event announcement"
        title="Dismiss"
      >
        ×
      </button>
    </MapNotice>
  );
}
