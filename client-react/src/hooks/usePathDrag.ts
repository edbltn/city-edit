import { useCallback, useRef, useEffect, useState } from "react";
import L from "leaflet";
import { useGhostPin } from "../context";
import type { RouteGeometry, LatLng } from "../types";

/**
 * Find the closest point on a path geometry to a given lat/lng,
 * working in screen space for accuracy at the current zoom level.
 */
function closestPointOnPath(
  map: L.Map,
  coordinates: [number, number][],
  latlng: L.LatLng,
  thresholdPx: number = 20
): L.LatLng | null {
  if (coordinates.length < 2) return null;

  const point = map.latLngToContainerPoint(latlng);
  let bestDist = Infinity;
  let bestPoint: L.Point | null = null;

  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = map.latLngToContainerPoint(L.latLng(coordinates[i][1], coordinates[i][0]));
    const b = map.latLngToContainerPoint(L.latLng(coordinates[i + 1][1], coordinates[i + 1][0]));
    const closest = L.LineUtil.closestPointOnSegment(point, a, b);
    const dist = point.distanceTo(closest);
    if (dist < bestDist) {
      bestDist = dist;
      bestPoint = closest;
    }
  }

  if (bestDist > thresholdPx || !bestPoint) return null;
  return map.containerPointToLatLng(bestPoint);
}

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
): LatLng | null {
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
  return { lat: coordinates[bestIdx][1], lng: coordinates[bestIdx][0] };
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

export function usePathDrag({
  map,
  geometry,
  segmentIndex,
  onSegmentDrag,
}: UsePathDragOptions): UsePathDragResult {
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const isDraggingRef = useRef(false);
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
        updateDrag(pos);
      }
    },
    [updateDrag, getEventPosition]
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

        // Snap to nearest geometry vertex so the coordinate matches a graph node,
        // enabling server-side sub-path cache hits
        const snapped = closestVertexOnPath(map, geometry.coordinates, latLng);
        const finalPos = snapped ?? { lat: latLng.lat, lng: latLng.lng };
        onSegmentDrag(segmentIndex, finalPos);
      }

      // Cleanup
      isDraggingRef.current = false;
      endDrag();
      map.dragging.enable();
      document.body.style.cursor = "";

      document.removeEventListener("mousemove", handleGlobalMove);
      document.removeEventListener("mouseup", handleGlobalEnd);
      document.removeEventListener("touchmove", handleGlobalMove);
      document.removeEventListener("touchend", handleGlobalEnd);
      document.removeEventListener("touchcancel", handleGlobalEnd);
    },
    [map, geometry.coordinates, segmentIndex, onSegmentDrag, endDrag, handleGlobalMove, getEventPosition]
  );

  // Start drag on mousedown/touchstart
  const handleStart = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (!onSegmentDrag) return;

      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      map.dragging.disable();
      document.body.style.cursor = "grabbing";
      setHoverLatLng(null);

      // Get initial position
      const pos = getEventPosition(e.originalEvent);
      startDrag(pos);

      // Attach global listeners for both mouse and touch
      document.addEventListener("mousemove", handleGlobalMove);
      document.addEventListener("mouseup", handleGlobalEnd);
      document.addEventListener("touchmove", handleGlobalMove, { passive: false });
      document.addEventListener("touchend", handleGlobalEnd);
      document.addEventListener("touchcancel", handleGlobalEnd);
    },
    [map, onSegmentDrag, startDrag, handleGlobalMove, handleGlobalEnd, getEventPosition]
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
      const snapped = closestPointOnPath(map, geometry.coordinates, e.latlng);
      setHoverLatLng(snapped);
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
        map.dragging.disable();

        startDrag({ x: touch.clientX, y: touch.clientY });

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
  }, [map, onSegmentDrag, isPointNearPath, startDrag, handleGlobalMove, handleGlobalEnd]);

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
        document.body.style.cursor = "";
        endDrag();
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
