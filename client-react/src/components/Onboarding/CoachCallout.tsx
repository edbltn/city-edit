import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { calloutMaxWidth, placeCallout, type CalloutPlacement } from "../../onboarding/calloutPlacement";
import type { AnchorBox } from "../../onboarding/coachAnchor";

// ==========================================================================
// The callout — a box with a tip on it, hung off a real control
// ==========================================================================
// Portalled to <body> and `position: fixed`, and both halves of that are
// load-bearing rather than habit. Every plate in the floating chrome carries a
// `backdrop-filter`, which makes it its own stacking context — so a callout
// rendered INSIDE the Start plate could not be drawn over anything outside that
// plate no matter what z-index it was given. The proposal cards learned this
// first and portal for the same reason.
//
// It is measured before it is placed. The box's height depends on how the ask
// wraps, which depends on the width it was allowed, which depends on the
// viewport — so the size cannot be known until it has been rendered once.
// `useLayoutEffect` reads it and positions it in the same frame the browser
// paints, so nothing is ever seen at the wrong coordinates. Until the first
// measurement lands the box is rendered invisible rather than not rendered at
// all: it has to be in the document to have a size.
//
// It does NOT close on Escape, and that is the opposite of the wall above it.
// The wall is a modal dialog and owes the reader an escape hatch; this is a
// label on a control, and the control it labels opens an address search whose
// OWN escape key closes the search. Listening on the window meant one Escape
// did both — abandoning a search took the coach with it, silently, at the exact
// moment somebody was doing the thing it had asked for. The × is the dismissal.
//
// It is NOT `pointer-events: none`. The nav rail's hover labels are, and the
// z-index ladder's note about transient things assumes it — but this one is
// dismissible, and a coach a first-timer cannot close is the worst version of
// this feature. What it does instead is stay small and never cover its own
// anchor: the tip's gap is real space, so the control it points at is always
// clickable, which is the whole ask.
// ==========================================================================

interface Props {
  anchor: AnchorBox;
  /** The sentence they chose, kept on screen the whole way through. */
  sentence: string | null;
  /** What to do right now. */
  ask: string;
  /** A decision the coach owns rather than the chrome — today, exactly one:
   *  declining an end point the flow was only guessing might exist. */
  actions?: ReactNode;
  onDismiss: () => void;
}

export function CoachCallout({ anchor, sentence, ask, actions, onDismiss }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<CalloutPlacement | null>(null);

  const reposition = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next = placeCallout(
      anchor,
      { width: el.offsetWidth, height: el.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight }
    );
    setPlacement((prev) =>
      prev &&
      prev.top === next.top &&
      prev.left === next.left &&
      prev.side === next.side &&
      prev.arrowLeft === next.arrowLeft &&
      prev.maxWidth === next.maxWidth
        ? prev
        : next
    );
  }, [anchor]);

  // Re-run whenever the anchor moves (the hook hands us a new box) and whenever
  // our own content changes size — the ask is a different length at each step,
  // and it re-wraps at every width.
  useLayoutEffect(() => {
    reposition();
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(reposition);
    ro.observe(el);
    return () => ro.disconnect();
  }, [reposition, sentence, ask, actions]);

  const maxWidth = placement?.maxWidth ?? calloutMaxWidth(
    typeof window === "undefined" ? 1024 : window.innerWidth
  );

  return createPortal(
    <div
      ref={ref}
      className={`coach-callout${placement ? ` coach-callout--${placement.side}` : " coach-callout--measuring"}`}
      style={{
        top: placement ? `${placement.top}px` : 0,
        left: placement ? `${placement.left}px` : 0,
        maxWidth: `${maxWidth}px`,
        // The tip's offset along the box's edge. Set as a custom property so the
        // arrow is one CSS rule for both sides rather than two inline styles.
        ["--coach-arrow-left" as string]: `${placement?.arrowLeft ?? 0}px`,
      }}
      role="status"
      aria-live="polite"
    >
      {sentence && <p className="coach-callout-sentence">{sentence}</p>}
      <p className="coach-callout-ask">{ask}</p>
      {actions && <div className="coach-callout-actions">{actions}</div>}
      <button
        type="button"
        className="coach-callout-close"
        onClick={onDismiss}
        aria-label="Close getting started"
      >
        ×
      </button>
      <span className="coach-callout-tip" aria-hidden="true" />
    </div>,
    document.body
  );
}
