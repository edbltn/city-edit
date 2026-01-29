import { useEffect, useMemo, useState } from "react";
import {
  MapContainer,
  TileLayer,
  useMap,
  useMapEvents,
} from "react-leaflet";
import L from "leaflet";
import { CONFIG } from "../../config";
import { useRoute } from "../../context";
import { useWebSocketContext } from "../../context";
import { useMapClick } from "../../hooks";
import { RouteMarker } from "../RouteMarker";
import { RouteLayer, EditableRouteLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { GhostPin } from "../GhostPin";
import { VotedPathsLayer } from "../VotedPathsLayer";
import { BasemapSwitcher } from "../BasemapSwitcher";
import { LayerControl } from "../LayerControl";
import { GISLayers } from "../GISLayers";
import { ZoomIndicator } from "../ZoomIndicator";
import type { LatLng, BasemapId } from "../../types";
import "leaflet/dist/leaflet.css";
import "./MapView.css";

// Custom pane setup component
function MapPanes() {
  const map = useMap();

  useEffect(() => {
    // Create pane for voted paths / community data (lowest - background)
    // Create this FIRST so it exists when VotedPathsLayer renders
    if (!map.getPane("votedPathsPane")) {
      map.createPane("votedPathsPane");
      const votedPathsPane = map.getPane("votedPathsPane");
      if (votedPathsPane) votedPathsPane.style.zIndex = "400";
    }

    // Create pane for desire path / edited segments (middle)
    if (!map.getPane("desirePathPane")) {
      map.createPane("desirePathPane");
      const desirePathPane = map.getPane("desirePathPane");
      if (desirePathPane) desirePathPane.style.zIndex = "450";
    }

    // Create pane for user's route (high - above voted paths)
    if (!map.getPane("routePane")) {
      map.createPane("routePane");
      const routePane = map.getPane("routePane");
      if (routePane) routePane.style.zIndex = "500";
    }

    // Create pane for vertex handles (highest - always on top of everything)
    if (!map.getPane("vertexPane")) {
      map.createPane("vertexPane");
      const vertexPane = map.getPane("vertexPane");
      if (vertexPane) vertexPane.style.zIndex = "650";
    }

    // Set default map cursor to pointer (for placing start/end points)
    map.getContainer().style.cursor = "pointer";
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
    desirePathData,
    waypoints,
    editVertices,
    originalRouteGeometry,
    editedSegments,
    modifiedSegmentIndices,
    suppressNextClick,
    setStartPoint,
    setEndPoint,
    dragVertex,
    insertVertexOnLine,
    updateWaypoint,
    clearSuppressClick,
    setSuppressClick,
  } = useRoute();
  const { mapState } = useWebSocketContext();
  const [currentBasemap, setCurrentBasemap] = useState<BasemapId>(CONFIG.defaultBasemap);

  const { handleMapClick } = useMapClick({
    state: { start, end },
    onUpdateStart: setStartPoint,
    onUpdateEnd: setEndPoint,
    suppressNextClick,
    onClearSuppress: clearSuppressClick,
  });

  const bounds = useMemo(
    () =>
      L.latLngBounds(
        [CONFIG.nycBounds.sw.lat, CONFIG.nycBounds.sw.lon],
        [CONFIG.nycBounds.ne.lat, CONFIG.nycBounds.ne.lon]
      ),
    []
  );

  // Determine which geometry to use for the editable layer
  const editableGeometry = useMemo(() => {
    if (!routeData?.geometry) return null;
    if (mode === "walk") return routeData.geometry;
    return desirePathData?.geometry || routeData.geometry;
  }, [routeData, desirePathData, mode]);

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
        key={currentBasemap}
        url={CONFIG.basemaps[currentBasemap].url}
        subdomains={CONFIG.tileSubdomains.split("")}
        maxZoom={CONFIG.maxZoom}
        attribution={CONFIG.basemaps[currentBasemap].attribution}
      />

      {/* Zoom control in bottom right */}
      <ZoomControl />

      {/* Zoom indicator in bottom left */}
      <ZoomIndicator />

      {/* Voted paths layer for polyline visualization */}
      <VotedPathsLayer overlay={mapState?.overlays?.desire_paths} />

      {/* GIS layers (crosswalks, stop signs, traffic signals, trees) */}
      <GISLayers />

      {/* Faded original route underneath when edits exist */}
      {originalRouteGeometry && (
        <RouteLayer geometry={originalRouteGeometry} mode={mode} />
      )}

      {/* Editable route with vertex handles */}
      {editableGeometry && editVertices.length >= 2 && (
        <EditableRouteLayer
          geometry={editableGeometry}
          mode={mode}
          editVertices={editVertices}
          editedSegments={editedSegments}
          modifiedSegmentIndices={modifiedSegmentIndices}
          onVertexDrag={dragVertex}
          onLineDrag={insertVertexOnLine}
          onDragStart={setSuppressClick}
        />
      )}

      {/* Main route layer when no editable layer (fallback) */}
      {routeData?.geometry && mode !== "walk" && !editableGeometry && (
        <RouteLayer geometry={routeData.geometry} mode={mode} />
      )}

      {/* Waypoint markers */}
      {waypoints.map((wp, index) => (
        <WaypointMarker
          key={`waypoint-${index}`}
          position={wp}
          index={index}
          onDragEnd={updateWaypoint}
        />
      ))}

      {/* Start marker - draggable to move start point */}
      {start.coords && (
        <RouteMarker
          position={start.coords}
          which="start"
          onDragStart={setSuppressClick}
          onDragEnd={(pos) => setStartPoint(pos, true)}
        />
      )}

      {/* End marker - draggable to move end point */}
      {end.coords && (
        <RouteMarker
          position={end.coords}
          which="end"
          onDragStart={setSuppressClick}
          onDragEnd={(pos) => setEndPoint(pos, true)}
        />
      )}
    </MapContainer>

    {/* Ghost pin rendered outside MapContainer for smooth 60fps positioning */}
    <GhostPin />

    {/* Basemap switcher control */}
    <BasemapSwitcher
      currentBasemap={currentBasemap}
      onBasemapChange={setCurrentBasemap}
    />

    {/* Layer control for GIS overlays */}
    <LayerControl />
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
