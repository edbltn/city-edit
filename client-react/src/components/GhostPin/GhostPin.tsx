import { useGhostPin } from "../../context";

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
      <div className="pin-container ghost-pin">
        <div className="pin-head" style={{ background: "#D4A017" }}></div>
        <div className="pin-needle"></div>
        <div className="pin-shadow"></div>
      </div>
    </div>
  );
}
