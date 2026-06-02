interface CheckIconProps {
  /** Width/height in px. */
  size?: number;
  className?: string;
}

/**
 * Straight-edged checkmark — square stroke caps and a mitered join give a crisp,
 * angular tick (unlike the rounded unicode ✓). Inherits color via currentColor.
 * Shared across the app: copy-link confirmation, dropdown selection ticks, etc.
 */
export function CheckIcon({ size = 12, className }: CheckIconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden="true"
      style={{ display: "block" }}
    >
      <path d="M4 12.5 L9.5 18 L20 6.5" />
    </svg>
  );
}
