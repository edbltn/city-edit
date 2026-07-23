import { useCallback, useEffect, useMemo, useState } from "react";
import {
  MapContainer,
  Marker,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import { CONFIG } from "../../config";
import { COLOR_START, COLOR_END } from "../../colors";
import { mapStyleForTheme } from "../../themes";
import { useRoute, useGhostPin, useTheme } from "../../context";
import { useMapClick } from "../../hooks";
import { kiteGhostIcon } from "../../utils/kiteIcon";
import { setMapViewState, setMapInstance, getInitialMapView } from "../../utils/mapViewState";
import { getMapLibreStatus, onMapLibreStatus, type MapLibreStatus } from "../../map/maplibreStatus";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { GraphLayer } from "../GraphLayer/GraphLayer";
import { BoundaryLayer } from "../BoundaryLayer";
import { MapLibreBackground } from "../MapLibreBackground";
import type { LatLng } from "../../types";
import "leaflet/dist/leaflet.css";
import "maplibre-gl/dist/maplibre-gl.css";
import "./MapView.css";

// Custom pane setup component
function MapPanes() {
  const map = useMap();

  useEffect(() => {
    // Create pane for user's route (above vote overlays)
    if (!map.getPane("routePane")) {
      map.createPane("routePane");
      const routePane = map.getPane("routePane");
      if (routePane) routePane.style.zIndex = "450";
    }

    // Create pane for OSM graph (below heatmap)
    if (!map.getPane("graphPane")) {
      map.createPane("graphPane");
      const graphPane = map.getPane("graphPane");
      if (graphPane) graphPane.style.zIndex = "430";
    }

    // Create pane for desire path (below main route)
    if (!map.getPane("desirePathPane")) {
      map.createPane("desirePathPane");
      const desirePathPane = map.getPane("desirePathPane");
      if (desirePathPane) {
        desirePathPane.style.zIndex = "440";
      }
    }

    // Set default map cursor to crosshair (for placing start/end points)
    map.getContainer().style.cursor = "crosshair";
  }, [map]);

  return null;
}

// Syncs the Leaflet map view state to the module-level store so
// ModeSwitcher can read current zoom/center when building theme URLs.
function MapViewTracker() {
  const map = useMap();

  useEffect(() => {
    const sync = () => {
      const c = map.getCenter();
      setMapViewState(map.getZoom(), { lat: c.lat, lng: c.lng });
    };
    sync();
    map.on("moveend", sync);
    map.on("zoomend", sync);
    return () => {
      map.off("moveend", sync);
      map.off("zoomend", sync);
    };
  }, [map]);

  return null;
}

// Component to handle grabbing cursor during any map drag
function MapDragCursor() {
  const map = useMap();

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
// hit-testing on start/end coords.
function MapClickHandler({
  onMapClick,
}: {
  onMapClick: (latlng: LatLng) => void;
}) {
  useMapEvents({
    click: (e) => {
      onMapClick({ lat: e.latlng.lat, lng: e.latlng.lng });
    },
  });
  return null;
}

// Tracks raw cursor position on the map so the ghost pin can follow the
// cursor exactly (not the snapped graph position).
function CursorTracker({
  onMove,
}: {
  onMove: (pos: LatLng | null) => void;
}) {
  useMapEvents({
    mousemove: (e) => onMove({ lat: e.latlng.lat, lng: e.latlng.lng }),
    mouseout: () => onMove(null),
  });
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
  const icon = useMemo(
    () => kiteGhostIcon(showAsEnd ? COLOR_END : COLOR_START),
    [showAsEnd]
  );

  if (suppress || !cursorLatLng) return null;
  if (!pointOnly && activeTool === "end" && !hasStart) return null;

  return <Marker position={[cursorLatLng.lat, cursorLatLng.lng]} icon={icon} interactive={false} />;
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

  const [leafletMap, setLeafletMap] = useState<L.Map | null>(null);
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

  // Wrapper for marker drag start — suppresses ghost pin and next click
  const handleMarkerDragStart = useCallback(() => {
    setIsDraggingMarker(true);
    setSuppressClick();
  }, [setSuppressClick]);

  // Wrapper for marker drag finish — re-enables ghost pin
  const handleMarkerDragFinish = useCallback(() => {
    setIsDraggingMarker(false);
  }, []);

  // Indicator click follows the same tool logic as a normal map click.
  const handleIndicatorClick = useCallback((latlng: LatLng) => {
    clearSplitPaths();
    if (activeTool === "end" && start.coords) {
      setEndPoint(latlng);
      setActiveTool("start");
    } else {
      if (start.coords || end.coords) clearPoints();
      setStartPoint(latlng);
    }
  }, [activeTool, start.coords, end.coords, clearPoints, clearSplitPaths, setStartPoint, setEndPoint, setActiveTool]);

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

  const bounds = useMemo(
    () =>
      L.latLngBounds(
        [CONFIG.nycBounds.sw.lat, CONFIG.nycBounds.sw.lon],
        [CONFIG.nycBounds.ne.lat, CONFIG.nycBounds.ne.lon]
      ),
    []
  );

  // Computed at first render (not module load) so it reflects the active city's
  // center after applyCityConfig has run — otherwise non-default cities (SF,
  // Chicago) would mount at the stale NYC default and get clamped off-center.
  const initialMapView = useMemo(() => getInitialMapView(), []);

  // MapLibre is the primary basemap. While it's live, the Leaflet container
  // goes transparent so the WebGL map shows through; the raster TileLayer is
  // mounted only if MapLibre failed (no WebGL).
  useEffect(() => onMapLibreStatus(setMlStatus), []);
  useEffect(() => {
    const el = leafletMap?.getContainer();
    if (!el) return;
    el.classList.toggle("ml-base", mlStatus === "ready");
  }, [leafletMap, mlStatus]);

  return (
    <>
    {/* MapLibre GL JS background — renders base map + graph from PMTiles */}
    <MapLibreBackground leafletMap={leafletMap} mapStyle={mapStyle} />

    <MapContainer
      center={[initialMapView.lat, initialMapView.lng]}
      zoom={initialMapView.zoom}
      minZoom={CONFIG.minZoom}
      maxZoom={CONFIG.maxZoom}
      maxBounds={bounds}
      maxBoundsViscosity={1.0}
      zoomControl={false}
      preferCanvas={CONFIG.preferCanvas}
      className="map-container"
    >
      <MapBridge onMap={setLeafletMap} />
      <MapViewTracker />
      <MapPanes />
      <BoundaryLayer />
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

      {/* Raster tile fallback — mounted only when MapLibre can't run (no WebGL).
          Otherwise the MapLibre GL background is the basemap and these tiles
          would just duplicate downloads and occlude it.
          maxNativeZoom caps tile requests at CartoDB's available zoom and upscales
          beyond it, so deep zoom (maxZoom) doesn't request nonexistent blank tiles. */}
      {mlStatus === "failed" && (
        <TileLayer
          key={mapStyle.id}
          url={mapStyle.tileUrl}
          subdomains={["a", "b", "c", "d"]}
          maxZoom={CONFIG.maxZoom}
          maxNativeZoom={19}
          attribution={mapStyle.tileAttribution}
        />
      )}

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
          onDragEnd={updateWaypoint}
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
    </MapContainer>

    {/* Ghost pin rendered outside MapContainer for smooth 60fps positioning */}
    <GhostPin />
    </>
  );
}

// Bridge to expose Leaflet map instance to parent and module-level store
function MapBridge({ onMap }: { onMap: (map: L.Map) => void }) {
  const map = useMap();
  useEffect(() => {
    onMap(map);
    setMapInstance(map);
    return () => setMapInstance(null);
  }, [map, onMap]);
  return null;
}

// Zoom control component
function ZoomControl() {
  const map = useMap();

  useEffect(() => {
    const zoomControl = L.control.zoom({ position: "bottomright" });
    zoomControl.addTo(map);
    return () => {
      zoomControl.remove();
    };
  }, [map]);

  return null;
}