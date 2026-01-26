import { useMemo, useRef } from "react";
import { Marker } from "react-leaflet";
import L from "leaflet";
import { COLOR_START, COLOR_END, ROUTE_COLORS } from "../../colors";
import type { LatLng } from "../../types";

interface RouteMarkerProps {
  position: LatLng;
  which: "start" | "end" | "waypoint";
  onDragEnd?: (newPosition: LatLng) => void;
  onDragStart?: () => void;
}

// Create custom icon for markers
function createCustomIcon(color: string): L.DivIcon {
  return L.divIcon({
    className: "custom-marker",
    html: `<div class="pin-container">
      <div class="pin-head" style="background: ${color};"></div>
      <div class="pin-needle"></div>
      <div class="pin-shadow"></div>
    </div>`,
    iconSize: [30, 40],
    iconAnchor: [15, 40],
  });
}

function getMarkerColor(which: "start" | "end" | "waypoint"): string {
  if (which === "start") return COLOR_START;
  if (which === "end") return COLOR_END;
  return ROUTE_COLORS.desire.middle; // Gold for waypoint
}

export function RouteMarker({ position, which, onDragEnd, onDragStart }: RouteMarkerProps) {
  const markerRef = useRef<L.Marker>(null);

  const icon = useMemo(() => {
    const color = getMarkerColor(which);
    return createCustomIcon(color);
  }, [which]);

  const eventHandlers = useMemo(
    () =>
      onDragEnd
        ? {
            dragstart: () => {
              onDragStart?.();
            },
            dragend: () => {
              const marker = markerRef.current;
              if (marker) {
                const latlng = marker.getLatLng();
                onDragEnd({ lat: latlng.lat, lng: latlng.lng });
              }
            },
          }
        : {},
    [onDragEnd, onDragStart]
  );

  return (
    <Marker
      ref={markerRef}
      position={[position.lat, position.lng]}
      icon={icon}
      draggable={!!onDragEnd}
      eventHandlers={eventHandlers}
      zIndexOffset={1000}
    />
  );
}
