import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentMap } from "../../map/runtime";
import "./EventBanner.css";

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
  const ref = useRef<HTMLElement | null>(null);

  const inAudience = AUDIENCE_CITIES.has(getCurrentMap()?.cityId ?? "");
  const visible = !dismissed && inAudience && Date.now() < EVENT_ENDS_AT;

  const dismiss = useCallback(() => {
    rememberDismissal();
    setDismissed(true);
  }, []);

  // The error toast shares this bottom-centre slot, so publish our height as a
  // CSS variable and let the toast stack on top of us (see ErrorToast.css)
  // rather than land underneath. Measured, not hardcoded: the strip is one line
  // on desktop and wraps to two or three on a phone.
  useEffect(() => {
    const el = ref.current;
    if (!visible || !el) return;
    const root = document.documentElement;
    const publish = () => {
      root.style.setProperty("--event-banner-offset", `${el.offsetHeight + 12}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(el);
    return () => {
      observer.disconnect();
      root.style.removeProperty("--event-banner-offset");
    };
  }, [visible]);

  // Deliberately no Escape-to-dismiss: unlike the error toast this dismissal is
  // permanent, and Escape is already the app's "close the modal / drop the
  // selection" key — one stray press would retire the banner for good.
  if (!visible) return null;

  return (
    <aside className="event-banner" ref={ref} aria-label="Upcoming event">
      <span className="event-banner-text">
        Join us for our first Tactical Urbanism Adventure on Aug. 22!
      </span>
      <a
        className="event-banner-cta"
        href={EVENT_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        RSVP
      </a>
      <button
        className="event-banner-close"
        onClick={dismiss}
        aria-label="Dismiss event announcement"
        title="Dismiss"
      >
        ×
      </button>
    </aside>
  );
}
