import { useEffect, useMemo, useRef } from "react";
import { Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { COLOR_START, COLOR_END } from "../../colors";
import { isWithinMappedBounds } from "../../utils/bounds";
import { kiteIcon } from "../../utils/kiteIcon";
import { useGraphSnap, useTheme } from "../../context";
import { mapStyleForTheme } from "../../themes";
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
  /** The point on the route this marker is connected to (the route endpoint it
   *  bridges to via the dotted connector). The drag trail anchors here instead
   *  of the marker's own offset position, so the dotted line stays rooted to the
   *  path the marker was connected to instead of jumping when the drag begins. */
  pathAnchor?: LatLng | null;
  /** Fires true/false as the cursor enters/leaves the marker, so the host can
   *  hide the start-placement ghost while the grab cursor is over a marker. */
  onHoverChange?: (hovering: boolean) => void;
}

// The mid-waypoint takes the active theme's selection color so the placed
// marker matches its drag-ghost (gray in light themes, white on dark ones).
function getMarkerColor(which: "start" | "end" | "waypoint", selection: string): string {
  if (which === "start") return COLOR_START;
  if (which === "end") return COLOR_END;
  return selection;
}

const DRAG_TRAIL_STYLE: L.PolylineOptions = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round",
};

export function RouteMarker({ position, which, onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, hidden, pathAnchor, onHoverChange }: RouteMarkerProps) {
  const map = useMap();
  const { snapToGraph, currentSnapRef, setDragging } = useGraphSnap();
  const markerRef = useRef<L.Marker>(null);
  const dragStartPosition = useRef<LatLng | null>(null);
  const dragTrailRef = useRef<L.Polyline | null>(null);
  const dragTrailOriginRef = useRef<LatLng | null>(null);
  const touchStartTime = useRef<number>(0);
  const wasDragged = useRef<boolean>(false);
  const originalSetLatLngRef = useRef<Function | null>(null);
  const hoveredRef = useRef(false);

  // If the marker unmounts while hovered (e.g. click-to-delete), release the
  // hover so the host's counter doesn't get stuck.
  const onHoverChangeRef = useRef(onHoverChange);
  useEffect(() => { onHoverChangeRef.current = onHoverChange; }, [onHoverChange]);
  useEffect(() => () => {
    if (hoveredRef.current) {
      hoveredRef.current = false;
      onHoverChangeRef.current?.(false);
    }
  }, []);

  // The mid-waypoint uses the theme's selection color (black on light themes,
  // white on dark) at full size/opacity, matching the hover and drag ghosts.
  const selection = mapStyleForTheme(useTheme()).selection;
  const icon = useMemo(() => kiteIcon(getMarkerColor(which, selection)), [which, selection]);

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
      mouseover: () => { hoveredRef.current = true; onHoverChange?.(true); },
      mouseout: () => { hoveredRef.current = false; onHoverChange?.(false); },
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

          // Anchor the dotted trail to the route point this marker was connected
          // to (its previous path connection), not the marker's own offset spot.
          // Otherwise the line visibly jumps off the path the instant you grab it.
          const origin = pathAnchor ?? { lat: latlng.lat, lng: latlng.lng };
          dragTrailOriginRef.current = origin;
          dragTrailRef.current = L.polyline(
            [[origin.lat, origin.lng], [origin.lat, origin.lng]],
            DRAG_TRAIL_STYLE
          ).addTo(map);
        }
        setDragging(true);
        onDragStart?.();
      },
      drag: () => {
        const marker = markerRef.current;
        if (marker && dragTrailOriginRef.current && dragTrailRef.current) {
          const snapped = currentSnapRef.current;
          const latlng = marker.getLatLng();
          const trailEnd = snapped ?? latlng;
          dragTrailRef.current.setLatLngs([
            [dragTrailOriginRef.current.lat, dragTrailOriginRef.current.lng],
            trailEnd,
          ]);
        }
      },
      dragend: () => {
        const marker = markerRef.current;

        // Remove drag trail and clear drag state
        dragTrailRef.current?.remove();
        dragTrailRef.current = null;
        dragTrailOriginRef.current = null;
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
    [map, onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, snapToGraph, currentSnapRef, setDragging, pathAnchor, onHoverChange]
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
