import { useEffect, useState } from "react";
import { CONFIG } from "../../config";
import { setPasscodeToken } from "../../map/runtime";
import { noAutofillProps } from "../../utils/noAutofill";
import "./PasscodeGate.css";

interface PasscodeGateProps {
  /**
   * Blocking mode: the map is locked at load and can't render until unlocked.
   * When set, the gate is always shown for this slug, has no Cancel/dismiss, and
   * calls `onUnlock` once the passcode checks out. Omit both props for the
   * event-driven mode (a vote hit a 401 mid-session — prompt, then let it retry).
   */
  slug?: string | null;
  onUnlock?: (slug: string) => void;
}

/**
 * Prompts for a map's passcode. Two modes:
 *  - blocking (props.slug set): gates the whole map at load time.
 *  - event-driven (no props): listens for "map-passcode-required", dispatched
 *    when a token expires mid-session, so the user can re-unlock and retry.
 */
export function PasscodeGate({ slug: blockingSlug, onUnlock }: PasscodeGateProps = {}) {
  const blocking = blockingSlug != null;
  const [eventSlug, setEventSlug] = useState<string | null>(null);
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (blocking) return; // blocking mode is driven by props, not events
    const onRequired = (e: Event) => {
      const detail = (e as CustomEvent).detail as { slug?: string };
      setEventSlug(detail?.slug || null);
      setError(null);
      setPasscode("");
    };
    window.addEventListener("map-passcode-required", onRequired);
    return () => window.removeEventListener("map-passcode-required", onRequired);
  }, [blocking]);

  const slug = blocking ? blockingSlug : eventSlug;
  if (!slug) return null;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${CONFIG.apiUrl}/maps/${encodeURIComponent(slug)}/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passcode: passcode.trim() }),
      });
      const data = await res.json();
      if (!res.ok || !data.token) {
        setError(data.error || "That passcode doesn't match — check it and try again.");
        setBusy(false);
        return;
      }
      setPasscodeToken(slug, data.token);
      if (blocking) {
        onUnlock?.(slug);
      } else {
        setEventSlug(null);
        setBusy(false);
      }
    } catch {
      setError("Network error — check your connection and try again.");
      setBusy(false);
    }
  };

  const dismiss = blocking ? undefined : () => setEventSlug(null);

  return (
    <div className="passcode-overlay" onClick={dismiss}>
      <div className="passcode-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Passcode required</h3>
        <p>
          {blocking
            ? "This map is private — enter its passcode to open it."
            : "This map needs a passcode before you can vote."}
        </p>
        <input
          type="text"
          name="map-passcode"
          className="passcode-input-masked"
          autoFocus
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Enter passcode"
          {...noAutofillProps}
        />
        {error && <div className="passcode-error">{error}</div>}
        <div className="passcode-actions">
          {!blocking && (
            <button className="passcode-cancel" onClick={dismiss}>Cancel</button>
          )}
          <button className="passcode-submit" disabled={busy || !passcode.trim()} onClick={submit}>
            {busy ? "Checking…" : "Unlock"}
          </button>
        </div>
      </div>
    </div>
  );
}
