/**
 * Hex Tooltip - Speech bubble showing top suggestions for a hovered hex.
 *
 * Styled to match .btn-header (paper background, hairline border, no shadow).
 * Positioned above the cursor with a CSS triangle pointing down.
 */

import { createPortal } from "react-dom";

interface HexTooltipProps {
  suggestions: string[];
  votes: number;
  x: number;
  y: number;
  container: HTMLElement;
}

export function HexTooltip({ suggestions, votes, x, y, container }: HexTooltipProps) {
  const voteCount = Math.max(1, Math.round(votes));
  const voteLabel = `${voteCount} vote${voteCount !== 1 ? "s" : ""}`;

  return createPortal(
    <div
      className="hex-tooltip"
      style={{ left: x, top: y }}
    >
      {suggestions.length > 0 ? (
        <>
          {suggestions.map((s, i) => (
            <div key={i} className="hex-tooltip-line">{s}</div>
          ))}
          {suggestions.length >= 3 && (
            <div className="hex-tooltip-ellipsis">...</div>
          )}
          <div className="hex-tooltip-votes">{voteLabel}</div>
        </>
      ) : (
        <div className="hex-tooltip-line">{voteLabel}</div>
      )}
    </div>,
    container
  );
}
