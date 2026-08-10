interface EyeIconProps {
  /** Width/height in px. */
  size?: number;
  /** Draw the struck-through (hidden) state. */
  off?: boolean;
  className?: string;
}

/**
 * Visibility toggle glyph — the legend's show/hide control (VoteTypeSelector).
 *
 * The conventional eye / eye-with-slash pair, drawn to the standard Feather
 * geometry on a 24 grid. Deliberately NOT restyled into the app's square-capped
 * house language (CheckIcon, the nav rail): this control's whole job is to be
 * recognised instantly, and recognition here comes from the shape people
 * already know. Callers dim the `off` state so the two read apart at a glance
 * as well as up close.
 */
export function EyeIcon({ size = 14, off = false, className }: EyeIconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ display: "block" }}
    >
      {off ? (
        <>
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
          <line x1="1" y1="1" x2="23" y2="23" />
        </>
      ) : (
        <>
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </>
      )}
    </svg>
  );
}
