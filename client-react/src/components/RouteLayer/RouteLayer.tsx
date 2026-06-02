import { useCallback, useEffect, useMemo } from "react";
import L from "leaflet";
import { GeoJSON, Marker, useMap } from "react-leaflet";
import { ROUTE_COLORS } from "../../colors";
import { kiteGhostIcon } from "../../utils/kiteIcon";
import { usePathDrag } from "../../hooks";
import type { RouteGeometry, LatLng, SplitDesirePath } from "../../types";
import type { PathOptions } from "leaflet";

const hoverGhostIcon = kiteGhostIcon(ROUTE_COLORS.desire.middle);

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

function getRouteStyles(): LayerStyle[] {
  const colors = ROUTE_COLORS.walk;
  return [
    {
      color: colors.glow,
      weight: 12,
      opacity: 0.4,
      dashArray: "5, 7",
      lineCap: "butt",
      lineJoin: "round",
    },
    {
      color: colors.edge,
      weight: 8,
      opacity: 0.9,
      dashArray: "5, 7",
      lineCap: "butt",
      lineJoin: "round",
    },
    {
      color: colors.core,
      weight: 5,
      opacity: 1,
      dashArray: "5, 7",
      lineCap: "butt",
      lineJoin: "round",
    },
  ];
}

function getDesirePathStyles(): LayerStyle[] {
  return [
    {
      color: ROUTE_COLORS.desire.middle,
      weight: 7,
      opacity: 1,
      lineCap: "round",
      lineJoin: "round",
    },
  ];
}

// Inject SVG contour filter into the path's own SVG and apply it directly.
// Uses onEachFeature + layer 'add' event to get the actual DOM element.
const CONTOUR_FILTER_ID = "desire-contour";

function ensureFilterInSvg(svg: SVGSVGElement) {
  if (svg.querySelector(`#${CONTOUR_FILTER_ID}`)) return;

  const ns = "http://www.w3.org/2000/svg";
  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = document.createElementNS(ns, "defs");
    svg.insertBefore(defs, svg.firstChild);
  }

  const filter = document.createElementNS(ns, "filter");
  filter.id = CONTOUR_FILTER_ID;
  filter.setAttribute("x", "-20%");
  filter.setAttribute("y", "-20%");
  filter.setAttribute("width", "140%");
  filter.setAttribute("height", "140%");

  const morph = document.createElementNS(ns, "feMorphology");
  morph.setAttribute("operator", "erode");
  morph.setAttribute("radius", "1.5");
  morph.setAttribute("in", "SourceGraphic");
  morph.setAttribute("result", "interior");

  const comp = document.createElementNS(ns, "feComposite");
  comp.setAttribute("in", "SourceGraphic");
  comp.setAttribute("in2", "interior");
  comp.setAttribute("operator", "out");
  comp.setAttribute("result", "border");

  const matrix = document.createElementNS(ns, "feColorMatrix");
  matrix.setAttribute("in", "interior");
  matrix.setAttribute("type", "matrix");
  matrix.setAttribute("values", "1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 0.12 0");
  matrix.setAttribute("result", "faintInterior");

  const merge = document.createElementNS(ns, "feMerge");
  const mn1 = document.createElementNS(ns, "feMergeNode");
  mn1.setAttribute("in", "faintInterior");
  const mn2 = document.createElementNS(ns, "feMergeNode");
  mn2.setAttribute("in", "border");
  merge.appendChild(mn1);
  merge.appendChild(mn2);

  filter.append(morph, comp, matrix, merge);
  defs.appendChild(filter);
}

function applyContourToLayer(_feature: unknown, layer: L.Layer) {
  const path = layer as L.Path;
  path.on("add", () => {
    const el = path.getElement();
    if (!el) return;
    const svg = el.closest("svg");
    if (!svg) return;
    ensureFilterInSvg(svg as SVGSVGElement);
    el.setAttribute("filter", `url(#${CONTOUR_FILTER_ID})`);
  });
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
}

export function RouteLayer({ geometry }: RouteLayerProps) {
  const styles = useMemo(() => getRouteStyles(), []);
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
          key={`route-${index}-${geometryKey}`}
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
  onPathHoverChange?: (isHovering: boolean) => void;
}

