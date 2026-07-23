import { useEffect, useMemo } from "react";
import { ROUTE_COLORS } from "../../colors";
import { kiteHtml, KITE_SIZE } from "../../utils/kiteIcon";
import { usePathDrag } from "../../hooks";
import { useMapFacade } from "../../map/MapFacadeContext";
import {
  ROUTE_SOURCE_ID,
  DESIRE_SOURCE_ID,
  setOverlayFeature,
  removeOverlayFeature,
} from "../../map/maplibreOverlays";
import { MapMarker } from "../MapMarker";
import type { RouteGeometry, LatLng, SplitDesirePath } from "../../types";

/**
 * Route/desire-path visuals are MapLibre GL layers (styled in MapCanvas);
 * components here just push their geometry into the keyed feature registry.
 * Drag-to-insert interaction lives in usePathDrag (pure-math hit-testing).
 */
function useOverlayGeometry(
  sourceId: string,
  key: string,
  geometry: RouteGeometry,
): void {
  useEffect(() => {
    setOverlayFeature(sourceId, key, geometry as unknown as GeoJSON.Geometry);
    return () => removeOverlayFeature(sourceId, key);
  }, [sourceId, key, geometry]);
}

const hoverGhostHtml = kiteHtml(ROUTE_COLORS.desire.middle, true);

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
}

export function RouteLayer({ geometry }: RouteLayerProps) {
  const geometryKey = useMemo(() => makeGeometryKey(geometry.coordinates), [geometry]);
  useOverlayGeometry(ROUTE_SOURCE_ID, `route-${geometryKey}`, geometry);
  return null;
}

// ============================================
// Desire Path Layer (with drag-to-insert interaction)
// ============================================

interface DesirePathLayerProps {
  geometry: RouteGeometry;
  segmentIndex?: number;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  onPathHoverChange?: (isHovering: boolean) => void;
}

export function DesirePathLayer({ geometry, segmentIndex = 0, onSegmentDrag, onPathHoverChange }: DesirePathLayerProps) {
  const map = useMapFacade();
  const { hoverLatLng } = usePathDrag({
    map,
    geometry,
    segmentIndex,
    onSegmentDrag,
    onHoverChange: onPathHoverChange,
  });

  const geometryKey = useMemo(() => makeGeometryKey(geometry.coordinates), [geometry]);
  useOverlayGeometry(DESIRE_SOURCE_ID, `desire-${segmentIndex}-${geometryKey}`, geometry);

  // Hover ghost pin — snapped to path. hoverLatLng is cleared when a drag
  // begins (usePathDrag.beginDrag), so no ghost shows mid-drag.
  if (!hoverLatLng) return null;
  return (
    <MapMarker
      position={hoverLatLng}
      html={hoverGhostHtml}
      className="hover-ghost-marker"
      size={KITE_SIZE}
      anchor="bottom"
      interactive={false}
    />
  );
}

// ============================================
// Split Desire Path Layer (renders split paths after ghost pin drop)
// ============================================

interface SplitDesirePathLayerProps {
  splitPath: SplitDesirePath;
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  onPathHoverChange?: (isHovering: boolean) => void;
}

export function SplitDesirePathLayer({ splitPath, onSegmentDrag, onPathHoverChange }: SplitDesirePathLayerProps) {
  const map = useMapFacade();
  const { hoverLatLng } = usePathDrag({
    map,
    geometry: splitPath.geometry,
    segmentIndex: splitPath.segmentIndex,
    onSegmentDrag,
    onHoverChange: onPathHoverChange,
  });

  const geometryKey = useMemo(
    () => makeGeometryKey(splitPath.geometry.coordinates, `${splitPath.id}-`),
    [splitPath]
  );
  useOverlayGeometry(DESIRE_SOURCE_ID, `split-${splitPath.id}-${geometryKey}`, splitPath.geometry);

  // Hover ghost pin — snapped to path. hoverLatLng is cleared when a drag
  // begins (usePathDrag.beginDrag), so no ghost shows mid-drag.
  if (!hoverLatLng) return null;
  return (
    <MapMarker
      position={hoverLatLng}
      html={hoverGhostHtml}
      className="hover-ghost-marker"
      size={KITE_SIZE}
      anchor="bottom"
      interactive={false}
    />
  );
}
