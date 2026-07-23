import { useCallback, useEffect, useMemo, useState } from "react";
import { COLOR_START, COLOR_END } from "../../colors";
import { mapStyleForTheme } from "../../themes";
import { useRoute, useGhostPin, useTheme } from "../../context";
import { useMapClick } from "../../hooks";
import { kiteHtml, KITE_SIZE } from "../../utils/kiteIcon";
import { setMapInstance } from "../../utils/mapViewState";
import { getMapLibreStatus, onMapLibreStatus, type MapLibreStatus } from "../../map/maplibreStatus";
import { MapFacadeProvider, useMapFacade } from "../../map/MapFacadeContext";
import type { MapFacade, MapMouseEvent } from "../../map/facade";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { GraphLayer } from "../GraphLayer/GraphLayer";
import { MapCanvas } from "../MapCanvas";
import { MapMarker } from "../MapMarker";
import type { LatLng } from "../../types";
import "./MapView.css";

// Syncs cursor affordances for map panning: grabbing while a drag-pan is in
// progress, crosshair otherwise (the crosshair default lives in CSS).
function MapDragCursor() {
  const map = useMapFacade();

  useEffect(() => {
    const onDragStart = () => {
      document.body.style.cursor = "grabbing";
    };
    const onDragEnd = () => {
      document.body.style.cursor = "";
    };

    map.on("dragstart", onDragStart);
    map.on("dragend", onDragEnd);

    return () => {
      map.off("dragstart", onDragStart);
      map.off("dragend", onDragEnd);
    };
  }, [map]);

  return null;
}

// Map click handler — clicks use raw cursor lat/lng so the indicator lands
// exactly where the user clicked. Segment selection happens via GraphLayer's
// hit-testing on start/end coords. Clicks that originate on a marker element
// are ignored: markers own their own click handling (delete/select), and
// unlike Leaflet, MapLibre fires a map click for them too.
function MapClickHandler({
  onMapClick,
}: {
  onMapClick: (latlng: LatLng) => void;
}) {
  const map = useMapFacade();

  useEffect(() => {
    const handleClick = (e: MapMouseEvent) => {
      const target = e.originalEvent.target as HTMLElement | null;
      if (target?.closest(".maplibregl-marker")) return;
      onMapClick(e.latlng);
    };
    map.on("click", handleClick);
    return () => map.off("click", handleClick);
  }, [map, onMapClick]);

  return null;
}

// Tracks raw cursor position on the map so the ghost pin can follow the
// cursor exactly (not the snapped graph position).
function CursorTracker({
  onMove,
}: {
  onMove: (pos: LatLng | null) => void;
}) {
  const map = useMapFacade();

  useEffect(() => {
    const handleMove = (e: MapMouseEvent) => onMove(e.latlng);
    const handleOut = () => onMove(null);
    map.on("mousemove", handleMove);
    map.on("mouseout", handleOut);
    return () => {
      map.off("mousemove", handleMove);
      map.off("mouseout", handleOut);
    };
  }, [map, onMove]);

  return null;
}

// Ghost pin marker — shows at the raw cursor position to preview where the
// indicator will land. Color reflects the active tool (start vs end).
function SnapMarker({
  cursorLatLng,
  activeTool,
  hasStart,
  suppress,
  pointOnly,
}: {
  cursorLatLng: LatLng | null;
  activeTool: "start" | "end";
  hasStart: boolean;
  suppress: boolean;
  pointOnly: boolean;
}) {
  // Point-only themes always place a fresh start. Otherwise, color follows
  // the armed tool — but if End is armed without a start, no ghost (click
  // would be ignored).
  const showAsEnd = !pointOnly && activeTool === "end" && hasStart;
  const html = useMemo(
    () => kiteHtml(showAsEnd ? COLOR_END : COLOR_START, true),
    [showAsEnd]
  );

  // Touch devices have no persistent cursor: the "follow the cursor" ghost
  // would freeze at the last tap position and read as a phantom pin.
  if (!window.matchMedia("(hover: hover)").matches) return null;
  if (suppress || !cursorLatLng) return null;
  if (!pointOnly && activeTool === "end" && !hasStart) return null;

  return (
    <MapMarker
      position={cursorLatLng}
      html={html}
      className="hover-ghost-marker"
      size={KITE_SIZE}
      anchor="bottom"
      interactive={false}
    />
  );
}

