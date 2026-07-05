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
import { useRoute, useGhostPin, useTheme, useHeatmap } from "../../context";
import { useMapClick } from "../../hooks";
import { kiteGhostIcon } from "../../utils/kiteIcon";
import { setMapViewState, setMapInstance, getInitialMapView } from "../../utils/mapViewState";
import { getCurrentMap } from "../../map/runtime";
import { chooseAnchorOrder, type RouteProposal } from "../GraphLayer/routeProposals";
import { RouteMarker } from "../RouteMarker";
import { DesirePathLayer, SplitDesirePathLayer } from "../RouteLayer";
import { WaypointMarker } from "../WaypointMarker";
import { WaypointConnectors } from "../WaypointConnectors";
import { GhostPin } from "../GhostPin";
import { NavHistoryControls } from "../NavHistoryControls/NavHistoryControls";
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
    startReplaceArmed,
    setStartPoint,
    setStartVoteEdgeId,
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

  const { setMapReady } = useHeatmap();

  // True once MapLibre's WebGL map has loaded — from then on MapLibre IS the
  // base map (raster + block heat + block selection), and the opaque Leaflet
  // raster fallback below must unmount or it paints over every MapLibre layer.
  const [maplibreActive, setMaplibreActive] = useState(false);
  const handleMapLibreReady = useCallback((active: boolean) => {
    if (active) {
      setMaplibreActive(true);
      // The Leaflet container's own opaque background must also get out of the
      // way (globals.css keys off this class).
      document.body.classList.add("maplibre-active");
    }
    setMapReady();
  }, [setMapReady]);

  // Backstop: if MapLibre's `load` never fires (e.g. it constructs but stalls),
  // don't trap the user behind the loader forever — mark the base map ready
  // after a generous timeout so the heatmap alone can dismiss the loader.
  useEffect(() => {
    const id = window.setTimeout(setMapReady, 15000);
    return () => window.clearTimeout(id);
  }, [setMapReady]);

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
  // GraphLayer publishes its cluster-exploder here (fan a crowded proposal stack
  // out at a tapped point, returning true when it consumed the tap). Every
  // proposal tap — on the path (handleTap) AND on a matched waypoint's kite
  // (handleProposalRestart) — routes through it so a stack always explodes first.
  const clusterExploderRef = useRef<((latlng: LatLng) => boolean) | null>(null);
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
  // The live drag position fed to GraphLayer (cluster-explode-on-drag + the
  // drop-target ring). It's the kite drag's `dragPoint` OR — when the ghost pin is
  // active (dragging the path to create a mid, and dragging a mid out of an
  // exploded proposal both use the ghost pin) — the ghost's live position. Derived,
  // not mirrored into state, so the two sources can't clobber each other.
  const effectiveDragPoint = useMemo<LatLng | null>(() => {
    if (ghostState.isDragging && ghostState.screenPosition && leafletMap) {
      const { x, y } = ghostState.screenPosition;
      const rect = leafletMap.getContainer().getBoundingClientRect();
      const ll = leafletMap.containerPointToLatLng(L.point(x - rect.left, y - rect.top));
      return { lat: ll.lat, lng: ll.lng };
    }
    return dragPoint;
  }, [ghostState, leafletMap, dragPoint]);
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
  // voteEdgeId pins the vote to a specific proposal edge (see RoutePoint.voteEdgeId);
  // a plain click/drag omits it and the start re-snaps its coordinate.
  const placeStart = useCallback((ll: LatLng, voteEdgeId?: number) => setStartPoint(ll, undefined, voteEdgeId), [setStartPoint]);
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
  const handleIndicatorClick = useCallback((latlng: LatLng, voteEdgeId?: number) => {
    // Drop any stale "skip next click" from an upgrade drag, so the click that
    // selects this proposal — and the next one after it — both register (bug
    // where a drag's trailing click landed on a marker, never consuming it).
    clearSuppressClick();
    clearSplitPaths();
    if (activeTool === "end" && start.coords) {
      placeEnd(latlng);
      setActiveTool("start");
    } else if (startReplaceArmed) {
      // Armed from the legend: move the start onto this proposal, keep the route.
      placeStart(latlng, voteEdgeId);
    } else {
      if (start.coords || end.coords) clearPoints();
      placeStart(latlng, voteEdgeId);
    }
  }, [activeTool, startReplaceArmed, start.coords, end.coords, clearPoints, clearSplitPaths, placeStart, placeEnd, setActiveTool, clearSuppressClick]);

  // Forcing the route through a ROUTE proposal (diamond). The corridor's two
  // anchor endpoints become waypoints: with an existing route they're inserted
  // as ghost mids in the faster order (start→A→B→end vs start→B→A→end); with no
  // route yet they seed start + end. Either way the route now traverses — and
  // thus selects — the corridor. Duration is approximated by equirectangular
  // distance (monotonic with foot time at city scale), so we don't block on OSRM.
  const handleRouteProposalClick = useCallback((p: RouteProposal) => {
    clearSuppressClick();
    clearSplitPaths();
    const [a, b] = p.anchorCoords;
    const durationOf = (x: LatLng, y: LatLng) => {
      const meanLat = ((x.lat + y.lat) / 2) * (Math.PI / 180);
      const dLat = (y.lat - x.lat) * (Math.PI / 180);
      const dLng = (y.lng - x.lng) * (Math.PI / 180) * Math.cos(meanLat);
      return Math.hypot(dLat, dLng); // proportional to meters; only the order matters
    };
    if (start.coords && end.coords && !isStationNetwork) {
      const [first, second] = chooseAnchorOrder([start.coords, end.coords], a, b, durationOf);
      insertWaypointAtSegment(0, first);
      insertWaypointAtSegment(1, second);
    } else {
      clearPoints();
      setStartPoint(a);
      if (!isStationNetwork) setEndPoint(b);
    }
  }, [start.coords, end.coords, isStationNetwork, insertWaypointAtSegment, clearPoints, setStartPoint, setEndPoint, clearSuppressClick, clearSplitPaths]);

  // Tapping a top-proposal waypoint (start/end/mid). A crowded stack fans out
  // first (no side effect) — the SAME exploder a path tap uses, so matched
  // proposals disambiguate exactly like on-path ones. Otherwise it restarts the
  // path from here: this becomes the new start, the rest clears. clearSuppressClick
  // drops any stale "skip next click" left by the drag that upgraded this mid (its
  // trailing click landed on the kite, not the map, so it was never consumed).
  const handleProposalRestart = useCallback((latlng: LatLng) => {
    clearSuppressClick();
    if (clusterExploderRef.current?.(latlng)) return;
    clearPoints();
    placeStart(latlng);
  }, [clearPoints, placeStart, clearSuppressClick]);

  // A real route exists (start AND end on a street map). Gates the click-to-restart
  // behavior to route mode — not a lone point and not station maps (where the
  // indicator IS the selection). GraphLayer applies the same gate to the [×] badge.
  const isRouteMode = !!start.coords && !!end.coords && !isStationNetwork;

  // Resolve the [×] badge baked into a matched proposal's indicator (GraphLayer)
  // to the right removal: the badge stamps the proposal's edge, and waypointMatch
  // tells us which waypoint sits on that edge. One handler for all three roles —
  // the badge rides inside the icon, so there's no per-role marker to wire.
  const removeProposalAtEdge = useCallback((edgeIdx: number) => {
    if (waypointMatch.start?.edgeIdx === edgeIdx) { clearStart(); return; }
    if (waypointMatch.end?.edgeIdx === edgeIdx) { clearEnd(); return; }
    const midIdx = waypointMatch.mids.findIndex((m) => m?.edgeIdx === edgeIdx);
    if (midIdx >= 0) removeGhostWaypoint(midIdx);
  }, [waypointMatch, clearStart, clearEnd, removeGhostWaypoint]);

  // Drop from a drag started on an exploded on-route proposal (GraphLayer renders
  // the ghost + dotted trail). Route by what the edge IS — same outcome as dragging
  // the non-exploded proposal: a matched start/end/mid MOVES that waypoint to the
  // drop; an on-path proposal inserts a NEW mid at the segment its `origin` lies on.
  const onProposalDrop = useCallback((edgeIdx: number, origin: LatLng, drop: LatLng) => {
    if (waypointMatch.start?.edgeIdx === edgeIdx) { setStartPoint(drop); return; }
    if (waypointMatch.end?.edgeIdx === edgeIdx) { setEndPoint(drop); return; }
    const midIdx = waypointMatch.mids.findIndex((m) => m?.edgeIdx === edgeIdx);
    if (midIdx >= 0) { updateGhostWaypoint(midIdx, drop); return; }

    // On-path (not a waypoint): insert a NEW mid into whichever route segment the
    // origin lies on, then insertWaypointAtSegment splits/reroutes like a path drag.
    const map = leafletMap;
    if (!map) return;
    const segs: { idx: number; coords: [number, number][] }[] =
      splitDesirePaths.length > 0
        ? splitDesirePaths.map((sp) => ({ idx: sp.segmentIndex, coords: sp.geometry.coordinates }))
        : (routeData?.geometry ? [{ idx: 0, coords: routeData.geometry.coordinates }] : []);
    if (segs.length === 0) return;
    const p = map.latLngToContainerPoint([origin.lat, origin.lng]);
    let bestIdx = segs[0].idx;
    let bestD = Infinity;
    for (const s of segs) {
      for (let i = 0; i < s.coords.length - 1; i++) {
        const a = map.latLngToContainerPoint([s.coords[i][1], s.coords[i][0]]);
        const b = map.latLngToContainerPoint([s.coords[i + 1][1], s.coords[i + 1][0]]);
        const d = L.LineUtil.pointToSegmentDistance(p, a, b);
        if (d < bestD) { bestD = d; bestIdx = s.idx; }
      }
    }
    insertWaypointAtSegment(bestIdx, drop);
  }, [waypointMatch, setStartPoint, setEndPoint, updateGhostWaypoint, leafletMap, splitDesirePaths, routeData, insertWaypointAtSegment]);

  const { handleMapClick } = useMapClick({
    state: { start, end },
    inputMode,
    activeTool,
    startReplaceArmed,
    onUpdateStart: placeStart,
    onUpdateEnd: placeEnd,
    onSetActiveTool: setActiveTool,
    onClearPoints: clearPoints,
    onClearGhostWaypoints: clearSplitPaths,
    onSetError: setError,
    suppressNextClick,
    onClearSuppress: clearSuppressClick,
  });

  // Any tap on the map or path: fan out a proposal stack at that spot if there is
  // one and CONSUME the tap (so exploding a cluster never also places/moves a
  // start), else fall through to the normal map-click. Used for path taps AND the
  // bare-map click, so a click anywhere over a cluster explodes rather than
  // selecting whichever icon sits on top. Mirrors handleProposalRestart (kite).
  const handleTap = useCallback((latlng: LatLng) => {
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
    <MapLibreBackground leafletMap={leafletMap} mapStyle={mapStyle} onReady={handleMapLibreReady} />

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
        dragPoint={effectiveDragPoint}
        hoverProposalPoint={hoverProposalPoint}
        onWaypointMatch={setWaypointMatch}
        onPinnedResolve={setStartVoteEdgeId}
        onIndicatorClick={handleIndicatorClick}
        onRouteProposalClick={handleRouteProposalClick}
        clusterExploderRef={clusterExploderRef}
        onRemoveProposal={removeProposalAtEdge}
        onProposalDrop={onProposalDrop}
        onRemoveSelected={clearStart}
        suppressHover={isHoveringPath || markerHoverCount > 0}
        pathEdgeIds={pathEdgeIds}
      />
      <MapDragCursor />
      <CursorTracker onMove={handleCursorMove} />
      <MapClickHandler onMapClick={handleTap} />
      <SnapMarker
        cursorLatLng={cursorLatLng}
        activeTool={activeTool}
        hasStart={!!start.coords}
        pointOnly={inputMode === "point"}
        suppress={isHoveringPath || ghostState.isDragging || isDraggingMarker || markerHoverCount > 0 || ghostSuppressed}
      />

      {/* Raster tile fallback — visible until MapLibre loads, or permanently when
          WebGL is unavailable. MUST unmount once MapLibre is active: it is opaque
          and sits above the MapLibre canvas, so leaving it hides the block heat
          and block-selection layers entirely. maxNativeZoom caps tile requests at
          CartoDB's available zoom and upscales beyond it, so deep zoom (maxZoom)
          doesn't request nonexistent blank tiles. */}
      {!maplibreActive && (
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
          onTap={handleTap}
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
            onTap={handleTap}
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

      {/* The remove-from-route [×] for a matched mid is baked into its proposal
          indicator (GraphLayer's `removeEdge` badge), so it scales and fans out
          with the icon and needs no marker here. Removal routes through
          `removeProposalAtEdge` → removeGhostWaypoint. */}

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
    </MapContainer>

    {/* Ghost pin rendered outside MapContainer for smooth 60fps positioning */}
    <GhostPin />

    {/* Back / forward through the selection history (bottom-left) */}
    <NavHistoryControls />
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