import { useEffect, useMemo, useRef } from "react";
import { COLOR_START, COLOR_END, ROUTE_COLORS } from "../../colors";
import { isWithinMappedBounds } from "../../utils/bounds";
import { kiteHtml, KITE_SIZE } from "../../utils/kiteIcon";
import { useGraphSnap } from "../../context";
import {
  UTIL_SOURCE_ID,
  setOverlayFeature,
  removeOverlayFeature,
} from "../../map/maplibreOverlays";
import { MapMarker, type MapMarkerHandle } from "../MapMarker";
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
  /** Fires true/false as the cursor enters/leaves the marker, so the host can
   *  hide the start-placement ghost while the grab cursor is over a marker. */
  onHoverChange?: (hovering: boolean) => void;
}

function getMarkerColor(which: "start" | "end" | "waypoint"): string {
  if (which === "start") return COLOR_START;
  if (which === "end") return COLOR_END;
  return ROUTE_COLORS.desire.middle;
}

let trailCounter = 0;

export function RouteMarker({ position, which, onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, hidden, onHoverChange }: RouteMarkerProps) {
  const { snapToGraph, currentSnapRef, setDragging } = useGraphSnap();
  const markerRef = useRef<MapMarkerHandle>(null);
  const dragStartPosition = useRef<LatLng | null>(null);
  const trailKeyRef = useRef(`trail-marker-${++trailCounter}`);
  const touchStartTime = useRef<number>(0);
  const wasDragged = useRef<boolean>(false);
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

  // Remove a stray drag trail if the marker unmounts mid-drag.
  useEffect(() => {
    const trailKey = trailKeyRef.current;
    return () => removeOverlayFeature(UTIL_SOURCE_ID, trailKey);
  }, []);

  const html = useMemo(() => kiteHtml(getMarkerColor(which)), [which]);

  const updateTrail = (end: LatLng) => {
    const origin = dragStartPosition.current;
    if (!origin) return;
    setOverlayFeature(UTIL_SOURCE_ID, trailKeyRef.current, {
      type: "LineString",
      coordinates: [[origin.lng, origin.lat], [end.lng, end.lat]],
    });
  };

  const handlers = useMemo(
    () => ({
      onMouseOver: () => { hoveredRef.current = true; onHoverChange?.(true); },
      onMouseOut: () => { hoveredRef.current = false; onHoverChange?.(false); },
      // Desktop: click fires if there was no drag
      onClick: () => {
        if (!wasDragged.current) {
          onDelete?.();
        }
        wasDragged.current = false;
      },
      // Mobile: track touch timing for tap detection
      onTouchStart: () => {
        touchStartTime.current = Date.now();
        wasDragged.current = false;
      },
      onTouchEnd: () => {
        // If touch was quick and no drag occurred, treat as tap
        const elapsed = Date.now() - touchStartTime.current;
        if (elapsed < TAP_TIMEOUT && !wasDragged.current) {
          onDelete?.();
        }
      },
      onDragStart: () => {
        wasDragged.current = true;
        const marker = markerRef.current;
        if (marker) {
          const latlng = marker.getLatLng();
          dragStartPosition.current = latlng;
          // Dotted trail from the original position
          updateTrail(latlng);
        }
        setDragging(true);
        onDragStart?.();
      },
      onDrag: () => {
        const marker = markerRef.current;
        if (marker && dragStartPosition.current) {
          const snapped = currentSnapRef.current;
          updateTrail(snapped ?? marker.getLatLng());
        }
      },
      onDragEnd: () => {
        const marker = markerRef.current;

        // Remove drag trail and clear drag state
        removeOverlayFeature(UTIL_SOURCE_ID, trailKeyRef.current);
        setDragging(false);

        if (!marker || !onDragEnd) {
          onDragFinish?.();
          return;
        }

        // Snap final position to graph node/edge
        const latlng = marker.getLatLng();
        const snapped = snapToGraph(latlng.lat, latlng.lng);
        const newPos = snapped ?? latlng;

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

        // If we have a start position, check if barely moved -> noop
        // If no start position recorded (shouldn't happen), proceed with update
        if (dragStartPosition.current) {
          const deltaLat = Math.abs(newPos.lat - dragStartPosition.current.lat);
          const deltaLng = Math.abs(newPos.lng - dragStartPosition.current.lng);
          const distance = Math.sqrt(deltaLat * deltaLat + deltaLng * deltaLng);

          if (distance < MIN_DRAG_DISTANCE) {
            // Reset marker to original position and do nothing
            marker.setLatLng(dragStartPosition.current);
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
    [onDragEnd, onDragStart, onDragFinish, onDelete, onOutOfBounds, snapToGraph, currentSnapRef, setDragging, onHoverChange]
  );

  return (
    <MapMarker
      ref={markerRef}
      position={position}
      html={html}
      className="custom-marker"
      size={KITE_SIZE}
      anchor="bottom"
      draggable={!!onDragEnd}
      hidden={hidden}
      zIndex={1000}
      {...handlers}
    />
  );
}
