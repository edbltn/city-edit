import { useEffect, useCallback, memo } from "react";
import "./ErrorToast.css";

interface ErrorToastProps {
  message: string | null;
  onRetry?: () => void;
  onDismiss: () => void;
}

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

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        onDismiss();
      }
    },
    [onDismiss]
  );

  if (!message) return null;

  return (
    <div
      className="error-toast"
      role="alert"
      aria-live="assertive"
      onKeyDown={handleKeyDown}
    >
      <div className="error-toast-content">
        <span className="error-toast-icon">!</span>
        <span className="error-toast-message">{message}</span>
      </div>
      <div className="error-toast-actions">
        {onRetry && (
          <button
            className="error-toast-btn error-toast-btn-retry"
            onClick={onRetry}
          >
            Retry
          </button>
        )}
        <button
          className="error-toast-btn error-toast-btn-dismiss"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
});
