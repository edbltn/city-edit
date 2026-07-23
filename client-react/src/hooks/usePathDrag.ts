import { useCallback, useRef, useEffect, useState } from "react";
import { useGhostPin, useGraphSnap } from "../context";
import {
  UTIL_SOURCE_ID,
  setOverlayFeature,
  removeOverlayFeature,
  setDesirePathDimmed,
} from "../map/maplibreOverlays";
import type { MapFacade, MapMouseEvent } from "../map/facade";
import type { RouteGeometry, LatLng } from "../types";

// Pixel radius around the path centerline that counts as "on the path" for
// hover + drag-start — half the old 20px-wide invisible Leaflet hit-line.
const PATH_HIT_PX = 10;

// Only one path layer may own the hover ghost / an active drag at a time.
// With split paths, two layers share an endpoint; without this claim both
// would show ghosts (the fat Leaflet hit-lines used to arbitrate by stacking).
let hoverClaim: symbol | null = null;
let dragActive = false;

/**
 * Closest point on the path *polyline* (not just a vertex) to a given lat/lng,
 * projected onto the nearest segment. Returns a continuous point so the hover
 * ghost glides smoothly along the path's shape instead of jumping between
 * discrete vertices. Display-only — the committed drop still snaps via the graph.
 */
function closestPointOnPath(
  map: MapFacade,
  coordinates: [number, number][],
  latlng: LatLng,
  thresholdPx: number = PATH_HIT_PX
): LatLng | null {
  if (coordinates.length === 0) return null;
  const p = map.latLngToContainerPoint(latlng);

  if (coordinates.length === 1) {
    const only = map.latLngToContainerPoint({ lat: coordinates[0][1], lng: coordinates[0][0] });
    const d = Math.hypot(p.x - only.x, p.y - only.y);
    return d <= thresholdPx
      ? { lat: coordinates[0][1], lng: coordinates[0][0] }
      : null;
  }

  let bestDist = Infinity;
  let bestPoint: { x: number; y: number } | null = null;

  for (let i = 0; i < coordinates.length - 1; i++) {
    const a = map.latLngToContainerPoint({ lat: coordinates[i][1], lng: coordinates[i][0] });
    const b = map.latLngToContainerPoint({ lat: coordinates[i + 1][1], lng: coordinates[i + 1][0] });
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lenSq = dx * dx + dy * dy;
    // Project p onto segment a→b, clamped to the segment.
    const t = lenSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lenSq));
    const projX = a.x + t * dx;
    const projY = a.y + t * dy;
    const dist = Math.hypot(p.x - projX, p.y - projY);
    if (dist < bestDist) {
      bestDist = dist;
      bestPoint = { x: projX, y: projY };
    }
  }

  if (bestDist > thresholdPx || !bestPoint) return null;
  return map.containerPointToLatLng(bestPoint);
}

interface UsePathDragOptions {
  map: MapFacade;
  geometry: RouteGeometry;
  segmentIndex: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  /** Fires true/false as the cursor enters/leaves the path's hit radius. */
  onHoverChange?: (isHovering: boolean) => void;
}

interface UsePathDragResult {
  isDraggingRef: React.MutableRefObject<boolean>;
  hoverLatLng: LatLng | null;
}

let trailCounter = 0;

/**
 * Drag-to-insert-waypoint interaction on a desire path. Hit-testing is pure
 * math against the geometry (the Flatbush pattern — camera-independent), not
 * a fat invisible hit-line: a map mousedown/touchstart within PATH_HIT_PX of
 * the polyline starts a drag; while idle, mousemoves within the radius show
 * the hover ghost pin.
 */
