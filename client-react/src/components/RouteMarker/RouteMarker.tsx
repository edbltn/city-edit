import { useEffect, useMemo, useRef } from "react";
import { Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { COLOR_START, COLOR_END, ROUTE_COLORS } from "../../colors";
import { isWithinMappedBounds } from "../../utils/bounds";
import { kiteIcon } from "../../utils/kiteIcon";
import { useGraphSnap } from "../../context";
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
  onDragFinish?: () => void;
  onDelete?: () => void;
  onOutOfBounds?: () => void;
  hidden?: boolean;
}

function getMarkerColor(which: "start" | "end" | "waypoint"): string {
  if (which === "start") return COLOR_START;
  if (which === "end") return COLOR_END;
  return ROUTE_COLORS.desire.middle;
}

const DRAG_TRAIL_STYLE: L.PolylineOptions = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round",
};

export function RouteMarker({ position, which, onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, hidden }: RouteMarkerProps) {
  const map = useMap();
  const { snapToGraph, currentSnapRef, setDragging } = useGraphSnap();
  const markerRef = useRef<L.Marker>(null);
  const dragStartPosition = useRef<LatLng | null>(null);
  const dragTrailRef = useRef<L.Polyline | null>(null);
  const touchStartTime = useRef<number>(0);
  const wasDragged = useRef<boolean>(false);
  const originalSetLatLngRef = useRef<Function | null>(null);

  const icon = useMemo(() => kiteIcon(getMarkerColor(which)), [which]);

  // Hide/show via Leaflet DOM directly — no unmount/remount flicker
  useEffect(() => {
    const el = markerRef.current?.getElement();
    if (el) {
      el.style.opacity = hidden ? "0" : "";
      el.style.pointerEvents = hidden ? "none" : "";
    }
  }, [hidden]);

  // Restore setLatLng if component unmounts during an active drag
  useEffect(() => {
    return () => {
      const marker = markerRef.current;
      if (marker && originalSetLatLngRef.current) {
        (marker as any).setLatLng = originalSetLatLngRef.current;
        originalSetLatLngRef.current = null;
      }
    };
  }, []);

  const eventHandlers = useMemo(
    () => ({
      // Desktop: click fires if there was no drag
      click: () => {
        if (!wasDragged.current) {
          onDelete?.();
        }
        wasDragged.current = false;
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
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          dragStartPosition.current = { lat: latlng.lat, lng: latlng.lng };
          originalSetLatLngRef.current = marker.setLatLng.bind(marker);
          (marker as any).setLatLng = function() { return this; };

          // Create dotted trail from original position
          dragTrailRef.current = L.polyline(
            [latlng, latlng],
            DRAG_TRAIL_STYLE
          ).addTo(map);
        }
        setDragging(true);
        onDragStart?.();
      },
      drag: () => {
        const marker = markerRef.current;
        if (marker && dragStartPosition.current && dragTrailRef.current) {
          const snapped = currentSnapRef.current;
          const latlng = marker.getLatLng();
          const trailEnd = snapped ?? latlng;
          dragTrailRef.current.setLatLngs([
            [dragStartPosition.current.lat, dragStartPosition.current.lng],
            trailEnd,
          ]);
        }
      },
      dragend: () => {
        const marker = markerRef.current;

        // Remove drag trail and clear drag state
        dragTrailRef.current?.remove();
        dragTrailRef.current = null;
        setDragging(false);

        // Restore original setLatLng before state updates trigger re-renders
        if (marker && originalSetLatLngRef.current) {
          (marker as any).setLatLng = originalSetLatLngRef.current;
          originalSetLatLngRef.current = null;
        }

        if (!marker || !onDragEnd) {
          onDragFinish?.();
          return;
        }

        // Snap final position to graph node/edge
        const latlng = marker.getLatLng();
        const snapped = snapToGraph(map, latlng.lat, latlng.lng);
        const newPos = snapped ?? { lat: latlng.lat, lng: latlng.lng };

        // Check if new position is within mapped bounds
        if (!isWithinMappedBounds(newPos)) {
          // Reset marker to original position
          if (dragStartPosition.current) {
            marker.setLatLng([dragStartPosition.current.lat, dragStartPosition.current.lng]);
          }
          dragStartPosition.current = null;
          onOutOfBounds?.();
          onDragFinish?.();
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
            onDragFinish?.();
            return;
          }
        }

        dragStartPosition.current = null;
        onDragEnd(newPos);
        onDragFinish?.();
      },
    }),
    [map, onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, snapToGraph, currentSnapRef, setDragging]
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
