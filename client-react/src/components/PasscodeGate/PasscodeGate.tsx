import { useEffect, useState } from "react";
import { CONFIG } from "../../config";
import { setPasscodeToken } from "../../map/runtime";
import "./PasscodeGate.css";

/**
 * Listens for the "map-passcode-required" event (dispatched when a vote returns
 * 401) and prompts for the map's passcode. On success the token is stored and
 * the user can retry their vote.
 */
export function PasscodeGate() {
  const [slug, setSlug] = useState<string | null>(null);
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onRequired = (e: Event) => {
      const detail = (e as CustomEvent).detail as { slug?: string };
      setSlug(detail?.slug || null);
      setError(null);
      setPasscode("");
    };
    window.addEventListener("map-passcode-required", onRequired);
    return () => window.removeEventListener("map-passcode-required", onRequired);
  }, []);

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
        setError(data.error || "Incorrect passcode");
        setBusy(false);
        return;
      }
      setPasscodeToken(slug, data.token);
      setSlug(null);
    } catch {
      setError("Network error");
      setBusy(false);
    }
  };

  return (
    <div className="passcode-overlay" onClick={() => setSlug(null)}>
      <div className="passcode-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Passcode required</h3>
        <p>This map needs a passcode before you can vote.</p>
        <input
          type="password"
          autoFocus
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="Enter passcode"
        />
        {error && <div className="passcode-error">{error}</div>}
        <div className="passcode-actions">
          <button className="passcode-cancel" onClick={() => setSlug(null)}>Cancel</button>
          <button className="passcode-submit" disabled={busy || !passcode.trim()} onClick={submit}>
            {busy ? "Checking…" : "Unlock"}
          </button>
        </div>
      </div>
    </div>
  );
}
