import { useEffect, useMemo } from "react";
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
import { RouteLayer, DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { GhostPin } from "../GhostPin";
import { HexHeatmapLayer } from "../HexHeatmapLayer";
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
    ghostWaypoints,
    splitDesirePaths,
    suppressNextClick,
    setStartPoint,
    setEndPoint,
    insertWaypointAtSegment,
    updateGhostWaypoint,
    updateWaypoint,
    clearSuppressClick,
    setSuppressClick,
  } = useRoute();
  const { mapState } = useWebSocketContext();

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

      {/* Hex heatmap layer for H3 hexagonal visualization */}
      <HexHeatmapLayer hexOverlay={mapState?.hex_overlay} />

      {/* Desire path layer for bike/drive - hidden when split paths exist */}
      {desirePathData?.geometry && mode !== "walk" && splitDesirePaths.length === 0 && (
        <DesirePathLayer
          geometry={desirePathData.geometry}
          segmentIndex={0}
          onSegmentDrag={insertWaypointAtSegment}
        />
      )}

      {/* Walk mode: route itself is draggable for waypoints */}
      {mode === "walk" && routeData?.geometry && splitDesirePaths.length === 0 && (
        <DesirePathLayer
          geometry={routeData.geometry}
          segmentIndex={0}
          onSegmentDrag={insertWaypointAtSegment}
          mode="walk"
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

      {/* Main route layer - not shown for walk mode (rendered as desire path instead) */}
      {routeData?.geometry && mode !== "walk" && (
        <RouteLayer geometry={routeData.geometry} mode={mode} />
      )}

      {/* Ghost waypoint markers - persistent after drop, draggable to recalculate split */}
      {ghostWaypoints.map((wp, index) => (
        <RouteMarker
          key={`ghost-waypoint-${index}`}
          position={wp}
          which="waypoint"
          onDragStart={setSuppressClick}
          onDragEnd={(pos) => updateGhostWaypoint(index, pos)}
        />
      ))}

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