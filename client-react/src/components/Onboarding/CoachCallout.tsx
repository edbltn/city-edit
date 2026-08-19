import { useCallback, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { calloutFit, placeCallout, type CalloutPlacement } from "../../onboarding/calloutPlacement";
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
  /** What to do right now. One line where it fits — the box is a label on a
   *  control, not a paragraph about it. */
  ask: string;
  /** A decision the coach owns rather than the chrome — today, exactly one:
   *  declining an end point the flow was only guessing might exist. */
  actions?: ReactNode;
  /** Close THIS mark. Every mark has one — the cast step's two boxes are two
   *  separate things to deal with, and a reader who has taken the point of one
   *  of them should be able to put it away without losing the other. The caller
   *  owns what that means (Onboarding.tsx): the box goes, the rest stand, and
   *  the last one to go ends the flow. */
  onDismiss: () => void;
  /** What the × closes, for a reader who cannot see which box it is in. */
  dismissLabel?: string;
  /** Hand this box its own element, so a later box can be told to avoid it. */
  boxRef?: RefObject<HTMLDivElement | null>;
  /** A box already on screen that this one must not land on top of. Read live
   *  rather than passed as numbers: the other box moves on every resize too, and
   *  a snapshot taken at render time would be a frame stale exactly when the
   *  layout is changing. */
  avoidRef?: RefObject<HTMLDivElement | null>;
  /** Controls this box must not cover — the OTHER mark's anchor, so a coach
   *  never lands on the control the other half of it is pointing at. */
  avoidBoxes?: AnchorBox[];
  /** Which side of the control this box would rather sit on. The cast step's
   *  two marks take opposite sides so they cannot meet — see calloutFit. */
  prefer?: "left" | "right";
}

export function CoachCallout({
  anchor, ask, actions, onDismiss, dismissLabel = "Close getting started",
  boxRef, avoidRef, avoidBoxes, prefer = "left",
}: Props) {
  const own = useRef<HTMLDivElement>(null);
  const ref = boxRef ?? own;
  const [placement, setPlacement] = useState<CalloutPlacement | null>(null);

  const reposition = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const other = avoidRef?.current?.getBoundingClientRect();
    const blockers = [
      ...(other && other.width > 0
        ? [{ top: other.top, left: other.left, width: other.width, height: other.height }]
        : []),
      ...(avoidBoxes ?? []),
    ];
    const next = placeCallout(
      anchor,
      { width: el.offsetWidth, height: el.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
      blockers,
      prefer
    );
    setPlacement((prev) =>
      prev &&
      prev.top === next.top &&
      prev.left === next.left &&
      prev.side === next.side &&
      prev.arrow === next.arrow &&
      prev.maxWidth === next.maxWidth
        ? prev
        : next
    );
  }, [anchor, avoidRef, avoidBoxes, ref, prefer]);

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
  }, [reposition, ask, actions]);

  // Before the first measurement there is no placement to read a width off, and
  // the width is what decides how the ask wraps — so it comes from the anchor
  // and the viewport, which are known, rather than from the box, which is not.
  const maxWidth =
    placement?.maxWidth ??
    calloutFit(
      anchor,
      {
        width: typeof window === "undefined" ? 1024 : window.innerWidth,
        height: typeof window === "undefined" ? 768 : window.innerHeight,
      },
      prefer
    ).maxWidth;

  return createPortal(
    <div
      ref={ref}
      className={
        `coach-callout` +
        (placement ? ` coach-callout--${placement.side}` : " coach-callout--measuring")
      }
      style={{
        top: placement ? `${placement.top}px` : 0,
        left: placement ? `${placement.left}px` : 0,
        maxWidth: `${maxWidth}px`,
        // The tip's offset along the box's leading edge — from the left for a
        // vertical tip, from the top for a horizontal one. One custom property
        // for all four sides, read differently by each side's rule.
        ["--coach-arrow" as string]: `${placement?.arrow ?? 0}px`,
      }}
      role="status"
      aria-live="polite"
    >
      <p className="coach-callout-ask">{ask}</p>
      {actions && <div className="coach-callout-actions">{actions}</div>}
      <button
        type="button"
        className="coach-callout-close"
        onClick={onDismiss}
        aria-label={dismissLabel}
      >
        ×
      </button>
      <span className="coach-callout-tip" aria-hidden="true" />
    </div>,
    document.body
  );
}
