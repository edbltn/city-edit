import { useEffect, useMemo, useRef } from "react";
import { Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { COLOR_START, COLOR_END } from "../../colors";
import { isWithinMappedBounds } from "../../utils/bounds";
import { kiteIcon } from "../../utils/kiteIcon";
import { useGraphSnap, useTheme } from "../../context";
import { mapStyleForTheme } from "../../themes";
import { isTap } from "../../utils/gesture";
import type { LatLng } from "../../types";

// Minimum distance (in degrees) to consider a drag as intentional movement
// ~1 meter at NYC latitude - very small to avoid false negatives
const MIN_DRAG_DISTANCE = 0.00001;

interface RouteMarkerProps {
  position: LatLng;
  which: "start" | "end" | "waypoint";
  onDragEnd?: (newPosition: LatLng) => void;
  onDragStart?: () => void;
  onDragFinish?: () => void;
  onDelete?: () => void;
  /** Overrides the tap/click action when set. A waypoint that sits on a top
   *  proposal uses this to "restart the path from here" (set as the new start &
   *  clear the rest) instead of deleting — removal moves to the indicator's [x].
   *  Drag is unaffected. Falls back to onDelete when unset (regular waypoints). */
  onTap?: () => void;
  onOutOfBounds?: () => void;
  hidden?: boolean;
  /** Fires true/false as the cursor enters/leaves the marker, so the host can
   *  hide the start-placement ghost while the grab cursor is over a marker. */
  onHoverChange?: (hovering: boolean) => void;
  /** Fires the live dragged position on every `drag` tick (desktop AND touch).
   *  Hosts use it to light up the proposal a drop would link to. */
  onDragMove?: (latlng: LatLng) => void;
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

export function RouteMarker({ position, which, onDragEnd, onDragStart, onDragFinish, onDelete, onTap, onOutOfBounds, hidden, onHoverChange, onDragMove }: RouteMarkerProps) {
  const map = useMap();
  const { snapToGraph, setDragging } = useGraphSnap();
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

  // When this waypoint sits on a proposal the host passes `hidden`: the fixed,
  // tinted proposal indicator stands in, so we make the kite invisible — but keep
  // it INTERACTIVE (the indicator above is click-through), so it's still the grab/
  // click handle, and it reappears (a kite) while dragging so the proposal stays
  // put and the kite is what moves.
  const hiddenRef = useRef(hidden);
  useEffect(() => { hiddenRef.current = hidden; }, [hidden]);
  const draggingRef = useRef(false);
  useEffect(() => {
    const el = markerRef.current?.getElement();
    if (el) el.style.opacity = (hidden && !draggingRef.current) ? "0" : "";
  }, [hidden]);

  // If the marker unmounts during an active drag (e.g. a recalc re-keys or
  // removes markers mid-drag), `dragend` never fires — so restore setLatLng and
  // tear down the live drag trail here. Otherwise the dotted trail polyline is
  // orphaned on the map and accumulates with every interrupted drag.
  useEffect(() => {
    return () => {
      const marker = markerRef.current;
      if (marker && originalSetLatLngRef.current) {
        (marker as any).setLatLng = originalSetLatLngRef.current;
        originalSetLatLngRef.current = null;
      }
      dragTrailRef.current?.remove();
      dragTrailRef.current = null;
      dragTrailOriginRef.current = null;
    };
  }, []);

  const eventHandlers = useMemo(
    () => ({
      mouseover: () => { hoveredRef.current = true; onHoverChange?.(true); },
      mouseout: () => { hoveredRef.current = false; onHoverChange?.(false); },
      // Desktop: click fires if there was no drag. A proposal waypoint restarts
      // the path from here (onTap); a regular waypoint deletes (onDelete).
      click: () => {
        if (!wasDragged.current) {
          (onTap ?? onDelete)?.();
        }
        wasDragged.current = false;
      },
      // Mobile: track touch timing for tap detection
      touchstart: () => {
        touchStartTime.current = Date.now();
        wasDragged.current = false;
      },
      touchend: () => {
        // A quick release with no drag is a tap (shared timing convention).
        if (isTap(touchStartTime.current) && !wasDragged.current) {
          (onTap ?? onDelete)?.();
        }
      },
      dragstart: () => {
        wasDragged.current = true;
        const marker = markerRef.current;
        if (marker) {
          // A hidden (on-proposal) waypoint reappears as a kite for the drag, so
          // the proposal indicator stays put and the kite is what moves.
          draggingRef.current = true;
          const el = marker.getElement();
          if (el) el.style.opacity = "";
          const latlng = marker.getLatLng();
          dragStartPosition.current = { lat: latlng.lat, lng: latlng.lng };
          originalSetLatLngRef.current = marker.setLatLng.bind(marker);
          (marker as any).setLatLng = function() { return this; };

          // The dotted trail is a plain rubber band: it anchors at the point the
          // marker was grabbed (where the waypoint was) and its other end tracks
          // the marker as it moves (below). It deliberately does NOT follow the
          // path connection or the graph snap — both diverge from the marker when
          // it sits on a proposal, which is what made the trail jump.
          const origin = { lat: latlng.lat, lng: latlng.lng };
          dragTrailOriginRef.current = origin;
          // Defensive: drop any trail left over from a prior drag that didn't
          // get torn down, so trails can't stack up on the map.
          dragTrailRef.current?.remove();
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
          // Trail end follows the marker itself — the thing being dragged — not
          // the node/edge it would snap to on drop. Keeps the dotted line glued
          // to the kite the whole way, including over proposals.
          const latlng = marker.getLatLng();
          dragTrailRef.current.setLatLngs([
            [dragTrailOriginRef.current.lat, dragTrailOriginRef.current.lng],
            [latlng.lat, latlng.lng],
          ]);
          // Report the live position (fires on touch too) for the drop-target ring.
          onDragMove?.({ lat: latlng.lat, lng: latlng.lng });
        }
      },
      dragend: () => {
        const marker = markerRef.current;

        // End the drag: re-hide the kite if it's still on a proposal (the prop
        // hasn't updated yet; the [hidden] effect re-applies after the re-render).
        draggingRef.current = false;
        const el = marker?.getElement();
        if (el) el.style.opacity = hiddenRef.current ? "0" : "";

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
    [map, onDragEnd, onDragStart, onDragFinish, onDelete, onTap, onOutOfBounds, snapToGraph, setDragging, onHoverChange, onDragMove]
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
