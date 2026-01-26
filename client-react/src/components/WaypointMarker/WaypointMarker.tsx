import { useMemo, useRef } from "react";
import { Marker } from "react-leaflet";
import L from "leaflet";
import type { LatLng } from "../../types";

interface WaypointMarkerProps {
  position: LatLng;
  index: number;
  onDragEnd: (index: number, newPosition: LatLng) => void;
}

export function WaypointMarker({
  position,
  index,
  onDragEnd,
}: WaypointMarkerProps) {
  const markerRef = useRef<L.Marker>(null);

  const icon = useMemo(
    () =>
      L.divIcon({
        className: "custom-marker",
        html: `<div class="pin-container">
          <div class="pin-head" style="background: #D4A017;"></div>
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
      dragend: () => {
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          onDragEnd(index, { lat: latlng.lat, lng: latlng.lng });
        }
      },
    }),
    [index, onDragEnd]
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
