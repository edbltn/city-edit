import { useMemo, useCallback, useRef, useEffect, useState } from "react";
import { GeoJSON, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { ROUTE_COLORS } from "../../colors";
import { useGhostPin } from "../../context";
import type { TransportMode, RouteGeometry, LatLng, SplitDesirePath } from "../../types";
import type { PathOptions } from "leaflet";

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

const hoverGhostIcon = L.divIcon({
  className: "hover-ghost-marker",
  html: `<div class="pin-container hover-ghost-pin">
    <div class="pin-head" style="background: #D4A017;"></div>
    <div class="pin-needle"></div>
    <div class="pin-shadow"></div>
  </div>`,
  iconSize: [20, 28],
  iconAnchor: [17, 44],
});

interface RouteLayerProps {
  geometry: RouteGeometry;
  mode: TransportMode;
}

interface LayerStyle extends PathOptions {
  pane?: string;
}

function getRouteStyles(mode: TransportMode): LayerStyle[] {
  if (mode === "walk") {
    const colors = ROUTE_COLORS.walk;
    return [
      {
        color: colors.glow,
        weight: 12,
        opacity: 0.4,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.edge,
        weight: 8,
        opacity: 0.9,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.core,
        weight: 5,
        opacity: 1,
        dashArray: "0, 12",
        lineCap: "round",
        lineJoin: "round",
      },
    ];
  }

  if (mode === "bike") {
    const colors = ROUTE_COLORS.bike;
    return [
      {
        color: colors.glow,
        weight: 8,
        opacity: 0.3,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.middle,
        weight: 6,
        opacity: 0.5,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.core,
        weight: 4,
        opacity: 1,
        lineCap: "round",
        lineJoin: "round",
      },
    ];
  }

  // Drive mode
  const colors = ROUTE_COLORS.drive;
  return [
    {
      color: colors.glow,
      weight: 12,
      opacity: 0.4,
      lineCap: "round",
      lineJoin: "round",
    },
    {
      color: colors.asphalt,
      weight: 7,
      opacity: 0.95,
      lineCap: "round",
      lineJoin: "round",
    },
    {
      color: colors.centerLine,
      weight: 1.5,
      opacity: 0.9,
      dashArray: "6, 12",
      lineCap: "butt",
      lineJoin: "round",
    },
  ];
}

export function RouteLayer({ geometry, mode }: RouteLayerProps) {
  const styles = useMemo(() => getRouteStyles(mode), [mode]);

  // Create a unique key based on geometry coordinates
  const geometryKey = useMemo(() => {
    const coords = geometry.coordinates;
    if (coords.length === 0) return "empty";
    const first = coords[0];
    const last = coords[coords.length - 1];
    return `${first[0]}-${first[1]}-${last[0]}-${last[1]}-${coords.length}`;
  }, [geometry]);

  const geojsonData = useMemo(
    () => ({
      type: "Feature" as const,
      properties: {},
      geometry,
    }),
    [geometry]
  );

  return (
    <>
      {styles.map((style, index) => (
        <GeoJSON
          key={`route-${mode}-${index}-${geometryKey}`}
          data={geojsonData}
          style={() => ({ ...style, pane: "routePane" })}
        />
      ))}
    </>
  );
}

// Desire Path Layer
interface DesirePathLayerProps {
  geometry: RouteGeometry;
  segmentIndex?: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  mode?: TransportMode; // Optional: use walk styling when mode is "walk"
}

export function DesirePathLayer({ geometry, segmentIndex = 0, onSegmentDrag, mode }: DesirePathLayerProps) {
  const map = useMap();
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const isDraggingRef = useRef(false);
  const [hoverLatLng, setHoverLatLng] = useState<L.LatLng | null>(null);

  // Interactive weight - larger than visual for easier clicking
  const INTERACTIVE_WEIGHT = 20;

  // Visual styles - mode-specific colors
  const visualStyles: LayerStyle[] = useMemo(() => {
    if (mode === "walk") {
      // Walk: blue dots
      const colors = ROUTE_COLORS.walk;
      return [
        {
          color: colors.glow,
          weight: 12,
          opacity: 0.4,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.edge,
          weight: 8,
          opacity: 0.9,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.core,
          weight: 5,
          opacity: 1,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
      ];
    }

    if (mode === "bike") {
      // Bike: solid green line (bike lane green)
      const colors = ROUTE_COLORS.bikeDesire;
      return [
        {
          color: colors.glow,
          weight: 8,
          opacity: 0.3,
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.edge,
          weight: 6,
          opacity: 0.5,
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.core,
          weight: 4,
          opacity: 1,
          lineCap: "round",
          lineJoin: "round",
        },
      ];
    }

    // Drive: black with yellow center line (road style)
    const colors = ROUTE_COLORS.drive;
    return [
      {
        color: colors.glow,
        weight: 12,
        opacity: 0.4,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.asphalt,
        weight: 7,
        opacity: 0.95,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.centerLine,
        weight: 1.5,
        opacity: 0.9,
        dashArray: "6, 12",
        lineCap: "butt",
        lineJoin: "round",
      },
    ];
  }, [mode]);

  // Transparent interactive layer for click detection
  const interactiveStyle: LayerStyle = useMemo(
    () => ({
      color: "#000000",
      weight: INTERACTIVE_WEIGHT,
      opacity: 0.01, // Low but visible enough for touch hit detection
      lineCap: "round",
      lineJoin: "round",
    }),
    []
  );

  // Create a unique key based on geometry coordinates
  const geometryKey = useMemo(() => {
    const coords = geometry.coordinates;
    if (coords.length === 0) return "empty";
    const first = coords[0];
    const last = coords[coords.length - 1];
    return `${first[0]}-${first[1]}-${last[0]}-${last[1]}-${coords.length}`;
  }, [geometry]);

  const geojsonData = useMemo(
    () => ({
      type: "Feature" as const,
      properties: {},
      geometry,
    }),
    [geometry]
  );

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
        onSegmentDrag(segmentIndex, { lat: latLng.lat, lng: latLng.lng });
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
    [map, segmentIndex, onSegmentDrag, endDrag, handleGlobalMove, getEventPosition]
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

  return (
    <>
      {/* Visual layers (no interaction) */}
      {visualStyles.map((style, index) => (
        <GeoJSON
          key={`desire-visual-${index}-${geometryKey}`}
          data={geojsonData}
          style={() => ({ ...style, pane: "desirePathPane" })}
          interactive={false}
        />
      ))}
      {/* Transparent interactive layer on top */}
      {onSegmentDrag && (
        <GeoJSON
          key={`desire-interactive-${geometryKey}`}
          data={geojsonData}
          style={() => ({
            ...interactiveStyle,
            pane: "desirePathPane",
            className: "desire-path-interactive",
          })}
          eventHandlers={{
            mousedown: handleStart,
            mouseover: handleHoverMove,
            mousemove: handleHoverMove,
            mouseout: handleHoverOut,
          }}
        />
      )}
      {/* Hover ghost pin - snapped to path */}
      {hoverLatLng && !isDraggingRef.current && (
        <Marker
          position={hoverLatLng}
          icon={hoverGhostIcon}
          interactive={false}
          pane="desirePathPane"
        />
      )}
    </>
  );
}

// Split Desire Path Layer - renders the split paths after ghost pin drop
interface SplitDesirePathLayerProps {
  splitPath: SplitDesirePath;
  mode?: TransportMode; // Optional: use walk styling when mode is "walk"
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
}

export function SplitDesirePathLayer({ splitPath, mode, onSegmentDrag }: SplitDesirePathLayerProps) {
  const map = useMap();
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const isDraggingRef = useRef(false);
  const [hoverLatLng, setHoverLatLng] = useState<L.LatLng | null>(null);

  // Interactive weight - larger than visual for easier clicking
  const INTERACTIVE_WEIGHT = 20;

  // Visual styles - mode-specific colors
  const visualStyles: LayerStyle[] = useMemo(() => {
    if (mode === "walk") {
      // Walk: blue dots
      const colors = ROUTE_COLORS.walk;
      return [
        {
          color: colors.glow,
          weight: 12,
          opacity: 0.4,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.edge,
          weight: 8,
          opacity: 0.9,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.core,
          weight: 5,
          opacity: 1,
          dashArray: "0, 12",
          lineCap: "round",
          lineJoin: "round",
        },
      ];
    }

    if (mode === "bike") {
      // Bike: solid green line (bike lane green)
      const colors = ROUTE_COLORS.bikeDesire;
      return [
        {
          color: colors.glow,
          weight: 8,
          opacity: 0.3,
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.edge,
          weight: 6,
          opacity: 0.5,
          lineCap: "round",
          lineJoin: "round",
        },
        {
          color: colors.core,
          weight: 4,
          opacity: 1,
          lineCap: "round",
          lineJoin: "round",
        },
      ];
    }

    // Drive: black with yellow center line (road style)
    const colors = ROUTE_COLORS.drive;
    return [
      {
        color: colors.glow,
        weight: 12,
        opacity: 0.4,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.asphalt,
        weight: 7,
        opacity: 0.95,
        lineCap: "round",
        lineJoin: "round",
      },
      {
        color: colors.centerLine,
        weight: 1.5,
        opacity: 0.9,
        dashArray: "6, 12",
        lineCap: "butt",
        lineJoin: "round",
      },
    ];
  }, [mode]);

  // Transparent interactive layer for click detection
  const interactiveStyle: LayerStyle = useMemo(
    () => ({
      color: "#000000",
      weight: INTERACTIVE_WEIGHT,
      opacity: 0.01, // Low but visible enough for touch hit detection
      lineCap: "round",
      lineJoin: "round",
    }),
    []
  );

  const geometryKey = useMemo(() => {
    const coords = splitPath.geometry.coordinates;
    if (coords.length === 0) return "empty";
    const first = coords[0];
    const last = coords[coords.length - 1];
    return `${splitPath.id}-${first[0]}-${first[1]}-${last[0]}-${last[1]}-${coords.length}`;
  }, [splitPath]);

  const geojsonData = useMemo(
    () => ({
      type: "Feature" as const,
      properties: {},
      geometry: splitPath.geometry,
    }),
    [splitPath.geometry]
  );

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
        onSegmentDrag(splitPath.segmentIndex, { lat: latLng.lat, lng: latLng.lng });
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
    [map, splitPath.segmentIndex, onSegmentDrag, endDrag, handleGlobalMove, getEventPosition]
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
      const coords = splitPath.geometry.coordinates;
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
    [map, splitPath.geometry]
  );

  // Hover ghost pin handlers
  const handleHoverMove = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (isDraggingRef.current) return;
      const snapped = closestPointOnPath(map, splitPath.geometry.coordinates, e.latlng);
      setHoverLatLng(snapped);
    },
    [map, splitPath.geometry.coordinates]
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
  }, [map, onSegmentDrag, splitPath.segmentIndex, isPointNearPath, startDrag, handleGlobalMove, handleGlobalEnd]);

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

  return (
    <>
      {/* Visual layers (no interaction) */}
      {visualStyles.map((style, index) => (
        <GeoJSON
          key={`split-${splitPath.id}-${index}-${geometryKey}`}
          data={geojsonData}
          style={() => ({ ...style, pane: "desirePathPane" })}
          interactive={false}
        />
      ))}
      {/* Transparent interactive layer on top */}
      {onSegmentDrag && (
        <GeoJSON
          key={`split-interactive-${splitPath.id}-${geometryKey}`}
          data={geojsonData}
          style={() => ({
            ...interactiveStyle,
            pane: "desirePathPane",
            className: "split-path-interactive",
          })}
          eventHandlers={{
            mousedown: handleStart,
            mouseover: handleHoverMove,
            mousemove: handleHoverMove,
            mouseout: handleHoverOut,
          }}
        />
      )}
      {/* Hover ghost pin - snapped to path */}
      {hoverLatLng && !isDraggingRef.current && (
        <Marker
          position={hoverLatLng}
          icon={hoverGhostIcon}
          interactive={false}
          pane="desirePathPane"
        />
      )}
    </>
  );
}
