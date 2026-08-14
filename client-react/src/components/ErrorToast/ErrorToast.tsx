import { useEffect, memo } from "react";
import { MapNotice } from "../MapNotice";

interface ErrorToastProps {
  message: string | null;
  onRetry?: () => void;
  onDismiss: () => void;
}

/**
 * The app's one error voice — "No route found to that point", "That's outside
 * this map". Layout, palette, stacking, and mobile behaviour come from
 * {@link MapNotice}; this owns the copy, the auto-dismiss, and the buttons.
 */
export const ErrorToast = memo(function ErrorToast({
  message,
  onRetry,
  onDismiss,
}: ErrorToastProps) {
  // Auto-dismiss after 10 seconds
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onDismiss, 10000);
    return () => clearTimeout(timer);
  }, [message, onDismiss]);

  // Escape dismisses from anywhere — the strip never takes focus (it's
  // pointer-transparent so it can't shadow the map), so a keydown handler on
  // the element itself would never fire. Window-level, and only while up.
  useEffect(() => {
    if (!message) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [message, onDismiss]);

  if (!message) return null;

  return (
    <MapNotice tone="alert" role="alert" aria-live="assertive">
      <div className="map-notice-body">
        <span className="map-notice-icon" aria-hidden>!</span>
        <span className="map-notice-message">{message}</span>
      </div>
      <div className="map-notice-actions">
        {onRetry && (
          <button className="map-notice-btn map-notice-btn-primary" onClick={onRetry}>
            Retry
          </button>
        )}
        <button className="map-notice-btn" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </MapNotice>
  );
});
