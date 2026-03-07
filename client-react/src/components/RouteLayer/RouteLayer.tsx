import { useMemo } from "react";
import { GeoJSON, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import { ROUTE_COLORS } from "../../colors";
import { usePathDrag } from "../../hooks";
import type { TransportMode, RouteGeometry, LatLng, SplitDesirePath } from "../../types";
import type { PathOptions } from "leaflet";

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

interface LayerStyle extends PathOptions {
  pane?: string;
}

// Interactive weight - larger than visual for easier clicking
const INTERACTIVE_WEIGHT = 20;

// Transparent interactive layer for click detection
const interactiveStyle: LayerStyle = {
  color: "#000000",
  weight: INTERACTIVE_WEIGHT,
  opacity: 0.01,
  lineCap: "round",
  lineJoin: "round",
};

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

function getDesirePathStyles(mode?: TransportMode): LayerStyle[] {
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

function makeGeometryKey(coordinates: [number, number][], prefix: string = ""): string {
  if (coordinates.length === 0) return `${prefix}empty`;
  const first = coordinates[0];
  const last = coordinates[coordinates.length - 1];
  return `${prefix}${first[0]}-${first[1]}-${last[0]}-${last[1]}-${coordinates.length}`;
}

// ============================================
// Route Layer (visual only, no drag interaction)
// ============================================

interface RouteLayerProps {
  geometry: RouteGeometry;
  mode: TransportMode;
}

export function RouteLayer({ geometry, mode }: RouteLayerProps) {
  const styles = useMemo(() => getRouteStyles(mode), [mode]);
  const geometryKey = useMemo(() => makeGeometryKey(geometry.coordinates), [geometry]);

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

// ============================================
// Desire Path Layer (with drag-to-insert interaction)
// ============================================

interface DesirePathLayerProps {
  geometry: RouteGeometry;
  segmentIndex?: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  mode?: TransportMode;
}

export function DesirePathLayer({ geometry, segmentIndex = 0, onSegmentDrag, mode }: DesirePathLayerProps) {
  const map = useMap();
  const { isDraggingRef, hoverLatLng, handleStart, handleHoverMove, handleHoverOut } =
    usePathDrag({ map, geometry, segmentIndex, onSegmentDrag });

  const visualStyles = useMemo(() => getDesirePathStyles(mode), [mode]);
  const geometryKey = useMemo(() => makeGeometryKey(geometry.coordinates), [geometry]);

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

// ============================================
// Split Desire Path Layer (renders split paths after ghost pin drop)
// ============================================

interface SplitDesirePathLayerProps {
  splitPath: SplitDesirePath;
  mode?: TransportMode;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
}

export function SplitDesirePathLayer({ splitPath, mode, onSegmentDrag }: SplitDesirePathLayerProps) {
  const map = useMap();
  const { isDraggingRef, hoverLatLng, handleStart, handleHoverMove, handleHoverOut } =
    usePathDrag({ map, geometry: splitPath.geometry, segmentIndex: splitPath.segmentIndex, onSegmentDrag });

  const visualStyles = useMemo(() => getDesirePathStyles(mode), [mode]);
  const geometryKey = useMemo(
    () => makeGeometryKey(splitPath.geometry.coordinates, `${splitPath.id}-`),
    [splitPath]
  );

  const geojsonData = useMemo(
    () => ({
      type: "Feature" as const,
      properties: {},
      geometry: splitPath.geometry,
    }),
    [splitPath.geometry]
  );

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
