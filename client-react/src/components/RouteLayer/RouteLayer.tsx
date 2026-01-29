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
          interactive={false}
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
import { useMap as useMapForVertex } from "react-leaflet";

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
      width: 16px; height: 16px; border-radius: 50%;
      background: ${fillColor}; border: 2px solid #fff;
      box-shadow: 0 2px 4px rgba(0,0,0,0.4);
      cursor: grab;
      pointer-events: auto;
      position: relative;
      z-index: 1000;
    "></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  }), [fillColor]);

  // Create marker on mount, destroy on unmount
  useEffect(() => {
    // Ensure vertex pane exists
    if (!map.getPane("vertexPane")) {
      map.createPane("vertexPane");
      const pane = map.getPane("vertexPane");
      if (pane) pane.style.zIndex = "650";
    }

    const marker = L.marker(position, {
      icon,
      draggable: true,
      pane: "vertexPane",
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
  const { startDrag, updateDrag, endDrag } = useGhostPin();
  const isDraggingRef = useRef(false);
  const dragSegmentRef = useRef(-1);

  const modeColor = useMemo(() => getModeColor(mode), [mode]);
  const desireColor = ROUTE_COLORS.desire.core;

  // Filter out stale or invalid edited segments
  const validEditedSegments = useMemo(() => {
    const maxSegmentIndex = editVertices.length - 2; // segments = vertices - 1, so max index = vertices - 2
    return editedSegments.filter(s => 
      s.segmentIndex >= 0 && 
      s.segmentIndex <= maxSegmentIndex &&
      s.geometry?.coordinates?.length > 0
    );
  }, [editedSegments, editVertices.length]);

  // Build set of segment indices that have edited geometry
  const editedSegmentIndices = useMemo(() => {
    return new Set(validEditedSegments.map(s => s.segmentIndex));
  }, [validEditedSegments]);

  // Build segments between vertices from original geometry
  // Also includes "pending" segments that are modified but don't have geometry yet
  const segments = useMemo(() => {
    if (editVertices.length < 2) return [];

    const segs: { positions: [number, number][]; index: number; isPending?: boolean }[] = [];
    for (let i = 0; i < editVertices.length - 1; i++) {
      const v1 = editVertices[i];
      const v2 = editVertices[i + 1];

      const isModified = modifiedSegmentIndices.has(i);
      const hasEditedGeometry = editedSegmentIndices.has(i);

      // If modified AND has edited geometry, skip (rendered separately)
      if (isModified && hasEditedGeometry) continue;

      // If modified but NO edited geometry yet, render a straight line placeholder
      if (isModified && !hasEditedGeometry) {
        segs.push({
          positions: [
            [v1.position.lng, v1.position.lat],
            [v2.position.lng, v2.position.lat],
          ],
          index: i,
          isPending: true,
        });
        continue;
      }

      // Not modified - extract from original geometry
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
  }, [editVertices, geometry, modifiedSegmentIndices, editedSegmentIndices]);

  // Handle mid-line drag (between vertices)
  const handleSegmentMouseDown = useCallback(
    (segmentIndex: number, e: L.LeafletMouseEvent) => {
      // Check if click is near an existing vertex - if so, let vertex handle it
      const clickLatLng = e.latlng;
      const VERTEX_PRIORITY_RADIUS = 15; // pixels
      
      for (const vertex of editVertices) {
        const vertexPoint = map.latLngToContainerPoint([vertex.position.lat, vertex.position.lng]);
        const clickPoint = map.latLngToContainerPoint(clickLatLng);
        const distance = Math.sqrt(
          Math.pow(vertexPoint.x - clickPoint.x, 2) + 
          Math.pow(vertexPoint.y - clickPoint.y, 2)
        );
        if (distance < VERTEX_PRIORITY_RADIUS) {
          // Too close to a vertex, don't start line drag
          return;
        }
      }

      L.DomEvent.stopPropagation(e);
      L.DomEvent.preventDefault(e.originalEvent);

      isDraggingRef.current = true;
      dragSegmentRef.current = segmentIndex;
      map.dragging.disable();
      document.body.style.cursor = "grabbing";
      onDragStart?.();

      // Start ghost pin at cursor position
      startDrag({
        x: e.originalEvent.clientX,
        y: e.originalEvent.clientY,
      });

      const handleMove = (ev: MouseEvent) => {
        if (isDraggingRef.current) {
          updateDrag({ x: ev.clientX, y: ev.clientY });
        }
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
        endDrag();
        map.dragging.enable();
        document.body.style.cursor = "";
        document.removeEventListener("mousemove", handleMove);
        document.removeEventListener("mouseup", handleUp);
      };

      document.addEventListener("mousemove", handleMove);
      document.addEventListener("mouseup", handleUp);
    },
    [map, editVertices, onLineDrag, onDragStart, startDrag, updateDrag, endDrag]
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

  // Create native Leaflet polylines for interactive segments
  useEffect(() => {
    const polylines: L.Polyline[] = [];
    
    segments.forEach(({ positions, index, isPending }) => {
      // Convert [lng, lat] to [lat, lng] for Leaflet
      const latLngs = positions.map(([lng, lat]) => [lat, lng] as [number, number]);
      
      // For pending segments (being calculated), show gold dashed placeholder
      const lineColor = isPending ? ROUTE_COLORS.desire.core : modeColor;
      const lineOpacity = isPending ? 0.5 : 1;
      const lineDash = isPending ? "8, 8" : (dashArray || undefined);
      
      // Create visual polyline
      const visualLine = L.polyline(latLngs, {
        color: lineColor,
        weight: 4,
        opacity: lineOpacity,
        dashArray: lineDash,
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
        pane: "routePane",
      }).addTo(map);
      polylines.push(visualLine);
      
      // Create interactive polyline (thick, nearly invisible)
      // Use desirePathPane so it's BELOW routePane and vertexPane
      const interactiveLine = L.polyline(latLngs, {
        color: "#000000",
        weight: 20,
        opacity: 0.001,
        lineCap: "round",
        lineJoin: "round",
        interactive: true,
        pane: "desirePathPane",
      }).addTo(map);
      polylines.push(interactiveLine);
      
      // Bind events
      interactiveLine.on("mousedown", (e) => {
        handleSegmentMouseDown(index, e);
      });
      interactiveLine.on("mouseover", () => {
        document.body.style.cursor = "grab";
      });
      interactiveLine.on("mouseout", () => {
        document.body.style.cursor = "";
      });
    });
    
    return () => {
      polylines.forEach(p => p.remove());
    };
  }, [map, segments, modeColor, dashArray, handleSegmentMouseDown]);

  // Create native Leaflet polylines for edited segments (gold) - interactive areas
  useEffect(() => {
    const polylines: L.Polyline[] = [];
    
    validEditedSegments.forEach((seg) => {
      const coords = seg.geometry.coordinates;
      const latLngs = coords.map(([lng, lat]: [number, number]) => [lat, lng] as [number, number]);
      
      // Interactive polyline for edited segment - uses desirePathPane (below vertexPane)
      const interactiveLine = L.polyline(latLngs, {
        color: "#000000",
        weight: 20,
        opacity: 0.001,
        lineCap: "round",
        lineJoin: "round",
        interactive: true,
        pane: "desirePathPane",
      }).addTo(map);
      polylines.push(interactiveLine);
      
      interactiveLine.on("mousedown", (e) => {
        handleSegmentMouseDown(seg.segmentIndex, e);
      });
      interactiveLine.on("mouseover", () => {
        document.body.style.cursor = "grab";
      });
      interactiveLine.on("mouseout", () => {
        document.body.style.cursor = "";
      });
    });
    
    return () => {
      polylines.forEach(p => p.remove());
    };
  }, [map, validEditedSegments, handleSegmentMouseDown]);

  return (
    <>

      {/* Modified segments - rendered in gold */}
      {validEditedSegments.map((seg) => {
        const key = `edited-${seg.id}-${seg.geometry.coordinates.length}`;
        
        // Create GeoJSON feature for this segment
        const geojsonData = {
          type: "Feature" as const,
          properties: {},
          geometry: seg.geometry,
        };

        return (
          <React.Fragment key={key}>
            {/* Gold glow */}
            <GeoJSON
              key={`glow-${key}`}
              data={geojsonData}
              style={() => ({
                color: ROUTE_COLORS.desire.glow,
                weight: 8,
                opacity: 0.3,
                lineCap: "round",
                lineJoin: "round",
                pane: "routePane",
              })}
              interactive={false}
            />
            {/* Gold core */}
            <GeoJSON
              key={`core-${key}`}
              data={geojsonData}
              style={() => ({
                color: desireColor,
                weight: 4,
                opacity: 1,
                lineCap: "round",
                lineJoin: "round",
                pane: "routePane",
              })}
              interactive={false}
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