export function usePathDrag({
  map,
  geometry,
  segmentIndex,
  onSegmentDrag,
  onHoverChange,
}: UsePathDragOptions): UsePathDragResult {
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const { snapToGraph, setDragging } = useGraphSnap();
  const isDraggingRef = useRef(false);
  const dragOriginRef = useRef<LatLng | null>(null);
  const dragStartClientRef = useRef<{ x: number; y: number } | null>(null);
  const [hoverLatLng, setHoverLatLng] = useState<LatLng | null>(null);

  // This instance's identity for the module-level hover/drag claims, and its
  // keyed trail feature in the util-lines source.
  const tokenRef = useRef<symbol | null>(null);
  if (!tokenRef.current) tokenRef.current = Symbol("path-drag");
  const trailKeyRef = useRef(`trail-path-${++trailCounter}`);

  const onHoverChangeRef = useRef(onHoverChange);
  useEffect(() => { onHoverChangeRef.current = onHoverChange; }, [onHoverChange]);

  const geometryRef = useRef(geometry);
  useEffect(() => { geometryRef.current = geometry; }, [geometry]);

  const setHovering = useCallback((pos: LatLng | null) => {
    const token = tokenRef.current!;
    if (pos) {
      if (hoverClaim && hoverClaim !== token) return; // another path owns it
      hoverClaim = token;
      map.getCanvas().style.cursor = "grab";
      setHoverLatLng(pos);
      onHoverChangeRef.current?.(true);
    } else if (hoverClaim === token) {
      hoverClaim = null;
      map.getCanvas().style.cursor = "";
      setHoverLatLng(null);
      onHoverChangeRef.current?.(false);
    } else {
      setHoverLatLng(null);
    }
  }, [map]);

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

  const updateTrail = useCallback((end: LatLng) => {
    const origin = dragOriginRef.current;
    if (!origin) return;
    setOverlayFeature(UTIL_SOURCE_ID, trailKeyRef.current, {
      type: "LineString",
      coordinates: [[origin.lng, origin.lat], [end.lng, end.lat]],
    });
  }, []);

  // Global move handler for drag (mouse + touch)
  const handleGlobalMove = useCallback(
    (e: MouseEvent | TouchEvent) => {
      if (isDraggingRef.current) {
        const pos = getEventPosition(e);
        const rect = map.getContainer().getBoundingClientRect();
        const latLng = map.containerPointToLatLng({ x: pos.x - rect.left, y: pos.y - rect.top });
        // Underlying snap (edge/node via hitTest — never a top proposal). The
        // ghost itself follows the raw cursor, like the start/end markers; the
        // snap only drives the trail end and the committed drop position.
        const snapped = snapToGraph(latLng.lat, latLng.lng);

        updateDrag(pos, snapped);
        updateTrail(snapped ?? latLng);
      }
    },
    [updateDrag, getEventPosition, map, snapToGraph, updateTrail]
  );

  // Global end handler - convert screen position to lat/lng and call callback
  const handleGlobalEnd = useCallback(
    (e: MouseEvent | TouchEvent) => {
      if (isDraggingRef.current && onSegmentDrag) {
        const pos = getEventPosition(e);
        // A press-and-release without real movement is a tap, not a drag —
        // inserting a waypoint at the grab point would be a no-op for the
        // route but adds a stray marker + recalc (easy to trigger on touch,
        // e.g. a double-tap landing on the fresh path).
        const startPos = dragStartClientRef.current;
        const movedPx = startPos ? Math.hypot(pos.x - startPos.x, pos.y - startPos.y) : Infinity;
        if (movedPx >= 8) {
          const rect = map.getContainer().getBoundingClientRect();
          const latLng = map.containerPointToLatLng({ x: pos.x - rect.left, y: pos.y - rect.top });

          // Snap to nearest graph node/edge
          const snapped = snapToGraph(latLng.lat, latLng.lng);
          const finalPos = snapped ?? latLng;
          onSegmentDrag(segmentIndex, finalPos);
        }
      }

      // Cleanup
      isDraggingRef.current = false;
      dragActive = false;
      setDragging(false);
      endDrag();
      removeOverlayFeature(UTIL_SOURCE_ID, trailKeyRef.current);
      dragOriginRef.current = null;
      dragStartClientRef.current = null;
      setDesirePathDimmed(false);
      document.body.classList.remove("dragging-from-path");
      map.getCanvas().style.cursor = "";

      document.removeEventListener("mousemove", handleGlobalMove);
      document.removeEventListener("mouseup", handleGlobalEnd);
      document.removeEventListener("touchmove", handleGlobalMove);
      document.removeEventListener("touchend", handleGlobalEnd);
      document.removeEventListener("touchcancel", handleGlobalEnd);
    },
    [map, segmentIndex, onSegmentDrag, endDrag, handleGlobalMove, getEventPosition, snapToGraph, setDragging]
  );

  // Shared drag-start: grab point in client coords + the lat/lng under it.
  const beginDrag = useCallback(
    (clientPos: { x: number; y: number }, latlng: LatLng) => {
      isDraggingRef.current = true;
      dragActive = true;
      dragStartClientRef.current = clientPos;
      setDragging(true);
      setDesirePathDimmed(true);
      document.body.classList.add("dragging-from-path");
      map.getCanvas().style.cursor = "grabbing";
      setHovering(null);

      // Ghost starts at the raw grab point (unsnapped); snapped is the
      // underlying edge/node used for the trail + drop.
      const snapped = snapToGraph(latlng.lat, latlng.lng);
      startDrag(clientPos, snapped);

      const origin = snapped ?? latlng;
      dragOriginRef.current = origin;
      updateTrail(origin);

      // Attach global listeners for both mouse and touch
      document.addEventListener("mousemove", handleGlobalMove);
      document.addEventListener("mouseup", handleGlobalEnd);
      document.addEventListener("touchmove", handleGlobalMove, { passive: false });
      document.addEventListener("touchend", handleGlobalEnd);
      document.addEventListener("touchcancel", handleGlobalEnd);
    },
    [map, setDragging, setHovering, snapToGraph, startDrag, updateTrail, handleGlobalMove, handleGlobalEnd]
  );

  // Map mouse handlers: hover ghost while idle, drag start on press.
  useEffect(() => {
    if (!onSegmentDrag) return;

    const handleMapMouseMove = (e: MapMouseEvent) => {
      if (isDraggingRef.current || dragActive) return;
      const snapped = closestPointOnPath(map, geometryRef.current.coordinates, e.latlng);
      setHovering(snapped);
    };

    const handleMapMouseOut = () => {
      if (isDraggingRef.current) return;
      setHovering(null);
    };

    const handleMapMouseDown = (e: MapMouseEvent) => {
      if (isDraggingRef.current || dragActive) return;
      const near = closestPointOnPath(map, geometryRef.current.coordinates, e.latlng);
      if (!near) return;
      // Ours — stop the map's drag-pan for this gesture.
      e.preventDefault();
      e.originalEvent.preventDefault();
      beginDrag({ x: e.originalEvent.clientX, y: e.originalEvent.clientY }, e.latlng);
    };

    map.on("mousemove", handleMapMouseMove);
    map.on("mouseout", handleMapMouseOut);
    map.on("mousedown", handleMapMouseDown);
    return () => {
      map.off("mousemove", handleMapMouseMove);
      map.off("mouseout", handleMapMouseOut);
      map.off("mousedown", handleMapMouseDown);
    };
  }, [map, onSegmentDrag, setHovering, beginDrag]);

  // Touch: listen on the map container in the capture phase (MapLibre's own
  // touch handlers sit on the canvas container below), grab the gesture when
  // it lands near the path.
  useEffect(() => {
    if (!onSegmentDrag) return;

    const container = map.getContainer();

    const handleContainerTouchStart = (e: TouchEvent) => {
      if (isDraggingRef.current || dragActive) return;
      if (e.touches.length !== 1) return;

      // Don't capture touches on markers - let them handle tap-to-delete
      const target = e.target as HTMLElement;
      if (target.closest(".maplibregl-marker, .custom-marker, .pin-container")) {
        return;
      }

      const touch = e.touches[0];
      const rect = container.getBoundingClientRect();
      const latlng = map.containerPointToLatLng({
        x: touch.clientX - rect.left,
        y: touch.clientY - rect.top,
      });

      // Slightly wider radius than mouse — fingers are imprecise.
      if (closestPointOnPath(map, geometryRef.current.coordinates, latlng, 25)) {
        e.stopPropagation();
        e.preventDefault();
        beginDrag({ x: touch.clientX, y: touch.clientY }, latlng);
      }
    };

    container.addEventListener("touchstart", handleContainerTouchStart, { passive: false, capture: true });

    return () => {
      container.removeEventListener("touchstart", handleContainerTouchStart, { capture: true });
    };
  }, [map, onSegmentDrag, beginDrag]);

  // Cleanup listeners on unmount
  useEffect(() => {
    const trailKey = trailKeyRef.current;
    return () => {
      document.removeEventListener("mousemove", handleGlobalMove);
      document.removeEventListener("mouseup", handleGlobalEnd);
      document.removeEventListener("touchmove", handleGlobalMove);
      document.removeEventListener("touchend", handleGlobalEnd);
      document.removeEventListener("touchcancel", handleGlobalEnd);
      if (hoverClaim === tokenRef.current) {
        hoverClaim = null;
        map.getCanvas().style.cursor = "";
        onHoverChangeRef.current?.(false);
      }
      if (isDraggingRef.current) {
        isDraggingRef.current = false;
        dragActive = false;
        document.body.classList.remove("dragging-from-path");
        map.getCanvas().style.cursor = "";
        setDragging(false);
        endDrag();
        setDesirePathDimmed(false);
        removeOverlayFeature(UTIL_SOURCE_ID, trailKey);
        dragOriginRef.current = null;
      }
    };
  }, [map, endDrag, handleGlobalMove, handleGlobalEnd, setDragging]);

  return {
    isDraggingRef,
    hoverLatLng,
  };
}
