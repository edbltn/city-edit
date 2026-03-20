import { useCallback, useRef, useEffect, useState } from "react";
import L from "leaflet";
import { useGhostPin, useGraphSnap } from "../context";
import type { RouteGeometry, LatLng } from "../types";


/**
 * Find the closest geometry vertex to a given lat/lng.
 * Returns actual graph node coordinates (not interpolated) so the
 * server-side coordinate cache can match pre-cached sub-paths.
 */
function closestVertexOnPath(
  map: L.Map,
  coordinates: [number, number][],
  latlng: L.LatLng,
  thresholdPx: number = 40
): (LatLng & { index: number }) | null {
  if (coordinates.length === 0) return null;

  const point = map.latLngToContainerPoint(latlng);
  let bestDist = Infinity;
  let bestIdx = -1;

  for (let i = 0; i < coordinates.length; i++) {
    const vertex = map.latLngToContainerPoint(
      L.latLng(coordinates[i][1], coordinates[i][0])
    );
    const dist = point.distanceTo(vertex);
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = i;
    }
  }

  if (bestDist > thresholdPx || bestIdx < 0) return null;
  return { lat: coordinates[bestIdx][1], lng: coordinates[bestIdx][0], index: bestIdx };
}

interface UsePathDragOptions {
  map: L.Map;
  geometry: RouteGeometry;
  segmentIndex: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
}

interface UsePathDragResult {
  isDraggingRef: React.MutableRefObject<boolean>;
  hoverLatLng: L.LatLng | null;
  handleStart: (e: L.LeafletMouseEvent) => void;
  handleHoverMove: (e: L.LeafletMouseEvent) => void;
  handleHoverOut: () => void;
}

const DRAG_TRAIL_STYLE: L.PolylineOptions = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round",
};

