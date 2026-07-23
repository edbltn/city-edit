import { useEffect, useMemo, useRef } from "react";
import { isWithinMappedBounds } from "../../utils/bounds";
import { MapMarker, type MapMarkerHandle } from "../MapMarker";
import type { LatLng } from "../../types";

const PIN_HTML = `<div class="pin-container">
  <div class="pin-head" style="background: #D64045;"></div>
  <div class="pin-needle"></div>
  <div class="pin-shadow"></div>
</div>`;

interface WaypointMarkerProps {
  position: LatLng;
  index: number;
  onDragEnd: (index: number, newPosition: LatLng) => void;
  /** Suppresses the ghost pin / next map click while dragging (see MapView). */
  onDragStart?: () => void;
  onDragFinish?: () => void;
  onOutOfBounds?: () => void;
  /** Fires true/false as the cursor enters/leaves the marker, so the host can
   *  hide the start-placement ghost while the grab cursor is over a marker. */
  onHoverChange?: (hovering: boolean) => void;
}

export function WaypointMarker({
  position,
  index,
  onDragEnd,
  onDragStart,
  onDragFinish,
  onOutOfBounds,
  onHoverChange,
}: WaypointMarkerProps) {
  const markerRef = useRef<MapMarkerHandle>(null);
  const dragStartPosition = useRef<LatLng | null>(null);
  const hoveredRef = useRef(false);

  // Release hover if the marker unmounts while hovered.
  const onHoverChangeRef = useRef(onHoverChange);
  useEffect(() => { onHoverChangeRef.current = onHoverChange; }, [onHoverChange]);
  useEffect(() => () => {
    if (hoveredRef.current) {
      hoveredRef.current = false;
      onHoverChangeRef.current?.(false);
    }
  }, []);

  const handlers = useMemo(
    () => ({
      onMouseOver: () => { hoveredRef.current = true; onHoverChange?.(true); },
      onMouseOut: () => { hoveredRef.current = false; onHoverChange?.(false); },
      onDragStart: () => {
        const marker = markerRef.current;
        if (marker) {
          dragStartPosition.current = marker.getLatLng();
        }
        onDragStart?.();
      },
      onDragEnd: () => {
        const marker = markerRef.current;
        if (marker) {
          const newPos = marker.getLatLng();

          // Check if new position is within mapped bounds
          if (!isWithinMappedBounds(newPos)) {
            // Reset marker to original position
            if (dragStartPosition.current) {
              marker.setLatLng(dragStartPosition.current);
            }
            dragStartPosition.current = null;
            onOutOfBounds?.();
            onDragFinish?.();
            return;
          }

          dragStartPosition.current = null;
          onDragEnd(index, newPos);
        }
        onDragFinish?.();
      },
    }),
    [index, onDragEnd, onDragStart, onDragFinish, onOutOfBounds, onHoverChange]
  );

  return (
    <MapMarker
      ref={markerRef}
      position={position}
      html={PIN_HTML}
      className="custom-marker"
      size={[30, 40]}
      anchor="bottom"
      draggable={true}
      {...handlers}
    />
  );
}