// Zoom control — custom buttons (bottom right) calling the facade, replacing
// the old L.control.zoom with the same look.
function ZoomControl() {
  const map = useMapFacade();
  return (
    <div className="map-zoom-control">
      <button type="button" aria-label="Zoom in" title="Zoom in" onClick={() => map.zoomIn()}>+</button>
      <button type="button" aria-label="Zoom out" title="Zoom out" onClick={() => map.zoomOut()}>−</button>
    </div>
  );
}

export function MapView() {
  const {
    start,
    end,
    routeData,
    waypoints,
    ghostWaypoints,
    ghostWaypointIds,
    splitDesirePaths,
    suppressNextClick,
    activeTool,
    setStartPoint,
    setEndPoint,
    setActiveTool,
    clearStart,
    clearEnd,
    clearPoints,
    setError,
    insertWaypointAtSegment,
    updateGhostWaypoint,
    removeGhostWaypoint,
    updateWaypoint,
    clearSuppressClick,
    setSuppressClick,
    clearSplitPaths,
  } = useRoute();

  const theme = useTheme();
  const mapStyle = mapStyleForTheme(theme);

  const { ghostState } = useGhostPin();

  const [facade, setFacade] = useState<MapFacade | null>(null);
  const [mlStatus, setMlStatus] = useState<MapLibreStatus>(() => getMapLibreStatus());
  const [cursorLatLng, setCursorLatLng] = useState<LatLng | null>(null);
  const [isHoveringPath, setIsHoveringPath] = useState(false);
  const [isDraggingMarker, setIsDraggingMarker] = useState(false);
  // Count of draggable markers (start/end/waypoints) currently hovered, so the
  // start-placement ghost hides while the grab cursor is over one.
  const [markerHoverCount, setMarkerHoverCount] = useState(0);
  const handleMarkerHover = useCallback((hovering: boolean) => {
    setMarkerHoverCount((c) => Math.max(0, c + (hovering ? 1 : -1)));
  }, []);

  useEffect(() => onMapLibreStatus(setMlStatus), []);

  // Expose the facade to the module-level store (TopBar address search panTo).
  useEffect(() => {
    setMapInstance(facade);
    return () => setMapInstance(null);
  }, [facade]);

  // Wrapper for marker drag start — suppresses ghost pin and next click
  const handleMarkerDragStart = useCallback(() => {
    setIsDraggingMarker(true);
    setSuppressClick();
  }, [setSuppressClick]);

  // Wrapper for marker drag finish — re-enables ghost pin
  const handleMarkerDragFinish = useCallback(() => {
    setIsDraggingMarker(false);
  }, []);

  // Selecting a top proposal no longer places a route pin — GraphLayer pins
  // the card/highlight itself. Any in-progress split-path editing is cleared
  // so stale ghost waypoints don't linger under the selection.
  const handleIndicatorClick = useCallback(() => {
    clearSplitPaths();
  }, [clearSplitPaths]);

  const { handleMapClick } = useMapClick({
    state: { start, end },
    inputMode: theme.inputMode,
    activeTool,
    onUpdateStart: setStartPoint,
    onUpdateEnd: setEndPoint,
    onSetActiveTool: setActiveTool,
    onClearPoints: clearPoints,
    onClearGhostWaypoints: clearSplitPaths,
    onSetError: setError,
    suppressNextClick,
    onClearSuppress: clearSuppressClick,
  });

  const handleOutOfBounds = useCallback(() => {
    setError("That's outside this map — drop your pins inside the highlighted area.");
  }, [setError]);

  return (
    <>
    <div className="map-container">
      {/* The interactive MapLibre GL map — basemap, graph, and camera owner */}
      <MapCanvas mapStyle={mapStyle} onFacade={setFacade} />

      {mlStatus === "failed" && (
        <div className="map-webgl-error">
          This map needs WebGL, which your browser or device has disabled.
          Try updating your browser or enabling hardware acceleration.
        </div>
      )}

      {facade && (
        <MapFacadeProvider facade={facade}>
          <GraphLayer
            pinnedPoint={start.coords && !end.coords ? start.coords : null}
            onIndicatorClick={handleIndicatorClick}
            onRemoveSelected={clearStart}
            suppressHover={isHoveringPath || markerHoverCount > 0}
          />
          <MapDragCursor />
          <CursorTracker onMove={setCursorLatLng} />
          <MapClickHandler onMapClick={handleMapClick} />
          <SnapMarker
            cursorLatLng={cursorLatLng}
            activeTool={activeTool}
            hasStart={!!start.coords}
            pointOnly={theme.inputMode === "point"}
            suppress={isHoveringPath || ghostState.isDragging || isDraggingMarker || markerHoverCount > 0}
          />

          {/* Zoom control in bottom right */}
          <ZoomControl />

          {/* Desire path layer for all modes - shows the walk route */}
          {/* Only show when no ghost waypoints (otherwise we're mid-calculation or showing splits) */}
          {routeData?.geometry && splitDesirePaths.length === 0 && ghostWaypoints.length === 0 && (
            <DesirePathLayer
              geometry={routeData.geometry}
              segmentIndex={0}
              onSegmentDrag={insertWaypointAtSegment}
              onPathHoverChange={setIsHoveringPath}
            />
          )}

          {/* Split desire path layers - shown after ghost pin drop */}
          {splitDesirePaths.map((splitPath) => {
            return (
              <SplitDesirePathLayer
                key={splitPath.id}
                splitPath={splitPath}
                onSegmentDrag={insertWaypointAtSegment}
                onPathHoverChange={setIsHoveringPath}
              />
            );
          })}

          {/* Grey arc connectors from waypoints to path endpoints */}
          <WaypointConnectors
            start={start.coords}
            end={end.coords}
            ghostWaypoints={ghostWaypoints}
            routeGeometry={routeData?.geometry?.coordinates ?? null}
            splitDesirePaths={splitDesirePaths}
          />

          {/* Ghost waypoint markers - persistent after drop, draggable to recalculate split */}
          {ghostWaypoints.map((wp, index) => (
            <RouteMarker
              key={ghostWaypointIds[index] ?? `ghost-waypoint-${index}`}
              position={wp}
              which="waypoint"
              onDragStart={handleMarkerDragStart}
              onDragEnd={(pos) => updateGhostWaypoint(index, pos)}
              onDragFinish={handleMarkerDragFinish}
              onDelete={() => removeGhostWaypoint(index)}
              onOutOfBounds={handleOutOfBounds}
              onHoverChange={handleMarkerHover}
            />
          ))}

          {/* Waypoint markers */}
          {waypoints.map((wp, index) => (
            <WaypointMarker
              key={`waypoint-${index}`}
              position={wp}
              index={index}
              onDragStart={handleMarkerDragStart}
              onDragEnd={updateWaypoint}
              onDragFinish={handleMarkerDragFinish}
              onOutOfBounds={handleOutOfBounds}
              onHoverChange={handleMarkerHover}
            />
          ))}

          {/* Start marker - draggable to move start point */}
          {start.coords && (
            <RouteMarker
              position={start.coords}
              which="start"
              onDragStart={handleMarkerDragStart}
              onDragEnd={setStartPoint}
              onDragFinish={handleMarkerDragFinish}
              onDelete={clearStart}
              onOutOfBounds={handleOutOfBounds}
              onHoverChange={handleMarkerHover}
            />
          )}

          {/* End marker - draggable to move end point */}
          {end.coords && (
            <RouteMarker
              position={end.coords}
              which="end"
              onDragStart={handleMarkerDragStart}
              onDragEnd={setEndPoint}
              onDragFinish={handleMarkerDragFinish}
              onDelete={clearEnd}
              onOutOfBounds={handleOutOfBounds}
              onHoverChange={handleMarkerHover}
            />
          )}
        </MapFacadeProvider>
      )}
    </div>

    {/* Ghost pin rendered outside the map container for smooth 60fps positioning */}
    <GhostPin />
    </>
  );
}
