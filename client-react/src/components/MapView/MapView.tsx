import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { getCurrentMap } from "../../map/runtime";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { GraphLayer } from "../GraphLayer/GraphLayer";
import { BoundaryLayer } from "../BoundaryLayer";
import { MapLibreBackground } from "../MapLibreBackground";
import type { LatLng, ProposalMatch } from "../../types";
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
    routeEdgeIds,
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

  // Edge IDs of the CURRENT rendered route — split segments when there are mids,
  // else the direct route. Drives GraphLayer's "proposals the path passes through"
  // highlight (recomputed as the route/votes change).
  const pathEdgeIds = useMemo(
    () => (splitDesirePaths.length > 0
      ? splitDesirePaths.flatMap((sp) => sp.edgeIds)
      : (routeEdgeIds ?? [])),
    [splitDesirePaths, routeEdgeIds]
  );

  const theme = useTheme();
  const mapStyle = mapStyleForTheme(theme);

  // Station networks (e.g. ebikes) vote on a single point — never a route — so
  // the map behaves point-only regardless of the theme's input mode (no end pin).
  const isStationNetwork = (getCurrentMap()?.network ?? "streets") !== "streets";
  const inputMode = isStationNetwork ? "point" : theme.inputMode;

  const { ghostState } = useGhostPin();

  const [leafletMap, setLeafletMap] = useState<L.Map | null>(null);
  const [cursorLatLng, setCursorLatLng] = useState<LatLng | null>(null);
  const [isHoveringPath, setIsHoveringPath] = useState(false);
  const [isDraggingMarker, setIsDraggingMarker] = useState(false);
  // Count of draggable markers (start/end/waypoints) currently hovered, so the
  // start-placement ghost hides while the grab cursor is over one.
  const [markerHoverCount, setMarkerHoverCount] = useState(0);
  // Which start/end waypoint currently sits on a top proposal (matched edge index,
  // Which waypoints sit on a top proposal (edge + label, or null), parallel to
  // ghostWaypoints for `mids`. A matched waypoint's kite is hidden (the tinted
  // proposal indicator stands in for it); the kite stays interactive underneath.
  const [waypointMatch, setWaypointMatch] = useState<{
    start: ProposalMatch | null;
    end: ProposalMatch | null;
    mids: (ProposalMatch | null)[];
  }>({ start: null, end: null, mids: [] });
  // The matched waypoint (start/end/mid on a proposal) the cursor is hovering,
  // fed to GraphLayer so it can light that proposal's hover card — the indicator
  // is passthrough (its kite takes the pointer), so it can't do so itself.
  const [hoverProposalPoint, setHoverProposalPoint] = useState<LatLng | null>(null);
  // Live dragged-waypoint position, fed to GraphLayer to light the drop-target
  // proposal. rAF-coalesced so a 60fps drag re-renders at most once per frame.
  const [dragPoint, setDragPoint] = useState<LatLng | null>(null);
  const latestDragRef = useRef<LatLng | null>(null);
  const dragRafRef = useRef(0);
  const handleDragMove = useCallback((ll: LatLng) => {
    latestDragRef.current = ll;
    if (dragRafRef.current) return;
    dragRafRef.current = requestAnimationFrame(() => {
      dragRafRef.current = 0;
      setDragPoint(latestDragRef.current);
    });
  }, []);
  const clearDragPoint = useCallback(() => {
    if (dragRafRef.current) { cancelAnimationFrame(dragRafRef.current); dragRafRef.current = 0; }
    setDragPoint(null);
  }, []);
  // Momentary placement-ghost suppression — one unified rule, no per-action wiring:
  // ANY click/tap hides the hover ghost, and the next hover (cursor move) re-shows
  // it. On desktop a move follows the click almost immediately, so the ghost
  // reappears at once (a brief, intended flicker). On touch there's no hover after
  // a tap, so the ghost stays hidden — no stuck ghost lingering wherever the user
  // last tapped (a proposal, a marker delete, a copy/close button…).
  const [ghostSuppressed, setGhostSuppressed] = useState(false);
  // Why `click` (not pointerup/touchend): a mobile tap emits a synthetic mousemove
  // *before* its click, and that mousemove (handleCursorMove) would clear the
  // suppression — but `click` fires last in the tap sequence, so it wins. Capture
  // phase so Leaflet handlers that stopPropagation (markers, proposal indicators)
  // can't hide the event from us. This only flips a boolean — it never
  // preventDefaults or stops the event — so cluster spread and every other click
  // handler are completely untouched.
  useEffect(() => {
    const suppressGhost = () => setGhostSuppressed(true);
    document.addEventListener("click", suppressGhost, true);
    return () => document.removeEventListener("click", suppressGhost, true);
  }, []);
  // The next hover (cursor move) clears the suppression — desktop re-shows the
  // ghost at once; touch fires no hover, so the ghost stays hidden after a tap.
  const handleCursorMove = useCallback((ll: LatLng | null) => {
    setCursorLatLng(ll);
    setGhostSuppressed(false);
  }, []);
  // Thin wrappers kept so call sites (indicator click, map click) stay stable.
  // Suppression is handled globally by the click listener above, not here.
  const placeStart = useCallback((ll: LatLng) => setStartPoint(ll), [setStartPoint]);
  const placeEnd = useCallback((ll: LatLng) => setEndPoint(ll), [setEndPoint]);
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
    clearDragPoint();
  }, [clearDragPoint]);

  // Indicator click follows the same tool logic as a normal map click.
  const handleIndicatorClick = useCallback((latlng: LatLng) => {
    clearSplitPaths();
    if (activeTool === "end" && start.coords) {
      placeEnd(latlng);
      setActiveTool("start");
    } else {
      if (start.coords || end.coords) clearPoints();
      placeStart(latlng);
    }
  }, [activeTool, start.coords, end.coords, clearPoints, clearSplitPaths, placeStart, placeEnd, setActiveTool]);

  // Clicking a top-proposal waypoint (start/end/mid) restarts the path from it:
  // it becomes the new start and the rest of the path is cleared — like clicking
  // any point on the map. Removal from the sequence moves to the indicator's [x].
  const handleProposalRestart = useCallback((latlng: LatLng) => {
    clearPoints();
    placeStart(latlng);
  }, [clearPoints, placeStart]);

  // A real route exists (start AND end on a street map). Gates the proposal [x]
  // remove-boxes and the click-to-restart behavior to route mode — not a lone
  // point and not station maps (where the indicator IS the selection).
  const isRouteMode = !!start.coords && !!end.coords && !isStationNetwork;

  // Tiny [x] affordance pinned to a selected proposal's top-right corner. Default
  // Leaflet div-icon box is reset away in globals.css (.proposal-x-icon). The box
  // border matches the proposal's selection-ring color by role (start=teal,
  // end=red, mid=ink), so the [x] reads as part of that highlighted icon.
  const makeProposalXIcon = (role: "start" | "end" | "mid") =>
    L.divIcon({
      className: "proposal-x-icon",
      html: `<span class="proposal-x-hit proposal-x-hit--${role}" aria-label="Remove from route">×</span>`,
      iconSize: [13, 13],
      iconAnchor: [-9, 40],
    });
  const proposalXIconStart = useMemo(() => makeProposalXIcon("start"), []);
  const proposalXIconEnd = useMemo(() => makeProposalXIcon("end"), []);
  const proposalXIconMid = useMemo(() => makeProposalXIcon("mid"), []);

  const { handleMapClick } = useMapClick({
    state: { start, end },
    inputMode,
    activeTool,
    onUpdateStart: placeStart,
    onUpdateEnd: placeEnd,
    onSetActiveTool: setActiveTool,
    onClearPoints: clearPoints,
    onClearGhostWaypoints: clearSplitPaths,
    onSetError: setError,
    suppressNextClick,
    onClearSuppress: clearSuppressClick,
  });

  // GraphLayer publishes its cluster-exploder here. A tap on the path first tries
  // to fan out a stack of proposals at that spot (no side effect yet); only if
  // there's nothing to fan does the tap fall through to the map-click (restart).
  const clusterExploderRef = useRef<
    ((latlng: LatLng) => boolean) | null
  >(null);
  const handlePathTap = useCallback((latlng: LatLng) => {
    if (clusterExploderRef.current?.(latlng)) return;
    handleMapClick(latlng);
  }, [handleMapClick]);

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
        startPoint={start.coords}
        endPoint={end.coords}
        ghostWaypoints={ghostWaypoints}
        dragPoint={dragPoint}
        hoverProposalPoint={hoverProposalPoint}
        onWaypointMatch={setWaypointMatch}
        onIndicatorClick={handleIndicatorClick}
        clusterExploderRef={clusterExploderRef}
        onRemoveSelected={clearStart}
        suppressHover={isHoveringPath || markerHoverCount > 0}
        pathEdgeIds={pathEdgeIds}
      />
      <MapDragCursor />
      <CursorTracker onMove={handleCursorMove} />
      <MapClickHandler onMapClick={handleMapClick} />
      <SnapMarker
        cursorLatLng={cursorLatLng}
        activeTool={activeTool}
        hasStart={!!start.coords}
        pointOnly={inputMode === "point"}
        suppress={isHoveringPath || ghostState.isDragging || isDraggingMarker || markerHoverCount > 0 || ghostSuppressed}
      />

      {/* Raster tile fallback — visible until MapLibre loads, or when WebGL unavailable.
          maxNativeZoom caps tile requests at CartoDB's available zoom and upscales
          beyond it, so deep zoom (maxZoom) doesn't request nonexistent blank tiles. */}
      <TileLayer
        key={mapStyle.id}
        url={mapStyle.tileUrl}
        subdomains={["a", "b", "c", "d"]}
        maxZoom={CONFIG.maxZoom}
        maxNativeZoom={19}
        attribution={mapStyle.tileAttribution}
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
          onTap={handlePathTap}
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
            onTap={handlePathTap}
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
        isDragging={isDraggingMarker}
      />

      {/* Ghost waypoint markers - persistent after drop, draggable to recalculate
          split. When a mid sits on a top proposal the kite is hidden (the tinted
          proposal indicator stands in) but stays interactive: grabbing it shows a
          kite that drags out while the proposal stays put; clicking removes it. */}
      {ghostWaypoints.map((wp, index) => (
        <RouteMarker
          key={ghostWaypointIds[index] ?? `ghost-waypoint-${index}`}
          position={wp}
          which="waypoint"
          hidden={waypointMatch.mids[index] != null}
          onDragStart={handleMarkerDragStart}
          onDragMove={handleDragMove}
          onDragEnd={(pos) => updateGhostWaypoint(index, pos)}
          onDragFinish={handleMarkerDragFinish}
          onDelete={() => removeGhostWaypoint(index)}
          // On a proposal: tapping restarts the path from here ([x] removes it).
          onTap={waypointMatch.mids[index] != null ? () => handleProposalRestart(wp) : undefined}
          onOutOfBounds={handleOutOfBounds}
          onHoverChange={(h) => {
            handleMarkerHover(h);
            // On a proposal, surface that proposal's hover card while the kite is hovered.
            if (waypointMatch.mids[index] != null) setHoverProposalPoint(h ? wp : null);
          }}
        />
      ))}

      {/* Remove-from-route [x] for each mid that sits on a top proposal (route
          mode only). Reuses removeGhostWaypoint — the old sequence-removal path. */}
      {isRouteMode && ghostWaypoints.map((wp, index) => (
        waypointMatch.mids[index] != null ? (
          <Marker
            key={`mid-x-${ghostWaypointIds[index] ?? index}`}
            position={[wp.lat, wp.lng]}
            icon={proposalXIconMid}
            zIndexOffset={4000}
            eventHandlers={{ click: () => removeGhostWaypoint(index) }}
          />
        ) : null
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

      {/* Start marker — the universal draggable/removable waypoint marker. On a
          street map it always renders; when the start sits on a proposal the kite
          is hidden (the tinted indicator stands in) but stays interactive as the
          grab/click handle. On a station network it's never rendered — the tinted
          station indicator is the start, and GraphLayer handles click-to-deselect. */}
      {start.coords && !isStationNetwork && (
        <RouteMarker
          position={start.coords}
          which="start"
          hidden={waypointMatch.start != null}
          onDragStart={handleMarkerDragStart}
          onDragMove={handleDragMove}
          onDragEnd={setStartPoint}
          onDragFinish={handleMarkerDragFinish}
          onDelete={clearStart}
          // On a proposal in route mode: tap restarts from here ([x] removes).
          // As a lone start point, keep tap-to-delete (no [x] is shown there).
          onTap={isRouteMode && waypointMatch.start != null && start.coords
            ? () => handleProposalRestart(start.coords!) : undefined}
          onOutOfBounds={handleOutOfBounds}
          onHoverChange={(h) => {
            handleMarkerHover(h);
            if (waypointMatch.start != null && start.coords) setHoverProposalPoint(h ? start.coords : null);
          }}
        />
      )}

      {isRouteMode && waypointMatch.start != null && start.coords && (
        <Marker
          position={[start.coords.lat, start.coords.lng]}
          icon={proposalXIconStart}
          zIndexOffset={4000}
          eventHandlers={{ click: () => clearStart() }}
        />
      )}

      {/* End marker — same universal marker; the kite is hidden when the end sits
          on a proposal (the red-tinted indicator stands in). */}
      {end.coords && !isStationNetwork && (
        <RouteMarker
          position={end.coords}
          which="end"
          hidden={waypointMatch.end != null}
          onDragStart={handleMarkerDragStart}
          onDragMove={handleDragMove}
          onDragEnd={setEndPoint}
          onDragFinish={handleMarkerDragFinish}
          onDelete={clearEnd}
          // End only exists with a start, so always route mode: tap restarts.
          onTap={waypointMatch.end != null && end.coords
            ? () => handleProposalRestart(end.coords!) : undefined}
          onOutOfBounds={handleOutOfBounds}
          onHoverChange={(h) => {
            handleMarkerHover(h);
            if (waypointMatch.end != null && end.coords) setHoverProposalPoint(h ? end.coords : null);
          }}
        />
      )}

      {isRouteMode && waypointMatch.end != null && end.coords && (
        <Marker
          position={[end.coords.lat, end.coords.lng]}
          icon={proposalXIconEnd}
          zIndexOffset={4000}
          eventHandlers={{ click: () => clearEnd() }}
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