export function usePathDrag({
  map,
  geometry,
  segmentIndex,
  onSegmentDrag,
}: UsePathDragOptions): UsePathDragResult {
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const { snapToGraph, setDragging } = useGraphSnap();
  const isDraggingRef = useRef(false);
  const dragOriginRef = useRef<L.LatLng | null>(null);
  const dragTrailRef = useRef<L.Polyline | null>(null);
  const [hoverLatLng, setHoverLatLng] = useState<L.LatLng | null>(null);

  // Get position from mouse or touch event
  const getEventPosition = useCallback((e: MouseEvent | TouchEvent) => {
    if ("touches" in e && e.touches.length > 0) {
      return { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }
    if ("changedTouches" in e && e.changedTouches.length > 0) {
      return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
    }
    return { x: (e as MouseEvent).clientX, y: (e as MouseEvent).clientY };
  }, []);

  // Global move handler for drag (mouse + touch)
  const handleGlobalMove = useCallback(
    (e: MouseEvent | TouchEvent) => {
      if (isDraggingRef.current) {
        const pos = getEventPosition(e);
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const containerPoint = L.point(pos.x - rect.left, pos.y - rect.top);
        const latLng = map.containerPointToLatLng(containerPoint);
        // Compute snap position directly for immediate feedback
        const snapped = snapToGraph(map, latLng.lat, latLng.lng);

        // Convert snapped position to screen position for ghost pin rendering
        let screenPos = pos;
        if (snapped) {
          const snappedScreenPoint = map.latLngToContainerPoint(L.latLng(snapped.lat, snapped.lng));
          screenPos = {
            x: snappedScreenPoint.x + rect.left,
            y: snappedScreenPoint.y + rect.top
          };
        }

        updateDrag(screenPos, snapped);

        // Update drag trail line
        const trailEnd = snapped ? L.latLng(snapped.lat, snapped.lng) : latLng;
        if (dragOriginRef.current && dragTrailRef.current) {
          dragTrailRef.current.setLatLngs([dragOriginRef.current, trailEnd]);
        }
      }
    },
    [updateDrag, getEventPosition, map, snapToGraph]
  );

  // Global end handler - convert screen position to lat/lng and call callback
  const handleGlobalEnd = useCallback(
    (e: MouseEvent | TouchEvent) => {
      if (isDraggingRef.current && onSegmentDrag) {
        const pos = getEventPosition(e);
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const containerPoint = L.point(pos.x - rect.left, pos.y - rect.top);
        const latLng = map.containerPointToLatLng(containerPoint);

        // Snap to nearest graph node/edge
        const snapped = snapToGraph(map, latLng.lat, latLng.lng);
        const finalPos = snapped ?? { lat: latLng.lat, lng: latLng.lng };
        onSegmentDrag(segmentIndex, finalPos);
      }

      // Cleanup
      isDraggingRef.current = false;
      setDragging(false);
      endDrag();
      dragTrailRef.current?.remove();
      dragTrailRef.current = null;
      dragOriginRef.current = null;
      map.dragging.enable();
      document.body.classList.remove("dragging-from-path");

      document.removeEventListener("mousemove", handleGlobalMove);
      document.removeEventListener("mouseup", handleGlobalEnd);
      document.removeEventListener("touchmove", handleGlobalMove);
      document.removeEventListener("touchend", handleGlobalEnd);
      document.removeEventListener("touchcancel", handleGlobalEnd);
    },
    [map, segmentIndex, onSegmentDrag, endDrag, handleGlobalMove, getEventPosition, snapToGraph]
  );

  // Start drag on mousedown/touchstart
  const handleStart = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (!onSegmentDrag) return;

      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      setDragging(true);
      map.dragging.disable();
      document.body.classList.add("dragging-from-path");
      setHoverLatLng(null);

      // Get initial position with snapping
      const pos = getEventPosition(e.originalEvent);
      const snapped = snapToGraph(map, e.latlng.lat, e.latlng.lng);

      // Convert snapped position to screen position for ghost pin rendering
      let screenPos = pos;
      if (snapped) {
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const snappedScreenPoint = map.latLngToContainerPoint(L.latLng(snapped.lat, snapped.lng));
        screenPos = {
          x: snappedScreenPoint.x + rect.left,
          y: snappedScreenPoint.y + rect.top
        };
      }

      startDrag(screenPos, snapped);

      // Create drag trail from grab point
      const origin = snapped ? L.latLng(snapped.lat, snapped.lng) : e.latlng;
      dragOriginRef.current = origin;
      dragTrailRef.current = L.polyline([origin, origin], DRAG_TRAIL_STYLE).addTo(map);

      // Attach global listeners for both mouse and touch
      document.addEventListener("mousemove", handleGlobalMove);
      document.addEventListener("mouseup", handleGlobalEnd);
      document.addEventListener("touchmove", handleGlobalMove, { passive: false });
      document.addEventListener("touchend", handleGlobalEnd);
      document.addEventListener("touchcancel", handleGlobalEnd);
    },
    [map, onSegmentDrag, startDrag, handleGlobalMove, handleGlobalEnd, getEventPosition, snapToGraph]
  );

  // Check if a point is near the path geometry (within threshold pixels)
  const isPointNearPath = useCallback(
    (latlng: L.LatLng, thresholdPx: number = 25): boolean => {
      const coords = geometry.coordinates;
      if (coords.length < 2) return false;

      const point = map.latLngToContainerPoint(latlng);

      for (let i = 0; i < coords.length - 1; i++) {
        const a = map.latLngToContainerPoint(L.latLng(coords[i][1], coords[i][0]));
        const b = map.latLngToContainerPoint(L.latLng(coords[i + 1][1], coords[i + 1][0]));
        const dist = L.LineUtil.pointToSegmentDistance(point, a, b);
        if (dist <= thresholdPx) return true;
      }
      return false;
    },
    [map, geometry]
  );

  // Hover ghost pin handlers
  const handleHoverMove = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (isDraggingRef.current) return;
      const snapped = closestVertexOnPath(map, geometry.coordinates, e.latlng);
      setHoverLatLng(snapped ? L.latLng(snapped.lat, snapped.lng) : null);
    },
    [map, geometry.coordinates]
  );

  const handleHoverOut = useCallback(() => {
    setHoverLatLng(null);
  }, []);

  // Listen for touchstart on map container and check if near path
  useEffect(() => {
    if (!onSegmentDrag) return;

    const container = map.getContainer();

    const handleContainerTouchStart = (e: TouchEvent) => {
      if (isDraggingRef.current) return;
      if (e.touches.length !== 1) return;

      // Don't capture touches on markers - let them handle tap-to-delete
      const target = e.target as HTMLElement;
      if (target.closest(".custom-marker, .leaflet-marker-icon, .pin-container")) {
        return;
      }

      const touch = e.touches[0];
      const rect = container.getBoundingClientRect();
      const containerPoint = L.point(touch.clientX - rect.left, touch.clientY - rect.top);
      const latlng = map.containerPointToLatLng(containerPoint);

      if (isPointNearPath(latlng)) {
        e.stopPropagation();
        e.preventDefault();

        isDraggingRef.current = true;
        setDragging(true);
        map.dragging.disable();

        const snapped = snapToGraph(map, latlng.lat, latlng.lng);
        const pos = { x: touch.clientX, y: touch.clientY };

        // Convert snapped position to screen position for ghost pin rendering
        let screenPos = pos;
        if (snapped) {
          const rect = container.getBoundingClientRect();
          const snappedScreenPoint = map.latLngToContainerPoint(L.latLng(snapped.lat, snapped.lng));
          screenPos = {
            x: snappedScreenPoint.x + rect.left,
            y: snappedScreenPoint.y + rect.top
          };
        }

        startDrag(screenPos, snapped);

        // Create drag trail from grab point
        const origin = snapped ? L.latLng(snapped.lat, snapped.lng) : latlng;
        dragOriginRef.current = origin;
        dragTrailRef.current = L.polyline([origin, origin], DRAG_TRAIL_STYLE).addTo(map);

        document.addEventListener("mousemove", handleGlobalMove);
        document.addEventListener("mouseup", handleGlobalEnd);
        document.addEventListener("touchmove", handleGlobalMove, { passive: false });
        document.addEventListener("touchend", handleGlobalEnd);
        document.addEventListener("touchcancel", handleGlobalEnd);
      }
    };

    container.addEventListener("touchstart", handleContainerTouchStart, { passive: false });

    return () => {
      container.removeEventListener("touchstart", handleContainerTouchStart);
    };
  }, [map, onSegmentDrag, isPointNearPath, startDrag, handleGlobalMove, handleGlobalEnd, snapToGraph]);

  // Cleanup listeners on unmount
  useEffect(() => {
    return () => {
      document.removeEventListener("mousemove", handleGlobalMove);
      document.removeEventListener("mouseup", handleGlobalEnd);
      document.removeEventListener("touchmove", handleGlobalMove);
      document.removeEventListener("touchend", handleGlobalEnd);
      document.removeEventListener("touchcancel", handleGlobalEnd);
      if (isDraggingRef.current) {
        map.dragging.enable();
        document.body.classList.remove("dragging-from-path");
        setDragging(false);
        endDrag();
        dragTrailRef.current?.remove();
        dragTrailRef.current = null;
        dragOriginRef.current = null;
      }
    };
  }, [map, endDrag, handleGlobalMove, handleGlobalEnd]);

  return {
    isDraggingRef,
    hoverLatLng,
    handleStart,
    handleHoverMove,
    handleHoverOut,
  };
}
