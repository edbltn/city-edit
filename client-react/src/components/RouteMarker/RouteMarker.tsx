import { useMemo, useRef } from "react";
import { Marker } from "react-leaflet";
import L from "leaflet";
import { COLOR_START, COLOR_END, ROUTE_COLORS } from "../../colors";
import { isWithinMappedBounds } from "../../utils/bounds";
import type { LatLng } from "../../types";

// Minimum distance (in degrees) to consider a drag as intentional movement
// ~1 meter at NYC latitude - very small to avoid false negatives
const MIN_DRAG_DISTANCE = 0.00001;

// Max time (ms) between touchstart and touchend to consider it a tap
const TAP_TIMEOUT = 300;

interface RouteMarkerProps {
  position: LatLng;
  which: "start" | "end" | "waypoint";
  onDragEnd?: (newPosition: LatLng) => void;
  onDragStart?: () => void;
  onDelete?: () => void;
  onOutOfBounds?: () => void;
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

export function RouteMarker({ position, which, onDragEnd, onDragStart, onDelete, onOutOfBounds }: RouteMarkerProps) {
  const markerRef = useRef<L.Marker>(null);
  const dragStartPosition = useRef<LatLng | null>(null);
  const touchStartTime = useRef<number>(0);
  const wasDragged = useRef<boolean>(false);

  const icon = useMemo(() => {
    const color = getMarkerColor(which);
    return createCustomIcon(color);
  }, [which]);

  const eventHandlers = useMemo(
    () => ({
      // Desktop: click fires if there was no drag
      click: () => {
        onDelete?.();
      },
      // Mobile: track touch timing for tap detection
      touchstart: () => {
        touchStartTime.current = Date.now();
        wasDragged.current = false;
      },
      touchend: () => {
        // If touch was quick and no drag occurred, treat as tap
        const elapsed = Date.now() - touchStartTime.current;
        if (elapsed < TAP_TIMEOUT && !wasDragged.current) {
          onDelete?.();
        }
      },
      dragstart: () => {
        wasDragged.current = true;
        // Store the position when drag starts for distance comparison
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          dragStartPosition.current = { lat: latlng.lat, lng: latlng.lng };
        }
        onDragStart?.();
      },
      dragend: () => {
        const marker = markerRef.current;
        if (!marker || !onDragEnd) return;

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

        // If we have a start position, check if barely moved -> noop
        // If no start position recorded (shouldn't happen), proceed with update
        if (dragStartPosition.current) {
          const deltaLat = Math.abs(newPos.lat - dragStartPosition.current.lat);
          const deltaLng = Math.abs(newPos.lng - dragStartPosition.current.lng);
          const distance = Math.sqrt(deltaLat * deltaLat + deltaLng * deltaLng);

          if (distance < MIN_DRAG_DISTANCE) {
            // Reset marker to original position and do nothing
            marker.setLatLng([dragStartPosition.current.lat, dragStartPosition.current.lng]);
            dragStartPosition.current = null;
            return;
          }
        }

        dragStartPosition.current = null;
        onDragEnd(newPos);
      },
    }),
    [onDragEnd, onDragStart, onDelete, onOutOfBounds]
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
