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
import { useRoute, useGhostPin, useTheme } from "../../context";
import { useMapClick } from "../../hooks";
import { kiteGhostIcon } from "../../utils/kiteIcon";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { GraphLayer } from "../GraphLayer/GraphLayer";
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

// Map click handler — uses snappedNode when available so clicks land exactly
// on the displayed ghost pin position
function MapClickHandler({
  onMapClick,
  snappedNode,
}: {
  onMapClick: (latlng: LatLng) => void;
  snappedNode: LatLng | null;
}) {
  useMapEvents({
    click: (e) => {
      onMapClick(snappedNode ?? { lat: e.latlng.lat, lng: e.latlng.lng });
    },
  });
  return null;
}

// Ghost pin marker for the snapped node position (from GraphLayer onSnap)
function SnapMarker({
  snappedNode,
  hasStart,
  suppress,
  pointOnly,
}: {
  snappedNode: LatLng | null;
  hasStart: boolean;
  suppress: boolean;
  pointOnly: boolean;
}) {
  // In point-only themes (e.g. trees) every click resets the single waypoint,
  // so the hover ghost always represents a fresh start — never an end pin.
  const showAsEnd = !pointOnly && hasStart;
  const icon = useMemo(
    () => kiteGhostIcon(showAsEnd ? COLOR_END : COLOR_START),
    [showAsEnd]
  );

  if (suppress || !snappedNode) return null;
  return <Marker position={[snappedNode.lat, snappedNode.lng]} icon={icon} interactive={false} />;
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
    setStartPoint,
    setEndPoint,
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

  const { ghostState } = useGhostPin();

  const [leafletMap, setLeafletMap] = useState<L.Map | null>(null);
  const [snappedNode, setSnappedNode] = useState<LatLng | null>(null);
  const [isHoveringPath, setIsHoveringPath] = useState(false);
  const [isDraggingMarker, setIsDraggingMarker] = useState(false);

  // Wrapper for marker drag start — suppresses ghost pin and next click
  const handleMarkerDragStart = useCallback(() => {
    setIsDraggingMarker(true);
    setSuppressClick();
  }, [setSuppressClick]);

  // Wrapper for marker drag finish — re-enables ghost pin
  const handleMarkerDragFinish = useCallback(() => {
    setIsDraggingMarker(false);
  }, []);

  const { handleMapClick } = useMapClick({
    state: { start, end },
    inputMode: theme.inputMode,
    onUpdateStart: setStartPoint,
    onUpdateEnd: setEndPoint,
    onClearPoints: clearPoints,
    onClearGhostWaypoints: clearSplitPaths,
    onSetError: setError,
    suppressNextClick,
    onClearSuppress: clearSuppressClick,
  });

  const handleOutOfBounds = useCallback(() => {
    setError("Not mapped yet — please limit to Manhattan");
  }, [setError]);

  const bounds = useMemo(
    () =>
      L.latLngBounds(
        [CONFIG.nycBounds.sw.lat, CONFIG.nycBounds.sw.lon],
        [CONFIG.nycBounds.ne.lat, CONFIG.nycBounds.ne.lon]
      ),
    []
  );

  return (
    <>
    {/* MapLibre GL JS background — renders base map + graph from PMTiles */}
    <MapLibreBackground leafletMap={leafletMap} />

    <MapContainer
      center={[CONFIG.initialView.lat, CONFIG.initialView.lon]}
      zoom={CONFIG.initialView.zoom}
      minZoom={CONFIG.minZoom}
      maxZoom={CONFIG.maxZoom}
      maxBounds={bounds}
      maxBoundsViscosity={1.0}
      zoomControl={false}
      preferCanvas={CONFIG.preferCanvas}
      className="map-container"
    >
      <MapBridge onMap={setLeafletMap} />
      <MapPanes />
      <GraphLayer
        onSnap={setSnappedNode}
        pinnedPoint={start.coords && !end.coords ? start.coords : null}
      />
      <MapDragCursor />
      <MapClickHandler onMapClick={handleMapClick} snappedNode={snappedNode} />
      <SnapMarker
        snappedNode={snappedNode}
        hasStart={!!start.coords}
        pointOnly={theme.inputMode === "point"}
        suppress={isHoveringPath || ghostState.isDragging || isDraggingMarker}
      />

      {/* Raster tile fallback — visible until MapLibre loads, or when WebGL unavailable */}
      <TileLayer
        url={CONFIG.tileUrlTemplate}
        subdomains={["a", "b", "c", "d"]}
        maxZoom={CONFIG.maxZoom}
        attribution={CONFIG.tileAttribution}
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
        />
      )}
    </MapContainer>

    {/* Ghost pin rendered outside MapContainer for smooth 60fps positioning */}
    <GhostPin />
    </>
  );
}

// Bridge to expose Leaflet map instance to parent
function MapBridge({ onMap }: { onMap: (map: L.Map) => void }) {
  const map = useMap();
  useEffect(() => { onMap(map); }, [map, onMap]);
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