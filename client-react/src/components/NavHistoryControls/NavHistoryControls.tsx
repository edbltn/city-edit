import { useRoute } from "../../context";

/**
 * Back / forward through the selection history — bottom-left, mirroring the
 * zoom control opposite it. Stepping is driven by the in-app history stack in
 * RouteContext (not the browser history), so the buttons reflect exactly what
 * can be navigated. Hidden until there's any history to move through.
 */
export function NavHistoryControls() {
  const { stepBack, stepForward, canStepBack, canStepForward } = useRoute();

  if (!canStepBack && !canStepForward) return null;

  return (
    <div className="nav-history" role="group" aria-label="Selection history">
      <button
        type="button"
        className="nav-history-btn"
        onClick={stepBack}
        disabled={!canStepBack}
        aria-label="Previous selection"
        title="Back"
      >
        ‹
      </button>
      <button
        type="button"
        className="nav-history-btn"
        onClick={stepForward}
        disabled={!canStepForward}
        aria-label="Next selection"
        title="Forward"
      >
        ›
      </button>
    </div>
  );
}
