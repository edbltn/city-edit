import { useMemo, useCallback, useRef, useEffect } from "react";
import { GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import { ROUTE_COLORS } from "../../colors";
import { useGhostPin } from "../../context";
import type { TransportMode, RouteGeometry, LatLng, SplitDesirePath } from "../../types";
import type { PathOptions } from "leaflet";

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

  // Interactive weight - larger than visual for easier clicking
  const INTERACTIVE_WEIGHT = 20;

  // Visual styles - use walk styling for walk mode, desire styling otherwise
  const visualStyles: LayerStyle[] = useMemo(() => {
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

    // Default: gold desire path styling
    const colors = ROUTE_COLORS.desire;
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
  }, [mode]);

  // Transparent interactive layer for click detection
  const interactiveStyle: LayerStyle = useMemo(
    () => ({
      color: "#000000",
      weight: INTERACTIVE_WEIGHT,
      opacity: 0.001, // Nearly invisible but still interactive
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

  // Global mousemove handler for drag
  const handleGlobalMouseMove = useCallback(
    (e: MouseEvent) => {
      if (isDraggingRef.current) {
        updateDrag({ x: e.clientX, y: e.clientY });
      }
    },
    [updateDrag]
  );

  // Global mouseup handler - convert screen position to lat/lng and call callback
  const handleGlobalMouseUp = useCallback(
    (e: MouseEvent) => {
      if (isDraggingRef.current && onSegmentDrag) {
        // Get map container rect to calculate relative position
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const containerPoint = L.point(
          e.clientX - rect.left,
          e.clientY - rect.top
        );
        const latLng = map.containerPointToLatLng(containerPoint);
        onSegmentDrag(segmentIndex, { lat: latLng.lat, lng: latLng.lng });
      }

      // Cleanup
      isDraggingRef.current = false;
      endDrag();
      map.dragging.enable();

      // Restore default cursor
      document.body.style.cursor = "";

      document.removeEventListener("mousemove", handleGlobalMouseMove);
      document.removeEventListener("mouseup", handleGlobalMouseUp);
    },
    [map, segmentIndex, onSegmentDrag, endDrag, handleGlobalMouseMove]
  );

  // Mousedown on path starts the drag
  const handleMouseDown = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (!onSegmentDrag) return;

      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      map.dragging.disable();

      // Set grabbing cursor globally
      document.body.style.cursor = "grabbing";

      // Start ghost pin at cursor position
      startDrag({
        x: e.originalEvent.clientX,
        y: e.originalEvent.clientY,
      });

      // Attach global listeners
      document.addEventListener("mousemove", handleGlobalMouseMove);
      document.addEventListener("mouseup", handleGlobalMouseUp);
    },
    [map, onSegmentDrag, startDrag, handleGlobalMouseMove, handleGlobalMouseUp]
  );

  // Cleanup listeners on unmount
  useEffect(() => {
    return () => {
      document.removeEventListener("mousemove", handleGlobalMouseMove);
      document.removeEventListener("mouseup", handleGlobalMouseUp);
      if (isDraggingRef.current) {
        map.dragging.enable();
        document.body.style.cursor = "";
        endDrag();
      }
    };
  }, [map, endDrag, handleGlobalMouseMove, handleGlobalMouseUp]);

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
          eventHandlers={{ mousedown: handleMouseDown }}
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

  // Interactive weight - larger than visual for easier clicking
  const INTERACTIVE_WEIGHT = 20;

  const visualStyles: LayerStyle[] = useMemo(() => {
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

    // Default: gold split desire path styling
    const colors = ROUTE_COLORS.splitDesire;
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
  }, [mode]);

  // Transparent interactive layer for click detection
  const interactiveStyle: LayerStyle = useMemo(
    () => ({
      color: "#000000",
      weight: INTERACTIVE_WEIGHT,
      opacity: 0.001, // Nearly invisible but still interactive
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

  // Global mousemove handler for drag
  const handleGlobalMouseMove = useCallback(
    (e: MouseEvent) => {
      if (isDraggingRef.current) {
        updateDrag({ x: e.clientX, y: e.clientY });
      }
    },
    [updateDrag]
  );

  // Global mouseup handler - convert screen position to lat/lng and call callback
  const handleGlobalMouseUp = useCallback(
    (e: MouseEvent) => {
      if (isDraggingRef.current && onSegmentDrag) {
        // Get map container rect to calculate relative position
        const container = map.getContainer();
        const rect = container.getBoundingClientRect();
        const containerPoint = L.point(
          e.clientX - rect.left,
          e.clientY - rect.top
        );
        const latLng = map.containerPointToLatLng(containerPoint);
        onSegmentDrag(splitPath.segmentIndex, { lat: latLng.lat, lng: latLng.lng });
      }

      // Cleanup
      isDraggingRef.current = false;
      endDrag();
      map.dragging.enable();

      // Restore default cursor
      document.body.style.cursor = "";

      document.removeEventListener("mousemove", handleGlobalMouseMove);
      document.removeEventListener("mouseup", handleGlobalMouseUp);
    },
    [map, onSegmentDrag, splitPath.segmentIndex, endDrag, handleGlobalMouseMove]
  );

  // Mousedown on path starts the drag
  const handleMouseDown = useCallback(
    (e: L.LeafletMouseEvent) => {
      if (!onSegmentDrag) return;

      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      map.dragging.disable();

      // Set grabbing cursor globally
      document.body.style.cursor = "grabbing";

      // Start ghost pin at cursor position
      startDrag({
        x: e.originalEvent.clientX,
        y: e.originalEvent.clientY,
      });

      // Attach global listeners
      document.addEventListener("mousemove", handleGlobalMouseMove);
      document.addEventListener("mouseup", handleGlobalMouseUp);
    },
    [map, onSegmentDrag, startDrag, handleGlobalMouseMove, handleGlobalMouseUp]
  );

  // Cleanup listeners on unmount
  useEffect(() => {
    return () => {
      document.removeEventListener("mousemove", handleGlobalMouseMove);
      document.removeEventListener("mouseup", handleGlobalMouseUp);
      if (isDraggingRef.current) {
        map.dragging.enable();
        document.body.style.cursor = "";
        endDrag();
      }
    };
  }, [map, endDrag, handleGlobalMouseMove, handleGlobalMouseUp]);

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
          eventHandlers={{ mousedown: handleMouseDown }}
        />
      )}
    </>
  );
}
