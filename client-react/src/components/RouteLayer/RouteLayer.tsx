import React, { useMemo, useCallback, useRef, useEffect } from "react";
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

// ── Editable Route Layer (Google Maps-style) ─────────────────────────
// Renders the route with draggable vertex handles. Unmodified segments
// use the mode color; modified segments use gold desire-path color.
// Dragging between handles inserts a new vertex.

import type { EditVertex } from "../../types";
import { Polyline, useMap as useMapForVertex } from "react-leaflet";

// Draggable vertex handle that doesn't fight with React during drag
function DraggableVertexHandle({
  position,
  fillColor,
  vertexIndex,
  onDragEnd,
  onDragStart,
}: {
  position: [number, number];
  fillColor: string;
  vertexIndex: number;
  onDragEnd: (vertexIndex: number, e: L.LeafletEvent) => void;
  onDragStart?: () => void;
}) {
  const map = useMapForVertex();
  const markerRef = useRef<L.Marker | null>(null);
  const isDraggingRef = useRef(false);

  const icon = useMemo(() => L.divIcon({
    className: "",
    html: `<div style="
      width: 12px; height: 12px; border-radius: 50%;
      background: ${fillColor}; border: 2px solid #fff;
      box-shadow: 0 1px 3px rgba(0,0,0,0.3);
      cursor: grab;
    "></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  }), [fillColor]);

  // Create marker on mount, destroy on unmount
  useEffect(() => {
    const marker = L.marker(position, {
      icon,
      draggable: true,
      pane: "routePane",
    }).addTo(map);

    marker.on("dragstart", () => {
      isDraggingRef.current = true;
      map.dragging.disable();
      onDragStart?.();
    });

    marker.on("dragend", (e) => {
      isDraggingRef.current = false;
      map.dragging.enable();
      onDragEnd(vertexIndex, e);
    });

    markerRef.current = marker;

    return () => {
      marker.remove();
      markerRef.current = null;
    };
    // Only recreate when vertexIndex changes (stable identity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, vertexIndex]);

  // Update position from props only when NOT dragging
  useEffect(() => {
    if (markerRef.current && !isDraggingRef.current) {
      markerRef.current.setLatLng(position);
    }
  }, [position]);

  // Update icon when color changes
  useEffect(() => {
    if (markerRef.current) {
      markerRef.current.setIcon(icon);
    }
  }, [icon]);

  return null;
}

interface EditableRouteLayerProps {
  geometry: RouteGeometry;
  mode: TransportMode;
  editVertices: EditVertex[];
  editedSegments: SplitDesirePath[];
  modifiedSegmentIndices: Set<number>;
  onVertexDrag: (vertexIndex: number, position: LatLng) => void;
  onLineDrag: (afterVertexIndex: number, position: LatLng) => void;
  onDragStart?: () => void;
}

// Get the core color for a mode (used for unmodified segments)
function getModeColor(mode: TransportMode): string {
  if (mode === "walk") return ROUTE_COLORS.walk.core;
  if (mode === "bike") return ROUTE_COLORS.bike.core;
  return ROUTE_COLORS.drive.asphalt;
}

export function EditableRouteLayer({
  geometry,
  mode,
  editVertices,
  editedSegments,
  modifiedSegmentIndices,
  onVertexDrag,
  onLineDrag,
  onDragStart,
}: EditableRouteLayerProps) {
  const map = useMap();
  const isDraggingRef = useRef(false);
  const dragSegmentRef = useRef(-1);

  const modeColor = useMemo(() => getModeColor(mode), [mode]);
  const desireColor = ROUTE_COLORS.desire.core;

  // Build segments between vertices from original geometry
  const segments = useMemo(() => {
    if (editVertices.length < 2) return [];

    const segs: { positions: [number, number][]; index: number }[] = [];
    for (let i = 0; i < editVertices.length - 1; i++) {
      const v1 = editVertices[i];
      const v2 = editVertices[i + 1];

      // If this segment is modified, we render the edited geometry instead
      if (modifiedSegmentIndices.has(i)) continue;

      // Extract coordinates between these two vertex coord indices from original geometry
      if (v1.coordIndex >= 0 && v2.coordIndex >= 0) {
        const slice = geometry.coordinates.slice(v1.coordIndex, v2.coordIndex + 1);
        segs.push({ positions: slice, index: i });
      } else {
        // Fallback: straight line between vertex positions
        segs.push({
          positions: [
            [v1.position.lng, v1.position.lat],
            [v2.position.lng, v2.position.lat],
          ],
          index: i,
        });
      }
    }
    return segs;
  }, [editVertices, geometry, modifiedSegmentIndices]);

  // Handle mid-line drag (between vertices)
  const handleSegmentMouseDown = useCallback(
    (segmentIndex: number, e: L.LeafletMouseEvent) => {
      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      dragSegmentRef.current = segmentIndex;
      map.dragging.disable();
      document.body.style.cursor = "grabbing";
      onDragStart?.();

      const handleMove = (_ev: MouseEvent) => {
        // Ghost pin visual could be added here; for now just cursor
      };

      const handleUp = (ev: MouseEvent) => {
        if (isDraggingRef.current) {
          const container = map.getContainer();
          const rect = container.getBoundingClientRect();
          const containerPoint = L.point(ev.clientX - rect.left, ev.clientY - rect.top);
          const latLng = map.containerPointToLatLng(containerPoint);
          onLineDrag(dragSegmentRef.current, { lat: latLng.lat, lng: latLng.lng });
        }
        isDraggingRef.current = false;
        map.dragging.enable();
        document.body.style.cursor = "";
        document.removeEventListener("mousemove", handleMove);
        document.removeEventListener("mouseup", handleUp);
      };

      document.addEventListener("mousemove", handleMove);
      document.addEventListener("mouseup", handleUp);
    },
    [map, onLineDrag, onDragStart]
  );

  // Vertex handle drag end
  const handleVertexDragEnd = useCallback(
    (vertexIndex: number, e: L.LeafletEvent) => {
      const marker = e.target as L.Marker;
      const pos = marker.getLatLng();
      onVertexDrag(vertexIndex, { lat: pos.lat, lng: pos.lng });
    },
    [onVertexDrag]
  );

  // Walk mode uses dashed styling
  const isWalk = mode === "walk";
  const dashArray = isWalk ? "0, 12" : undefined;

  return (
    <>
      {/* Unmodified segments - rendered in mode color */}
      {segments.map(({ positions, index }) => {
        // Convert [lng, lat] to [lat, lng] for Leaflet
        const latLngs = positions.map(([lng, lat]) => [lat, lng] as [number, number]);
        const key = `seg-${index}-${latLngs.length}-${latLngs[0]?.[0]}`;

        return (
          <React.Fragment key={key}>
            {/* Visual line */}
            <Polyline
              positions={latLngs}
              pathOptions={{
                color: modeColor,
                weight: 4,
                opacity: 1,
                dashArray,
                lineCap: "round",
                lineJoin: "round",
              }}
              pane="routePane"
              interactive={false}
            />
            {/* Interactive transparent overlay for mid-line dragging */}
            <Polyline
              positions={latLngs}
              pathOptions={{
                color: "#000000",
                weight: 20,
                opacity: 0.001,
                lineCap: "round",
                lineJoin: "round",
              }}
              pane="routePane"
              eventHandlers={{
                mousedown: (e) => handleSegmentMouseDown(index, e),
              }}
            />
          </React.Fragment>
        );
      })}

      {/* Modified segments - rendered in gold */}
      {editedSegments.map((seg) => {
        const coords = seg.geometry.coordinates;
        const latLngs = coords.map(([lng, lat]) => [lat, lng] as [number, number]);
        const key = `edited-${seg.id}-${latLngs.length}`;

        return (
          <React.Fragment key={key}>
            {/* Gold glow */}
            <Polyline
              positions={latLngs}
              pathOptions={{
                color: ROUTE_COLORS.desire.glow,
                weight: 8,
                opacity: 0.3,
                lineCap: "round",
                lineJoin: "round",
              }}
              pane="routePane"
              interactive={false}
            />
            {/* Gold core */}
            <Polyline
              positions={latLngs}
              pathOptions={{
                color: desireColor,
                weight: 4,
                opacity: 1,
                lineCap: "round",
                lineJoin: "round",
              }}
              pane="routePane"
              interactive={false}
            />
            {/* Interactive overlay for further dragging */}
            <Polyline
              positions={latLngs}
              pathOptions={{
                color: "#000000",
                weight: 20,
                opacity: 0.001,
                lineCap: "round",
                lineJoin: "round",
              }}
              pane="routePane"
              eventHandlers={{
                mousedown: (e) => handleSegmentMouseDown(seg.segmentIndex, e),
              }}
            />
          </React.Fragment>
        );
      })}

      {/* Vertex handles - skip first and last (those are start/end markers) */}
      {editVertices.slice(1, -1).map((vertex, i) => {
        const vertexIndex = i + 1;
        const isModified = modifiedSegmentIndices.has(vertexIndex - 1) || modifiedSegmentIndices.has(vertexIndex);
        const fillColor = isModified ? desireColor : modeColor;

        return (
          <DraggableVertexHandle
            key={`vertex-${vertexIndex}`}
            position={[vertex.position.lat, vertex.position.lng]}
            fillColor={fillColor}
            vertexIndex={vertexIndex}
            onDragEnd={handleVertexDragEnd}
            onDragStart={onDragStart}
          />
        );
      })}
    </>
  );
}
