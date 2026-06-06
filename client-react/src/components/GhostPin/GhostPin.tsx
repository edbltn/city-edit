import { useGhostPin, useTheme } from "../../context";
import { mapStyleForTheme } from "../../themes";

export function GhostPin() {
  const { ghostState } = useGhostPin();
  const selection = mapStyleForTheme(useTheme()).selection;

  if (!ghostState.isDragging || !ghostState.screenPosition) {
    return null;
  }

  const { x, y } = ghostState.screenPosition;

  return (
    <div
      className="ghost-pin-overlay"
      style={{ transform: `translate(${x}px, ${y}px)` }}
    >
      {/* Matches the hover ghost and placed waypoint: selection color by default,
          or the drag's own color (teal/red when moving a start/end proposal). */}
      <div className="ascii-marker ghost-pin" style={{ color: ghostState.color ?? selection }}>
        <span className="ascii-kite">◆</span>
        <span className="ascii-stem"></span>
      </div>
    </div>
  );
}
