import { useMemo, useRef } from "react";
import { Marker } from "react-leaflet";
import L from "leaflet";
import { isWithinMappedBounds } from "../../utils/bounds";
import type { LatLng } from "../../types";

interface WaypointMarkerProps {
  position: LatLng;
  index: number;
  onDragEnd: (index: number, newPosition: LatLng) => void;
  onOutOfBounds?: () => void;
}

export function WaypointMarker({
  position,
  index,
  onDragEnd,
  onOutOfBounds,
}: WaypointMarkerProps) {
  const markerRef = useRef<L.Marker>(null);
  const dragStartPosition = useRef<LatLng | null>(null);

  const icon = useMemo(
    () =>
      L.divIcon({
        className: "custom-marker",
        html: `<div class="pin-container">
          <div class="pin-head" style="background: #D64045;"></div>
          <div class="pin-needle"></div>
          <div class="pin-shadow"></div>
        </div>`,
        iconSize: [30, 40],
        iconAnchor: [15, 40],
      }),
    []
  );

  const eventHandlers = useMemo(
    () => ({
      dragstart: () => {
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          dragStartPosition.current = { lat: latlng.lat, lng: latlng.lng };
        }
      },
      dragend: () => {
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          const newPos = { lat: latlng.lat, lng: latlng.lng };

          // Check if new position is within mapped bounds
          if (!isWithinMappedBounds(newPos)) {
            // Reset marker to original position
            if (dragStartPosition.current) {
              marker.setLatLng([dragStartPosition.current.lat, dragStartPosition.current.lng]);
            }
            dragStartPosition.current = null;
            onOutOfBounds?.();
            return;
          }

          dragStartPosition.current = null;
          onDragEnd(index, newPos);
        }
      },
    }),
    [index, onDragEnd, onOutOfBounds]
  );

  return (
    <Marker
      ref={markerRef}
      position={[position.lat, position.lng]}
      icon={icon}
      draggable={true}
      eventHandlers={eventHandlers}
    />
  );
}
