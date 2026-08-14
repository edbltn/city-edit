import { useEffect, useRef, type ReactNode } from "react";
import "./MapNotice.css";

// ==========================================================================
// MapNotice — the shared base for every strip that speaks to the user from the
// bottom-centre of the map: the error toast and the event announcement.
//
// They had drifted apart. The toast was hardcoded dark (#0d0d0d on #d4d4d4)
// so it stayed a black bar on the light basemaps, and its `white-space: nowrap`
// pushed a long sentence off both edges of a phone. The banner had already
// solved both. One base now owns the slot, the palette, the safe-area insets,
// the wrapping, and the stacking; the variants own only their own voice.
//
// Two rules this base enforces that neither could enforce alone:
//
//   1. It sits BELOW the map's cards (pinned proposal 1100, route summary
//      1300, hover card 1400). A transient notice must never be the reason a
//      selected proposal is unreadable.
//   2. The strip itself is transparent to the pointer; only its controls take
//      clicks. Even where a notice overlaps a card, the card underneath stays
//      clickable — which z-index alone can't promise, since the cards portal
//      to document.body and mount in whatever order they mount.
//
// Notices share one bottom slot and stack: whichever one is `anchor` publishes
// its measured height, and the other rides above it. Measured rather than
// hardcoded, because the strip is one line on a desktop and wraps to three on
// a phone.
// ==========================================================================

/** Published by the anchor notice; read by the one stacking above it. */
const OFFSET_VAR = "--map-notice-offset";

interface MapNoticeProps {
  /** `alert` speaks up (accent rule + icon); `notice` is a quiet aside. */
  tone: "alert" | "notice";
  /**
   * True for the notice that owns the bottom of the stack. It publishes its
   * height so the other one can sit on top instead of landing over it. Exactly
   * one notice should claim this.
   */
  anchor?: boolean;
  role?: string;
  "aria-live"?: "polite" | "assertive";
  "aria-label"?: string;
  children: ReactNode;
}

export function MapNotice({
  tone,
  anchor = false,
  role,
  "aria-live": ariaLive,
  "aria-label": ariaLabel,
  children,
}: MapNoticeProps) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!anchor || !el) return;
    const root = document.documentElement;
    const publish = () => {
      root.style.setProperty(OFFSET_VAR, `${el.offsetHeight + 12}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(el);
    return () => {
      observer.disconnect();
      root.style.removeProperty(OFFSET_VAR);
    };
  }, [anchor]);

  return (
    <div
      ref={ref}
      className={`map-notice map-notice--${tone}${anchor ? " map-notice--anchor" : ""}`}
      role={role}
      aria-live={ariaLive}
      aria-label={ariaLabel}
    >
      {children}
    </div>
  );
}
