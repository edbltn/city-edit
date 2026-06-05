import { useCallback, useRef, useEffect, useState } from "react";
import L from "leaflet";
import { useGhostPin, useGraphSnap } from "../context";
import { isTap } from "../utils/gesture";
import type { RouteGeometry, LatLng } from "../types";


/**
 * Closest point on the path *polyline* (not just a vertex) to a given lat/lng,
 * projected onto the nearest segment. Returns a continuous point so the hover
 * ghost glides smoothly along the path's shape instead of jumping between
 * discrete vertices. Display-only — the committed drop still snaps via the graph.
 */
function closestPointOnPath(
  map: L.Map,
  coordinates: [number, number][],
  latlng: L.LatLng,
  thresholdPx: number = 24
): LatLng | null {
  if (coordinates.length === 0) return null;
  if (coordinates.length === 1) {
    const only = map.latLngToContainerPoint(L.latLng(coordinates[0][1], coordinates[0][0]));
    const p0 = map.latLngToContainerPoint(latlng);
    return p0.distanceTo(only) <= thresholdPx
      ? { lat: coordinates[0][1], lng: coordinates[0][0] }
      : null;
  }

  const p = map.latLngToContainerPoint(latlng);
  let bestDist = Infinity;
  let bestPoint: L.Point | null = null;

  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = map.latLngToContainerPoint(L.latLng(coordinates[i][1], coordinates[i][0]));
    const b = map.latLngToContainerPoint(L.latLng(coordinates[i + 1][1], coordinates[i + 1][0]));
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lenSq = dx * dx + dy * dy;
    // Project p onto segment a→b, clamped to the segment.
    const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
    const proj = L.point(a.x + t * dx, a.y + t * dy);
    const dist = p.distanceTo(proj);
    if (dist < bestDist) {
      bestDist = dist;
      bestPoint = proj;
    }
  }

  if (bestDist > thresholdPx || !bestPoint) return null;
  const ll = map.containerPointToLatLng(bestPoint);
  return { lat: ll.lat, lng: ll.lng };
}

interface UsePathDragOptions {
  map: L.Map;
  geometry: RouteGeometry;
  segmentIndex: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  /** Quick press-and-release (tap) on the path — the time-based counterpart to a
   *  drag (which inserts a mid). The host restarts the route from here (clear +
   *  new start); on a passthrough on-path proposal this is its tap side effect.
   *  See the TOP-PROPOSAL INTERACTION MODEL comment in GraphLayer.tsx. */
  onTap?: (position: LatLng) => void;
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
  onTap,
}: UsePathDragOptions): UsePathDragResult {
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const { snapToGraph, setDragging } = useGraphSnap();
  const isDraggingRef = useRef(false);
  const dragOriginRef = useRef<L.LatLng | null>(null);
  const dragTrailRef = useRef<L.Polyline | null>(null);
  // When the press began (Date.now), so release can tell a tap (quick → restart)
  // from a drag (held → insert a mid) via the shared TAP_MAX_MS convention. The
  // polyline isn't a Leaflet draggable, so timing is the only signal we get.
  const pressStartRef = useRef(0);
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
        // Underlying snap (edge/node via hitTest — never a top proposal). The
        // ghost itself follows the raw cursor, like the start/end markers; the
        // snap only drives the trail end and the committed drop position.
        const snapped = snapToGraph(map, latLng.lat, latLng.lng);

        updateDrag(pos, snapped);

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
      if (isDraggingRef.current) {
        const pos = getEventPosition(e);
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const containerPoint = L.point(pos.x - rect.left, pos.y - rect.top);
        const latLng = map.containerPointToLatLng(containerPoint);

        // Snap to nearest graph node/edge
        const snapped = snapToGraph(map, latLng.lat, latLng.lng);
        const finalPos = snapped ?? { lat: latLng.lat, lng: latLng.lng };
        // Quick release → a tap on the path → restart the route from here.
        // A held press → a drag → insert a mid at the drop point. (A long press
        // that never moved still reads as a drag and drops a mid where pressed —
        // a "pin here" — which is safer than a stray route-clear.)
        if (isTap(pressStartRef.current)) {
          onTap?.(finalPos);
        } else {
          onSegmentDrag?.(segmentIndex, finalPos);
        }
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
    [map, segmentIndex, onSegmentDrag, onTap, endDrag, handleGlobalMove, getEventPosition, snapToGraph]
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

      // Ghost starts at the raw grab point (unsnapped); snapped is the
      // underlying edge/node used for the trail + drop.
      const pos = getEventPosition(e.originalEvent);
      pressStartRef.current = Date.now();
      const snapped = snapToGraph(map, e.latlng.lat, e.latlng.lng);

      startDrag(pos, snapped);

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
      const snapped = closestPointOnPath(map, geometry.coordinates, e.latlng);
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
        pressStartRef.current = Date.now();

        // Ghost follows the finger (unsnapped); snapped is the underlying drop.
        startDrag(pos, snapped);

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