export function DesirePathLayer({ geometry, segmentIndex = 0, onSegmentDrag, onPathHoverChange }: DesirePathLayerProps) {
  const map = useMap();
  const { isDraggingRef, hoverLatLng, handleStart, handleHoverMove, handleHoverOut } =
    usePathDrag({ map, geometry, segmentIndex, onSegmentDrag });

  // Force SVG renderer for visual layer so SVG filters work (map uses Canvas globally)
  const svgRenderer = useMemo(() => L.svg({ pane: "desirePathPane" }), []);
  useEffect(() => () => { svgRenderer.remove(); }, [svgRenderer]);

  // Reset hover state on unmount — prevents isHoveringPath getting stuck true
  // when the component unmounts while the cursor is over the interactive layer
  useEffect(() => () => { onPathHoverChange?.(false); }, [onPathHoverChange]);

  // Wrap handleStart to reset hover state when drag begins
  const handleStartWithHoverReset = useCallback(
    (e: L.LeafletMouseEvent) => {
      onPathHoverChange?.(false);
      handleStart(e);
    },
    [handleStart, onPathHoverChange]
  );

  const visualStyles = useMemo(() => getDesirePathStyles(), []);
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
      {/* Visual layer with contour filter (SVG renderer for filter support) */}
      {visualStyles.map((style, index) => (
        <GeoJSON
          key={`desire-visual-${index}-${geometryKey}`}
          data={geojsonData}
          style={() => ({ ...style, pane: "desirePathPane", renderer: svgRenderer })}
          onEachFeature={applyContourToLayer}
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
            // Force SVG (a real DOM <path>) so hover events fire reliably and
            // aren't shadowed by a sibling canvas renderer under preferCanvas.
            renderer: svgRenderer,
            className: "desire-path-interactive",
          })}
          eventHandlers={{
            mousedown: handleStartWithHoverReset,
            mouseover: (e) => { onPathHoverChange?.(true); handleHoverMove(e); },
            mousemove: (e) => { onPathHoverChange?.(true); handleHoverMove(e); },
            mouseout: () => { onPathHoverChange?.(false); handleHoverOut(); },
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
  onSegmentDrag?: (segmentIndex: number, position: LatLng) => void;
  onPathHoverChange?: (isHovering: boolean) => void;
}

export function SplitDesirePathLayer({ splitPath, onSegmentDrag, onPathHoverChange }: SplitDesirePathLayerProps) {
  const map = useMap();
  const { isDraggingRef, hoverLatLng, handleStart, handleHoverMove, handleHoverOut } =
    usePathDrag({ map, geometry: splitPath.geometry, segmentIndex: splitPath.segmentIndex, onSegmentDrag });

  // Force SVG renderer for visual layer so SVG filters work (map uses Canvas globally)
  const svgRenderer = useMemo(() => L.svg({ pane: "desirePathPane" }), []);
  useEffect(() => () => { svgRenderer.remove(); }, [svgRenderer]);

  // Reset hover state on unmount — prevents isHoveringPath getting stuck true
  useEffect(() => () => { onPathHoverChange?.(false); }, [onPathHoverChange]);

  // Wrap handleStart to reset hover state when drag begins
  const handleStartWithHoverReset = useCallback(
    (e: L.LeafletMouseEvent) => {
      onPathHoverChange?.(false);
      handleStart(e);
    },
    [handleStart, onPathHoverChange]
  );

  const visualStyles = useMemo(() => getDesirePathStyles(), []);
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
      {/* Visual layer with contour filter (SVG renderer for filter support) */}
      {visualStyles.map((style, index) => (
        <GeoJSON
          key={`split-${splitPath.id}-${index}-${geometryKey}`}
          data={geojsonData}
          style={() => ({ ...style, pane: "desirePathPane", renderer: svgRenderer })}
          onEachFeature={applyContourToLayer}
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
            // Force SVG (a real DOM <path>) so hover events fire reliably and
            // aren't shadowed by a sibling canvas renderer under preferCanvas.
            renderer: svgRenderer,
            className: "split-path-interactive",
          })}
          eventHandlers={{
            mousedown: handleStartWithHoverReset,
            mouseover: (e) => { onPathHoverChange?.(true); handleHoverMove(e); },
            mousemove: (e) => { onPathHoverChange?.(true); handleHoverMove(e); },
            mouseout: () => { onPathHoverChange?.(false); handleHoverOut(); },
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
