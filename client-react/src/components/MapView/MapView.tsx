import { useCallback, useEffect, useMemo } from "react";
import {
  MapContainer,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import { CONFIG } from "../../config";
import { useRoute } from "../../context";
import { useMapClick } from "../../hooks";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { WeightedSegmentsLayer } from "../WeightedSegmentsLayer";
import type { LatLng } from "../../types";
import "leaflet/dist/leaflet.css";
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

// Map click handler component
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

export function MapView() {
  const {
    start,
    end,
    mode,
    routeData,
    waypoints,
    ghostWaypoints,
    splitDesirePaths,
    suppressNextClick,
    setStartPoint,
    setEndPoint,
    clearStart,
    clearEnd,
    setError,
    insertWaypointAtSegment,
    updateGhostWaypoint,
    removeGhostWaypoint,
    updateWaypoint,
    clearSuppressClick,
    setSuppressClick,
    clearSplitPaths,
  } = useRoute();

  const { handleMapClick } = useMapClick({
    state: { start, end },
    onUpdateStart: setStartPoint,
    onUpdateEnd: setEndPoint,
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
      <MapPanes />
      <MapDragCursor />
      <MapClickHandler onMapClick={handleMapClick} />

      <TileLayer
        url={CONFIG.tileUrlTemplate}
        subdomains={["a", "b", "c", "d"]}
        maxZoom={CONFIG.maxZoom}
        attribution={CONFIG.tileAttribution}
      />

      {/* Zoom control in bottom right */}
      <ZoomControl />

      {/* Weighted segments layer - vote-weighted line rendering */}
      <WeightedSegmentsLayer />

      {/* Desire path layer for all modes - shows the walk route */}
      {/* Only show when no ghost waypoints (otherwise we're mid-calculation or showing splits) */}
      {routeData?.geometry && splitDesirePaths.length === 0 && ghostWaypoints.length === 0 && (
        <DesirePathLayer
          geometry={routeData.geometry}
          segmentIndex={0}
          onSegmentDrag={insertWaypointAtSegment}
          mode={mode}
        />
      )}

      {/* Split desire path layers - shown after ghost pin drop */}
      {splitDesirePaths.map((splitPath) => (
        <SplitDesirePathLayer
          key={splitPath.id}
          splitPath={splitPath}
          mode={mode}
          onSegmentDrag={insertWaypointAtSegment}
        />
      ))}

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
          key={`ghost-waypoint-${index}`}
          position={wp}
          which="waypoint"
          onDragStart={setSuppressClick}
          onDragEnd={(pos) => updateGhostWaypoint(index, pos)}
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
          onDragStart={setSuppressClick}
          onDragEnd={setStartPoint}
          onDelete={clearStart}
          onOutOfBounds={handleOutOfBounds}
        />
      )}

      {/* End marker - draggable to move end point */}
      {end.coords && (
        <RouteMarker
          position={end.coords}
          which="end"
          onDragStart={setSuppressClick}
          onDragEnd={setEndPoint}
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