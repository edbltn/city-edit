import { useGhostPin } from "../../context";
import { ROUTE_COLORS } from "../../colors";

const GOLD = ROUTE_COLORS.desire.middle;

export function GhostPin() {
  const { ghostState } = useGhostPin();

  if (!ghostState.isDragging || !ghostState.screenPosition) {
    return null;
  }

  const { x, y } = ghostState.screenPosition;

  return (
    <div
      className="ghost-pin-overlay"
      style={{ transform: `translate(${x}px, ${y}px)` }}
    >
      <div className="ascii-marker ghost-pin" style={{ color: GOLD }}>
        <span className="ascii-kite">◆</span>
        <span className="ascii-stem"></span>
      </div>
    </div>
  );
}
