/**
 * Graph Layer - OSM Network Vote Visualization
 *
 * Canvas-based renderer for edges/nodes with vote counts from /api/graph.
 * Edges glow blue based on vote intensity (log-scaled).
 * Hovering an edge or node highlights it and shows a tooltip with:
 *   - Street name (or reverse-geocoded address)
 *   - Vote count
 *   - Top 3 vote types
 * Clears on zoom, redraws on zoom end and pan.
 */

import { useEffect, useLayoutEffect, useRef, useCallback, useState, useMemo } from "react";
import type { CSSProperties, MutableRefObject } from "react";
import { useMap, Marker } from "react-leaflet";
import { createPortal } from "react-dom";
import L from "leaflet";
import Flatbush from "flatbush";
import { CONFIG } from "../../config";
import { COLOR_START, COLOR_END } from "../../colors";
import { withMap, getMapSlug, passcodeHeaders, getCurrentMap } from "../../map/runtime";
import { useWebSocketContext } from "../../context/WebSocketContext";
import { useGraphSnap, useTheme, useHeatmap, useGhostPin, useRoute } from "../../context";
import type { GraphData, ProposalMatch } from "../../types";
import {
  BLOCK_VOTES_EVENT, BLOCK_SELECT_EVENT, blockIdAtLatLng,
  type BlockVotesDetail, type BlockSelectDetail,
} from "../MapLibreBackground/MapLibreBackground";
import { makeVoteTypeIcon } from "./voteTypeIcon";
import { suggestionGlyphForLabel } from "../../utils/suggestionIcon";
import { selectTopProposals, topLabelForEdges, TOP_PROPOSAL_MIN_SPACING_M, type VoteTypeWinner } from "./topProposals";
import {
  createRouteProposalJob, corridorCoordinates, corridorFromEdgeIds, routeBlockEdges, isRouteCovered,
  expandSelectionToUndirected,
  type RouteProposal,
} from "./routeProposals";
import { applyMyVoteChange, applyEdgeVoteChange, applyAuthoritativeCounts, applyBlockCounts, topProposalDiffs } from "./voteApply";
import {
  COORD_SCALE,
  type GraphTopology,
  type NodeAdj,
  type BlockIndex,
  nodeLat,
  nodeLon,
  edgeFrom,
  edgeTo,
  edgeName,
  topologyFromJson,
  decodeTopologyBin,
  buildNodeAdj,
  buildBlockIndex,
  touchedBlockKeys,
  adjEdgesOf,
  adjShortest,
} from "./graphTopology";
import { materializeBlocks, selectionVoteRows } from "../../utils/blockSelection";
import { iconForLabel, iconSrc, mapStyleForTheme, pointTypeForLabel } from "../../themes";
import { buildHeatRampStops, buildPinRampStops, sampleHeatRamp, HEAT_PEAK_POS } from "../../mapStyles";
import {
  getCachedTopology,
  setCachedTopology,
  getCachedTopologyBin,
  setCachedTopologyBin,
  getCachedVotes,
  setCachedVotes,
  clearGraphCache,
} from "../../utils/graphCache";
import { decodeSparseVotes, isSparseVotes } from "../../utils/sparseVotes";
import { reportTopologySource } from "../../utils/loadTelemetry";
import {
  blockCoverage, getVotesVersion, reconcileEdge, resetMapVotes, setVoteTypeMap,
  type VoteDirection,
} from "../../utils/voteStore";
import { castVotes, voteButtonState } from "../../utils/castVote";
import { dlog, dwarn, derror, debugState, debugProbe } from "../../utils/debugLog";
import { useVotesVersion } from "../../utils/useVotesVersion";
import { getVoterId } from "../../utils/voterIdentity";
import { isHoverSuppressed } from "../../utils/touchHover";
import { buildSelectionUrl, copyToClipboard } from "../../utils/shareLink";
import { CheckIcon } from "../CheckIcon";
import { arrayMax, haversineMeters, HEAT_FULL_SCALE, NEG_HEAT_FULL_SCALE, HEATMAP_OPACITY } from "./geometryHelpers";
import { resolveAddress } from "./geocodingHelpers";
import {
  STICKY_RESELECT_PX, HIGHLIGHT_RING_WIDTH, HIGHLIGHT_INNER_WIDTH,
  HIGHLIGHT_NODE_OUTER_R, HIGHLIGHT_NODE_INNER_R, HIGHLIGHT_INTERIOR_ALPHA,
  type HoverTarget, type ResolvedSelection, type VoteTypeRow,
  decodeVoteTypes, TOP_PROPOSAL_LIMIT, CLUSTER_RADIUS_PX, SPREAD_CELL_PX,
  SPREAD_DURATION_MS, SPREAD_ANIM_MS, PROPOSALS_REFRESH_INTERVAL_MS,
  scheduleIdleSlice, MID_DRAG_THRESHOLD_SQ, MID_DRAG_TRAIL_STYLE,
  MY_VOTES_EDGE_CAP, ROUTE_VOTES_EDGE_CAP, ROUTE_VOTES_DEBOUNCE_MS,
  buildNodeIndex, buildEdgeIndex,
  hitTest, blockFiltersAt, adjShortestInBlock, projectOntoEdge,
} from "./spatialLookup";
import { targetLatLng, rbtpDisplayPos, spreadKeyEdge, spreadKeyRoute, votesMatchTopology } from "./topologyHelpers";

// ---------------------------------------------------------------------------
// Top-proposal indicator marker
// ---------------------------------------------------------------------------

/**
 * A single top-proposal icon. Wraps a Leaflet Marker but adds an unmount
 * release: if the icon disappears while the cursor is still over it — e.g. the
 * winners list changing under the pointer —
 * Leaflet never fires `mouseout`, so `onDeactivate` would never run and the
 * map hover handler's `overIndicatorRef` would stay stuck `true`, silently
 * killing all hover. The cleanup fires the release on unmount-while-hovered,
 * mirroring the same guard RouteMarker uses for its hover counter.
 */
function IndicatorMarker({
  position, icon, zIndexOffset, onActivate, onDeactivate, onClick, onMidDragDown,
}: {
  position: [number, number];
  icon: L.DivIcon;
  zIndexOffset: number;
  onActivate: () => void;
  onDeactivate: () => void;
  onClick: () => void;
  /** When set, a press on the icon can become a "drag a new mid out of this
   *  proposal" gesture (exploded on-route proposals only). Wired through Leaflet's
   *  OWN marker mousedown (not a native DOM listener) so it survives setIcon —
   *  Leaflet re-binds marker events when the icon element is recreated, whereas a
   *  listener pinned to a specific element would be left on a stale node. */
  onMidDragDown?: (e: L.LeafletMouseEvent) => void;
}) {
  const hoveredRef = useRef(false);
  const onDeactivateRef = useRef(onDeactivate);
  useEffect(() => { onDeactivateRef.current = onDeactivate; }, [onDeactivate]);
  useEffect(() => () => {
    if (hoveredRef.current) {
      hoveredRef.current = false;
      onDeactivateRef.current();
    }
  }, []);

  return (
    // ALWAYS created interactive: a passive (passthrough) proposal is gated by
    // its icon's `passthrough` class (pointer-events:none), NOT by Leaflet's
    // `interactive` option. react-leaflet applies `interactive` only when the
    // marker is CREATED (its updater handles position/icon/zIndex/opacity and
    // nothing else), so a marker that mounted passive would stay event-dead
    // for its whole life — the "exploded route diamonds aren't clickable" bug:
    // corridor diamonds stacked on a waypoint mounted as passthrough, and no
    // later state (fan-out, waypoint moved away) could ever revive them. The
    // CSS class swaps with the icon on every render, so it is the one gate
    // that tracks state correctly.
    <Marker
      position={position}
      icon={icon}
      zIndexOffset={zIndexOffset}
      eventHandlers={{
        mouseover: () => { hoveredRef.current = true; onActivate(); },
        mouseout: () => { hoveredRef.current = false; onDeactivate(); },
        click: onClick,
        ...(onMidDragDown ? { mousedown: onMidDragDown } : {}),
      }}
    />
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

// Stable default so an omitted `ghostWaypoints` prop doesn't churn the match effect.
const EMPTY_WAYPOINTS: { lat: number; lng: number }[] = [];

interface GraphLayerProps {
  onSnap?: (pos: { lat: number; lng: number } | null) => void;
  /** When set, a tooltip is pinned at this point showing nearest node vote data. */
  pinnedPoint?: { lat: number; lng: number } | null;
  /** The active start/end/mid waypoints. When one coincides with a top proposal,
   *  the host renders that waypoint AS the proposal pin (and GraphLayer suppresses
   *  its own indicator for that edge) — see `onWaypointMatch`. */
  startPoint?: { lat: number; lng: number } | null;
  endPoint?: { lat: number; lng: number } | null;
  /** The mid (ghost) waypoints, so they too can match/skin onto proposals. */
  ghostWaypoints?: { lat: number; lng: number }[];
  /** Live position of the waypoint currently being dragged (from the marker's
   *  `drag` event, so it updates on desktop AND touch). Drives the white/black
   *  drop-target ring on the proposal the drop would link to. Null when idle. */
  dragPoint?: { lat: number; lng: number } | null;
  /** Position of a matched waypoint (start/end/mid sitting on a proposal) the
   *  cursor is currently hovering. That proposal's indicator is passthrough — its
   *  kite RouteMarker takes the pointer — so it can't show its own hover card; the
   *  host reports the hover here and we light the same card the indicator would.
   *  Null when no matched waypoint is hovered. */
  hoverProposalPoint?: { lat: number; lng: number } | null;
  /** Fires when the waypoint → proposal-edge matches change. Each entry is the
   *  matched proposal's edge index + label, or null when that waypoint isn't on a
   *  proposal. `mids` is parallel to `ghostWaypoints`. The host tints/hides the
   *  matching waypoint marker. */
  onWaypointMatch?: (m: {
    start: ProposalMatch | null;
    end: ProposalMatch | null;
    mids: (ProposalMatch | null)[];
  }) => void;
  /** Called when a user clicks/taps a top-proposal indicator. Receives the
   *  edge midpoint of the indicator's segment. Hosts use this to place a
   *  start point at that location so the segment becomes selected. `voteEdgeId`
   *  is the exact edge the modal votes on — pass it through so the banner votes
   *  on the same edge instead of re-snapping the midpoint (which can diverge). */
  onIndicatorClick?: (latlng: { lat: number; lng: number }, voteEdgeId?: number) => void;
  /** Called when a user clicks/taps a ROUTE-proposal diamond. The host selects
   *  the corridor FLAT OUT — start/end seeded at its two anchors, replacing any
   *  existing route. (Threading into an existing route is drag-drop only.) */
  onRouteProposalClick?: (proposal: RouteProposal) => void;
  /** GraphLayer writes a function here that, given a tapped point, fans out a
   *  crowded proposal cluster at that spot and returns true (consuming the tap).
   *  Returns false if there's nothing to fan out, so the host runs its tap action.
   *  Lets a path tap explode a stack of stacked proposals before any side effect. */
  clusterExploderRef?: MutableRefObject<
    ((latlng: { lat: number; lng: number }) => boolean) | null
  >;
  /** Removes the route waypoint (start/end/mid) that sits on the given proposal
   *  edge. Fired by the [×] badge baked into a matched proposal's indicator (the
   *  badge lives inside the icon, so it needs no position plumbing). The host
   *  resolves edge → which waypoint from its current match set. */
  onRemoveProposal?: (edgeIdx: number) => void;
  /** Removes the currently-selected point. Wired to the same handler as the
   *  start marker's delete so the modal's X is functionally identical. */
  onRemoveSelected?: () => void;
  /** Clears the ENTIRE route selection — the route-summary card's [×]. */
  onClearRoute?: () => void;
  /** Removes a route proposal's corridor from the route (both anchors together).
   *  Fired by the [×] badge on a selected RBTP diamond (`data-x-route`). */
  onRouteProposalRemove?: (proposal: RouteProposal) => void;
  /** GraphLayer writes a resolver here: given a dropped point, the RBTP diamond
   *  whose icon sits there (at its DISPLAY position — fanned cell when spread),
   *  or null. Resolving a hit also marks that RBTP selected (the analogue of the
   *  diamond's own click), so the host can thread the corridor's anchors into
   *  the route and have the diamond immediately read selected. */
  routeProposalAtRef?: MutableRefObject<
    ((latlng: { lat: number; lng: number }) => RouteProposal | null) | null
  >;
  /** True while the cursor is over the route/desire path. The graph hover yields
   *  (no card/highlight) so the path's own midwaypoint grab affordance isn't
   *  competed with by a graph proposal tooltip. */
  suppressHover?: boolean;
  /** Graph edge IDs of the CURRENT rendered route (direct route, or the split
   *  segments when there are mids). Used to highlight (not add) the top proposals
   *  the path passes through, matched by undirected node-pair. */
  pathEdgeIds?: number[] | null;
  /** Drop handler for a drag started on an exploded on-route proposal. `edgeIdx`
   *  is the proposal's edge, `origin` its real point on the path (the dotted-line
   *  anchor), `drop` the snapped release point. The host routes by what the edge
   *  IS: a matched start/end/mid waypoint MOVES that waypoint to `drop`; an on-path
   *  proposal (not a waypoint) inserts a NEW mid at the segment `origin` sits on. */
  onProposalDrop?: (edgeIdx: number, origin: { lat: number; lng: number }, drop: { lat: number; lng: number }) => void;
  /** Fires with the edge the pinned modal would vote on (after override / sticky /
   *  deep-link reconciliation) whenever it resolves. The host pins it onto the
   *  selected point's `voteEdgeId` so the top-bar banner votes on EXACTLY the edge
   *  the modal does — a deep link or a click that lands near (not on) a proposal
   *  otherwise re-snaps geometrically in the banner and diverges from the card. */
  onPinnedResolve?: (voteEdgeId: number | null) => void;
}

export function GraphLayer({ onSnap, pinnedPoint, startPoint, endPoint, ghostWaypoints = EMPTY_WAYPOINTS, dragPoint = null, hoverProposalPoint = null, onWaypointMatch, onIndicatorClick, onRouteProposalClick, clusterExploderRef, onRemoveProposal, onRemoveSelected, onClearRoute, onRouteProposalRemove, routeProposalAtRef, suppressHover = false, pathEdgeIds = null, onProposalDrop, onPinnedResolve }: GraphLayerProps) {
  const map = useMap();
  const { subscribeToDelta } = useWebSocketContext();
  const { setSnapFn, setResolveVoteEdgeId, setResolveTopLabelForPath, setCurrentSnap, isDraggingRef: graphDraggingRef, snapToGraph, setDragging } = useGraphSnap();
  const { setBlockMaterializer, setCorridorSegmentResolver, notifyCorridorsChanged } = useRoute();
  // Ghost-pin drag (the dotted-trail + ghost-kite mechanism the path-drag uses).
  // Reused so dragging a NEW mid out of an exploded on-route proposal renders the
  // same ghost the path-drag does, and so the host's ghost→dragPoint mirror lights
  // cluster-explode + drop-target for it too.
  const { startDrag: startGhostDrag, updateDrag: updateGhostDrag, endDrag: endGhostDrag } = useGhostPin();
  const { setHeatmapLoaded, isHeatmapLoading } = useHeatmap();
  const theme = useTheme();
  const themeMode = theme.mode;
  // Station networks (e.g. ebikes): every graph point is a fixed station drawn as
  // a permanent icon, there is no routing, and cards omit the "Top Proposal"
  // header. Each station is a self-edge whose index equals its node index, so the
  // edge `name` (the intersection) is its label. See graph_registry.load_station_graph.
  const stationNetwork = getCurrentMap()?.network;
  const isStationNetwork = !!stationNetwork && stationNetwork !== "streets";
  // One shared icon for every station, like a pin. Prefer a point-type vote type
  // (stations are points), else the map's first vote type.
  const stationLabel =
    theme.suggestions.find((s) => s.pointType === "point")?.label
    ?? theme.suggestions[0]?.label ?? "";
  const mapStyle = mapStyleForTheme(theme);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  const hoverCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const hoverCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  const graphDataRef = useRef<GraphData | null>(null);
  const topologyRef = useRef<GraphTopology | null>(null);
  const edgeIndexRef = useRef<Flatbush | null>(null);
  const nodeIndexRef = useRef<Flatbush | null>(null);
  const redrawTimeoutRef = useRef<number | null>(null);
  // Timer backstop for the rAF redraw — see scheduleRedraw.
  const redrawFallbackRef = useRef<number | null>(null);
  const isZoomingRef = useRef(false);
  // The map state the heatmap bitmap was last painted at: the zoom, and the
  // lat/lng sitting at the canvas's top-left corner (container point 0,0). The
  // zoom-animation handler uses these to scale + translate the EXISTING bitmap to
  // the target zoom, so the heatmap rides Leaflet's zoom animation instead of
  // being cleared and repainted only at the end (which made it vanish mid-zoom).
  const drawStateRef = useRef<{ zoom: number; nw: L.LatLng } | null>(null);

  // Per-zoom projection cache for node layer-points. Leaflet layer-points are
  // stable across pans at a fixed zoom (panning only translates the map pane),
  // so we project each node at most once per zoom and reuse it across all pan
  // frames — turning every redraw into cheap "layerPoint + paneOffset" adds.
  // Keyed by zoom + pixelOrigin (origin only changes on zoom / view reset).
  // `done` marks which nodes have been projected so we project lazily, paying
  // only for nodes that actually enter the viewport.
  const projCacheRef = useRef<{
    zoom: number;
    ox: number;
    oy: number;
    xs: Float64Array;
    ys: Float64Array;
    done: Uint8Array;
  } | null>(null);

  // Memoized max edge-vote count, recomputed only when the vote revision
  // changes (i.e. on a vote/delta) rather than on every zoom/pan frame.
  const maxVotesRef = useRef(1);
  // True when this map has a block layer — blocks become the heat display
  // (MapLibre fill), so the canvas skips the per-edge heatmap (edges show only
  // on hover/selection).
  const blocksActiveRef = useRef(false);
  const maxVotesRevRef = useRef(-1);

  // Node adjacency list — node index → [edge indices]. Built once from topology,
  // used to derive node votes from edge totals (max of adjacent edges).
  const nodeAdjRef = useRef<NodeAdj | null>(null);

  // Block layer (docs/three-layer-model.md §2): block → edge-ids CSR index and
  // whether the loaded topology carries an edge→block mapping (GTB2 blob). Both
  // set alongside nodeAdj on topology load; null/false = singleton-block maps.
  const blockIndexRef = useRef<BlockIndex | null>(null);
  const hasBlocksRef = useRef(false);

  // Last-seen revision for gap detection
  const lastRevRef = useRef(0);

  // Deltas received before the initial vote fetch completes
  const pendingDeltasRef = useRef<import("../../types").VoteDelta[]>([]);

  // Ring of recently APPLIED deltas. A refetched /api/graph-votes body may be
  // slightly older than deltas we already applied (the server debounces
  // snapshot rebuilds under sustained voting), and installing it wholesale
  // would regress lastRev and the counts — making the very next delta look
  // like a gap and triggering a refetch loop. Replaying the ring's newer
  // deltas over the installed body heals both (deltas SET authoritative
  // counts, so replay is idempotent).
  const recentDeltasRef = useRef<import("../../types").VoteDelta[]>([]);
  const RECENT_DELTAS_MAX = 500;

  // Stable ref for onSnap callback
  const onSnapRef = useRef(onSnap);
  useEffect(() => { onSnapRef.current = onSnap; }, [onSnap]);

  // Unified selection resolver — the single source of truth for "what graph
  // component does this point map to". Used by hover, the pinned/selected
  // effect, the point-vote handler, and top-proposal indicators so the
  // highlighted component always equals the vote target. Hierarchy:
  //   1. overrideEdgeIdx (a top-proposal icon) dominates everything.
  //   2. the block polygon under the point constrains the search to that
  //      block's own members — a block is selectable from anywhere inside
  //      its polygon and never resolves to a neighbour's edges.
  //   3. no block under the point: unrestricted search.
  // Both searches are ONE uncapped hitTest: nearest edge with its ends
  // handed to their nodes (NODE_END_SHARE parity split), else nearest node —
  // so a selection always resolves and nodes are as reachable as edges
  // everywhere, not just inside a priority disc.
  // Pulls indices/data from refs so the closure stays stable across renders.
  const resolveSelection = useCallback((
    lat: number, lng: number, overrideEdgeIdx?: number | null
  ): ResolvedSelection | null => {
    const data = graphDataRef.current;
    if (!data?.nEdges) return null;

    // 1. Indicator override — select that edge directly.
    if (overrideEdgeIdx != null) {
      const snap = projectOntoEdge(data, overrideEdgeIdx, lat, lng);
      return {
        target: { kind: "edge", index: overrideEdgeIdx },
        snapLat: snap.lat, snapLng: snap.lng, voteEdgeId: overrideEdgeIdx,
      };
    }

    const pt = map.latLngToContainerPoint([lat, lng]);

    // 2. Block constraint: the polygon under the cursor defines the eligible
    // member set — a point inside a block resolves to THAT block's own
    // edges/nodes, so every block is selectable from anywhere in its polygon
    // and never steals a neighbour's members. No block under the cursor (or
    // MapLibre not ready) → unrestricted, as before.
    const ebi = data.edgeBlockId;
    const adj = nodeAdjRef.current;
    const filters = blockFiltersAt(data, adj, lat, lng);
    if (filters && ebi) {
      const { edgeInBlock, nodeInBlock } = filters;
      const hit = hitTest(
        data, map, pt.x, pt.y, lat, lng,
        edgeIndexRef.current, nodeIndexRef.current, edgeInBlock, nodeInBlock,
        Infinity
      );
      if (hit) {
        const voteEdgeId = hit.target.kind === "edge"
          ? hit.target.index
          : adjShortestInBlock(data, adj, hit.target.index, filters.blockId);
        return { target: hit.target, snapLat: hit.snapLat, snapLng: hit.snapLng, voteEdgeId };
      }
      // A block with no reachable members (stale bake) falls through to the
      // unrestricted path below.
    }

    // 3. Unrestricted, uncapped — always resolves when the graph has edges.
    const hit = hitTest(
      data, map, pt.x, pt.y, lat, lng,
      edgeIndexRef.current, nodeIndexRef.current, undefined, undefined,
      Infinity
    );
    if (hit) {
      const voteEdgeId = hit.target.kind === "edge"
        ? hit.target.index
        : adjShortest(data, nodeAdjRef.current, hit.target.index);
      return { target: hit.target, snapLat: hit.snapLat, snapLng: hit.snapLng, voteEdgeId };
    }

    return null;
  }, [map]);

  // Ref so stable effects (hover, point-vote listener) can call the resolver
  // without re-subscribing when it changes.
  const resolveSelectionRef = useRef(resolveSelection);
  useEffect(() => { resolveSelectionRef.current = resolveSelection; }, [resolveSelection]);

  // The last selection the waypoint snap resolved, keyed by the exact
  // coordinate it returned. When a placed point comes back to the pinned
  // effect carrying that coordinate, the recorded target is used verbatim —
  // the click pins precisely what its own resolution (= hover's) found,
  // never a re-derivation from the stored point.
  const lastSnapSelectionRef = useRef<ResolvedSelection | null>(null);

  // The shared hover/click/drag resolver — ONE function used by the hover
  // highlight, the click pin, the registered snapFn, the drop-preview
  // highlight, and the live-trail snap so they always agree. It IS the
  // selection resolver: ALWAYS resolves to the closest node/edge (block-
  // constrained when a block polygon is under the point), so there are no
  // dead zones — any hover or click maps to the nearest component and its
  // encompassing block, even off-polygon. (Previously off-polygon points
  // fell back to a 4px radius-bounded hit-test, which left unhoverable/
  // unclickable gaps wherever block coverage had holes.)
  const resolveDragSnap = useCallback((
    lat: number, lng: number
  ): ResolvedSelection | null => {
    return resolveSelectionRef.current(lat, lng);
  }, []);
  const resolveDragSnapRef = useRef(resolveDragSnap);
  useEffect(() => { resolveDragSnapRef.current = resolveDragSnap; }, [resolveDragSnap]);

  // Console/headless probe: cityedit.resolveAt(lat, lng) interrogates the live
  // resolver so a debugging session can see exactly what a point resolves to
  // (hovered block, target kind/index, vote edge) without synthetic mouse events.
  useEffect(() => {
    debugProbe("resolveAt", (lat: number, lng: number) => {
      const sel = resolveSelectionRef.current(lat, lng);
      const data = graphDataRef.current;
      // hoverTarget is what the cursor/click actually see (the gated drag-snap
      // resolver): null far from the graph, where a click pins nothing.
      // sticky (a top-proposal snap annulus) means a click LINKS to that
      // proposal instead of pinning — hover rings stickyEdgeIdx.
      const hover = resolveDragSnapRef.current(lat, lng);
      const sticky = stickyProposalSnapRef.current(lat, lng);
      return {
        hoveredBlock: data?.edgeBlockId ? blockIdAtLatLng(lat, lng) : null,
        target: sel?.target ?? null,
        hoverTarget: hover?.target ?? null,
        sticky: !!sticky,
        stickyEdgeIdx: sticky?.edgeIdx ?? null,
        snapLat: sel?.snapLat ?? null,
        snapLng: sel?.snapLng ?? null,
        voteEdgeId: sel?.voteEdgeId ?? null,
        voteEdgeBlock: sel && data?.edgeBlockId && sel.voteEdgeId != null
          ? data.edgeBlockId[sel.voteEdgeId] : null,
      };
    });
  }, []);

  // Register graph snap function for use by path drag. Resolves through the
  // shared drag-snap resolver and RECORDS the resolution — the pinned effect
  // reuses the recorded target when the point it receives is the coordinate
  // this snap returned, so the click selects exactly what hover showed.
  // Re-deriving a target from a stored coordinate is inherently leaky: the
  // snapped point can sit in a different polygon than the cursor, and pixel
  // radii shift with zoom.
  useEffect(() => {
    setSnapFn((_m: L.Map, lat: number, lng: number) => {
      // Proposals are sticky drop targets: a drop near one links to its midpoint.
      const sticky = stickyProposalSnapRef.current(lat, lng);
      if (sticky) return { lat: sticky.lat, lng: sticky.lng };
      const sel = resolveDragSnapRef.current(lat, lng);
      if (!sel) return null;
      lastSnapSelectionRef.current = sel;
      return { lat: sel.snapLat, lng: sel.snapLng };
    });
  }, [setSnapFn]);

  // Register the point→vote-edge resolver so the route/point cast (RouteContext)
  // resolves a click to the SAME edge the in-map hover/click would — one snap
  // path, no client/server divergence. resolveSelection always yields an edge.
  useEffect(() => {
    setResolveVoteEdgeId((lat: number, lng: number) => {
      return resolveSelectionRef.current(lat, lng)?.voteEdgeId ?? null;
    });
  }, [setResolveVoteEdgeId]);

  // Register the path→top-vote-type resolver: the highest net-voted label across a
  // set of edges. RouteContext uses it to pick a sensible default vote type when a
  // deep link's requested type isn't valid for the map. Reads the live vote data.
  useEffect(() => {
    setResolveTopLabelForPath((edgeIds: number[]) => {
      const data = graphDataRef.current;
      if (!data) return null;
      return topLabelForEdges(
        data.vote_type_legend ?? [],
        data.edge_vote_types ?? [],
        edgeIds
      );
    });
  }, [setResolveTopLabelForPath]);

  // Register the selection→blocks materializer so RouteContext's cast + pressed
  // state use the same block semantics as the in-map modal. The closure reads
  // the topology/index refs, so it's a singleton-[e] fallback until the graph
  // loads and on maps without block artifacts — identical to today's behavior.
  useEffect(() => {
    setBlockMaterializer((edgeIds: number[]) => {
      const topo = topologyRef.current;
      if (!topo) return edgeIds.map((e) => [e]);
      return materializeBlocks(topo, blockIndexRef.current, edgeIds);
    });
    return () => setBlockMaterializer(null);
  }, [setBlockMaterializer]);

  // Register the forced-segment→corridor resolver: a segment the selection FLAGS
  // as forced (SelWaypoint.forcedCorridor) routes through its proposal's corridor
  // VERBATIM — the stored path edges + geometry — instead of whatever OSRM picks
  // between the anchors. That's what keeps the rendered selection, the heat/hover
  // highlight, the block coverage (and thus the diamond's selected ring + card
  // header), and the vote target all tracing the proposal the user threaded.
  // Resolution order: the LIVE proposal by id (deep links carry only the id, and
  // live keeps the corridor current while the proposal exists) → the flag's
  // edge-id snapshot (survives proposal churn) → null (OSRM fallback). Reads
  // proposals and topology via refs, so one registration serves every recompute.
  useEffect(() => {
    setCorridorSegmentResolver((a, b, forced) => {
      const topo = topologyRef.current;
      if (!topo) return null;
      const p = routeProposalsRef.current.find((x) => x.id === forced.proposalId);
      if (p) {
        const coords = corridorCoordinates(topo, p);
        if (coords) {
          // Orient a→b: the walk starts at anchors[0]; reverse when `a` is the
          // other anchor (compare squared deltas — the anchors are far apart).
          const sq = (x: { lat: number; lng: number }, y: { lat: number; lng: number }) =>
            (x.lat - y.lat) ** 2 + (x.lng - y.lng) ** 2;
          const backward = sq(a, p.anchorCoords[1]) < sq(a, p.anchorCoords[0]);
          return {
            coordinates: backward ? [...coords].reverse() : coords,
            edgeIds: p.edgeIds,
          };
        }
      }
      // Proposal retired/reshaped (or not computed yet): rebuild from the
      // snapshot taken when the user threaded it.
      if (forced.edgeIds?.length) return corridorFromEdgeIds(topo, forced.edgeIds, a, b);
      return null;
    });
    return () => setCorridorSegmentResolver(null);
  }, [setCorridorSegmentResolver, map]);

  // Hover state
  const [hoverTarget, setHoverTarget] = useState<HoverTarget | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoverTargetRef = useRef<HoverTarget | null>(null);
  const hoverRafRef = useRef<number | null>(null);
  // The hovered RBTP diamond — drives the route-flavored hover card (the
  // diamond counterpart of the squares' hoverTarget card; shares tooltipPos).
  // Ref mirrors state so stable handlers/effects can clear it without a dep.
  const [hoverRbtp, setHoverRbtp] = useState<RouteProposal | null>(null);
  const hoverRbtpRef = useRef<RouteProposal | null>(null);
  // True while the cursor is over a top-proposal icon, so the map hover handler
  // yields the highlight to the icon (hierarchy rule #1).
  const overIndicatorRef = useRef(false);
  // True while the cursor is over the pinned/selected modal, so the map hover
  // handler doesn't fire (and surface other hover modals) underneath it.
  const overModalRef = useRef(false);
  // The pinned modal's DOM node. Its body is pointer-events:none (so the route
  // path stays grabbable through it), which means onMouseEnter can't fire over
  // the body — so the hover handler hit-tests the cursor against this rect.
  const pinnedModalElRef = useRef<HTMLDivElement | null>(null);
  // Mirrors the `suppressHover` prop (cursor over the route path) for the
  // stable mousemove handler to read without re-subscribing.
  const suppressHoverRef = useRef(suppressHover);
  useEffect(() => { suppressHoverRef.current = suppressHover; }, [suppressHover]);

  // Pinned tooltip screen position (follows start pin on map pan/zoom)
  const [pinnedScreenPos, setPinnedScreenPos] = useState<{ x: number; y: number } | null>(null);
  const pinnedRafRef = useRef<number | null>(null);
  // Route-summary card screen position (anchored to the route's middle edge,
  // follows pan/zoom the same way). Null when no route selection exists.
  const [routeCardPos, setRouteCardPos] = useState<{ x: number; y: number } | null>(null);
  const routeCardRafRef = useRef<number | null>(null);
  // Pinned target (node or edge) for highlight and tooltip
  const pinnedTargetRef = useRef<HoverTarget | null>(null);
  // The graph node/edge a live waypoint drag would snap to — drawn on the hover
  // canvas as a drop preview so you can see exactly which edge you'll land on
  // before releasing. Derived from `dragPoint` by mirroring the committed snapFn.
  const dragSnapTargetRef = useRef<HoverTarget | null>(null);
  // When an indicator is clicked, store the exact edge index so the
  // pinnedPoint effect uses it directly instead of re-running hitTest
  // (which can snap to a neighboring edge).
  const pinnedEdgeOverrideRef = useRef<number | null>(null);
  // Locks the resolved PROPOSAL edge to the current pinned coords. The resolve
  // effect re-runs on every vote (votesVersion) and when winners arrive, but a
  // point that has settled on a proposal must keep that exact edge — a vote can
  // reshuffle the spaced `winners` list so geometric reconciliation drifts to a
  // neighbour. Reset when the point moves (new coords) or an override repins.
  const pinnedLockRef = useRef<{ lat: number; lng: number; edgeIdx: number } | null>(null);

  // Increments when a geocode resolves, forcing tooltip re-render
  const [geocodeVersion, setGeocodeVersion] = useState(0);
  const bumpGeocode = useCallback(() => setGeocodeVersion((v) => v + 1), []);

  // Increments when graph vote data mutates (refreshHeatmapDisplay), forcing
  // tooltip re-render. The value is read so the vote-type decode memos re-run
  // whenever the underlying vote arrays change — avoiding a stale winner
  // indexing into emptied vote data ("no votes yet" on a top proposal).
  const [graphVoteVersion, setGraphVoteVersion] = useState(0);
  void graphVoteVersion; // read so decode memos/winners re-render in lockstep

  // Increments on any vote change (this map's proposal modal, the top-bar
  // banner, or server reconciliation) so the pinned-selection resolve effect
  // re-runs and the modal re-evaluates +/- state. The store is the single
  // signal — see useVotesVersion.
  const votesVersion = useVotesVersion();

  // Vote-type indicator markers — top-voted segment per vote type
  const [winners, setWinners] = useState<VoteTypeWinner[]>([]);
  // Server-computed ROUTE proposals (corridors). Fetched in parallel with the
  // vote heatmap and refetched on the same vote-delta signal (the endpoint is
  // revision-cached, so a refetch on each vote is cheap / 304s when unchanged).
  const [routeProposals, setRouteProposals] = useState<RouteProposal[]>([]);
  // The RBTP the user explicitly tapped (see the terminology note above
  // routeIndicatorMarkers). Tapping a diamond force-routes through its anchors,
  // but the OSRM leg between them rarely re-traces the vote corridor exactly —
  // so the block-coverage rule alone almost never marks the tapped proposal
  // selected. The tapped id keeps it selected for as long as BOTH its anchors
  // remain waypoints of the current route (anchorsAreWaypoints below); editing
  // an anchor away or clearing the route deselects it. Coverage remains the
  // OTHER route to selected (hand-tracing a corridor on a block map).
  const [selectedRbtpId, setSelectedRbtpId] = useState<string | null>(null);
  // Mirror for synchronous reads in delegated/hot-path handlers (the [×] badge
  // capture click, the diamond drop hit-test).
  const routeProposalsRef = useRef<RouteProposal[]>(routeProposals);
  useEffect(() => { routeProposalsRef.current = routeProposals; }, [routeProposals]);
  // Poke RouteContext when the live proposal set changes: a forced segment that
  // couldn't resolve earlier (deep link restored before proposals computed, or
  // the proposal churned away and back) recalcs once and snaps onto its corridor.
  useEffect(() => {
    if (routeProposals.length > 0) notifyCorridorsChanged();
  }, [routeProposals, notifyCorridorsChanged]);
  // The edge set a hovered route diamond highlights — all of its blocks' edges.
  const routeHighlightEdgesRef = useRef<number[] | null>(null);
  const [currentZoom, setCurrentZoom] = useState<number>(() => map.getZoom());

  // ── Block highlight (docs §2.4) ────────────────────────────────────────────
  // Selecting or hovering any node/edge/proposal lights the covering block
  // polygons: broadcast the touched REAL block ids to the MapLibre block-select
  // layer. The per-edge highlight canvas stays as the anchor-edge emphasis.
  // Selection = the route's path edges when a route exists, else the pinned
  // target's vote edge; hover (edge/node/route diamond) rides on top,
  // transiently. Maps without blocks broadcast an empty set.
  const pathEdgeIdsRef = useRef(pathEdgeIds);
  useEffect(() => { pathEdgeIdsRef.current = pathEdgeIds; }, [pathEdgeIds]);
  const lastBlockSelectKeyRef = useRef<string | null>(null);
  const dispatchBlockSelect = useCallback(() => {
    const topo = topologyRef.current;
    if (!topo) return;
    let blockIds: number[] = [];
    if (hasBlocksRef.current) {
      const edgeIds: number[] = [];
      const pushTarget = (t: HoverTarget | null) => {
        if (!t) return;
        const e = t.kind === "edge" ? t.index : adjShortest(topo, nodeAdjRef.current, t.index);
        if (e != null) edgeIds.push(e);
      };
      const path = pathEdgeIdsRef.current;
      if (path && path.length > 0) {
        for (const e of path) edgeIds.push(e);
      } else {
        pushTarget(pinnedTargetRef.current);
      }
      pushTarget(hoverTargetRef.current);
      const routeEdges = routeHighlightEdgesRef.current;
      if (routeEdges) for (const e of routeEdges) edgeIds.push(e);
      blockIds = touchedBlockKeys(topo, edgeIds).filter((k) => k >= 0);
    }
    const key = blockIds.join(",");
    if (lastBlockSelectKeyRef.current === key) return;
    lastBlockSelectKeyRef.current = key;
    const detail: BlockSelectDetail = { blockIds };
    window.dispatchEvent(new CustomEvent(BLOCK_SELECT_EVENT, { detail }));
  }, []);
  const dispatchBlockSelectRef = useRef(dispatchBlockSelect);
  useEffect(() => { dispatchBlockSelectRef.current = dispatchBlockSelect; }, [dispatchBlockSelect]);
  const iconCacheRef = useRef<Map<string, L.DivIcon>>(new Map());

  // "Fan out crowded icons into a grid" state. Only ONE cluster is ever fanned
  // out at a time — exploding a new cluster replaces (collapses) whatever was
  // open. The map keys a proposal's spread key (spreadKeyEdge for a PBTP square,
  // spreadKeyRoute for an RBTP diamond — both kinds fan out together) ->
  // overridden [lat, lng]; the render reads it as a per-icon position override.
  // (Edge, not legendIdx, for points: a vote type can win on several edges —
  // top-N-per-type — so legendIdx is no longer unique per icon.)
  //
  // A spread starts TRANSIENT — a hover/snap-back timer collapses it. Picking one
  // of its boxes LOCKS it (`spreadLockedRef`), so it persists with no timer until
  // the selection clears or the map pans/zooms. Locked vs transient is the ONLY
  // difference between the two; the position map is identical, so the lock is just
  // a ref flag. Refs mirror the state for synchronous reads in handlers.
  type SpreadMap = Map<string, [number, number]>;
  const [spread, setSpread] = useState<SpreadMap | null>(null);
  const spreadRef = useRef<SpreadMap | null>(null);
  const spreadLockedRef = useRef(false);
  const spreadTimeoutRef = useRef<number | null>(null);
  // collapseSpread is defined far below; this ref lets earlier effects call it.
  const collapseSpreadRef = useRef<() => void>(() => {});
  // The cluster exploder (assigned by the clusterEngine memo below, for BOTH
  // proposal kinds), kept on an internal ref too (the prop ref is optional) so
  // the route-diamond markers — built in a separate memo — can run the same
  // "crowded stack fans out before any side effect" rule the point pins use.
  const internalExploderRef = useRef<((latlng: { lat: number; lng: number }) => boolean) | null>(null);

  // Set (or clear, with null) the open spread, mirroring it into the ref the
  // hot-path hit-test reads — proposalIconAt runs on every drag mousemove and
  // needs each icon's CURRENT display position: its fanned-out cell when spread,
  // its real midpoint otherwise.
  const applySpread = useCallback((next: SpreadMap | null) => {
    spreadRef.current = next;
    setSpread(next);
  }, []);

  // Random tiebreak salt — stable for this page load, different next time.
  // Used so that equal-count proposals don't always favor the same labels.
  const tiebreakSaltRef = useRef<number>(Date.now());

  // Only update winners state when the list actually changes (avoids
  // re-mounting indicator markers on every vote poll). `force` bypasses the
  // skip when the legend grew, so a winner can't keep pointing at a legend
  // index the card would decode differently.
  const winnersRef = useRef(winners);
  const lastLegendLenRef = useRef(0);
  const setStableWinners = useCallback((next: VoteTypeWinner[], force = false) => {
    const prev = winnersRef.current;
    if (
      !force &&
      prev.length === next.length &&
      prev.every((w, i) => w.edgeIdx === next[i].edgeIdx && w.count === next[i].count && w.legendIdx === next[i].legendIdx)
    ) return;
    winnersRef.current = next;
    setWinners(next);
  }, []);

  // Label → route/point kind, resolved against the active map's vote types and
  // its search-only custom types (see pointTypeForLabel). Splits the proposal
  // families: PBTPs exclude route-kind labels, RBTPs exclude point-kind ones.
  // Station networks vote only on fixed points, so every kind is admitted there
  // (no resolver — same rule as mapVoteTypesForPointType).
  const voteTypeKindOf = useCallback((label: string) => {
    const cfg = getCurrentMap();
    return pointTypeForLabel(label, cfg?.voteTypes, cfg?.searchVoteTypes);
  }, []);

  // Cheap per-vote display refresh: repaint the heatmap and bump the version
  // the vote-count readouts key off. Deliberately does NOT touch the top
  // proposals — recomputing those walks the full edge table (seconds on the
  // NYC bike graph), so votes only mark them dirty and the batched sweep
  // (requestProposalsRecompute / PROPOSALS_REFRESH_INTERVAL_MS) catches up.
  const refreshHeatmapDisplay = useCallback(() => {
    if (!graphDataRef.current) return;
    setGraphVoteVersion((v) => v + 1);
    scheduleRedrawRef.current();
  }, []);

  const refreshHeatmapDisplayRef = useRef(refreshHeatmapDisplay);
  useEffect(() => { refreshHeatmapDisplayRef.current = refreshHeatmapDisplay; }, [refreshHeatmapDisplay]);

  // PBTP winners recompute — the full edge-table scan. Only called from the
  // batched recompute path (never per vote).
  const recomputeTopProposals = useCallback(() => {
    const data = graphDataRef.current;
    if (!data) return;
    const legendLen = data.vote_type_legend?.length ?? 0;
    const legendChanged = legendLen !== lastLegendLenRef.current;
    lastLegendLenRef.current = legendLen;
    setStableWinners(selectTopProposals(
      data, tiebreakSaltRef.current, TOP_PROPOSAL_LIMIT,
      TOP_PROPOSAL_MIN_SPACING_M,
      isStationNetwork ? undefined : voteTypeKindOf,
    ), legendChanged);
  }, [setStableWinners, isStationNetwork, voteTypeKindOf]);

  const recomputeTopProposalsRef = useRef(recomputeTopProposals);
  useEffect(() => { recomputeTopProposalsRef.current = recomputeTopProposals; }, [recomputeTopProposals]);

  // Stable ref for the indicator-click callback (avoids re-running the
  // useMemo that builds marker components every time the parent re-renders).
  const onRouteProposalClickRef = useRef(onRouteProposalClick);
  useEffect(() => { onRouteProposalClickRef.current = onRouteProposalClick; }, [onRouteProposalClick]);

  const onIndicatorClickRef = useRef(onIndicatorClick);
  useEffect(() => { onIndicatorClickRef.current = onIndicatorClick; }, [onIndicatorClick]);

  const onRemoveSelectedRef = useRef(onRemoveSelected);
  useEffect(() => { onRemoveSelectedRef.current = onRemoveSelected; }, [onRemoveSelected]);

  const onRemoveProposalRef = useRef(onRemoveProposal);
  useEffect(() => { onRemoveProposalRef.current = onRemoveProposal; }, [onRemoveProposal]);

  const onClearRouteRef = useRef(onClearRoute);
  useEffect(() => { onClearRouteRef.current = onClearRoute; }, [onClearRoute]);

  const onRouteProposalRemoveRef = useRef(onRouteProposalRemove);
  useEffect(() => { onRouteProposalRemoveRef.current = onRouteProposalRemove; }, [onRouteProposalRemove]);

  // One delegated handler for every proposal's [×] badge, in CAPTURE phase on the
  // map container so it pre-empts both Leaflet's map click and the indicator
  // marker's own click (which would otherwise also restart/select). The badge is
  // pointer-events:auto inside an icon that may be pointer-events:none, so without
  // this stop the clickthrough trap (see modal_clickthrough memory) would fire the
  // map click. data-x-edge (a matched waypoint's proposal) / data-x-route (a
  // selected RBTP corridor) tell us what to drop.
  useEffect(() => {
    const container = map.getContainer();
    const onCaptureClick = (e: MouseEvent) => {
      const badge = (e.target as HTMLElement | null)?.closest?.(".vote-type-indicator-x");
      if (!badge) return;
      e.stopPropagation();
      e.preventDefault();
      const routeId = badge.getAttribute("data-x-route");
      if (routeId) {
        const p = routeProposalsRef.current.find((rp) => rp.id === routeId);
        if (p) onRouteProposalRemoveRef.current?.(p);
        return;
      }
      const edge = Number(badge.getAttribute("data-x-edge"));
      if (Number.isFinite(edge)) onRemoveProposalRef.current?.(edge);
    };
    container.addEventListener("click", onCaptureClick, true);
    return () => container.removeEventListener("click", onCaptureClick, true);
  }, [map]);

  // A real route (start AND end) on a street map. Gates the proposal [×] remove
  // badge — a lone point (no end) keeps tap-to-delete on its kite, and station
  // maps select via the indicator itself, so neither shows a badge.
  const isRouteMode = !!startPoint && !!endPoint && !isStationNetwork;

  // Nearest "top proposal" edge to a point, by edge-midpoint distance, within a
  // threshold (meters). The candidate set is what actually renders an indicator:
  // every station self-edge on a station network (all stations ARE top proposals),
  // otherwise the vote winners. Used for deep-link reconciliation (a shared point
  // is a proposal's midpoint) and for matching a start/end waypoint to a proposal.
  const nearestProposalEdgeIndex = useCallback((
    lat: number, lng: number, thresholdM: number
  ): number | null => {
    const g = graphDataRef.current;
    if (!g) return null;
    const candidates = isStationNetwork
      ? Array.from({ length: g.nEdges }, (_, i) => i)
      : winners.map((w) => w.edgeIdx);
    let best: number | null = null;
    let bestDist = Infinity;
    for (const edgeIdx of candidates) {
      if (edgeIdx >= g.nEdges) continue;
      const fromIdx = edgeFrom(g, edgeIdx), toIdx = edgeTo(g, edgeIdx);
      const midLat = (nodeLat(g, fromIdx) + nodeLat(g, toIdx)) / 2;
      const midLng = (nodeLon(g, fromIdx) + nodeLon(g, toIdx)) / 2;
      const dist = haversineMeters(lat, lng, midLat, midLng);
      if (dist < bestDist) { bestDist = dist; best = edgeIdx; }
    }
    return best !== null && bestDist <= thresholdM ? best : null;
  }, [winners, isStationNetwork]);

  // The incident edge that owns a node's STRONGEST proposal (the max net across
  // all adjacent edges and vote types), or null if the intersection has no
  // proposals. A node modal MERGES its incident edges' proposals for display
  // (the server derives node_vote_types as the per-type max over adjacencies —
  // see rederiveNodes), but a vote can only land on ONE edge. Voting from a node
  // modal therefore hit an arbitrary incident edge (nodeAdj[0]) and the view
  // then snapped onto it. The pinned resolver upgrades a node to this edge so the
  // selection is edge-based from the start — votable, optimistic-updatable, and
  // stable on a vote. The strongest proposal is exactly node_vote_types[0], the
  // label a node's share link encodes as `vt`, so the upgrade honours the link.
  const strongestProposalEdgeForNode = useCallback((nodeIdx: number): number | null => {
    const g = graphDataRef.current;
    const adjList = nodeAdjRef.current;
    if (!g?.edge_vote_types || !adjList) return null;
    const adj = adjEdgesOf(adjList, nodeIdx);
    let bestEdge: number | null = null;
    let bestNet = 0; // net ≤ 0 is not a proposal, so only positive nets qualify
    for (const eid of adj) {
      const pairs = g.edge_vote_types[eid];
      if (!pairs) continue;
      for (const [, up, down] of pairs) {
        const net = up - down;
        if (net > bestNet) { bestNet = net; bestEdge = eid; }
      }
    }
    return bestEdge;
  }, []);

  // Match the start/end waypoints to a coincident proposal so its icon can be
  // tinted (teal start / red end) and the host can hide the generic marker.
  // On a point/station network EVERY point IS a station, and the selection
  // always snaps to the nearest one, so match with no distance limit (Infinity)
  // — that way the redundant generic pin disappears even when the point was set
  // by clicking the map a few metres off the station. On street maps a tight 5m
  // limit keeps it to true coincidences (a proposal click sets the exact edge
  // midpoint), not arbitrary nearby clicks.
  const matchThresholdM = isStationNetwork ? Infinity : 5;

  // The matched proposal (edge + label) at a point, or null. The label (the
  // winning vote type, or the shared station label) builds the pin's glyph.
  const proposalMatchFor = useCallback((
    lat: number, lng: number, thresholdM: number
  ): ProposalMatch | null => {
    const edgeIdx = nearestProposalEdgeIndex(lat, lng, thresholdM);
    if (edgeIdx === null) return null;
    const label = isStationNetwork
      ? stationLabel
      : (winners.find((w) => w.edgeIdx === edgeIdx)?.label ?? "");
    return { edgeIdx, label };
  }, [nearestProposalEdgeIndex, isStationNetwork, stationLabel, winners]);

  const onWaypointMatchRef = useRef(onWaypointMatch);
  useEffect(() => { onWaypointMatchRef.current = onWaypointMatch; }, [onWaypointMatch]);
  const [startEdgeIdx, setStartEdgeIdx] = useState<number | null>(null);
  const [endEdgeIdx, setEndEdgeIdx] = useState<number | null>(null);
  const [midEdgeIdxs, setMidEdgeIdxs] = useState<(number | null)[]>([]);
  // Top proposals the current route merely PASSES THROUGH (not waypoints). They
  // render selected (highlight only) and stay click-through so the path under
  // them is still draggable — dragging there spawns a real mid. Recomputed
  // reactively from winners + the path, so a vote that promotes/demotes a
  // proposal adds/removes its highlight.
  const [onPathEdgeSet, setOnPathEdgeSet] = useState<Set<number>>(() => new Set());
  const startLat = startPoint?.lat ?? null;
  const startLng = startPoint?.lng ?? null;
  const endLat = endPoint?.lat ?? null;
  const endLng = endPoint?.lng ?? null;
  // Read mids via a ref + a string key so the effect re-runs on coord changes
  // without depending on the array identity (which churns each render).
  const ghostWaypointsRef = useRef(ghostWaypoints);
  ghostWaypointsRef.current = ghostWaypoints;
  const ghostKey = ghostWaypoints.map((w) => `${w.lat},${w.lng}`).join(";");
  useEffect(() => {
    const s = startLat !== null && startLng !== null
      ? proposalMatchFor(startLat, startLng, matchThresholdM) : null;
    const e = endLat !== null && endLng !== null
      ? proposalMatchFor(endLat, endLng, matchThresholdM) : null;
    const mids = ghostWaypointsRef.current.map(
      (w) => proposalMatchFor(w.lat, w.lng, matchThresholdM)
    );
    setStartEdgeIdx(s?.edgeIdx ?? null);
    setEndEdgeIdx(e?.edgeIdx ?? null);
    setMidEdgeIdxs(mids.map((m) => m?.edgeIdx ?? null));
    onWaypointMatchRef.current?.({ start: s, end: e, mids });
    // isHeatmapLoading re-runs this once the graph/votes arrive, so a cold
    // deep-link (waypoint set before the candidates exist) still resolves.
  }, [startLat, startLng, endLat, endLng, ghostKey, proposalMatchFor, matchThresholdM, isHeatmapLoading]);

  // Edges that host a mid waypoint (start/end are tracked separately). A linked
  // proposal indicator stays visible as the FIXED pin for its waypoint — tinted
  // by role and made click-through (the kite RouteMarker underneath handles the
  // drag/click). So we don't suppress these; we tint + passthrough them.
  const midEdgeSet = useMemo(() => {
    const set = new Set<number>();
    for (const m of midEdgeIdxs) if (m !== null) set.add(m);
    return set;
  }, [midEdgeIdxs]);

  // Does the current route still run its corridor leg through this RBTP's
  // anchors — i.e. are both anchors waypoints AND consecutive ones (no mid
  // between them)? Governs how long an explicitly-tapped diamond stays
  // selected: the tap inserted these exact anchor coords as waypoints, so a
  // tight meters match (they only move if the user edits them) is the right
  // "still my selection" test — not path-edge coverage, which OSRM's routing
  // between the anchors rarely satisfies. Adjacency matters because inserting
  // a mid BETWEEN the anchors un-forces the corridor segment (reducer
  // insertMid → clearForcedAt): the leg reverts to OSRM and stops selecting
  // the corridor's blocks, so the proposal must read deselected too.
  const anchorsAreWaypoints = useCallback((p: RouteProposal): boolean => {
    const wps: { lat: number; lng: number }[] = [];
    if (startLat !== null && startLng !== null) wps.push({ lat: startLat, lng: startLng });
    wps.push(...ghostWaypointsRef.current);
    if (endLat !== null && endLng !== null) wps.push({ lat: endLat, lng: endLng });
    if (wps.length < 2) return false;
    // Route-ordered list (start, mids…, end): each anchor must match a
    // waypoint, and the two matches must be neighbors.
    const idx = p.anchorCoords.map((a) =>
      wps.findIndex((w) => map.distance([a.lat, a.lng], [w.lat, w.lng]) < 5));
    return idx[0] >= 0 && idx[1] >= 0 && Math.abs(idx[0] - idx[1]) === 1;
    // ghostKey stands in for the mids (read via ref; the array identity churns).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startLat, startLng, endLat, endLng, ghostKey, map]);

  // Viewport-space boxes of the current waypoint markers (start/end kites +
  // mid pins), for the floating cards to dodge — a modal parked on top of a
  // waypoint hides the very thing the selection is about. Projected fresh at
  // call time: the cards invoke this from their positioning layout effect,
  // which re-runs whenever the anchor moves (pan/zoom updates screenX/Y) or a
  // waypoint changes (this callback's identity is a dep). Box is the union of
  // the kite (26×38) and mid-pin (30×40) geometry, both anchored at the tip,
  // with a little breathing room.
  const getWaypointAvoidRects = useCallback((): AvoidRect[] => {
    const rect = map.getContainer().getBoundingClientRect();
    const rects: AvoidRect[] = [];
    const push = (lat: number, lng: number) => {
      const pt = map.latLngToContainerPoint([lat, lng]);
      const x = rect.left + pt.x, y = rect.top + pt.y;
      rects.push({ left: x - 17, top: y - 42, right: x + 17, bottom: y + 4 });
    };
    if (startLat !== null && startLng !== null) push(startLat, startLng);
    if (endLat !== null && endLng !== null) push(endLat, endLng);
    for (const wp of ghostWaypointsRef.current) push(wp.lat, wp.lng);
    return rects;
    // ghostKey stands in for the mids (read via ref; the array identity churns).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startLat, startLng, endLat, endLng, ghostKey, map]);

  // The hover card additionally dodges the OPEN modal (pinned point card or
  // route-summary card — whichever registered pinnedModalElRef), so browsing
  // proposals never papers over the selection you're working with. When every
  // quadrant collides it still pops OVER the modal (z-index 1400 beats the
  // route card's 1300) rather than hiding under it.
  const getHoverAvoidRects = useCallback((): AvoidRect[] => {
    const rects = getWaypointAvoidRects();
    const modalEl = pinnedModalElRef.current;
    if (modalEl) {
      const r = modalEl.getBoundingClientRect();
      rects.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
    }
    return rects;
  }, [getWaypointAvoidRects]);

  // Highlight (but do NOT add as waypoints) the top proposals the current route
  // PASSES THROUGH. Matched by UNDIRECTED node-pair, not raw edge index: a two-way
  // street stores each direction as its own edge, and the route often traverses
  // the twin of the vote winner's edge (an exact edge-id intersection misses it).
  // Reactive to winners + the path, so a vote promoting/demoting a proposal
  // adds/removes its highlight. Waypoint proposals (start/end/mid) are excluded —
  // those are styled by `role`. These stay click-through so the path beneath is
  // still draggable (dragging there spawns a real mid).
  useEffect(() => {
    const clear = () => setOnPathEdgeSet((prev) => (prev.size ? new Set() : prev));
    if (isStationNetwork || isHeatmapLoading) { clear(); return; }
    if (startLat === null || endLat === null) { clear(); return; }
    if (!pathEdgeIds || pathEdgeIds.length === 0 || winners.length === 0) { clear(); return; }
    const g = graphDataRef.current;
    if (!g) { clear(); return; }

    const pairKey = (a: number, b: number) => (a < b ? `${a}|${b}` : `${b}|${a}`);
    const pathPairs = new Set<string>();
    for (const ei of pathEdgeIds) {
      if (ei < g.nEdges) pathPairs.add(pairKey(edgeFrom(g, ei), edgeTo(g, ei)));
    }
    const next = new Set<number>();
    for (const w of winners) {
      // Skip proposals that ARE waypoints — `role` styles those.
      if (w.edgeIdx === startEdgeIdx || w.edgeIdx === endEdgeIdx || midEdgeSet.has(w.edgeIdx)) continue;
      if (w.edgeIdx >= g.nEdges) continue;
      if (pathPairs.has(pairKey(edgeFrom(g, w.edgeIdx), edgeTo(g, w.edgeIdx)))) next.add(w.edgeIdx);
    }
    setOnPathEdgeSet((prev) => {
      if (prev.size === next.size && [...next].every((x) => prev.has(x))) return prev;
      return next;
    });
  }, [startLat, startLng, endLat, endLng, pathEdgeIds, winners, startEdgeIdx, endEdgeIdx, midEdgeSet, isStationNetwork, isHeatmapLoading]);

  // Pixel hit-test: is a screen point over a top-proposal ICON? Tests the icon's
  // on-screen box (tip at the DISPLAY position, body above). Pixel-based (not
  // meters) so dropping/hovering ON the icon body links regardless of zoom — a
  // small meters threshold missed the icon at most zooms. Street maps only.
  //
  // The box is tested at the icon's DISPLAY position — its fanned-out cell when
  // the cluster is spread, its real midpoint otherwise — so a drag snaps onto an
  // EXPLODED icon exactly where it's drawn (no separate code path for spread
  // icons). The returned lat/lng is always the REAL edge midpoint, so the dropped
  // waypoint links to the proposal's edge, not its temporary grid cell.
  const ICON_HALF_W = 21, ICON_TOP_PX = 40, ICON_BOTTOM_PX = 10;
  const proposalIconAt = useCallback((pt: L.Point): { edgeIdx: number; lat: number; lng: number } | null => {
    if (isStationNetwork) return null;
    const g = graphDataRef.current;
    if (!g) return null;
    const spread = spreadRef.current;
    let best: { edgeIdx: number; lat: number; lng: number } | null = null;
    let bestD = Infinity;
    for (const w of winnersRef.current) {
      if (w.edgeIdx >= g.nEdges) continue;
      const fromIdx = edgeFrom(g, w.edgeIdx), toIdx = edgeTo(g, w.edgeIdx);
      const lat = (nodeLat(g, fromIdx) + nodeLat(g, toIdx)) / 2;
      const lng = (nodeLon(g, fromIdx) + nodeLon(g, toIdx)) / 2;
      // Hit-test at where the icon is actually drawn; snap to the real midpoint.
      const override = spread?.get(spreadKeyEdge(w.edgeIdx));
      const mp = map.latLngToContainerPoint(override ? [override[0], override[1]] : [lat, lng]);
      if (pt.x < mp.x - ICON_HALF_W || pt.x > mp.x + ICON_HALF_W
          || pt.y < mp.y - ICON_TOP_PX || pt.y > mp.y + ICON_BOTTOM_PX) continue;
      const dx = pt.x - mp.x, dy = pt.y - (mp.y - 18);
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = { edgeIdx: w.edgeIdx, lat, lng }; }
    }
    return best;
  }, [isStationNetwork, map]);

  // Same pixel hit-test for ROUTE-proposal diamonds: the RBTP whose icon box
  // (at its DISPLAY position — fanned grid cell when spread, settled diamond
  // spot otherwise) contains the point. Backs the diamond sticky snap, the
  // drop-target ring, and the host's drop-on-diamond resolver.
  const routeProposalIconAt = useCallback((pt: L.Point): RouteProposal | null => {
    if (isStationNetwork) return null;
    const topo = topologyRef.current;
    if (!topo) return null;
    const spread = spreadRef.current;
    let best: RouteProposal | null = null;
    let bestD = Infinity;
    for (const p of routeProposalsRef.current) {
      const override = spread?.get(spreadKeyRoute(p.id));
      const [lat, lng] = override ?? rbtpDisplayPos(topo, p);
      const mp = map.latLngToContainerPoint([lat, lng]);
      if (pt.x < mp.x - ICON_HALF_W || pt.x > mp.x + ICON_HALF_W
          || pt.y < mp.y - ICON_TOP_PX || pt.y > mp.y + ICON_BOTTOM_PX) continue;
      const dx = pt.x - mp.x, dy = pt.y - (mp.y - 18);
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = p; }
    }
    return best;
  }, [isStationNetwork, map]);
  const routeProposalIconAtRef = useRef(routeProposalIconAt);
  useEffect(() => { routeProposalIconAtRef.current = routeProposalIconAt; }, [routeProposalIconAt]);

  // Publish the drop-on-diamond resolver for the host (MapView): dropping a
  // dragged mid / path-ghost onto a diamond threads the route through the whole
  // corridor. Resolving a hit also SELECTS that RBTP — exactly what the
  // diamond's own click does — so once the host inserts the anchors as
  // waypoints, the diamond (and the route card header) read selected.
  useEffect(() => {
    if (!routeProposalAtRef) return;
    routeProposalAtRef.current = (latlng) => {
      const p = routeProposalIconAtRef.current(
        map.latLngToContainerPoint([latlng.lat, latlng.lng]));
      if (p) setSelectedRbtpId(p.id);
      return p;
    };
    return () => { routeProposalAtRef.current = null; };
  }, [routeProposalAtRef, map]);

  // Sticky snap: a drag on/near a proposal icon snaps to its exact midpoint so
  // the waypoint links cleanly. Consulted by the registered snapFn (the committed
  // drop) and the mousemove drag branch (the live trail). Route-proposal
  // diamonds are sticky the same way (point pins win where the two overlap), so
  // a drag hovering a diamond glues the ghost to it before the corridor drop.
  const stickyProposalSnap = useCallback(
    (lat: number, lng: number) => {
      const pt = map.latLngToContainerPoint([lat, lng]);
      const point = proposalIconAt(pt);
      if (point) return point;
      const topo = topologyRef.current;
      const rbtp = topo ? routeProposalIconAt(pt) : null;
      if (rbtp && topo) {
        const [rLat, rLng] = rbtpDisplayPos(topo, rbtp);
        return { edgeIdx: null, lat: rLat, lng: rLng };
      }
      return null;
    },
    [proposalIconAt, routeProposalIconAt, map]
  );
  const stickyProposalSnapRef = useRef(stickyProposalSnap);
  useEffect(() => { stickyProposalSnapRef.current = stickyProposalSnap; }, [stickyProposalSnap]);

  // The proposal a current waypoint drag would link to → its white/black
  // drop-target ring. Derived from the host-reported live drag position
  // (`dragPoint`), which the marker emits on its `drag` event on BOTH desktop and
  // touch — so the affordance works on mobile (a mousemove source would not).
  const dropTargetEdgeIdx = useMemo(
    () => (dragPoint ? proposalIconAt(map.latLngToContainerPoint([dragPoint.lat, dragPoint.lng]))?.edgeIdx ?? null : null),
    [dragPoint, proposalIconAt, map]
  );

  // The RBTP diamond a live drag hovers — its drop-target ring (the diamond
  // analogue of dropTargetEdgeIdx). Dropping there threads the corridor.
  const dropTargetRbtpId = useMemo(
    () => (dragPoint ? routeProposalIconAt(map.latLngToContainerPoint([dragPoint.lat, dragPoint.lng]))?.id ?? null : null),
    [dragPoint, routeProposalIconAt, map]
  );

  // Light the graph edge (or node) a live waypoint drag would snap to — the drop
  // preview. Mirrors the registered snapFn exactly (sticky proposal first, then
  // the shared resolver, which always resolves to the closest component) so the
  // highlight matches where the waypoint actually lands on release.
  useEffect(() => {
    let next: HoverTarget | null = null;
    if (dragPoint) {
      const data = graphDataRef.current;
      if (data) {
        const sticky = stickyProposalSnapRef.current(dragPoint.lat, dragPoint.lng);
        if (sticky) {
          // A diamond sticky (edgeIdx null) has no single edge to ring — the
          // diamond's own drop-target ring (dropTargetRbtpId) is the affordance.
          next = sticky.edgeIdx != null ? { kind: "edge", index: sticky.edgeIdx } : null;
        } else {
          next = resolveDragSnapRef.current(dragPoint.lat, dragPoint.lng)?.target ?? null;
        }
      }
    }
    const prev = dragSnapTargetRef.current;
    const same = (!prev && !next)
      || (!!prev && !!next && prev.kind === next.kind && prev.index === next.index);
    if (!same) {
      dragSnapTargetRef.current = next;
      redrawHoverHighlightRef.current();
    }
  }, [dragPoint, map]);

  // A waypoint drag whose live position hovers over a crowded proposal cluster
  // fans it out — the SAME exploder a tap uses — so you can drop onto a specific
  // icon instead of blindly hitting whichever sits on top. The exploder no-ops
  // once a spread is open (and when not over a cluster), so firing it on every
  // drag frame is cheap. clusterExploderRef.current is assigned during the
  // clusterEngine memo's render, so it's set by the time this effect runs.
  useEffect(() => {
    if (!dragPoint) return;
    clusterExploderRef?.current?.(dragPoint);
  }, [dragPoint, clusterExploderRef]);

  const onProposalDropRef = useRef(onProposalDrop);
  useEffect(() => { onProposalDropRef.current = onProposalDrop; }, [onProposalDrop]);

  // Set true for the duration of (and just after) a mid-drag out of a proposal,
  // so the trailing click the press generates doesn't ALSO select/restart. Reset
  // on the next macrotask, after that click has been swallowed.
  const proposalMidDraggedRef = useRef(false);

  // Drag a NEW mid out of an exploded on-route proposal. The proposal ICON stays
  // put (this runs a manual pointer drag, not a Leaflet marker drag, so the marker
  // never moves); a dotted trail anchors at the proposal's real point (`anchor`)
  // while a ghost kite follows the cursor — the same affordance as dragging the
  // path. Started from Leaflet's marker `mousedown`, which we STOP so the press
  // doesn't reach the map's pan drag (that's why the exploded icon used to just
  // pan). The move/up cycle is global Pointer Events (mouse + touch). A press that
  // never crosses the move threshold stays a tap (the icon's click selects).
  const beginProposalMidDrag = useCallback((
    anchor: { lat: number; lng: number }, edgeIdx: number, color: string, e: L.LeafletMouseEvent,
  ) => {
    const oe = e.originalEvent as MouseEvent & Partial<TouchEvent>;
    if (oe.button != null && oe.button > 0) return; // primary button only
    // Stop the map from starting a pan on this press, and stop the gesture from
    // bubbling — this is the fix for "exploded icon drag just pans the map."
    L.DomEvent.stop(e.originalEvent);
    const touch = oe.touches?.[0];
    const startX = touch ? touch.clientX : oe.clientX;
    const startY = touch ? touch.clientY : oe.clientY;
    let dragging = false;
    let trail: L.Polyline | null = null;
    const cursorLatLng = (cx: number, cy: number) => {
      const rect = map.getContainer().getBoundingClientRect();
      return map.containerPointToLatLng(L.point(cx - rect.left, cy - rect.top));
    };

    const onMove = (ev: PointerEvent) => {
      if (!dragging) {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (dx * dx + dy * dy < MID_DRAG_THRESHOLD_SQ) return;
        dragging = true;
        proposalMidDraggedRef.current = true;
        map.dragging.disable();
        setDragging(true);
        startGhostDrag({ x: ev.clientX, y: ev.clientY }, null, color);
        trail = L.polyline(
          [[anchor.lat, anchor.lng], [anchor.lat, anchor.lng]],
          MID_DRAG_TRAIL_STYLE,
        ).addTo(map);
      }
      const cur = cursorLatLng(ev.clientX, ev.clientY);
      const snapped = snapToGraph(map, cur.lat, cur.lng);
      const end = snapped ?? { lat: cur.lat, lng: cur.lng };
      trail?.setLatLngs([[anchor.lat, anchor.lng], [end.lat, end.lng]]);
      updateGhostDrag({ x: ev.clientX, y: ev.clientY }, snapped);
    };

    const onUp = (ev: PointerEvent) => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      if (!dragging) return; // a tap — let the icon's click select as usual
      const cur = cursorLatLng(ev.clientX, ev.clientY);
      const snapped = snapToGraph(map, cur.lat, cur.lng);
      const drop = snapped ?? { lat: cur.lat, lng: cur.lng };
      trail?.remove();
      endGhostDrag();
      setDragging(false);
      map.dragging.enable();
      onProposalDropRef.current?.(edgeIdx, anchor, drop);
      // Swallow the click this press emits (handleClick checks the ref), then clear
      // on the next macrotask — after that click has fired.
      window.setTimeout(() => { proposalMidDraggedRef.current = false; }, 0);
    };

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  }, [map, snapToGraph, setDragging, startGhostDrag, updateGhostDrag, endGhostDrag]);

  // Reset icon cache + winners when theme switches (different vote namespace)
  useEffect(() => {
    iconCacheRef.current.clear();
    setWinners([]);
  }, [themeMode]);

  // Initialize canvases once
  useEffect(() => {
    const canvas = document.createElement("canvas");
    // `leaflet-zoom-animated` opts the canvas into Leaflet's zoom-animation CSS
    // transition (transform-origin: 0 0; transition: transform …): when the
    // zoomanim handler sets the target transform, the browser tweens the existing
    // bitmap smoothly to the new zoom instead of it disappearing until zoomend.
    canvas.className = "graph-layer leaflet-zoom-animated";
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";
    // CSS-level softness: a hint of blur smooths the geometric snap. The blend
    // mode comes from the map style — `screen` lightens a dark basemap where
    // heat accumulates; `multiply` darkens a light basemap.
    canvas.style.filter = "blur(0.6px)";
    canvas.style.mixBlendMode = mapStyle.heatBlend;
    canvas.style.opacity = HEATMAP_OPACITY;

    const hoverCanvas = document.createElement("canvas");
    hoverCanvas.className = "graph-layer-hover leaflet-zoom-animated";
    hoverCanvas.style.position = "absolute";
    hoverCanvas.style.top = "0";
    hoverCanvas.style.left = "0";
    hoverCanvas.style.pointerEvents = "none";

    const ctx = canvas.getContext("2d");
    const hoverCtx = hoverCanvas.getContext("2d");
    canvasRef.current = canvas;
    ctxRef.current = ctx;
    hoverCanvasRef.current = hoverCanvas;
    hoverCtxRef.current = hoverCtx;

    const pane = map.getPane("graphPane");
    if (pane) {
      pane.appendChild(canvas);
      pane.appendChild(hoverCanvas);
    }

    return () => {
      if (redrawTimeoutRef.current) cancelAnimationFrame(redrawTimeoutRef.current);
      if (redrawFallbackRef.current) window.clearTimeout(redrawFallbackRef.current);
      redrawTimeoutRef.current = null;
      redrawFallbackRef.current = null;
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      if (hoverCanvas.parentNode) hoverCanvas.parentNode.removeChild(hoverCanvas);
    };
  }, [map, mapStyle.heatBlend]);

  // Block layer (when the map has one): broadcast the deduped block votes to the
  // MapLibre fill layer and switch the canvas edge-heatmap off — blocks are the
  // primary heat, edges show only on hover/selection. No-blocks maps keep the
  // per-edge canvas heatmap exactly as before.
  const broadcastBlockVotes = useCallback((voteData: Partial<GraphData>) => {
    const blockVotes = voteData.block_votes;
    // NOT Array.isArray — the sparse decoder hands us an Int32Array, which is
    // not an Array but is exactly as broadcastable.
    blocksActiveRef.current = blockVotes != null && blockVotes.length > 0;
    if (!blocksActiveRef.current) return;
    // Block heat is the SIGNED vote differential (up − down) of the block's
    // top-ranked proposal — top-ranked BY differential (topProposalDiffs). A
    // block whose best proposal is still net-against gets a negative value
    // and renders on the cold arm of the ramp.
    const { diff, maxPos, maxNeg } = topProposalDiffs(
      blockVotes!, voteData.block_vote_types);
    const detail: BlockVotesDetail = {
      blockDiff: diff,
      max: Math.max(HEAT_FULL_SCALE, maxPos),
      maxNeg: Math.max(NEG_HEAT_FULL_SCALE, maxNeg),
    };
    window.dispatchEvent(new CustomEvent(BLOCK_VOTES_EVENT, { detail }));
  }, []);

  // Route proposals are DERIVED state — a pure, deterministic function of
  // (topology, vote state) computed locally (docs/three-layer-model.md §3).
  // The clustering walks the full vote graph (hundreds of ms on the NYC bike
  // map even after the sparse-nets rework), so it runs as a SLICED job: one
  // vote type per idle slice, yielding to input between slices. Kicking off a
  // new recompute cancels any in-flight job (its half-built result would mix
  // vote states); a delta landing mid-job just dirties the sweep again.
  const routeJobTokenRef = useRef<{ cancelled: boolean } | null>(null);
  const recomputeRouteProposals = useCallback(() => {
    const topo = topologyRef.current;
    const adj = nodeAdjRef.current;
    const data = graphDataRef.current;
    if (!topo || !adj || !data?.edge_vote_types) return;
    if (routeJobTokenRef.current) routeJobTokenRef.current.cancelled = true;
    const token = { cancelled: false };
    routeJobTokenRef.current = token;

    const t0 = performance.now();
    // kindOf keeps POINT-kind vote types out of the corridor family (their
    // votes surface as PBTP pins instead) — the mirror of the PBTP filter.
    // The prebuilt block index skips the job's own O(nEdges) rebuild
    // (GraphLayer already built one for hover/selection).
    const job = createRouteProposalJob(topo, adj, data, {
      kindOf: voteTypeKindOf, blockIndex: blockIndexRef.current,
    });
    const perType: RouteProposal[][] = [];
    let i = 0;
    let slices = 0;

    const finishJob = () => {
      const next = job.finish(perType);
      dlog("proposals", `recompute: ${next.length} corridors in ${(performance.now() - t0).toFixed(1)}ms `
        + `(${job.types.length} types over ${slices} slices)`,
        next.map((p) => `${p.label}#${p.id}(${p.score})`));
      debugState("routeProposals", next.length);
      // Clustering is deterministic, so an unchanged vote state yields an
      // identical list — keep the previous array to avoid remounting diamonds.
      setRouteProposals((prev) =>
        prev.length === next.length
          && prev.every((p, i2) => p.id === next[i2].id && p.score === next[i2].score)
          ? prev : next);
    };

    const slice = (deadline?: IdleDeadline) => {
      if (token.cancelled) return;
      slices++;
      // Always run at least one type per slice; keep going while the idle
      // budget holds (small maps finish in one slice, the NYC bike map
      // spreads its heavy types across several).
      do {
        if (i >= job.types.length) { finishJob(); return; }
        perType.push(job.step(job.types[i++]));
      } while (deadline && deadline.timeRemaining() > 10 && i < job.types.length);
      if (i >= job.types.length) { finishJob(); return; }
      scheduleIdleSlice(slice);
    };
    // The first slice is deferred too, so the caller (often itself an idle
    // callback that just ran the PBTP scan) returns before any heavy type runs.
    scheduleIdleSlice(slice);
  }, [voteTypeKindOf]);

  const recomputeRouteProposalsRef = useRef(recomputeRouteProposals);
  useEffect(() => { recomputeRouteProposalsRef.current = recomputeRouteProposals; }, [recomputeRouteProposals]);

  // Both proposal families in one sweep — the only place either recompute runs.
  const recomputeAllProposals = useCallback(() => {
    recomputeTopProposalsRef.current();
    recomputeRouteProposalsRef.current();
  }, []);

  // Coalesced, idle-time recompute: at most one queued at a time, run via
  // requestIdleCallback so it never lands mid-gesture. Used for the "must be
  // fresh" moments (initial load, full refetch, mode switch) and by the dirty
  // sweep below; per-vote paths only set proposalsDirtyRef.
  const proposalsIdleRef = useRef<number | null>(null);
  const requestProposalsRecompute = useCallback(() => {
    if (proposalsIdleRef.current != null) return;
    const run = () => {
      proposalsIdleRef.current = null;
      recomputeAllProposals();
    };
    proposalsIdleRef.current = typeof requestIdleCallback === "function"
      ? requestIdleCallback(run, { timeout: 4000 })
      : (window.setTimeout(run, 200) as unknown as number);
  }, [recomputeAllProposals]);
  const requestProposalsRecomputeRef = useRef(requestProposalsRecompute);
  useEffect(() => { requestProposalsRecomputeRef.current = requestProposalsRecompute; }, [requestProposalsRecompute]);

  // The batched sweep: votes (own casts and WS deltas) mark this flag; every
  // PROPOSALS_REFRESH_INTERVAL_MS — or as soon as a hidden tab comes back —
  // one idle recompute folds them all in. Proposals may therefore lag votes by
  // up to a minute; the heatmap and count readouts stay live (they don't go
  // through this path).
  const proposalsDirtyRef = useRef(false);
  useEffect(() => {
    const flush = () => {
      if (!proposalsDirtyRef.current || document.hidden) return;
      proposalsDirtyRef.current = false;
      requestProposalsRecomputeRef.current();
    };
    const intervalId = window.setInterval(flush, PROPOSALS_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", flush);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", flush);
      if (proposalsIdleRef.current != null) {
        if (typeof cancelIdleCallback === "function") cancelIdleCallback(proposalsIdleRef.current);
        else window.clearTimeout(proposalsIdleRef.current);
        proposalsIdleRef.current = null;
      }
      // Abandon any in-flight sliced route-proposal job — its next slice
      // would setState on an unmounted component.
      if (routeJobTokenRef.current) routeJobTokenRef.current.cancelled = true;
    };
  }, []);

  // Full vote fetch — used on initial load and revision-gap recovery.
  const fetchVotes = useCallback(async () => {
    if (!topologyRef.current) return;
    try {
      const url = `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}&format=sparse`;
      const response = await fetch(url, { cache: "no-store", headers: passcodeHeaders() });
      if (!response.ok) throw new Error(`Vote fetch failed: ${response.status}`);
      const voteRaw = await response.json();
      const voteData = isSparseVotes(voteRaw) ? decodeSparseVotes(voteRaw) : voteRaw;
      // If the graph was rebuilt mid-session, these votes no longer line up with
      // our topology. Don't paint the mismatch (it would crash); leave the current
      // heatmap and let the next full page load reconcile against fresh topology.
      if (!votesMatchTopology(voteData, topologyRef.current)) {
        dwarn("votes", "skipping vote refresh: topology/vote dimension mismatch");
        return;
      }
      graphDataRef.current = { ...topologyRef.current!, ...voteData };
      lastRevRef.current = voteData.rev ?? 0;
      dlog("votes", `loaded rev ${voteData.rev}: `
        + `${voteData.block_votes?.length ?? 0} block slots, `
        + `legend [${(voteData.vote_type_legend ?? []).join(", ")}]`);
      debugState("votesRev", voteData.rev);
      // Teach the vote store this map's label→id map so packed lookups resolve.
      setVoteTypeMap(voteData.vote_types);
      broadcastBlockVotes(voteData);

      // Replay deltas newer than the installed body: both any buffered before
      // the initial load (pendingDeltasRef) and the recent-applied ring — the
      // server may serve a debounced snapshot a couple of revisions old, and
      // the wholesale install above just overwrote those deltas' counts.
      const bodyRev = voteData.rev ?? 0;
      const replay = [...pendingDeltasRef.current, ...recentDeltasRef.current]
        .filter((d) => d.rev > bodyRev)
        .sort((a, b) => a.rev - b.rev);
      pendingDeltasRef.current = [];
      for (const d of replay) {
        if (d.rev <= lastRevRef.current) continue; // duplicate rev across lists
        applyDeltaToGraphData(d);
      }
      if (replay.length > 0 && replay[0].rev > bodyRev + 1) {
        // The ring couldn't bridge body→first-replayed; those revisions'
        // counts are lost until a fresh snapshot. One DELAYED refetch (past
        // the server's debounce) instead of an immediate one — an immediate
        // retry would get the same stale snapshot and loop.
        dwarn("votes", `snapshot rev ${bodyRev} + ring from ${replay[0].rev} `
          + "leave a hole — scheduling one delayed refetch");
        setTimeout(() => fetchVotesRef.current(), 2500);
      }

      refreshHeatmapDisplayRef.current();
      requestProposalsRecomputeRef.current();
    } catch (error) {
      derror("votes", "failed to fetch graph votes:", error);
    }
  }, [themeMode, broadcastBlockVotes]);

  const fetchVotesRef = useRef(fetchVotes);
  useEffect(() => { fetchVotesRef.current = fetchVotes; }, [fetchVotes]);

  // Shared helper: increment edge votes, update vote-type breakdowns, re-derive
  // affected node votes. Used by both WebSocket deltas and optimistic updates.
  // Apply a vote change to the in-memory graph data. `dir` is +1 (up) / -1
  // (down); `reversed` means a prior opposite vote is being flipped, so one vote
  // moves across directions (net delta ±2). Updates the per-type [li, up, down]
  // triples, the net edge_votes total, and re-derives affected node values.
  // Apply a WebSocket delta to graphDataRef. Every delta carries `vtCounts` —
  // the server's authoritative [up, down] for each changed proposal — which we
  // SET (idempotent). Because the SET is idempotent, the caster's own optimistic
  // guess is corrected to truth rather than double-counted, and re-applying a
  // delta is a no-op. The pre-vtCounts increment path is kept only as a
  // defensive fallback for any delta that somehow lacks counts.
  const applyDeltaToGraphData = useCallback((delta: import("../../types").VoteDelta) => {
    const data = graphDataRef.current;
    const adj = nodeAdjRef.current;
    if (!data?.edge_votes || !adj) { lastRevRef.current = delta.rev; return; }
    const vtLabel = delta.vtLabel ?? "";

    if (delta.vtCounts) {
      applyAuthoritativeCounts(data, adj, vtLabel, delta.vtCounts);
    } else {
      applyEdgeVoteChange(data, adj, delta.edges, vtLabel, delta.dir ?? 1, delta.reversed ?? false);
    }
    if (delta.blockCounts && applyBlockCounts(data, vtLabel, delta.blockCounts)) {
      broadcastBlockVotes(data);
    }
    lastRevRef.current = delta.rev;
  }, [broadcastBlockVotes]);

  // Load topology + votes on mount, preferring persisted caches.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      // 0. Kick the authoritative vote fetch off immediately, in parallel with
      //    the version probe and the topology load/decode: the request depends
      //    only on slug+mode. On a cold load it used to start only AFTER the
      //    multi-MB topology finished downloading, serializing the two biggest
      //    fetches. The early block-heat paint (in step 1) and step 3 both
      //    consume it; the noop catches only silence the unhandled-rejection
      //    warning when topology fails first.
      const votesFetchPromise = fetch(
        `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}&format=sparse`,
        { headers: passcodeHeaders() },
      ).then((r) => {
        if (!r.ok) throw new Error(`Vote fetch failed: ${r.status}`);
        return r.json();
      });
      votesFetchPromise.catch(() => {});
      // Decode ONCE, shared by the early block-heat paint below and step 3 —
      // whichever awaits it second reuses the same decoded arrays.
      const votesDecodedPromise = votesFetchPromise.then((voteRaw) => ({
        voteRaw,
        voteData: isSparseVotes(voteRaw) ? decodeSparseVotes(voteRaw) : voteRaw,
      }));
      votesDecodedPromise.catch(() => {});
      // Set once the authoritative body's block heat is on screen — step 2
      // must not repaint the (older-rev) cached snapshot over it.
      let earlyHeatPainted = false;

      // 1. Resolve the graph version, then load topology from IndexedDB when it
      //    matches — skipping the multi-MB download and JSON parse entirely.
      let topology: GraphTopology | null = null;
      let version: string | null = null;
      let blocksVersion: string | null = null;
      let usedCachedTopology = false;
      // Assigned inside the try below; the step-3 stale-topology guard reuses it.
      let fetchFreshTopology: ((forceReload?: boolean) => Promise<GraphTopology | null>) | null = null;
      try {
        try {
          const vr = await fetch(withMap(`${CONFIG.apiUrl}/graph-version`), { headers: passcodeHeaders() });
          if (vr.ok) {
            const vj = await vr.json();
            version = vj.version ?? null;
            blocksVersion = vj.blocks ?? null;
          }
        } catch {
          // Version probe failed — fall back to a direct topology fetch.
        }

        // EARLY BLOCK-HEAT PAINT — the sparse vote body (~57KB) carries the
        // complete block heat, and MapLibre applies it as feature-state keyed
        // purely by block id on the blocks PMTiles source (the payload is
        // retained + re-applied until the source exists), so it needs NO
        // topology or edge arrays. Painting the moment the votes land —
        // instead of after the multi-MB topology download + decode below —
        // puts first heat on screen seconds earlier on a cold load. Gate:
        // blocks_version must equal the probe's (block ids renumber on every
        // re-bake; a mid-deploy mismatch would light the wrong polygons) — on
        // mismatch skip and let the topology-gated flow below reconcile
        // exactly as before. Edge-array reconciliation (votesMatchTopology,
        // graphDataRef) still happens only in step 3.
        votesDecodedPromise.then(({ voteData }) => {
          if (cancelled) return;
          if (!voteData.block_votes?.length) return;
          if ((voteData.blocks_version ?? null) !== blocksVersion) {
            dlog("blocks", "skipping early heat paint: blocks_version mismatch", {
              votes: voteData.blocks_version, probe: blocksVersion,
            });
            return;
          }
          earlyHeatPainted = true;
          broadcastBlockVotes(voteData);
          dlog("blocks", `first-heat-paint: ${voteData.block_votes.length} block slots `
            + `at rev ${voteData.rev} (pre-topology)`);
        }).catch(() => {});

        // Cache-bust the topology URL by graph version. /graph-topology is served
        // with max-age=86400, so after a deploy that renumbers edge ids the browser
        // HTTP cache would otherwise serve a STALE topology under the same URL —
        // which, paired with fresh (new-edge-id) votes, mismatches and crashes
        // mobile Safari. A version-scoped URL guarantees a fresh fetch on change
        // while still caching repeat loads of the same version.
        const withVersion = (url: string, v: string | null = version) =>
          v ? url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(v) : url;

        // The binary blob's format AND block mapping are versioned SEPARATELY
        // from the topology content: adding the edge_block_id section (GTB2) —
        // or re-baking the block set against the same graph — doesn't change
        // the node/edge content, so /graph-version still reports the same etag.
        // Suffix the blob's cache key + URL buster with the format tag and the
        // blocks version — mirroring the server's "-bin2-<blocks>" ETag — or a
        // client holding an old blob under the same version would pin it (in
        // IndexedDB AND the day-long HTTP cache) and keep painting selections
        // through a stale edge→block mapping.
        const binVersion = version
          ? `${version}-bin2${blocksVersion ? `-${blocksVersion}` : ""}`
          : null;

        // Fetch the topology from the NETWORK (not IndexedDB). `forceReload` adds
        // cache:"reload" so the browser HTTP cache is bypassed too — used by the
        // stale-topology recovery path in step 3, where even the version-busted
        // URL may have been served stale from Safari's HTTP cache.
        const fetchTopologyFromNetwork = async (forceReload = false): Promise<GraphTopology | null> => {
          const init: RequestInit = forceReload
            ? { headers: passcodeHeaders(), cache: "reload" }
            : { headers: passcodeHeaders() };
          // Station networks (tiny, names matter) use the JSON topology; large
          // street graphs use the binary topology so a phone never JSON.parse-es a
          // ~150MB string (the OOM that crashed mobile Safari on the NYC graph).
          // Both are normalized to the flat typed-array GraphTopology so consumers
          // (and the heatmap's memory footprint) don't depend on the wire format.
          if (isStationNetwork) {
            const r = await fetch(withVersion(withMap(`${CONFIG.apiUrl}/graph-topology`)), init);
            if (!r.ok) throw new Error(`Topology fetch failed: ${r.status}`);
            const t = topologyFromJson(await r.json());
            if (version) setCachedTopology(version, t);
            return t;
          }
          // Street networks are binary-only (the server no longer serves their
          // JSON topology) — a failure here surfaces instead of falling back.
          const r = await fetch(
            withVersion(withMap(`${CONFIG.apiUrl}/graph-topology?format=bin`), binVersion),
            init,
          );
          if (!r.ok) throw new Error(`Binary topology fetch failed: ${r.status}`);
          const buf = await r.arrayBuffer();
          const t = decodeTopologyBin(buf);
          if (binVersion) setCachedTopologyBin(binVersion, buf);
          return t;
        };
        fetchFreshTopology = fetchTopologyFromNetwork;

        if (version) {
          if (isStationNetwork) {
            const cached = await getCachedTopology<GraphTopology>(version);
            if (cached) { topology = cached; usedCachedTopology = true; }
          } else {
            const buf = await getCachedTopologyBin(binVersion!);
            // Decode can throw on a corrupt/truncated cached blob — ignore the
            // cache and fall through to a fresh network fetch rather than aborting.
            if (buf) {
              try {
                topology = decodeTopologyBin(buf);
                usedCachedTopology = true;
              } catch (decodeErr) {
                dwarn("topo", "cached binary topology was corrupt, refetching:", decodeErr);
              }
            }
          }
        }
        if (!topology) {
          topology = await fetchTopologyFromNetwork();
        }
      } catch (error) {
        derror("topo", "failed to load graph topology:", error);
        return;
      }
      if (cancelled || !topology) return;

      topologyRef.current = topology;
      graphDataRef.current = topology;
      nodeAdjRef.current = buildNodeAdj(topology);
      blockIndexRef.current = buildBlockIndex(topology);
      hasBlocksRef.current = !!topology.edgeBlockId;
      dlog("topo", `ready: ${topology.nEdges} edges, ${topology.nNodes} nodes, `
        + `${topology.nBlocks ?? 0} blocks (${usedCachedTopology ? "IndexedDB cache" : "network"})`);
      // Label this page load's [MAPLOAD] beacon: cache = repeat visit,
      // network = true cold first load (the P99 the dashboard tracks).
      reportTopologySource(usedCachedTopology);
      debugState("topology", {
        nEdges: topology.nEdges, nBlocks: topology.nBlocks ?? 0, cached: usedCachedTopology,
      });
      scheduleRedrawRef.current();
      // Spatial indexes build in yielding batches (see INDEX_YIELD_BATCH) so
      // the tile/heat paint happening right now isn't janked; hitTest returns
      // no hit until each ref is set with a COMPLETE index. Clear any stale
      // indexes from a previous effect run first — a mousemove landing in a
      // yield gap must never pair an old index with the new topology. Build
      // into locals and install only after the cancelled check, so a torn-down
      // run never publishes its indexes; redraw() skips frames while the refs
      // are null, so repaint once they land.
      edgeIndexRef.current = null;
      nodeIndexRef.current = null;
      const builtEdgeIndex = await buildEdgeIndex(topology);
      if (cancelled) return;
      const builtNodeIndex = await buildNodeIndex(topology);
      if (cancelled) return;
      edgeIndexRef.current = builtEdgeIndex;
      nodeIndexRef.current = builtNodeIndex;
      scheduleRedrawRef.current();

      // 2. Paint immediately from cached votes (same graph version only) so the
      //    heatmap appears without waiting on the network. Skipped when the
      //    topology was freshly downloaded, since edge indices may have shifted.
      //    The body must also match the CURRENT block set — block ids renumber
      //    on every blocks re-bake under the SAME topology etag, and a
      //    pre-re-bake body paints the wrong polygons (node blocks dark, e.g.)
      //    which live deltas then layer onto instead of healing.
      if (usedCachedTopology && version) {
        // Cached entries written after the sparse-format rollout hold the raw
        // (tiny) sparse payload; older entries hold the dense decoded shape.
        // Decode either into the same in-memory form.
        const cachedRaw = await getCachedVotes<unknown>(getMapSlug() || themeMode, version);
        const cachedVotes = isSparseVotes(cachedRaw)
          ? decodeSparseVotes(cachedRaw)
          : cachedRaw as Partial<GraphData> | undefined;
        if (cancelled) return;
        const blocksMatch = cachedVotes
          && (cachedVotes.blocks_version ?? null) === blocksVersion
          && votesMatchTopology(cachedVotes, topology);
        if (cachedVotes && blocksMatch) {
          graphDataRef.current = { ...topology, ...cachedVotes };
          lastRevRef.current = cachedVotes.rev ?? 0;
          setVoteTypeMap(cachedVotes.vote_types);
          // The edge-array install above still proceeds, but don't repaint
          // the cached (older-rev) block heat over the authoritative body if
          // the early paint already landed.
          if (!earlyHeatPainted) broadcastBlockVotes(cachedVotes);
          refreshHeatmapDisplayRef.current();
          requestProposalsRecomputeRef.current();
          setHeatmapLoaded();
        } else if (cachedVotes) {
          dwarn("votes", "ignoring cached votes: stale block set", {
            cached: cachedVotes.blocks_version, live: blocksVersion,
          });
        }
      }

      // 3. Authoritative vote fetch — replaces the cached snapshot and replays
      //    any deltas that arrived while the fetch was in flight.
      try {
        // Awaits the request started in step 0 (usually already resolved by
        // now — it ran concurrently with the topology download/decode).
        // format=sparse: nonzero-only body decoded into typed/holey arrays —
        // the dense body's ~9M boxed slots were the mobile-Safari OOM (see
        // utils/sparseVotes.ts). A dense body still decodes as-is (old server).
        const { voteRaw, voteData } = await votesDecodedPromise;
        if (cancelled) return;

        // Stale-topology guard. The vote arrays are indexed against the server's
        // CURRENT graph. If our topology (often from the day-long cache) has a
        // different node/edge count, painting these votes indexes past the array
        // ends — the mobile-Safari crash. Refetch the topology fresh (bypassing
        // both the IndexedDB and HTTP caches), rebuild the indices, and only apply
        // the votes once they line up. If still mismatched, drop the persisted
        // caches and bail rather than paint a crash.
        if (!votesMatchTopology(voteData, topologyRef.current)) {
          dwarn("topo", "topology/vote mismatch — refetching fresh topology", {
            topoEdges: topologyRef.current?.nEdges,
            voteEdges: voteData.n_edges ?? voteData.edge_votes?.length,
          });
          let fresh: GraphTopology | null = null;
          try {
            fresh = fetchFreshTopology ? await fetchFreshTopology(true) : null;
          } catch (refetchErr) {
            derror("topo", "Fresh topology refetch failed:", refetchErr);
          }
          if (cancelled) return;
          if (fresh && votesMatchTopology(voteData, fresh)) {
            // Build the fresh indexes into locals FIRST, then install every
            // ref in one synchronous block. Assigning topologyRef/the fresh
            // edge index before the yielding builds finish would let a
            // mousemove pair the fresh index with the OLD graphDataRef arrays
            // (out-of-range edge ids → NaN coords → Leaflet throws) — the
            // exact torn pairing the null-out guard exists to prevent.
            const freshEdgeIndex = await buildEdgeIndex(fresh);
            if (cancelled) return;
            const freshNodeIndex = await buildNodeIndex(fresh);
            if (cancelled) return;
            topology = fresh;
            topologyRef.current = fresh;
            nodeAdjRef.current = buildNodeAdj(fresh);
            blockIndexRef.current = buildBlockIndex(fresh);
            hasBlocksRef.current = !!fresh.edgeBlockId;
            edgeIndexRef.current = freshEdgeIndex;
            nodeIndexRef.current = freshNodeIndex;
            scheduleRedrawRef.current();
          } else {
            await clearGraphCache();
            setHeatmapLoaded();
            return;
          }
        }

        graphDataRef.current = { ...topologyRef.current!, ...voteData };
        lastRevRef.current = voteData.rev ?? 0;
        setVoteTypeMap(voteData.vote_types);
        // Persist the RAW wire payload: the sparse form is ~50x smaller to
        // structured-clone into IndexedDB than the decoded dense arrays, and
        // the read path re-decodes it.
        if (version) setCachedVotes(getMapSlug() || themeMode, version, voteRaw);
        broadcastBlockVotes(voteData);

        // Replay any deltas that arrived while waiting for the fetch
        const pending = pendingDeltasRef.current;
        if (pending.length > 0) {
          pendingDeltasRef.current = [];
          for (const d of pending) {
            if (d.rev <= lastRevRef.current) continue;
            applyDeltaToGraphData(d);
          }
        }

        refreshHeatmapDisplayRef.current();
        setHeatmapLoaded();
        requestProposalsRecomputeRef.current();
      } catch (error) {
        derror("votes", "failed to fetch graph votes:", error);
      }
    })();
    return () => { cancelled = true; };
  }, [applyDeltaToGraphData, setHeatmapLoaded, broadcastBlockVotes]);

  // Recompute proposals when the vote namespace (theme mode) switches. On
  // first mount topology isn't loaded yet so this no-ops; the post-vote call in
  // the loader above does the initial compute.
  useEffect(() => { requestProposalsRecomputeRef.current(); }, [themeMode]);

  // Subscribe to WebSocket deltas — apply each directly to the vote arrays.
  // If a revision gap is detected, do a full refetch to recover.
  useEffect(() => {
    const unsubscribe = subscribeToDelta((delta) => {
      // Filter by current theme mode
      if (delta.m !== themeMode) {
        return;
      }

      // If votes haven't loaded yet, buffer the delta
      if (!graphDataRef.current?.edge_votes) {
        pendingDeltasRef.current.push(delta);
        return;
      }

      // Remember every received delta (bounded) so a refetch can replay the
      // ones newer than the (possibly debounced) snapshot it installs. This
      // includes gap-triggering deltas below — the refetch body may not cover
      // them, but the replay will.
      recentDeltasRef.current.push(delta);
      if (recentDeltasRef.current.length > RECENT_DELTAS_MAX) {
        recentDeltasRef.current.splice(
          0, recentDeltasRef.current.length - RECENT_DELTAS_MAX);
      }

      // Gap detection: if we missed revisions, full refetch
      if (lastRevRef.current > 0 && delta.rev > lastRevRef.current + 1) {
        dwarn("votes", `delta gap (have rev ${lastRevRef.current}, got ${delta.rev}) — full refetch`);
        fetchVotesRef.current();
        return;
      }

      // Skip duplicates
      if (delta.rev <= lastRevRef.current) {
        return;
      }

      dlog("votes", `delta rev ${delta.rev}: "${delta.vtLabel ?? delta.vt}" `
        + `edges=${delta.edges.length} blocks=${Object.keys(delta.blockCounts ?? {}).length}`);
      const legendLenBefore = graphDataRef.current?.vote_type_legend?.length ?? 0;
      applyDeltaToGraphData(delta);
      refreshHeatmapDisplayRef.current();
      // A vote only dirties the proposal lists (the batched sweep folds it in)
      // — EXCEPT when the delta grew the legend: the current winners were
      // ranked against a shorter legend, so refresh promptly (still idle-time).
      const legendLenAfter = graphDataRef.current?.vote_type_legend?.length ?? 0;
      if (legendLenAfter !== legendLenBefore) requestProposalsRecomputeRef.current();
      else proposalsDirtyRef.current = true;
    });
    return unsubscribe;
  }, [subscribeToDelta, themeMode, applyDeltaToGraphData]);

  // Optimistic vote — apply this user's vote transition (prevDir → newDir)
  // immediately on cast so the heatmap and top-proposal counts update before the
  // server round-trip completes. No ledger is needed: every server delta now
  // carries authoritative vtCounts and is applied as an idempotent SET (see
  // applyDeltaToGraphData), so the caster's own optimistic guess is corrected to
  // truth rather than double-counted. The cast path (utils/castVote.ts) emits a
  // reverse transition to roll back if the request fails.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as import("../../utils/castVote").OptimisticVoteDetail;
      if (detail.mode !== themeMode) return;

      const data = graphDataRef.current;
      const adj = nodeAdjRef.current;
      if (!data?.edge_votes || !adj) return;
      if (!detail.edgeIds?.length) return;

      const legendLenBefore = data.vote_type_legend?.length ?? 0;
      applyMyVoteChange(data, adj, detail.edgeIds, detail.label, detail.prevDir, detail.newDir);
      refreshHeatmapDisplayRef.current();
      // Same rule as the WS-delta path: dirty-mark for the batched sweep,
      // prompt (idle) refresh only if this cast introduced a new legend entry.
      if ((data.vote_type_legend?.length ?? 0) !== legendLenBefore) requestProposalsRecomputeRef.current();
      else proposalsDirtyRef.current = true;
    };
    window.addEventListener("optimistic-vote", handler);
    return () => window.removeEventListener("optimistic-vote", handler);
  }, [themeMode]);

  // Draw hover and pinned highlights on separate canvas
  const redrawHoverHighlight = useCallback(() => {
    const hoverCanvas = hoverCanvasRef.current;
    const hoverCtx = hoverCtxRef.current;
    const data = graphDataRef.current;
    if (!hoverCanvas || !hoverCtx) return;

    // Same mid-zoom guard as redraw(): a highlight painted against a
    // mid-animation pane transform (route-highlight/select events can land
    // here) draws the white corridors offset from the map and they stay that
    // way until the next repaint. zoomend's redraw re-invokes this.
    if (isZoomingRef.current) return;

    const size = map.getSize();
    hoverCanvas.width = size.x;
    hoverCanvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(hoverCanvas, topLeft);
    hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);

    if (!data) return;

    hoverCtx.lineCap = "round";
    hoverCtx.lineJoin = "round";

    // Renders a hollow white ring with faint interior — same look as the
    // desire path. `alpha` scales overall opacity (e.g. hover dimmer than pinned).
    // Three passes:
    //   1. Stroke wide white (covers full path footprint)
    //   2. destination-out narrower stroke (carves the hole)
    //   3. Stroke narrower at low alpha (faint white interior)
    const drawNode = (nodeIndex: number, alpha: number) => {
      if (nodeIndex >= data.nNodes) return;
      const pt = map.latLngToContainerPoint([nodeLat(data, nodeIndex), nodeLon(data, nodeIndex)]);

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = alpha;
      hoverCtx.fillStyle = mapStyle.selection;
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_OUTER_R, 0, Math.PI * 2);
      hoverCtx.fill();

      hoverCtx.globalCompositeOperation = "destination-out";
      hoverCtx.globalAlpha = 1.0;
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_INNER_R, 0, Math.PI * 2);
      hoverCtx.fill();

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = HIGHLIGHT_INTERIOR_ALPHA * alpha;
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_INNER_R, 0, Math.PI * 2);
      hoverCtx.fill();
    };

    const drawEdge = (edgeIndex: number, alpha: number) => {
      if (edgeIndex >= data.nEdges) return;
      const fromIdx = edgeFrom(data, edgeIndex), toIdx = edgeTo(data, edgeIndex);
      const fromScreen = map.latLngToContainerPoint([nodeLat(data, fromIdx), nodeLon(data, fromIdx)]);
      const toScreen = map.latLngToContainerPoint([nodeLat(data, toIdx), nodeLon(data, toIdx)]);

      const strokeLine = () => {
        hoverCtx.beginPath();
        hoverCtx.moveTo(fromScreen.x, fromScreen.y);
        hoverCtx.lineTo(toScreen.x, toScreen.y);
        hoverCtx.stroke();
      };

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = alpha;
      hoverCtx.strokeStyle = mapStyle.selection;
      hoverCtx.lineWidth = HIGHLIGHT_RING_WIDTH;
      strokeLine();

      hoverCtx.globalCompositeOperation = "destination-out";
      hoverCtx.globalAlpha = 1.0;
      hoverCtx.lineWidth = HIGHLIGHT_INNER_WIDTH;
      strokeLine();

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = HIGHLIGHT_INTERIOR_ALPHA * alpha;
      hoverCtx.lineWidth = HIGHLIGHT_INNER_WIDTH;
      strokeLine();
    };

    const drawTarget = (t: HoverTarget, alpha: number) => {
      if (t.kind === "edge") drawEdge(t.index, alpha);
      else drawNode(t.index, alpha);
    };

    // Pinned highlight (selected start point) — full opacity, exact path match
    const pinned = pinnedTargetRef.current;
    if (pinned) drawTarget(pinned, 1.0);

    // Hover highlight — slightly dimmer to read as "preview". Suppress it when
    // it resolves to the already-pinned target so the selection's hover version
    // doesn't double-draw; only show hover for a *different* proposal.
    const hover = hoverTargetRef.current;
    const hoverIsPinned = pinned && hover && hover.kind === pinned.kind && hover.index === pinned.index;
    if (hover && !hoverIsPinned) drawTarget(hover, 0.6);

    // Drag-snap preview — the edge/node the waypoint being dragged would land on.
    // Full opacity so the active drop target reads clearly over hover/pinned.
    const dragSnap = dragSnapTargetRef.current;
    if (dragSnap) drawTarget(dragSnap, 1.0);

    // Hovered ROUTE proposal — light up every edge in all of its blocks so the
    // whole corridor previews at once (not just the diamond's anchor edge).
    const routeEdges = routeHighlightEdgesRef.current;
    if (routeEdges) for (const e of routeEdges) drawEdge(e, 0.7);

    // Reset state for any subsequent canvas operations
    hoverCtx.globalCompositeOperation = "source-over";
    hoverCtx.globalAlpha = 1.0;
  }, [map, mapStyle.selection]);

  const redrawHoverHighlightRef = useRef(redrawHoverHighlight);
  useEffect(() => { redrawHoverHighlightRef.current = redrawHoverHighlight; }, [redrawHoverHighlight]);

  // Redraw function - renders edges with vote-scaled styling
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;
    const data = graphDataRef.current;

    if (!canvas || !ctx || !data) return;

    // Never paint mid-zoom: container/layer projections are inconsistent while
    // the pane transform is animating, so a paint here (e.g. triggered by a WS
    // delta) lands misaligned — and STAYS misaligned if the zoomend repaint is
    // dropped. zoomend always schedules a redraw, so deferring loses nothing.
    if (isZoomingRef.current) return;

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Record the camera this bitmap is painted at — BEFORE any early return, so
    // block maps (where the canvas holds only highlights) still anchor the
    // zoom-anim ride and the drift failsafe in ensureAnchored.
    drawStateRef.current = { zoom: map.getZoom(), nw: map.containerPointToLatLng([0, 0]) };

    if (!data.ends || !data.coords) return;

    // Blocks are the heat display (MapLibre fill layer): skip the per-edge canvas
    // heatmap entirely. The hover/selection highlight (its own canvas) still
    // paints, so edges appear only when hovered or on a route.
    if (blocksActiveRef.current) {
      redrawHoverHighlightRef.current();
      return;
    }

    const coords = data.coords;
    const ends = data.ends;
    const edgeVotes = data.edge_votes ?? [];

    // maxVotes is global (so colors stay consistent across the viewport) but
    // only changes when votes change — recompute on revision change, not every
    // frame. lastRevRef advances on every full fetch and applied delta.
    if (maxVotesRevRef.current !== lastRevRef.current) {
      maxVotesRef.current = Math.max(1, arrayMax(edgeVotes));
      maxVotesRevRef.current = lastRevRef.current;
    }
    // Floor the scale so low-traffic maps don't saturate (see HEAT_FULL_SCALE).
    const maxVotes = Math.max(maxVotesRef.current, HEAT_FULL_SCALE);

    const bounds = map.getBounds();
    const zoom = map.getZoom();
    const zoomScale = Math.pow(2, (zoom - 14) / 2);

    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // ----------------------------------------------------------------
    // Projection cache — node layer-points, valid for the current zoom.
    // Rebuilt when zoom or pixelOrigin changes; nodes projected lazily as
    // they're first drawn and reused across pans. Per-frame we only add the
    // pane offset to convert cached layer-points → container pixels.
    // ----------------------------------------------------------------
    const origin = map.getPixelOrigin();
    let proj = projCacheRef.current;
    if (!proj || proj.zoom !== zoom || proj.ox !== origin.x || proj.oy !== origin.y) {
      const n = data.nNodes;
      proj = {
        zoom,
        ox: origin.x,
        oy: origin.y,
        xs: new Float64Array(n),
        ys: new Float64Array(n),
        done: new Uint8Array(n),
      };
      projCacheRef.current = proj;
    }
    const { xs, ys, done } = proj;
    const offset = map.layerPointToContainerPoint(L.point(0, 0));
    const offX = offset.x;
    const offY = offset.y;

    const projectNode = (i: number) => {
      if (done[i]) return;
      const p = map.latLngToLayerPoint([coords[2 * i] / COORD_SCALE, coords[2 * i + 1] / COORD_SCALE]);
      xs[i] = p.x;
      ys[i] = p.y;
      done[i] = 1;
    };

    const drawSeg = (i: number) => {
      const a = ends[2 * i];
      const b = ends[2 * i + 1];
      projectNode(a);
      projectNode(b);
      ctx.beginPath();
      ctx.moveTo(xs[a] + offX, ys[a] + offY);
      ctx.lineTo(xs[b] + offX, ys[b] + offY);
      ctx.stroke();
    };

    // ----------------------------------------------------------------
    // Viewport culling — query the edge spatial index for edges whose bbox
    // intersects the visible bounds (+ small margin for wide strokes). Falls
    // back to all edges only in the brief window before the index is built.
    // ----------------------------------------------------------------
    const mLat = (bounds.getNorth() - bounds.getSouth()) * 0.05;
    const mLng = (bounds.getEast() - bounds.getWest()) * 0.05;
    const edgeIndex = edgeIndexRef.current;
    // No index yet (the chunked build is still yielding): skip the frame
    // rather than fall back to drawing ALL edges — on NYC that fallback is
    // 1.97M strokes in one rAF task, a multi-second freeze. The build's
    // completion schedules a repaint, so nothing is lost.
    if (!edgeIndex) return;
    const visible = edgeIndex.search(
      bounds.getWest() - mLng,
      bounds.getSouth() - mLat,
      bounds.getEast() + mLng,
      bounds.getNorth() + mLat,
    );
    const count = visible.length;
    const edgeAt = (k: number): number => visible[k];

    // ----------------------------------------------------------------
    // Phase 1 — zero-vote baseline (source-over, faint white)
    // Drawn before lighter mode so the network outline doesn't itself
    // accumulate at intersections (which would highlight random nodes).
    // ----------------------------------------------------------------
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = 0.5 * zoomScale;
    ctx.globalAlpha = 0.05;
    ctx.strokeStyle = mapStyle.selection;
    for (let k = 0; k < count; k++) {
      const i = edgeAt(k);
      if ((edgeVotes[i] ?? 0) > 0) continue;
      drawSeg(i);
    }

    // ----------------------------------------------------------------
    // Phase 2 — voted edges, additive blending (Strava-style)
    // Sort ascending so the hottest edges paint last and dominate at
    // overlaps; "lighter" composite means RGB channels sum, naturally
    // shifting toward yellow/white as intensity stacks. Only the visible
    // subset is collected/sorted, so this is cheap regardless of graph size.
    // ----------------------------------------------------------------
    const voted: number[] = [];
    for (let k = 0; k < count; k++) {
      const i = edgeAt(k);
      if ((edgeVotes[i] ?? 0) > 0) voted.push(i);
    }
    voted.sort((a, b) => (edgeVotes[a] ?? 0) - (edgeVotes[b] ?? 0));

    ctx.globalCompositeOperation = mapStyle.heatComposite;
    const heat = mapStyle.heat;

    // Sample the ramp by an edge's OWN intensity so hue encodes vote count:
    // low-traffic edges are cool (halo), hot corridors warm (peak). Both the
    // glow and the core take this single per-edge color — critical under the
    // dark themes' additive (`lighter`) blend, where a fixed cool halo drawn
    // for every edge would accumulate and wash the whole map to one hue.
    // Stops mirror heatGradientCss (mapStyles.ts) so the legend matches the map.
    // The same ramp feeds the top-proposal pins (see indicatorMarkers), so a pin
    // glows the exact hue the heatmap paints at its vote count.
    const rampStops = buildHeatRampStops(heat, mapStyle.basemap);
    const sampleRamp = (t: number): string => sampleHeatRamp(rampStops, t);
    // The ramp's incandescent tip (sampled at 1.0) — used for the hottest-edge
    // accent pass so the very top of the scale reads white-hot, not flat peak.
    const tipColor = sampleRamp(1);

    for (const i of voted) {
      const norm = Math.log((edgeVotes[i] ?? 0) + 1) / Math.log(maxVotes + 1);
      const color = sampleRamp(norm);

      // Pass 1 — wide outer halo (low alpha, broad falloff). Same hue as the
      // core so a hot edge glows warm and a cold edge glows cool.
      ctx.lineWidth = (2 + norm * 8) * zoomScale;
      ctx.globalAlpha = 0.03 + norm * 0.05;
      ctx.strokeStyle = color;
      drawSeg(i);

      // Pass 2 — the core stroke (the dominant, readable color).
      ctx.lineWidth = (0.8 + norm * 2) * zoomScale;
      ctx.globalAlpha = 0.10 + norm * 0.22;
      ctx.strokeStyle = color;
      drawSeg(i);

      // Pass 3 — bright peak accent on only the hottest edges, for extra punch.
      if (norm > 0.7) {
        const t = (norm - 0.7) / 0.3;
        ctx.lineWidth = Math.max(0.3, 0.5 * zoomScale);
        ctx.globalAlpha = 0.18 * t;
        ctx.strokeStyle = tipColor;
        drawSeg(i);
      }
    }

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1.0;

    redrawHoverHighlightRef.current();
  }, [map, mapStyle.heat, mapStyle.basemap, mapStyle.heatComposite, mapStyle.selection]);

  // Schedule redraw. rAF is the fast path; the timer is a backstop because rAF
  // never fires in hidden/occluded windows — without it a zoomend repaint can be
  // dropped entirely, leaving the bitmap anchored at the OLD zoom (visible as a
  // scaled/offset heatmap once the window surfaces).
  const scheduleRedraw = useCallback(() => {
    if (redrawTimeoutRef.current) cancelAnimationFrame(redrawTimeoutRef.current);
    if (redrawFallbackRef.current) window.clearTimeout(redrawFallbackRef.current);
    redrawTimeoutRef.current = requestAnimationFrame(() => {
      redrawTimeoutRef.current = null;
      if (redrawFallbackRef.current) {
        window.clearTimeout(redrawFallbackRef.current);
        redrawFallbackRef.current = null;
      }
      redraw();
    });
    redrawFallbackRef.current = window.setTimeout(() => {
      redrawFallbackRef.current = null;
      if (redrawTimeoutRef.current) {
        cancelAnimationFrame(redrawTimeoutRef.current);
        redrawTimeoutRef.current = null;
      }
      redraw();
    }, 200);
  }, [redraw]);

  const scheduleRedrawRef = useRef(scheduleRedraw);
  useEffect(() => { scheduleRedrawRef.current = scheduleRedraw; }, [scheduleRedraw]);
  const redrawRef = useRef(redraw);
  useEffect(() => { redrawRef.current = redraw; }, [redraw]);

  // Map event listeners — topology is pre-loaded, just redraw on pan/zoom
  useEffect(() => {
    const handleZoomStart = () => {
      isZoomingRef.current = true;
      // NOTE: we intentionally DON'T clear the heatmap canvas here anymore. The
      // existing bitmap stays up and gets scaled by handleZoomAnim so the heatmap
      // visibly zooms with the map instead of vanishing until zoomend. Only the
      // transient hover highlight is dropped (it would be wrong mid-zoom anyway).
      const hoverCtx = hoverCtxRef.current;
      const hoverCanvas = hoverCanvasRef.current;
      if (hoverCanvas && hoverCtx) hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);
      hoverTargetRef.current = null;
      setHoverTarget(null);
    };

    // Ride Leaflet's zoom animation: set the canvas transform to where its
    // top-left corner lands at the target zoom, scaled by the zoom ratio. Because
    // the canvas carries the `leaflet-zoom-animated` class, the mapPane's
    // `leaflet-zoom-anim` state makes the browser TRANSITION this transform — the
    // same mechanism L.Canvas/tile layers use — so the heatmap glides + scales
    // into the new zoom. zoomend then repaints crisply at the new resolution.
    const handleZoomAnim = (e: L.ZoomAnimEvent) => {
      const canvas = canvasRef.current;
      const draw = drawStateRef.current;
      if (!canvas || !draw) return;
      // getZoomScale(toZoom, fromZoom) = 2^(toZoom - fromZoom): how much the
      // painted bitmap must grow/shrink to match the target zoom.
      const scale = map.getZoomScale(e.zoom, draw.zoom);
      // Where the bitmap's top-left geographic corner sits in the target frame.
      // _latLngToNewLayerPoint is the internal Leaflet helper L.Canvas itself uses
      // for this; it's not in the public typings, so reach it through a cast.
      const offset = (
        map as unknown as {
          _latLngToNewLayerPoint(latlng: L.LatLng, zoom: number, center: L.LatLng): L.Point;
        }
      )._latLngToNewLayerPoint(draw.nw, e.zoom, e.center);
      L.DomUtil.setTransform(canvas, offset, scale);
      const hoverCanvas = hoverCanvasRef.current;
      if (hoverCanvas) L.DomUtil.setTransform(hoverCanvas, offset, scale);
    };

    const handleZoomEnd = () => {
      isZoomingRef.current = false;
      setCurrentZoom(map.getZoom());
      // redraw() runs setPosition (resetting the scale transform) then repaints
      // the bitmap at the new zoom's native resolution and line widths.
      scheduleRedrawRef.current();
    };
    const handleMoveEnd = () => {
      // Always redraw on moveend — isZoomingRef is cleared synchronously in zoomend
      scheduleRedrawRef.current();
    };
    const handleResize = () => scheduleRedrawRef.current();

    // Failsafe re-anchor: the bitmap records the zoom it was painted at; if it
    // disagrees with the map's zoom while no zoom is in flight, the zoomend
    // repaint was dropped (frozen rAF, interrupted animation) and the canvas is
    // sitting misaligned. Repaint synchronously — don't re-enter the rAF path
    // that already failed. Checked per move frame (a number compare) and on
    // visibility restore, since a hidden window is where rAF freezes.
    const ensureAnchored = () => {
      if (isZoomingRef.current) return;
      const painted = drawStateRef.current;
      if (!painted || painted.zoom === map.getZoom()) return;
      dwarn("topo", `anchor drift: bitmap painted at z${painted.zoom}, map at z${map.getZoom()} — repainting`);
      redrawRef.current();
    };
    const handleVisibility = () => {
      if (!document.hidden) ensureAnchored();
    };

    map.on("zoomstart", handleZoomStart);
    map.on("zoomanim", handleZoomAnim);
    map.on("zoomend", handleZoomEnd);
    map.on("moveend", handleMoveEnd);
    map.on("resize", handleResize);
    map.on("move", ensureAnchored);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      map.off("zoomstart", handleZoomStart);
      map.off("zoomanim", handleZoomAnim);
      map.off("zoomend", handleZoomEnd);
      map.off("moveend", handleMoveEnd);
      map.off("resize", handleResize);
      map.off("move", ensureAnchored);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [map]);

  // Hover detection and snap — uses hitTest for unified logic.
  useEffect(() => {
    const canHover = window.matchMedia("(hover: hover)").matches;
    if (!canHover) return;

    const handleMouseMove = (e: L.LeafletMouseEvent) => {
      if (hoverRafRef.current) return;
      hoverRafRef.current = requestAnimationFrame(() => {
        hoverRafRef.current = null;
        const data = graphDataRef.current;
        if (!data?.nEdges) {
          if (hoverTargetRef.current) {
            hoverTargetRef.current = null;
            setHoverTarget(null);
            redrawHoverHighlightRef.current();
          }
          onSnapRef.current?.(null);
          setCurrentSnap(null);
          return;
        }

        // A top-proposal icon owns the highlight while hovered (hierarchy rule
        // #1) — it sets the hover target itself, so just yield without clearing.
        if (overIndicatorRef.current) return;

        // The pinned modal and the route path (mid-waypoint grab) both suppress
        // the graph hover beneath them; clear any active card and yield. The
        // modal's body is pointer-events:none, so onMouseEnter (overModalRef)
        // only catches its controls — hit-test the cursor against the modal rect
        // so the whole card blocks hover, not just its buttons.
        const modalEl = pinnedModalElRef.current;
        const overModalBox = !!modalEl && (() => {
          const r = modalEl.getBoundingClientRect();
          const { clientX, clientY } = e.originalEvent;
          return clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom;
        })();
        if (overModalRef.current || overModalBox || suppressHoverRef.current) {
          // Over the route path we'd normally blank the card — but an on-path top
          // proposal is passthrough (the path takes its pointer), so it can't show
          // its own card. If the cursor resolves to a top-proposal edge here, show
          // that card anyway (this is how hovering an auto-selected proposal lights
          // up). The post-click guard still suppresses it for a beat after a tap.
          let proposalTarget: HoverTarget | null = null;
          if (suppressHoverRef.current && !overModalRef.current && !overModalBox
              && !graphDraggingRef.current && !isHoverSuppressed()) {
            const sel = resolveDragSnapRef.current(e.latlng.lat, e.latlng.lng);
            if (sel?.target?.kind === "edge"
                && winnersRef.current.some((w) => w.edgeIdx === sel.target.index)) {
              proposalTarget = sel.target;
            }
          }
          if (proposalTarget) {
            const prev = hoverTargetRef.current;
            if (!prev || prev.kind !== "edge" || prev.index !== proposalTarget.index) {
              hoverTargetRef.current = proposalTarget;
              setHoverTarget(proposalTarget);
              redrawHoverHighlightRef.current();
            }
            setTooltipPos({ x: e.originalEvent.clientX, y: e.originalEvent.clientY });
          } else if (hoverTargetRef.current) {
            hoverTargetRef.current = null;
            setHoverTarget(null);
            redrawHoverHighlightRef.current();
          }
          return;
        }

        const dragging = graphDraggingRef.current;

        // During drag: suppress hover highlight and tooltip, but still compute
        // snap below. Otherwise resolve the hover target through the SAME
        // hierarchy a click's snap uses — sticky proposal first (a click in a
        // proposal's snap annulus links to it, so hover rings the proposal's
        // edge exactly like the drag drop-preview), then the shared resolver,
        // which ALWAYS resolves to the closest node/edge (block-constrained
        // over a polygon) — hover shows exactly what a click would select,
        // and no point on the map is a dead zone.
        let newTarget: HoverTarget | null = null;
        if (!dragging) {
          const sticky = stickyProposalSnapRef.current(e.latlng.lat, e.latlng.lng);
          if (sticky) {
            newTarget = sticky.edgeIdx != null ? { kind: "edge", index: sticky.edgeIdx } : null;
          } else {
            const sel = resolveDragSnapRef.current(e.latlng.lat, e.latlng.lng);
            newTarget = sel?.target ?? null;
          }
        }

        // Don't hover the component that's already selected — its pinned modal
        // is showing, so a duplicate hover highlight/card adds nothing.
        const pinned = pinnedTargetRef.current;
        if (newTarget && pinned
            && newTarget.kind === pinned.kind && newTarget.index === pinned.index) {
          newTarget = null;
        }

        const prev = hoverTargetRef.current;
        const changed = !prev && newTarget
          || prev && !newTarget
          || (prev && newTarget && (prev.kind !== newTarget.kind || prev.index !== newTarget.index));

        if (changed) {
          hoverTargetRef.current = newTarget;
          setHoverTarget(newTarget);
          redrawHoverHighlightRef.current();
        }

        if (newTarget) {
          setTooltipPos({ x: e.originalEvent.clientX, y: e.originalEvent.clientY });
        }

        // Snap position: only compute when actually dragging (path-drag system
        // is the only consumer). Skipping when idle saves ~100k iterations
        // on every mousemove. Uses the shared resolver so the live trail
        // agrees with the committed drop (block-aware over polygons, closest
        // node/edge everywhere else — a drag always has a snap target).
        if (!dragging) return;

        // Sticky proposal snap wins: snap the live trail to the proposal midpoint
        // (desktop). Mirrors the registered snapFn so the trail and the committed
        // drop agree. The drop-target ring is driven separately by `dragPoint`.
        const sticky = stickyProposalSnapRef.current(e.latlng.lat, e.latlng.lng);
        if (sticky) {
          const snapPos = { lat: sticky.lat, lng: sticky.lng };
          onSnapRef.current?.(snapPos);
          setCurrentSnap(snapPos);
          return;
        }

        const hit = resolveDragSnapRef.current(e.latlng.lat, e.latlng.lng);
        if (hit) {
          const snapPos = { lat: hit.snapLat, lng: hit.snapLng };
          onSnapRef.current?.(snapPos);
          setCurrentSnap(snapPos);
        } else {
          // Fallback: nearest node out of range (top-1 by bbox distance —
          // for points that's exact). Index-only; no result while the index
          // is still building — this fires per mousemove during a drag, so a
          // brute-force node scan here would jank exactly like hitTest's.
          const nodeIdx = nodeIndexRef.current;
          let nearestIdx = -1;
          if (nodeIdx) {
            const candidates = nodeIdx.neighbors(e.latlng.lng, e.latlng.lat, 1);
            if (candidates.length > 0) nearestIdx = candidates[0];
          }
          if (nearestIdx >= 0) {
            const snapPos = { lat: nodeLat(data, nearestIdx), lng: nodeLon(data, nearestIdx) };
            onSnapRef.current?.(snapPos);
            setCurrentSnap(snapPos);
          } else {
            onSnapRef.current?.(null);
            setCurrentSnap(null);
          }
        }
      });
    };

    const handleMouseOut = () => {
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
        redrawHoverHighlightRef.current();
      }
      onSnapRef.current?.(null);
      setCurrentSnap(null);
    };

    map.on("mousemove", handleMouseMove);
    map.on("mouseout", handleMouseOut);
    return () => {
      map.off("mousemove", handleMouseMove);
      map.off("mouseout", handleMouseOut);
      if (hoverRafRef.current) cancelAnimationFrame(hoverRafRef.current);
    };
  }, [map]);

  // Touch devices fire an indicator's `mouseover` (which sets the hover card and
  // overIndicatorRef) but never the matching `mouseout`, so overIndicatorRef
  // stays `true` and permanently short-circuits the mousemove cleanup above —
  // leaving the hover card stuck on screen. A tap on the map itself (select,
  // deselect, or tap-away) is an unambiguous signal to drop stale hover state;
  // marker/modal clicks stop their own propagation, so this only fires for true
  // map taps. Hover devices re-derive the card on the next mousemove.
  useEffect(() => {
    const clearStaleHover = () => {
      overIndicatorRef.current = false;
      if (hoverRbtpRef.current) {
        hoverRbtpRef.current = null;
        setHoverRbtp(null);
        routeHighlightEdgesRef.current = null;
      }
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
      }
      redrawHoverHighlightRef.current();
    };
    map.on("click", clearStaleHover);
    return () => { map.off("click", clearStaleHover); };
  }, [map]);

  // Track pinned tooltip screen position on map pan/zoom
  // The last live map click, recorded verbatim. A pinned point whose coords
  // equal it was placed by an in-app click (plain clicks store the RAW click
  // latlng — the snap path only records drags), as opposed to a deep link /
  // history restore, whose coords come from URL parsing and can never be the
  // same doubles. The pinned effect uses this to tell interactive pins (hover
  // already told the user what they're selecting — honour it verbatim) from
  // link-derived pins (a bare lat/lng that may need proposal reconciliation).
  const lastMapClickRef = useRef<{ lat: number; lng: number } | null>(null);
  useEffect(() => {
    const onClick = (e: L.LeafletMouseEvent) => {
      lastMapClickRef.current = { lat: e.latlng.lat, lng: e.latlng.lng };
    };
    map.on("click", onClick);
    return () => { map.off("click", onClick); };
  }, [map]);

  const pinnedLat = pinnedPoint?.lat ?? null;
  const pinnedLng = pinnedPoint?.lng ?? null;

  useEffect(() => {
    if (pinnedLat === null || pinnedLng === null) {
      setPinnedScreenPos(null);
      pinnedTargetRef.current = null;
      // Clear any pending click-override. Clicking a proposal to set it as the
      // END pins an override here, but setting an end nulls the pinned point, so
      // this branch runs WITHOUT the consume-and-clear below. Left set, that
      // stale override later hijacks the single-start selection once the route is
      // deleted back down to one point (it would resolve to the old end's edge —
      // a phantom ring + modal on the wrong proposal).
      pinnedEdgeOverrideRef.current = null;
      pinnedLockRef.current = null;
      redrawHoverHighlightRef.current();
      return;
    }

    // Resolve through the unified hierarchy. An indicator click pre-sets the
    // edge via pinnedEdgeOverrideRef (single-shot), which dominates; otherwise
    // it's node-within-radius then nearest edge — same logic as hover.
    const hadOverride = pinnedEdgeOverrideRef.current != null;
    const prevTarget = pinnedTargetRef.current;
    // A point placed through the snap carries the snap's exact coordinate —
    // reuse its recorded resolution verbatim (see lastSnapSelectionRef).
    const lastSnap = lastSnapSelectionRef.current;
    const snapMatch = !hadOverride && lastSnap != null &&
      Math.abs(lastSnap.snapLat - pinnedLat) < 1e-9 &&
      Math.abs(lastSnap.snapLng - pinnedLng) < 1e-9;
    // Interactive = this point was placed by a live gesture in THIS session: a
    // drag that recorded its snap resolution, or a plain click whose raw coords
    // the map click listener recorded. Everything else (deep link, history
    // restore, address search) is link-derived.
    const lastClick = lastMapClickRef.current;
    const clickMatch = !hadOverride && lastClick != null &&
      lastClick.lat === pinnedLat && lastClick.lng === pinnedLng;
    const interactive = snapMatch || clickMatch;
    // Interactive pins resolve through the SAME gated resolver hover uses —
    // including its "nothing far from the graph" answer, where the click
    // placed a free waypoint and hover showed nothing, so nothing may pin.
    // Only link-derived pins use resolveSelection's always-resolve contract.
    const sel = snapMatch
      ? lastSnap
      : clickMatch
        ? resolveDragSnapRef.current(pinnedLat, pinnedLng)
        : resolveSelection(pinnedLat, pinnedLng, pinnedEdgeOverrideRef.current);
    pinnedEdgeOverrideRef.current = null;
    let target = sel?.target ?? null;
    dlog("cast", `pinned resolve @${pinnedLat.toFixed(5)},${pinnedLng.toFixed(5)}`,
      { hadOverride, snapMatch, clickMatch, target, voteEdgeId: sel?.voteEdgeId });

    // Drop the edge-lock the moment the point itself moves (a click/drag sets new
    // coords); a re-run at the SAME coords (vote tick / winners load) keeps it.
    const lock = pinnedLockRef.current;
    if (lock && (lock.lat !== pinnedLat || lock.lng !== pinnedLng)) {
      pinnedLockRef.current = null;
    }

    // Deep-link reconciliation: a shared link encodes only a lat/lng (the
    // proposal's edge midpoint), so without the click-time pinnedEdgeOverrideRef
    // a top proposal can resolve to a node (short edges at low zoom), the
    // reverse-direction twin edge, or a neighbor — none of which carry the
    // proposal's edge index, so the card reads as a regular proposal. Since the
    // shared point IS a proposal's midpoint, snap to the nearest proposal midpoint
    // within a tight threshold (URL coords are truncated to ~1m, so a few meters
    // cleanly catches it without hijacking unrelated links). The candidate set
    // includes every station on a station network, where each station IS a top
    // proposal even with no net-positive votes (so it isn't in `winners`).
    //
    // Skip when an override was set: an indicator click and the re-pin in
    // castProposalVote both name an EXACT edge to keep selected. A vote can
    // momentarily drop that edge out of the spaced `winners` list, so without
    // this guard isProposalTarget reads false and reconciliation re-snaps the
    // modal onto a neighbouring winner — the selection appears to jump to a
    // different proposal the instant you vote.
    //
    // Skip equally for INTERACTIVE pins (a live click or drag): hover already
    // told the user exactly what they are selecting — hover must equal the
    // pin, verbatim. Reconciliation is for link-derived pins only, where a
    // bare lat/lng is all the URL gives us: a point that was shared AS a
    // proposal midpoint should reconcile to that proposal even if the raw
    // resolve lands elsewhere. (Regression this guards against: clicking a
    // junction node within 8 m of a winner's midpoint hijacked the selection
    // onto the neighbouring street's proposal — flipped-name card, wrong
    // block highlighted.)
    const isProposalTarget = target?.kind === "edge"
      && (isStationNetwork || winners.some((w) => w.edgeIdx === target!.index));
    if (!hadOverride && !interactive && !isProposalTarget) {
      const snapped = nearestProposalEdgeIndex(pinnedLat, pinnedLng, 8);
      if (snapped !== null) target = { kind: "edge", index: snapped };
    }

    // Node → proposal-edge upgrade — link-derived pins only, same reasoning.
    // A shared link whose slat/slng IS a node's coords was probably minted from
    // a proposal view, so resolve the node to the edge owning its strongest
    // proposal (a node modal merges incident proposals but a vote lands on one
    // edge). An INTERACTIVE click on a node pins the node exactly as hovered —
    // its vote still lands on voteEdgeId (adjShortestInBlock) by the per-edge
    // storage rule; only the TARGET must match hover. Bare intersections stay
    // a node either way — their modal is an unvotable "No votes yet" preview.
    let didNodeUpgrade = false;
    if (!interactive && target?.kind === "node") {
      const up = strongestProposalEdgeForNode(target.index);
      if (up !== null) { target = { kind: "edge", index: up }; didNodeUpgrade = true; }
    }

    // Sticky reselection: each map click re-places the start waypoint (= the
    // pinned point), so clicking the map while a card is open re-resolves the
    // selection. Because the open card occludes its own icon, those clicks land
    // off-centre and would otherwise flip the target to an endpoint node or a
    // neighbouring edge, making the card appear to drift. If the new click stays
    // within STICKY_RESELECT_PX of the currently-pinned feature (point-to-segment
    // for edges), keep that feature. A direct indicator click (override) always
    // wins, so you can still jump to another proposal by tapping its icon.
    // TOUCH ONLY: on a hover-capable device an interactive click means hover
    // already showed the user the new target — keeping the old one would make
    // the pin contradict hover. Touch has no hover to honour, so sloppy re-taps
    // near the open card still stick to it there.
    const canHover = window.matchMedia("(hover: hover)").matches;
    if (!hadOverride && !(interactive && canHover) && prevTarget && target &&
        (prevTarget.kind !== target.kind || prevTarget.index !== target.index)) {
      const gd = graphDataRef.current;
      const onFeature = gd && (prevTarget.kind === "edge"
        ? projectOntoEdge(gd, prevTarget.index, pinnedLat, pinnedLng)
        : (prevTarget.index < gd.nNodes
            ? { lat: nodeLat(gd, prevTarget.index), lng: nodeLon(gd, prevTarget.index) }
            : null));
      if (onFeature) {
        const a = map.latLngToContainerPoint([onFeature.lat, onFeature.lng]);
        const b = map.latLngToContainerPoint([pinnedLat, pinnedLng]);
        if (Math.hypot(a.x - b.x, a.y - b.y) <= STICKY_RESELECT_PX) target = prevTarget;
      }
    }

    // Keep a locked proposal edge across same-coords re-runs (vote ticks). An
    // override (indicator click / castProposalVote repin) always wins, and the
    // lock only holds a still-valid edge — so a deep-link's first geometric
    // resolve can still UPGRADE to its proposal once winners arrive (no lock
    // yet), but after that the selection never drifts to a neighbour on a vote.
    const lockNow = pinnedLockRef.current;
    if (!hadOverride && lockNow && lockNow.edgeIdx < (graphDataRef.current?.nEdges ?? 0)
        && (target?.kind !== "edge" || target.index !== lockNow.edgeIdx)) {
      target = { kind: "edge", index: lockNow.edgeIdx };
    }

    pinnedTargetRef.current = target;
    // The probe records the FINAL target, after reconciliation/upgrade/sticky/
    // lock — what the card and highlight actually show. Recording the raw
    // resolution here once masked a hover≠pin hijack from the test harness.
    debugState("pinnedTarget", target);
    // Lock once settled on a proposal edge (override pin, a winner, or a node
    // upgraded to its strongest proposal edge), so subsequent vote-driven
    // re-resolves hold this exact edge. A node-upgrade target may not be a spaced
    // `winner`, so include it explicitly — else a vote tick could re-snap it.
    if (target?.kind === "edge"
        && (hadOverride || isStationNetwork || didNodeUpgrade
            || winners.some((w) => w.edgeIdx === target!.index))) {
      pinnedLockRef.current = { lat: pinnedLat, lng: pinnedLng, edgeIdx: target.index };
    }
    redrawHoverHighlightRef.current();

    // Anchor the modal to the resolved target's geometry (edge midpoint / node)
    // — NOT the raw click — via the shared targetLatLng helper, the same point
    // used for the card's React key. So the card sits on the feature's icon and
    // only moves when the feature itself changes. Falls back to the click only
    // if the target has no geometry.
    const targetAnchor = targetLatLng(target, graphDataRef.current);
    const anchorLat = targetAnchor?.lat ?? pinnedLat;
    const anchorLng = targetAnchor?.lng ?? pinnedLng;

    const update = () => {
      const pt = map.latLngToContainerPoint([anchorLat, anchorLng]);
      const rect = map.getContainer().getBoundingClientRect();
      setPinnedScreenPos({ x: rect.left + pt.x, y: rect.top + pt.y - 8 });
    };

    const throttledUpdate = () => {
      if (pinnedRafRef.current) return;
      pinnedRafRef.current = requestAnimationFrame(() => {
        pinnedRafRef.current = null;
        update();
      });
    };

    update();
    map.on("move", throttledUpdate);
    map.on("zoom", throttledUpdate);
    return () => {
      map.off("move", throttledUpdate);
      map.off("zoom", throttledUpdate);
      if (pinnedRafRef.current) {
        cancelAnimationFrame(pinnedRafRef.current);
        pinnedRafRef.current = null;
      }
    };
    // isHeatmapLoading re-triggers resolution once the graph finishes loading,
    // so a cold deep-link (point set before the graph arrives) still resolves.
    // `winners` re-triggers it so a deep-linked top proposal reconciles to its
    // winner edge once the winners list arrives (votes load after the graph).
    // `votesVersion` re-triggers it on a vote so a freshly promoted/demoted
    // proposal re-resolves (consuming the pinnedEdgeOverrideRef set in
    // castProposalVote) and the modal + icon reflect the new winner status.
  }, [map, pinnedLat, pinnedLng, resolveSelection, isHeatmapLoading, winners,
      isStationNetwork, nearestProposalEdgeIndex, strongestProposalEdgeForNode, votesVersion]);

  // Anchor the route-summary card to the midpoint of the route's MIDDLE path
  // edge, tracking pan/zoom like the pinned card. Active only for a full route
  // selection on a street map (start AND end; a lone point uses the pinned card
  // instead). isHeatmapLoading re-runs it once topology arrives, so a deep-linked
  // route (waypoints set before the graph loads) still gets its card.
  useEffect(() => {
    const topo = topologyRef.current;
    const ids = pathEdgeIds;
    const hasRoute = !isStationNetwork && startLat !== null && endLat !== null
      && !!ids && ids.length > 0 && !!topo;
    if (!hasRoute) { setRouteCardPos(null); return; }
    const midEdge = ids![Math.floor(ids!.length / 2)];
    if (midEdge >= topo!.nEdges) { setRouteCardPos(null); return; }
    const f = edgeFrom(topo!, midEdge), t = edgeTo(topo!, midEdge);
    const anchorLat = (nodeLat(topo!, f) + nodeLat(topo!, t)) / 2;
    const anchorLng = (nodeLon(topo!, f) + nodeLon(topo!, t)) / 2;

    const update = () => {
      const pt = map.latLngToContainerPoint([anchorLat, anchorLng]);
      const rect = map.getContainer().getBoundingClientRect();
      setRouteCardPos({ x: rect.left + pt.x, y: rect.top + pt.y - 8 });
    };
    const throttledUpdate = () => {
      if (routeCardRafRef.current) return;
      routeCardRafRef.current = requestAnimationFrame(() => {
        routeCardRafRef.current = null;
        update();
      });
    };
    update();
    map.on("move", throttledUpdate);
    map.on("zoom", throttledUpdate);
    return () => {
      map.off("move", throttledUpdate);
      map.off("zoom", throttledUpdate);
      if (routeCardRafRef.current) {
        cancelAnimationFrame(routeCardRafRef.current);
        routeCardRafRef.current = null;
      }
    };
  }, [map, pathEdgeIds, startLat, startLng, endLat, endLng, isStationNetwork, isHeatmapLoading]);

  // Collapse any open spread when the selection is CLEARED (modal "X" / "Clear").
  // Keyed only on the pinned point, so it fires on the transition to null — NOT on
  // every re-run of the resolve effect above (which also runs on votesVersion /
  // winners). Bundling it there collapsed a freshly-fanned cluster on the next
  // vote tick while you were still browsing it (pinnedPoint null = no selection),
  // which read as "the icons jump back to one point instead of fanning out."
  useEffect(() => {
    if (pinnedLat !== null && pinnedLng !== null) return;
    if (spreadRef.current) collapseSpreadRef.current();
  }, [pinnedLat, pinnedLng]);

  // -------------------------------------------------------------------------
  // Tooltip content — hover
  // -------------------------------------------------------------------------

  const data = graphDataRef.current;
  const legend = data?.vote_type_legend ?? [];
  let tooltipName = "";
  let hoverVoteTypes: VoteTypeRow[] = [];

  if (hoverTarget && data) {
    if (hoverTarget.kind === "edge") {
      if (hoverTarget.index < data.nEdges) {
        const fromIdx = edgeFrom(data, hoverTarget.index), toIdx = edgeTo(data, hoverTarget.index);
        const midLat = (nodeLat(data, fromIdx) + nodeLat(data, toIdx)) / 2;
        const midLng = (nodeLon(data, fromIdx) + nodeLon(data, toIdx)) / 2;

        tooltipName = edgeName(data, hoverTarget.index) || resolveAddress(midLat, midLng, bumpGeocode);
        // Block grain (docs §2.4): the card sums the deduped per-block counts
        // over the hovered edge's block; per-edge rows when blocks are absent.
        hoverVoteTypes = selectionVoteRows(data, [hoverTarget.index])
          ?? decodeVoteTypes((data.edge_vote_types ?? [])[hoverTarget.index], legend);
      }
    } else {
      if (hoverTarget.index < data.nNodes) {
        tooltipName = resolveAddress(nodeLat(data, hoverTarget.index), nodeLon(data, hoverTarget.index), bumpGeocode);
        // A node belongs to its shortest incident edge's block (docs §2.1) —
        // the edge whose midpoint sits inside the junction's own disc block.
        const hoverNodeEdge = adjShortest(data, nodeAdjRef.current, hoverTarget.index);
        hoverVoteTypes = (hoverNodeEdge != null ? selectionVoteRows(data, [hoverNodeEdge]) : null)
          ?? decodeVoteTypes((data.node_vote_types ?? [])[hoverTarget.index], legend);
      }
    }
    // A station carries its name on its self-edge (index == node index); use it
    // instead of a (disabled) reverse-geocode regardless of node/edge resolution.
    if (isStationNetwork) tooltipName = edgeName(data, hoverTarget.index) || tooltipName;
  }

  // -------------------------------------------------------------------------
  // Tooltip content — pinned (uses pinnedTargetRef from hitTest)
  // -------------------------------------------------------------------------

  let pinnedName = "";
  let pinnedVoteTypes: VoteTypeRow[] = [];
  // Edge the pinned modal votes on (node targets snap to a representative edge)
  // and the lat/lng used for the shareable copy-link.
  let pinnedVoteEdgeId: number | null = null;
  let pinnedPointLatLng: { lat: number; lng: number } | null = null;
  const pinnedTarget = pinnedTargetRef.current;
  const showPinned = pinnedPoint && pinnedScreenPos && pinnedTarget && data;

  if (showPinned) {
    if (pinnedTarget.kind === "edge") {
      if (pinnedTarget.index < data.nEdges) {
        const fromIdx = edgeFrom(data, pinnedTarget.index), toIdx = edgeTo(data, pinnedTarget.index);
        const midLat = (nodeLat(data, fromIdx) + nodeLat(data, toIdx)) / 2;
        const midLng = (nodeLon(data, fromIdx) + nodeLon(data, toIdx)) / 2;
        pinnedName = edgeName(data, pinnedTarget.index) || resolveAddress(midLat, midLng, bumpGeocode);
        // Block grain (docs §2.4): sum the deduped per-block counts over the
        // selection's touched block(s); per-edge rows when blocks are absent.
        pinnedVoteTypes = selectionVoteRows(data, [pinnedTarget.index])
          ?? decodeVoteTypes((data.edge_vote_types ?? [])[pinnedTarget.index], legend);
        pinnedVoteEdgeId = pinnedTarget.index;
      }
    } else {
      if (pinnedTarget.index < data.nNodes) {
        pinnedName = resolveAddress(nodeLat(data, pinnedTarget.index), nodeLon(data, pinnedTarget.index), bumpGeocode);
        pinnedVoteEdgeId = adjShortest(data, nodeAdjRef.current, pinnedTarget.index);
        // A node belongs to its vote edge's block (docs §2.1 shortest-incident rule).
        pinnedVoteTypes = (pinnedVoteEdgeId != null ? selectionVoteRows(data, [pinnedVoteEdgeId]) : null)
          ?? decodeVoteTypes((data.node_vote_types ?? [])[pinnedTarget.index], legend);
      }
    }
    // Anchor + share-link coord come from the SAME helper the position effect
    // uses, so the card's key and its on-screen anchor can never drift apart.
    pinnedPointLatLng = targetLatLng(pinnedTarget, data);
    // Stations carry their name on the self-edge (index == node index).
    if (isStationNetwork) pinnedName = edgeName(data, pinnedTarget.index) || pinnedName;
  }

  // Publish the resolved vote edge back to the host so the selected point's
  // voteEdgeId equals the modal's target. Only when it actually resolves (never
  // push null — a momentary unresolved frame would clobber a freshly-set edge);
  // clearing the selection nulls the point on the host side anyway.
  const onPinnedResolveRef = useRef(onPinnedResolve);
  useEffect(() => { onPinnedResolveRef.current = onPinnedResolve; }, [onPinnedResolve]);
  useEffect(() => {
    if (pinnedVoteEdgeId != null) onPinnedResolveRef.current?.(pinnedVoteEdgeId);
  }, [pinnedVoteEdgeId]);

  // Broadcast the covering blocks whenever the selection or hover changes.
  // pathEdgeIds covers the route selection, pinnedVoteEdgeId the pinned point
  // (incl. node→edge upgrades), hoverTarget the transient hover;
  // graphVoteVersion re-fires once topology+votes arrive so a deep link's
  // pre-load selection still lights up. Route-diamond hover (refs only, no
  // re-render) calls the dispatcher directly from activate/deactivate.
  useEffect(() => {
    dispatchBlockSelectRef.current();
  }, [pathEdgeIds, pinnedVoteEdgeId, hoverTarget, graphVoteVersion]);

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const mapContainer = map.getContainer();
  // The hover card shows for any hovered segment/node — top proposal or not —
  // but NOT for the proposal that's currently pinned/selected: that one already
  // has its interactive card, so its hover version is suppressed. Hover only
  // surfaces when it resolves to a *different* proposal. "Top proposal" is simply
  // whether the hovered edge is a current winner, so hover and select route
  // through the same ProposalCard with a `winner` flag.
  const hoverMatchesPinned =
    showPinned && pinnedTarget && hoverTarget &&
    hoverTarget.kind === pinnedTarget.kind && hoverTarget.index === pinnedTarget.index;
  const hoverWinner = (hoverTarget?.kind === "edge")
    ? winners.find(w => w.edgeIdx === hoverTarget.index) ?? null
    : null;
  // With a route selected, a NON-winner edge of that route gets no hover card:
  // the route-summary card already speaks for the selection, and cursor travel
  // along the corridor would otherwise pop a card per segment right on top of
  // it. Winner (top-proposal) edges keep their card — that's how hovering an
  // auto-selected proposal on the path lights up. Matched with direction twins
  // (the hover often resolves to the twin of the edge the route recorded).
  const hoverOnSelectedRoute = !hoverWinner && hoverTarget?.kind === "edge"
    && !!data && !!pathEdgeIds && pathEdgeIds.length > 0
    && expandSelectionToUndirected(data, pathEdgeIds, [hoverTarget.index]).has(hoverTarget.index);
  const showHoverTooltip = hoverTarget !== null && !hoverMatchesPinned && !hoverOnSelectedRoute;

  const pinnedWinner = (showPinned && pinnedTarget?.kind === "edge")
    ? winners.find(w => w.edgeIdx === pinnedTarget.index) ?? null
    : null;

  // Safety net for a momentary winners/data desync: if an edge is a current
  // winner but its live breakdown decoded empty, show the winner's own proposal
  // so a top-proposal card never reads "No votes yet". Net support maps to
  // up (positive) so the row's net (up − down) matches the winner's count.
  if (hoverWinner && hoverVoteTypes.length === 0) {
    hoverVoteTypes = [{ label: hoverWinner.label, up: hoverWinner.count, down: 0 }];
  }
  if (pinnedWinner && pinnedVoteTypes.length === 0) {
    pinnedVoteTypes = [{ label: pinnedWinner.label, up: pinnedWinner.count, down: 0 }];
  }

  // -------------------------------------------------------------------------
  // Route-summary card content (docs §2.4: everything displays at block grain)
  // -------------------------------------------------------------------------

  // The selection's touched blocks, materialized once for the card (count +
  // the ± buttons' pressed state) and the route cast.
  const routeBlocks = useMemo(() => {
    void graphVoteVersion;
    const topo = topologyRef.current;
    if (!topo || !pathEdgeIds || pathEdgeIds.length === 0) return null;
    return materializeBlocks(topo, blockIndexRef.current, pathEdgeIds);
  }, [pathEdgeIds, graphVoteVersion, isHeatmapLoading]);

  // Vote rows over an edge set — block-grain (deduped per-block counts) when
  // the map has blocks, per-edge sums otherwise. Shared by the route-summary
  // card (the selection's path) and the diamond hover card (a corridor).
  const voteRowsForEdges = useCallback((edgeIds: readonly number[]): VoteTypeRow[] => {
    const d = graphDataRef.current;
    if (!d || edgeIds.length === 0) return [];
    const rows = selectionVoteRows(d, edgeIds as number[]);
    if (rows) return rows;
    const legendNow = d.vote_type_legend ?? [];
    const sums = new Map<string, { up: number; down: number }>();
    for (const eid of edgeIds) {
      for (const [li, up, down] of (d.edge_vote_types ?? [])[eid] ?? []) {
        const label = legendNow[li];
        if (!label) continue;
        const cur = sums.get(label) ?? { up: 0, down: 0 };
        cur.up += up ?? 0;
        cur.down += down ?? 0;
        sums.set(label, cur);
      }
    }
    return [...sums.entries()]
      .map(([label, v]) => ({ label, ...v }))
      .sort((a, b) => (b.up - b.down) - (a.up - a.down));
  }, []);

  const routeVoteRows = useMemo<VoteTypeRow[]>(() => {
    void graphVoteVersion;
    void votesVersion;
    if (!pathEdgeIds) return [];
    return voteRowsForEdges(pathEdgeIds);
  }, [pathEdgeIds, voteRowsForEdges, graphVoteVersion, votesVersion, isHeatmapLoading]);

  // Rows for the HOVERED diamond's corridor (its block-edge union). Same
  // safety net as the PBTP hover card: a live top proposal never reads
  // "No votes yet" even if the breakdown momentarily decodes empty.
  const hoverRbtpRows = useMemo<VoteTypeRow[]>(() => {
    void graphVoteVersion;
    void votesVersion;
    if (!hoverRbtp) return [];
    const rows = voteRowsForEdges(hoverRbtp.blockEdgeIds);
    return rows.length ? rows : [{ label: hoverRbtp.label, up: hoverRbtp.score, down: 0 }];
  }, [hoverRbtp, voteRowsForEdges, graphVoteVersion, votesVersion]);

  // Server-truth rows for the route card: DISTINCT devices per (vote type,
  // direction) across the selection's block edges (/api/route-votes). A route
  // cast fans ONE device's vote onto every edge of every block it covers, so the
  // local per-block sums above count the same person once per block — these
  // rows count them once, period. Local rows stand in until (or if never — no
  // DB in some dev setups) the fetch resolves. Refetched, debounced, on every
  // vote signal (own casts via votesVersion, everyone else's via the
  // graphVoteVersion delta bump).
  const [routeUniqueRows, setRouteUniqueRows] = useState<VoteTypeRow[] | null>(null);
  useEffect(() => { setRouteUniqueRows(null); }, [pathEdgeIds]);
  useEffect(() => {
    if (!routeBlocks || routeBlocks.length === 0) {
      setRouteUniqueRows(null);
      return;
    }
    // Union of the touched blocks' edges, capped to keep the request bounded
    // (a merged foot-component block can hold thousands of edges; past the cap
    // the count degrades gracefully toward the per-block numbers).
    const edgeIds: number[] = [];
    const seen = new Set<number>();
    for (const block of routeBlocks) {
      for (let i = 0; i < block.length; i++) {
        const e = block[i];
        if (!seen.has(e)) {
          seen.add(e);
          edgeIds.push(e);
          if (edgeIds.length >= ROUTE_VOTES_EDGE_CAP) break;
        }
      }
      if (edgeIds.length >= ROUTE_VOTES_EDGE_CAP) break;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetch(`${CONFIG.apiUrl}/route-votes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...passcodeHeaders() },
        body: JSON.stringify({ map: getMapSlug(), edge_ids: edgeIds }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          if (cancelled || !j?.rows) return;
          setRouteUniqueRows(j.rows as VoteTypeRow[]);
        })
        .catch(() => {});
    }, ROUTE_VOTES_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [routeBlocks, votesVersion, graphVoteVersion]);

  // The RBTP the current selection stands for, if any — it becomes the card's
  // header, so selecting a diamond (or manually tracing its corridor) titles
  // the summary with that proposal. Mirrors the diamonds' own selected rule:
  // block coverage (twin-expanded) first, else the explicitly-tapped RBTP
  // whose anchors are still waypoints.
  const coveredRouteProposal = useMemo(() => {
    void isHeatmapLoading;
    const topo = topologyRef.current;
    if (!topo || !pathEdgeIds || pathEdgeIds.length === 0 || routeProposals.length === 0) return null;
    const sel = expandSelectionToUndirected(
      topo, pathEdgeIds, routeProposals.flatMap((p) => p.blockEdgeIds));
    const byCoverage = routeProposals.find((p) => isRouteCovered(p.blocks, sel)) ?? null;
    if (byCoverage) return byCoverage;
    const tapped = routeProposals.find((p) => p.id === selectedRbtpId) ?? null;
    return tapped && anchorsAreWaypoints(tapped) ? tapped : null;
  }, [pathEdgeIds, routeProposals, isHeatmapLoading, selectedRbtpId, anchorsAreWaypoints]);

  // geocodeVersion used to re-render when async geocode completes
  void geocodeVersion;

  // Build indicator markers for the top-voted segment per vote type.
  // Position = edge midpoint. Hover/click sets the same hoverTarget the
  // map mousemove handler would, so the existing tooltip lights up.
  // Drop the .votes-spreading transition class once nothing is fanned out —
  // deferred so the snap-back of the last cluster still animates.
  const scheduleSpreadClassClear = useCallback(() => {
    const container = map.getContainer();
    window.setTimeout(() => {
      if (!spreadRef.current) container.classList.remove("votes-spreading");
    }, SPREAD_ANIM_MS);
  }, [map]);

  // Cancel the snap-back timer (no-op if none is armed).
  const clearSpreadTimer = useCallback(() => {
    if (spreadTimeoutRef.current) {
      clearTimeout(spreadTimeoutRef.current);
      spreadTimeoutRef.current = null;
    }
  }, []);

  // Collapse the open spread (locked or transient) and snap every icon back. The
  // snap-back timer, pan/zoom, and deselect all funnel through here.
  const collapseSpread = useCallback(() => {
    clearSpreadTimer();
    spreadLockedRef.current = false;
    applySpread(null);
    scheduleSpreadClassClear();
  }, [clearSpreadTimer, applySpread, scheduleSpreadClassClear]);
  useEffect(() => { collapseSpreadRef.current = collapseSpread; }, [collapseSpread]);

  // (Re)arm the snap-back countdown for a TRANSIENT (not yet locked) spread.
  // Paused while the cursor rests on one of its fanned icons — square or
  // diamond — and restarted when it leaves, so the cluster stays open as long
  // as you hover it. A locked spread has no timer.
  const armSpreadTimer = useCallback(() => {
    clearSpreadTimer();
    spreadTimeoutRef.current = window.setTimeout(collapseSpread, SPREAD_DURATION_MS);
  }, [clearSpreadTimer, collapseSpread]);

  // Panning or zooming collapses the open spread (including a locked, persisted
  // one) — the fanned grid was laid out for a fixed screen anchor, so moving the
  // map is the natural "I'm done with this cluster" gesture.
  useEffect(() => {
    const collapse = () => {
      if (spreadRef.current) collapseSpread();
    };
    map.on("zoomstart", collapse);
    map.on("dragstart", collapse);
    return () => {
      map.off("zoomstart", collapse);
      map.off("dragstart", collapse);
    };
  }, [map, collapseSpread]);

  // Edge index of the currently selected top proposal (if any), so its icon
  // can float above the others.
  const selectedEdgeIdx =
    pinnedTarget?.kind === "edge" ? pinnedTarget.index : null;

  // Indicators show at every zoom; ease them smaller at BOTH extremes — as you
  // zoom IN so they don't dominate street detail, and as you zoom OUT so a dense
  // field of them doesn't blanket the city. Full size only around the mid zooms
  // (~14–15). Driven via a CSS var on the map container (read by
  // .vote-type-indicator) so zooming never rebuilds icons.
  useEffect(() => {
    const zoomedIn = Math.max(0, currentZoom - 15) * 0.1;
    const zoomedOut = Math.max(0, 14 - currentZoom) * 0.1;
    const scale = Math.max(0.5, Math.min(1, 1 - zoomedIn - zoomedOut));
    map.getContainer().style.setProperty("--indicator-scale", String(scale));
  }, [currentZoom, map]);

  // Touch devices have no hover and never fire a reliable `mouseout`, so an
  // indicator's `mouseover`-driven hover card would stick after a tap (the
  // map-level hover handler is likewise gated on this). Mirror that gate here.
  const canHover = useMemo(() => window.matchMedia("(hover: hover)").matches, []);

  // Tracks that the matched-waypoint hover effect (below) owns the current card,
  // so releasing that hover only clears its own card.
  const bannerFromWaypointRef = useRef(false);

  // Hovering a matched waypoint's kite (its proposal indicator is passthrough)
  // lights that proposal's hover card — the same one the indicator's own
  // `mouseover` would. We set overIndicatorRef so the map mousemove yields
  // (hierarchy #1) instead of clearing the card as the cursor drifts. bannerRef
  // tracks that WE own the card, so releasing the hover only clears our own.
  // isHoverSuppressed() gates it through the shared "tap turns hover off" guard.
  useEffect(() => {
    if (!canHover) return;
    const p = hoverProposalPoint;
    if (isHoverSuppressed()) return;
    if (!p) {
      if (!bannerFromWaypointRef.current) return;
      bannerFromWaypointRef.current = false;
      overIndicatorRef.current = false;
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
        redrawHoverHighlightRef.current();
      }
      return;
    }
    const match = proposalMatchFor(p.lat, p.lng, matchThresholdM);
    if (!match) return;
    bannerFromWaypointRef.current = true;
    overIndicatorRef.current = true;
    const iconPt = map.latLngToContainerPoint([p.lat, p.lng]);
    const rect = map.getContainer().getBoundingClientRect();
    setTooltipPos({ x: rect.left + iconPt.x, y: rect.top + iconPt.y });
    const target: HoverTarget = { kind: "edge", index: match.edgeIdx };
    hoverTargetRef.current = target;
    setHoverTarget(target);
    redrawHoverHighlightRef.current();
  }, [hoverProposalPoint, proposalMatchFor, matchThresholdM, canHover, map]);

  // ===========================================================================
  // TOP-PROPOSAL INTERACTION MODEL  (read this before touching the markers)
  // ===========================================================================
  // A top proposal is the winning edge for a vote type, drawn as an icon. The
  // SAME icon serves three roles depending on the route, and each role hands the
  // pointer to a different piece — so the gesture is always handled by exactly
  // one thing, with no double-handling:
  //
  //   1. BROWSE  (no route through it). Interactive. Hover → its card. Click →
  //      select it + start a route from it. Crowded → fan the cluster out.
  //      Handled HERE (IndicatorMarker.onClick → handleClick).
  //
  //   2. ON-PATH (the route passes through it, but it isn't a waypoint —
  //      `onPathEdgeSet`). PASSTHROUGH (pointer-events:none) so the route polyline
  //      underneath owns the gesture: drag it → ghost mid + dotted trail → insert
  //      a mid (drop it back on the proposal and the mid links to it = "upgrade");
  //      tap it → restart from here. Both come from usePathDrag (tap vs drag), NOT
  //      from this file. Its hover card comes from the map-mousemove override (a
  //      passthrough icon can't fire its own mouseover).
  //
  //      back on the proposal upgrades it). A drag can land on a FANNED-OUT icon
  //      too: proposalIconAt hit-tests at each icon's display position (its grid
  //      cell when spread) and snaps to the real edge midpoint, so exploded icons
  //      are drop targets exactly like settled ones — no separate path.
  //
  //   3. MATCHED (a start/end/mid waypoint sits on it — `waypointMatch`).
  //      PASSTHROUGH; the kite RouteMarker the host renders underneath is the
  //      handle: click → restart, drag → move the waypoint. Removal is the [×]
  //      badge baked into the icon (`removeEdge`), which the delegated capture
  //      handler turns into `onRemoveProposal` — same in the settled and exploded
  //      states, since the badge rides inside the icon. Its hover card comes from
  //      `hoverProposalPoint` (the kite reports its hover).
  //
  // Cross-cutting rules:
  //   - Tap vs drag is TIME-based everywhere (utils/gesture.ts, TAP_MAX_MS), so a
  //     marker tap (RouteMarker) and a path tap (usePathDrag) feel identical.
  //   - A crowded stack fans out BEFORE any side effect: browse clicks do it in
  //     handleClick; path taps do it via `clusterExploderRef` (see MapView), then
  //     the picked fanned icon (interactive while spread) runs its action.
  //   - Hover cards honor isHoverSuppressed() (the shared `hover-off` guard) so a
  //     tap never strands a card; mobile additionally has no `canHover`.
  //   - Edge case: an instant client-side route split must inherit the segment's
  //     edge ids (RouteContext.insertWaypointAtSegment), or the on-path highlight
  //     AND the vote target both vanish.
  // ===========================================================================
  // CLUSTER FAN-OUT — shared by BOTH proposal kinds (PBTP squares and RBTP
  // diamonds). The clusterables list, cluster detection, the grid fan-out, and
  // the explode gate live HERE, outside the marker memos, because each marker
  // memo early-returns when its own kind is absent: this machinery's old home
  // (the PBTP memo below) skipped installing the exploder whenever there were
  // no point-based winners, so a stack of only route diamonds couldn't be
  // clicked open — and after winners emptied (e.g. a theme switch resets them)
  // a STALE exploder with the old positions lingered on the refs. This memo
  // runs whenever either kind exists and re-assigns the refs unconditionally,
  // so both kinds always share one fresh exploder.
  const clusterEngine = useMemo(() => {
    const topology = topologyRef.current;

    // The squares to render, resolved to their edge midpoints once up front so
    // click handlers can measure on-screen distance between icons for cluster
    // detection. Station networks: EVERY point as a permanent marker (a
    // self-edge per station), all sharing one icon — "as though they were top
    // proposals"; the legendIdx (== edge index) keys the marker. Streets: the
    // vote winners. The PBTP memo below renders from this same list.
    const placed: Array<{ w: VoteTypeWinner; midLat: number; midLng: number }> = [];
    // Everything that participates in a fan-out — the PBTP squares AND the RBTP
    // diamonds (routeIndicatorMarkers) — keyed by its spread key at its settled
    // display position. Both kinds cluster and explode TOGETHER: a stack of
    // mixed pins fans out as one grid.
    const clusterables: Array<{ key: string; lat: number; lng: number }> = [];
    if (topology) {
      const source: VoteTypeWinner[] = isStationNetwork
        ? Array.from({ length: topology.nEdges }, (_, i) => ({ legendIdx: i, label: stationLabel, edgeIdx: i, count: 0 }))
        : winners;
      for (const w of source) {
        if (w.edgeIdx >= topology.nEdges) continue;
        const fromIdx = edgeFrom(topology, w.edgeIdx), toIdx = edgeTo(topology, w.edgeIdx);
        const midLat = (nodeLat(topology, fromIdx) + nodeLat(topology, toIdx)) / 2;
        const midLng = (nodeLon(topology, fromIdx) + nodeLon(topology, toIdx)) / 2;
        placed.push({ w, midLat, midLng });
        clusterables.push({ key: spreadKeyEdge(w.edgeIdx), lat: midLat, lng: midLng });
      }
      for (const p of routeProposals) {
        const [lat, lng] = rbtpDisplayPos(topology, p);
        clusterables.push({ key: spreadKeyRoute(p.id), lat, lng });
      }
    }

    // The crowded proposal cluster around a screen anchor: the 2+ icons within
    // CLUSTER_RADIUS_PX of it. Shared by both explode entry points (browse click
    // and path/drag tap), so cluster detection lives in exactly one place.
    const clusterAround = (anchor: L.Point) =>
      clusterables.filter((m) => {
        const p = map.latLngToContainerPoint([m.lat, m.lng]);
        const dx = p.x - anchor.x, dy = p.y - anchor.y;
        return dx * dx + dy * dy <= CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX;
      });

    // Fan a cluster of icons out into a centered grid of cells around their shared
    // anchor point, REPLACING any currently-open spread (only one cluster is
    // fanned out at a time, so a fresh fanout collapses whatever was open), then
    // schedule the snap-back. The new spread is transient until a box is picked.
    const spreadCluster = (
      members: typeof clusterables,
      anchor: L.Point,
    ) => {
      const cols = Math.ceil(Math.sqrt(members.length));
      const rows = Math.ceil(members.length / cols);
      const next: SpreadMap = new Map();
      members.forEach((m, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const offsetX = (col - (cols - 1) / 2) * SPREAD_CELL_PX;
        const offsetY = (row - (rows - 1) / 2) * SPREAD_CELL_PX;
        const ll = map.containerPointToLatLng([
          anchor.x + offsetX,
          anchor.y + offsetY,
        ]);
        next.set(m.key, [ll.lat, ll.lng]);
      });

      map.getContainer().classList.add("votes-spreading");
      spreadLockedRef.current = false;
      applySpread(next);
      armSpreadTimer();
    };

    // A point's "is there a crowded proposal stack here?" gate, shared by taps
    // (path/kite) and the drag-hover effect. Returns true when the point sits over
    // a cluster of 2+ proposal icons — which both EXPLODES the stack (when nothing
    // is fanned yet) and CONSUMES the gesture. Consuming is the key: a tap over a
    // cluster must NEVER fall through to placing/moving a start — picking one
    // proposal happens only by tapping a fanned-out icon. Returns false only when
    // there's no crowded cluster here, so the host runs its normal tap action
    // (e.g. place a start on empty map / a lone proposal).
    const explodeClusterAt = (latlng: { lat: number; lng: number }): boolean => {
      const tapPt = map.latLngToContainerPoint([latlng.lat, latlng.lng]);
      let anchor: L.Point | null = null;
      let nearestSq = Infinity;
      for (const m of clusterables) {
        const p = map.latLngToContainerPoint([m.lat, m.lng]);
        const d = (p.x - tapPt.x) ** 2 + (p.y - tapPt.y) ** 2;
        if (d < nearestSq) { nearestSq = d; anchor = p; }
      }
      if (!anchor || nearestSq > CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX) return false;
      const cluster = clusterAround(anchor);
      if (cluster.length <= 1) return false;
      // Explode this cluster UNLESS it's already the open one — so a drag calling
      // this every frame doesn't re-fan the cluster it's already hovering.
      const alreadyOpen = cluster.some((m) => spreadRef.current?.has(m.key));
      if (!alreadyOpen) spreadCluster(cluster, anchor);
      return true;
    };
    // Internal ref: the route-diamond markers run the same gate on their own
    // clicks (a diamond in a crowded stack fans out instead of selecting); the
    // prop ref hands the SAME gate to the host for path taps and drag-hover.
    internalExploderRef.current = explodeClusterAt;
    if (clusterExploderRef) clusterExploderRef.current = explodeClusterAt;

    return { placed, clusterAround, spreadCluster };
    // isHeatmapLoading re-runs this once topology+votes arrive (topology is
    // read off a ref); isStationNetwork/stationLabel select the square source.
  }, [winners, routeProposals, map, applySpread, armSpreadTimer, clusterExploderRef,
      isStationNetwork, stationLabel, isHeatmapLoading]);

  // ===========================================================================
  // POINT-BASED top proposals — "PBTPs" (square pins): one hot edge per pin,
  // selected by topProposals.selectTopProposals. Their ROUTE-based counterpart
  // (RBTPs, the corridor diamonds) renders in routeIndicatorMarkers below;
  // terminology in docs/three-layer-model.md §3.1.
  const indicatorMarkers = useMemo(() => {
    const topology = topologyRef.current;
    if (!topology) return null;

    // The squares at their edge midpoints, from the shared cluster engine. A
    // winner that hosts a waypoint stays drawn (it's the fixed pin), just tinted
    // + click-through (see the icon opts below).
    const { placed, clusterAround, spreadCluster } = clusterEngine;
    if (placed.length === 0) return null;

    // Per-proposal "heat": the RANKING of the visible winners, spread across
    // the map's heat spectrum — dense rank over distinct vote counts (ties
    // share a color), lowest → just above the ramp's cold end, hottest → 1.
    // Ranking (not log-normalized counts) is the scale, so every top proposal
    // gets its own hue off the ramp instead of the pack clustering wherever
    // the vote distribution piles up. The color sample is capped at
    // HEAT_PEAK_POS: the hottest pin wears the ramp's named upper extreme
    // (peak), never the incandescent tip — bright is the heatmap's job. The
    // bucketed color keys the icon cache so equally-hot pins of a label still
    // share one divIcon (and so a re-render doesn't churn setIcon every frame).
    // buildPinRampStops: the heat ramp on dark basemaps, but accent-anchored on
    // light ones, whose multiply ramps read as greys on a pin ring.
    const rampStops = buildPinRampStops(mapStyle);
    const rankedCounts = Array.from(new Set(placed.map((m) => m.w.count).filter((c) => c > 0)))
      .sort((a, b) => a - b);
    const heatOf = (count: number): number =>
      count > 0 && rankedCounts.length > 0
        ? (rankedCounts.indexOf(count) + 1) / rankedCounts.length
        : 0;

    return placed.map(({ w, midLat, midLng }) => {
      const override = spread?.get(spreadKeyEdge(w.edgeIdx));
      const posLat = override ? override[0] : midLat;
      const posLng = override ? override[1] : midLng;

      // This proposal's role in the route, if any. A linked proposal is the FIXED
      // visual for its kite waypoint: tinted by role (start=teal, end=red,
      // mid=white/black ring) and — on street maps — click-through (`passthrough`,
      // interactive:false) so the kite RouteMarker underneath takes the drag/click.
      // Stations keep their indicator interactive (it IS the selection).
      const role: "start" | "end" | "mid" | null =
        w.edgeIdx === startEdgeIdx ? "start"
        : w.edgeIdx === endEdgeIdx ? "end"
        : midEdgeSet.has(w.edgeIdx) ? "mid"
        : null;
      // A proposal the route merely passes through (not a waypoint): highlight it
      // and make it click-through, like a waypoint pin — but it stays out of the
      // sequence (no [x], not addable). Click-through lets the path beneath it be
      // dragged (a drag pulls out a ghost mid with a dotted trail; dropping it
      // back on the proposal upgrades it), while a plain tap on the path restarts.
      // A fanned-out icon (spread override) sits off the path at its grid cell, so
      // it can't be click-through — it must take its own click to be pickable.
      const onPath = role === null && onPathEdgeSet.has(w.edgeIdx);
      const passthrough = (role !== null || onPath) && !isStationNetwork && !override;
      const tint: "start" | "end" | null =
        role === "start" ? "start" : role === "end" ? "end" : null;
      // White/black ring: a mid waypoint's pin, an on-path proposal, the drag's
      // drop-target affordance, or the pinned selection. (Same `is-selected`.)
      const isSelected =
        role === "mid" || onPath || w.edgeIdx === dropTargetEdgeIdx || w.edgeIdx === selectedEdgeIdx;
      const isSpread = !!override;
      // A matched waypoint (start/end/mid) in route mode carries a remove [×]
      // badge baked into its icon — pinned to the corner, so it scales and fans
      // out WITH the icon, with no separate marker. Clicking it drops the
      // waypoint via the delegated handler above. Same in both the normal and
      // exploded states (the badge is part of the icon, not a parallel overlay).
      const removeEdge = role !== null && isRouteMode ? w.edgeIdx : null;
      // Selected/tinted/linked/spread/removable icons are built fresh (a handful
      // at a time, the memo rebuilds on change); the rest use the shared per-label
      // cache. Spread icons drop the locating divot (the grid cell isn't the real
      // spot); removable icons can't be cached (the badge stamps an edge id).
      // Heat glow/tint, scaled by this proposal's votes. A tinted role (start/end
      // teal/red) owns the pin's color, so skip heat there to avoid fighting it;
      // otherwise even selected/on-path pins keep their warmth.
      const heat = tint ? 0 : heatOf(w.count);
      const heatColor = heat > 0 ? sampleHeatRamp(rampStops, heat * HEAT_PEAK_POS) : undefined;
      const heatBucket = Math.round(heat * 12);
      let icon: L.DivIcon;
      if (isSelected || tint || isSpread || passthrough || removeEdge !== null) {
        icon = makeVoteTypeIcon(w.label, theme.suggestions, { selected: isSelected, tint, square: isSpread, passthrough, removeEdge, heat, heatColor });
      } else {
        const cacheKey = `${w.label}|${heatBucket}`;
        icon = iconCacheRef.current.get(cacheKey) ?? makeVoteTypeIcon(w.label, theme.suggestions, { heat, heatColor });
        iconCacheRef.current.set(cacheKey, icon);
      }

      const activateIndicator = () => {
        // Hovering the icon sets the same hoverTarget the map mousemove would,
        // anchoring the hover card near the icon. The card derives "top proposal"
        // from this edge being a winner, so the markup matches a segment hover.
        // overIndicatorRef tells the map hover handler to yield (hierarchy #1).
        overIndicatorRef.current = true;
        // Hovering an icon of the open (transient) cluster pauses its snap-back
        // timer; a locked cluster has no timer to pause.
        if (!spreadLockedRef.current && spreadRef.current?.has(spreadKeyEdge(w.edgeIdx))) clearSpreadTimer();
        // The hover card is a pointer-only affordance. On touch there's no
        // mouseout to clear it, so a tap would leave it stuck and resurface it
        // once the pinned point moves off this edge. The pinned modal is the
        // selection UI on touch, so skip the hover card entirely there.
        // isHoverSuppressed() likewise hides it for a beat after any tap/click
        // (the shared `hover-off` guard), so a tap doesn't strand a card.
        if (!canHover || isHoverSuppressed()) return;
        const target: HoverTarget = { kind: "edge", index: w.edgeIdx };
        const iconPt = map.latLngToContainerPoint([posLat, posLng]);
        const rect = map.getContainer().getBoundingClientRect();
        setTooltipPos({ x: rect.left + iconPt.x, y: rect.top + iconPt.y });
        hoverTargetRef.current = target;
        setHoverTarget(target);
        redrawHoverHighlightRef.current();
      };

      const deactivateIndicator = () => {
        overIndicatorRef.current = false;
        hoverTargetRef.current = null;
        setHoverTarget(null);
        redrawHoverHighlightRef.current();
        // Leaving an open (transient) cluster icon (re)starts its snap-back
        // countdown. A locked cluster has no timer, so its icons don't trigger one.
        if (!spreadLockedRef.current && spreadRef.current?.has(spreadKeyEdge(w.edgeIdx))) armSpreadTimer();
      };

      const handleClick = () => {
        // A press that turned into a mid-drag emits a trailing click — swallow it
        // so dragging a mid out of this proposal doesn't ALSO re-select it.
        if (proposalMidDraggedRef.current) return;
        // Station networks: the selected station is its own indicator (not a
        // RouteMarker), so clicking the already-selected one de-selects it —
        // matching the click-to-remove behavior proposals get on street maps.
        if (isStationNetwork && w.edgeIdx === selectedEdgeIdx) {
          onRemoveSelectedRef.current?.();
          return;
        }

        // Run cluster detection for any icon that isn't part of the current
        // spread (an icon with no override). This covers both the first click
        // and clicking a *different* crowded cluster while one is already open
        // — the latter re-explodes the new cluster (collapsing the old one, since
        // only one fans out at a time). Clicking a fanned-out icon (override set)
        // selects normally.
        if (!override) {
          const selfPt = map.latLngToContainerPoint([midLat, midLng]);
          const cluster = clusterAround(selfPt);
          // Crowded: swallow this click and fan the cluster out, replacing any
          // open spread.
          if (cluster.length > 1) {
            spreadCluster(cluster, selfPt);
            return;
          }
        }

        // Picking a fanned-out box LOCKS the open spread (cancel its timer so it
        // persists until deselect / pan / zoom). The position map is unchanged, so
        // no re-render is needed — only the lock flag flips. A non-fanned lone icon
        // collapses any open spread.
        if (override) {
          clearSpreadTimer();
          spreadLockedRef.current = true;
        } else {
          collapseSpread();
        }
        activateIndicator();
        pinnedEdgeOverrideRef.current = w.edgeIdx;
        // Smart default: selecting a top proposal makes its vote type the active
        // one, so the Cast +/- control acts on the proposal you just clicked.
        window.dispatchEvent(new CustomEvent("proposal-vote-type", {
          detail: { label: w.label, mode: themeMode },
        }));
        // Lock the start point to the icon's true edge midpoint, not its
        // temporary fanned-out grid cell — the spread offset is display-only.
        // Pass w.edgeIdx so the banner votes on this exact proposal edge (the
        // same one the modal pins via pinnedEdgeOverrideRef), not whatever the
        // midpoint re-snaps to.
        onIndicatorClickRef.current?.({ lat: midLat, lng: midLng }, w.edgeIdx);
      };

      return (
        <IndicatorMarker
          key={w.edgeIdx}
          position={[posLat, posLng]}
          icon={icon}
          // Stacking: a state BAND (far-apart bases) plus the proposal's net
          // votes as the in-band offset, so where pins overlap the more-supported
          // one sits on top — but explode/waypoint/selected still lift a pin into
          // a higher band regardless of votes. Bands are 100k apart and votes are
          // capped at 50k, so the vote offset can never bleed one band into the
          // next. Highest band first (route diamonds slot in between — see
          // routeIndicatorMarkers):
          //   500000+  fanned-out (spread override) — the exploded cluster is the
          //            disambiguation UI, so it reads as one group on top of
          //            EVERYTHING, route diamonds included.
          //   400000+  a route diamond covered by the selection (routeIndicator).
          //   300000+  a route diamond, uncovered (routeIndicator).
          //   200000+  a MATCHED waypoint (start/end/mid). It carries the [×]
          //            badge, so it must sit above any overlapping non-matched
          //            sibling — else the upper icon conceals its badge.
          //   100000+  the selected (open-modal) proposal.
          //     1000+  everything else, ordered by votes.
          zIndexOffset={
            (override ? 500000
             : role !== null ? 200000
             : w.edgeIdx === selectedEdgeIdx ? 100000
             : 1000)
            + Math.min(50000, Math.max(0, w.count))
          }
          onActivate={activateIndicator}
          onDeactivate={deactivateIndicator}
          onClick={handleClick}
          // Exploded + ON THE ROUTE (a matched start/end/mid OR an on-path
          // proposal) + a route exists → the icon doubles as a handle to drag a NEW
          // mid out of it (the icon stays put; a ghost + dotted trail anchored at the
          // real midpoint follow the cursor). This mirrors the NON-exploded case,
          // where the passthrough icon lets the press fall through to the path and
          // start the same mid-drag — an exploded icon is displaced off the path, so
          // it must start that drag itself instead of falling through (and stop the
          // press from reaching the map's pan). A plain tap still selects via onClick.
          onMidDragDown={
            override && (role !== null || onPath) && isRouteMode
              ? (e) => beginProposalMidDrag(
                  { lat: midLat, lng: midLng },
                  w.edgeIdx,
                  // Ghost color matches the waypoint being moved: teal start, red
                  // end; a mid / on-path drag uses the selection color (a new mid).
                  role === "start" ? COLOR_START : role === "end" ? COLOR_END : mapStyle.selection,
                  e,
                )
              : undefined
          }
        />
      );
    });
    // clusterEngine carries the placed squares (winners/stations) and re-runs
    // once topology+votes arrive, so stations appear even with zero votes.
  }, [clusterEngine, currentZoom, map, spread, collapseSpread, clearSpreadTimer, armSpreadTimer,
      selectedEdgeIdx, startEdgeIdx, endEdgeIdx, midEdgeSet, onPathEdgeSet, dropTargetEdgeIdx,
      isRouteMode, beginProposalMidDrag, mapStyle.selection, mapStyle.heat, mapStyle.basemap, isStationNetwork, canHover]);

  // Cast a directional vote on a single proposal (edge, vote type) through the
  // SAME unified path the top-bar route cast uses. castVotes() handles the
  // optimistic apply, the local store, reversal, toggle-off (clicking the
  // direction you already hold removes the vote), rollback, and the POST.
  const castProposalVote = useCallback((
    edgeId: number | null, label: string, newDir: VoteDirection,
  ) => {
    if (edgeId == null || !label) return;
    // A vote can promote this edge into (or out of) the top proposals. Pin the
    // selection to the voted edge so the reconciliation (which re-runs when
    // castVotes bumps votesVersion via the store) resolves to it exactly —
    // rather than snapping by geometry, which can miss the now-winner edge — so
    // the modal flips to/from "Top Proposal" and the freshly-promoted icon shows
    // selected. The optimistic store write fires synchronously inside castVotes,
    // so the version bump (and re-resolve) happen immediately, not on settle.
    pinnedEdgeOverrideRef.current = edgeId;
    const topo = topologyRef.current;
    castVotes({
      mode: themeMode, edgeIds: [edgeId], label, direction: newDir,
      // Block-scoped semantics (docs §4): the plan/unvote spans the touched
      // block; omitted (pre-topology) castVotes falls back to singletons.
      blocks: topo ? materializeBlocks(topo, blockIndexRef.current, [edgeId]) : undefined,
    });
  }, [themeMode]);

  // Cast a directional vote on the WHOLE route selection — the route-summary
  // card's ± buttons. Same unified castVotes path (and the same edge set +
  // block materialization) as the top-bar route cast, so pressed/unvote
  // semantics match wherever the vote is made from. The card passes an edgeId
  // only to enable its buttons; the cast targets every path edge.
  const castRouteVote = useCallback((
    _edgeId: number | null, label: string, newDir: VoteDirection,
  ) => {
    const ids = pathEdgeIdsRef.current;
    if (!ids || ids.length === 0 || !label) return;
    const topo = topologyRef.current;
    castVotes({
      mode: themeMode, edgeIds: Array.from(ids), label, direction: newDir,
      blocks: topo ? materializeBlocks(topo, blockIndexRef.current, ids) : undefined,
    });
  }, [themeMode]);

  // On map load, RESET this mode's local my-votes to the server's full snapshot
  // (no edge_ids = the device's every row on this map). localStorage survives
  // things the server doesn't (a dev DB reset, a resnap that shifted edge ids),
  // and a store claiming votes the server lacks makes blockCoverage read "all"
  // — silently turning the user's next cast into a block-unvote no-op. Server
  // truth wins outright on load, including deletions.
  useEffect(() => {
    let cancelled = false;
    const url = `${CONFIG.apiUrl}/my-votes?map=${encodeURIComponent(getMapSlug())}`
      + `&mode=${encodeURIComponent(themeMode)}`
      + `&voter_id=${encodeURIComponent(getVoterId())}`;
    const versionAtFetch = getVotesVersion();
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (cancelled || !j?.votes) return;
        if (getVotesVersion() !== versionAtFetch) return; // a cast raced us
        resetMapVotes(themeMode, j.votes as Record<string, Record<string, number>>);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [themeMode]);

  // Reconcile local "my votes" against the server when a proposal modal opens
  // (authoritative across devices). Keyed on the pinned edge so it refetches
  // whenever the selection changes. Requested at BLOCK breadth — every edge of
  // the selection's touched block(s), capped to keep the URL sane — so
  // blockCoverage sees votes this device cast on sibling edges from another
  // session, not just the pinned edge.
  useEffect(() => {
    if (pinnedVoteEdgeId == null) return;
    let cancelled = false;
    const topo = topologyRef.current;
    let edgeIds: number[] = [pinnedVoteEdgeId];
    if (topo) {
      const rest = materializeBlocks(topo, blockIndexRef.current, [pinnedVoteEdgeId])
        .flatMap((b) => Array.from(b))
        .filter((e) => e !== pinnedVoteEdgeId)
        .slice(0, MY_VOTES_EDGE_CAP - 1);
      edgeIds = [pinnedVoteEdgeId, ...rest];
    }
    const url = `${CONFIG.apiUrl}/my-votes?map=${encodeURIComponent(getMapSlug())}`
      + `&mode=${encodeURIComponent(themeMode)}&edge_ids=${edgeIds.join(",")}`
      + `&voter_id=${encodeURIComponent(getVoterId())}`;
    const versionAtFetch = getVotesVersion();
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (cancelled || !j?.votes) return;
        // Drop the response if any cast mutated the store while this fetch was
        // in flight: overwriting a fresh optimistic vote with the fetch-time
        // server state makes the NEXT press read stale coverage and flip into
        // a block-unvote (the +/−/+ "heat vanished" bug).
        if (getVotesVersion() !== versionAtFetch) return;
        // Apply the FULL response in one pass — it's authoritative my-vote
        // state for every edge it covers. reconcileEdge only persists/notifies
        // (bumping votesVersion, which re-renders the modal) on actual change.
        const votes = j.votes as Record<string, Record<string, number>>;
        for (const [eid, labels] of Object.entries(votes)) {
          reconcileEdge(themeMode, Number(eid), labels);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pinnedVoteEdgeId, themeMode]);

  // ===========================================================================
  // ROUTE-BASED top proposals — "RBTPs" (diamonds)
  // ===========================================================================
  // Terminology (docs/three-layer-model.md §3.1): the map surfaces two kinds of
  // top proposal —
  //   PBTP — POINT-based top proposal: one hot edge, square pin, computed by
  //          topProposals.selectTopProposals, rendered by indicatorMarkers above.
  //   RBTP — ROUTE-based top proposal: a hot CORRIDOR (simple path through the
  //          vote graph), diamond pin at its middle edge, computed by
  //          routeProposals.computeRouteProposals, rendered here.
  //
  // One diamond per corridor. Hovering lights the whole corridor; clicking
  // selects it FLAT OUT (the corridor replaces the route — start/end at its
  // anchors; only a drag-DROP threads it into an existing route). A diamond
  // reads selected when either
  //   (a) the live route covers every one of its blocks (the auto-select rule,
  //       twin-expanded), or
  //   (b) it's the explicitly-tapped RBTP and both anchors are still waypoints
  //       — see selectedRbtpId; OSRM's leg between the anchors rarely re-traces
  //       the corridor, so (a) alone left a tapped diamond looking unselected.
  const routeIndicatorMarkers = useMemo(() => {
    const topology = topologyRef.current;
    if (!topology || routeProposals.length === 0) return null;
    // Coverage is matched with direction twins included (a routed path often
    // traverses the twin of the edge a corridor's block recorded).
    const selectedSet = pathEdgeIds
      ? expandSelectionToUndirected(
          topology, pathEdgeIds, routeProposals.flatMap((p) => p.blockEdgeIds))
      : null;

    // Same heat treatment as the PBTP squares (rank-based spectrum within the
    // RBTP family, capped at HEAT_PEAK_POS — see indicatorMarkers) so the two
    // marker kinds read as one visual system: each diamond gets its own hue
    // off the map's ramp by ranking, the hottest wearing peak.
    const rampStops = buildPinRampStops(mapStyle);
    const rankedScores = Array.from(new Set(routeProposals.map((p) => p.score).filter((s) => s > 0)))
      .sort((a, b) => a - b);
    const heatOf = (score: number): number =>
      score > 0 && rankedScores.length > 0
        ? (rankedScores.indexOf(score) + 1) / rankedScores.length
        : 0;

    // Container points of the current waypoints (start/end/mids). A diamond
    // whose icon box overlaps a waypoint's icon box goes PASSTHROUGH: the
    // diamond outranks every settled icon (bands below), so left interactive it
    // would swallow the press meant for the waypoint's kite RouteMarker — the
    // "can't drag a start that sits on a proposal" bug. Passthrough hands the
    // gesture to the kite, exactly like a matched point pin does. Pan shifts
    // both projections by the same delta, so only zoom (a dep) re-runs this.
    const waypointPts: L.Point[] = [];
    if (startLat !== null && startLng !== null) waypointPts.push(map.latLngToContainerPoint([startLat, startLng]));
    if (endLat !== null && endLng !== null) waypointPts.push(map.latLngToContainerPoint([endLat, endLng]));
    for (const wp of ghostWaypointsRef.current) waypointPts.push(map.latLngToContainerPoint([wp.lat, wp.lng]));
    // Both icons share the pin geometry: 34×42 box, anchor at the tip (17,36).
    const overlapsWaypoint = (pt: L.Point) =>
      waypointPts.some((wp) => Math.abs(wp.x - pt.x) < 34 && Math.abs(wp.y - pt.y) < 42);

    return routeProposals.map((p) => {
      // Fanned-out grid cell when this diamond's cluster is exploded, else its
      // settled display spot (middle edge midpoint / anchor mean).
      const override = spread?.get(spreadKeyRoute(p.id));
      const [posLat, posLng] = override ?? rbtpDisplayPos(topology, p);

      const covered = (selectedSet ? isRouteCovered(p.blocks, selectedSet) : false)
        || (p.id === selectedRbtpId && anchorsAreWaypoints(p));
      // A live drag hovering this diamond → drop-target ring (dropping threads
      // the route through the whole corridor).
      const isDropTarget = p.id === dropTargetRbtpId;
      const dp = map.latLngToContainerPoint([posLat, posLng]);
      // A fanned-out diamond sits off its real spot in the disambiguation grid —
      // it must take its own click to be pickable, never passthrough.
      const passthrough = overlapsWaypoint(dp) && !override;
      // The [×] badge: this corridor is IN the route (its anchors are current
      // waypoints, inserted by the diamond's click or a drop onto it) — mirror
      // the matched point pin's removal affordance as closely as possible.
      const removable = isRouteMode && p.id === selectedRbtpId && anchorsAreWaypoints(p);
      const heat = heatOf(p.score);
      const heatColor = heat > 0 ? sampleHeatRamp(rampStops, heat * HEAT_PEAK_POS) : undefined;
      // Plain diamonds share one cached divIcon per label+heat bucket (same
      // scheme as the squares above): this memo re-runs on every zoom, and a
      // fresh icon object per run would setIcon (tear down + recreate the DOM
      // element) for every diamond on every zoom step. Stateful variants
      // (selected/passthrough/fanned/removable) are a handful and stay fresh.
      const stateful = covered || isDropTarget || passthrough || !!override || removable;
      let icon: L.DivIcon;
      if (stateful) {
        icon = makeVoteTypeIcon(p.label, theme.suggestions, {
          diamond: true, selected: covered || isDropTarget, passthrough,
          square: !!override, heat, heatColor,
          removeRoute: removable ? p.id : null,
        });
      } else {
        const cacheKey = `${p.label}|d${Math.round(heat * 12)}`;
        icon = iconCacheRef.current.get(cacheKey)
          ?? makeVoteTypeIcon(p.label, theme.suggestions, { diamond: true, heat, heatColor });
        iconCacheRef.current.set(cacheKey, icon);
      }

      const activate = () => {
        // The diamond owns the hover while the cursor is on it — same
        // hierarchy rule #1 as the point squares. Without this the map
        // mousemove keeps resolving whatever segment/block sits under the
        // icon, popping a competing street-segment card over the corridor
        // preview (the "diamond hover doesn't show the proposal" bug).
        overIndicatorRef.current = true;
        routeHighlightEdgesRef.current = routeBlockEdges(p);
        redrawHoverHighlightRef.current();
        dispatchBlockSelectRef.current();
        // Hovering a fanned diamond pauses the open cluster's snap-back, same
        // as a fanned square (a locked spread has no timer to pause).
        if (!spreadLockedRef.current && spreadRef.current?.has(spreadKeyRoute(p.id))) clearSpreadTimer();
        // Route-flavored hover card, anchored at the icon like a square's.
        // Pointer-only (touch has no mouseout to clear it) and suppressed for
        // a beat after any tap — both exactly as activateIndicator does.
        if (!canHover || isHoverSuppressed()) return;
        if (hoverTargetRef.current) {
          hoverTargetRef.current = null;
          setHoverTarget(null);
        }
        const iconPt = map.latLngToContainerPoint([posLat, posLng]);
        const rect = map.getContainer().getBoundingClientRect();
        setTooltipPos({ x: rect.left + iconPt.x, y: rect.top + iconPt.y });
        hoverRbtpRef.current = p;
        setHoverRbtp(p);
      };
      const deactivate = () => {
        overIndicatorRef.current = false;
        routeHighlightEdgesRef.current = null;
        redrawHoverHighlightRef.current();
        dispatchBlockSelectRef.current();
        hoverRbtpRef.current = null;
        setHoverRbtp(null);
        if (!spreadLockedRef.current && spreadRef.current?.has(spreadKeyRoute(p.id))) armSpreadTimer();
      };

      return (
        <IndicatorMarker
          key={`route-${p.id}`}
          position={[posLat, posLng]}
          icon={icon}
          // Route diamonds sit ABOVE every settled point icon (matched/selected/
          // browse) so a corridor is never buried under point pins; covered ones
          // outrank uncovered; the fanned-out spread (500000+) alone stays above
          // them (it's the explicit disambiguation gesture) — a fanned diamond
          // joins that top band with the fanned squares. Bands documented at
          // the point-icon zIndexOffset.
          zIndexOffset={(override ? 500000 : covered ? 400000 : 300000) + Math.min(48000, Math.max(0, p.score))}
          onActivate={activate}
          onDeactivate={deactivate}
          onClick={() => {
            dlog("proposals", `diamond click ${p.id} override=${!!override} passthrough=${passthrough}`);
            // A crowded stack fans out BEFORE any side effect — the same rule
            // the point pins apply in their handleClick, so a diamond buried in
            // (or burying) a stack explodes it instead of selecting blind. A
            // fanned-out diamond (override) was already disambiguated — select.
            if (!override && internalExploderRef.current?.({ lat: posLat, lng: posLng })) return;
            // Drop any hover card sitting at the click point — the route-summary
            // card about to open would otherwise render underneath it until the
            // next mousemove refreshes the hover. The diamond's own hover card
            // yields for the same reason.
            if (hoverTargetRef.current) {
              hoverTargetRef.current = null;
              setHoverTarget(null);
              redrawHoverHighlightRef.current();
            }
            hoverRbtpRef.current = null;
            setHoverRbtp(null);
            // Picking a diamond ends any open fan-out — unlike a point pin it
            // anchors no modal at its grid cell, so nothing needs the spread
            // kept open (the corridor selection is the outcome).
            if (spreadRef.current) collapseSpread();
            // The tap selects this RBTP (rule (b) above); it stays selected
            // until an anchor leaves the route.
            setSelectedRbtpId(p.id);
            onRouteProposalClickRef.current?.(p);
          }}
        />
      );
    });
  }, [routeProposals, pathEdgeIds, currentZoom, map, theme.suggestions, mapStyle.heat, mapStyle.basemap,
      startLat, startLng, endLat, endLng, ghostKey, selectedRbtpId, anchorsAreWaypoints,
      spread, dropTargetRbtpId, isRouteMode, clearSpreadTimer, armSpreadTimer, collapseSpread, canHover]);

  return (
    <>
      {indicatorMarkers}
      {routeIndicatorMarkers}
      {showPinned && pinnedScreenPos && createPortal(
        <ProposalCard
          // Key by the selected feature's identity (kind + index), stable across
          // pan/zoom and click jitter. Selecting a DIFFERENT feature remounts the
          // card fresh — always expanded, never inheriting the previous
          // selection's minimized state — while re-clicking the SAME feature
          // keeps the existing card in place.
          key={pinnedTarget ? `${pinnedTarget.kind}:${pinnedTarget.index}` : "pinned"}
          winner={isStationNetwork ? null : pinnedWinner}
          // A point selection is a "Proposal"; only one sitting on a current
          // PBTP winner is a "Top Proposal".
          eyebrow={pinnedWinner && !isStationNetwork ? "Top Proposal" : "Proposal"}
          screenX={pinnedScreenPos.x}
          screenY={pinnedScreenPos.y}
          name={pinnedName}
          rows={pinnedVoteTypes}
          interactive
          getAvoidRects={getWaypointAvoidRects}
          edgeId={pinnedVoteEdgeId}
          blocks={pinnedVoteEdgeId != null && topologyRef.current
            ? materializeBlocks(topologyRef.current, blockIndexRef.current, [pinnedVoteEdgeId])
            : null}
          mode={themeMode}
          voteTypes={theme.suggestions}
          shareUrl={pinnedPointLatLng
            ? buildSelectionUrl(pinnedPointLatLng, pinnedWinner?.label ?? pinnedVoteTypes[0]?.label)
            : null}
          streetViewLatLng={pinnedPointLatLng}
          onVote={castProposalVote}
          onRemove={onRemoveSelectedRef.current}
          registerEl={(el) => { pinnedModalElRef.current = el; }}
          onHoverChange={(over) => {
            overModalRef.current = over;
            // Clear any transient hover card when entering the pinned modal so
            // it doesn't linger over the selection.
            if (over && hoverTargetRef.current) {
              hoverTargetRef.current = null;
              setHoverTarget(null);
              redrawHoverHighlightRef.current();
            }
          }}
        />,
        mapContainer
      )}
      {showHoverTooltip && createPortal(
        <ProposalCard
          winner={isStationNetwork ? null : hoverWinner}
          screenX={tooltipPos.x}
          screenY={tooltipPos.y}
          name={tooltipName}
          rows={hoverVoteTypes}
          voteTypes={theme.suggestions}
          getAvoidRects={getHoverAvoidRects}
          // The open modal can re-anchor (vote tick, pan) while the hover
          // target stays put — re-place the hover card when that happens.
          avoidKey={`${pinnedScreenPos?.x},${pinnedScreenPos?.y};${routeCardPos?.x},${routeCardPos?.y}`}
        />,
        mapContainer
      )}
      {/* Diamond (RBTP) hover card — the route counterpart of the square's
          hover card above: same anchor (the icon), same transient styling,
          route-flavored content (corridor block count + block-grain rows).
          Suppressed while the SAME proposal's interactive route-summary card
          is open — that card already speaks for it (the diamond mirror of
          hoverMatchesPinned). */}
      {hoverRbtp && !(routeCardPos && coveredRouteProposal?.id === hoverRbtp.id) && createPortal(
        <ProposalCard
          winner={{
            legendIdx: hoverRbtp.legendIdx,
            label: hoverRbtp.label,
            edgeIdx: hoverRbtp.edgeIds[0],
            count: hoverRbtp.score,
          }}
          eyebrow="Top Route Proposal"
          screenX={tooltipPos.x}
          screenY={tooltipPos.y}
          name=""
          metaText={`Covers ${hoverRbtp.blocks.length} ${hasBlocksRef.current ? "block" : "segment"}${hoverRbtp.blocks.length !== 1 ? "s" : ""}`}
          rows={hoverRbtpRows}
          voteTypes={theme.suggestions}
          getAvoidRects={getHoverAvoidRects}
          avoidKey={`${pinnedScreenPos?.x},${pinnedScreenPos?.y};${routeCardPos?.x},${routeCardPos?.y}`}
        />,
        mapContainer
      )}
      {/* Route-summary card — the modal for a FULL route selection (start+end),
          which the pinned card (lone point) never covers. Summarizes the blocks
          the route selects and the block-grain vote rows across them; headed by
          the covered route proposal when the selection covers one. The ±
          buttons cast on the whole selection via castRouteVote. A transient
          hover card DODGES this card (getHoverAvoidRects) and only overlaps it
          when no quadrant is clear — in which case the hover card wins the z
          contest (1400 vs 1300), being the thing the cursor is on right now. */}
      {routeCardPos && routeBlocks && routeBlocks.length > 0 && !showPinned && createPortal(
        <ProposalCard
          key={`route:${coveredRouteProposal?.id ?? "selection"}`}
          winner={coveredRouteProposal
            ? {
                legendIdx: coveredRouteProposal.legendIdx,
                label: coveredRouteProposal.label,
                edgeIdx: coveredRouteProposal.edgeIds[0],
                count: coveredRouteProposal.score,
              }
            : null}
          // "Top Route Proposal" only when the selection IS the covered RBTP —
          // the moment the path touches more blocks than the corridor's own,
          // it's just a route proposal that happens to contain one.
          eyebrow={coveredRouteProposal && routeBlocks.length <= coveredRouteProposal.blocks.length
            ? "Top Route Proposal"
            : "Route Proposal"}
          screenX={routeCardPos.x}
          screenY={routeCardPos.y}
          name=""
          // Maps without block artifacts fall back to singleton blocks (one per
          // edge), where "blocks" would read as a huge nonsense number — call
          // those what they are.
          metaText={`Selects ${routeBlocks.length} ${hasBlocksRef.current ? "block" : "segment"}${routeBlocks.length !== 1 ? "s" : ""}`}
          // Distinct-voter rows once the server answers (one person = one vote
          // however many blocks their cast fanned across); local sums meanwhile.
          rows={routeUniqueRows ?? routeVoteRows}
          interactive
          elevated
          getAvoidRects={getWaypointAvoidRects}
          edgeId={pathEdgeIds && pathEdgeIds.length > 0 ? pathEdgeIds[0] : null}
          blocks={routeBlocks}
          mode={themeMode}
          voteTypes={theme.suggestions}
          shareUrl={window.location.href}
          onVote={castRouteVote}
          onRemove={onClearRoute ? () => onClearRouteRef.current?.() : undefined}
          removeLabel="Deselect this route"
          registerEl={(el) => { pinnedModalElRef.current = el; }}
          onHoverChange={(over) => {
            overModalRef.current = over;
            if (over && hoverTargetRef.current) {
              hoverTargetRef.current = null;
              setHoverTarget(null);
              redrawHoverHighlightRef.current();
            }
          }}
        />,
        mapContainer
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Proposal card — one component for hover preview and the pinned/selected
// modal. They render identically (same markup + spacing); only when
// `interactive` does the −/+ tally become a pressable button and a discrete
// copy-link icon appear. Minus is on the left, plus on the right; each shows
// its own count so you see how many of each there are.
// ---------------------------------------------------------------------------

// Size every −/net/+ cell in a card to the widest value across ALL rows, so the
// columns line up vertically even when only one row has a two-digit count. The
// directional cells (−down / +up) share one width; the net cell gets its own
// (its value can be negative, so it counts the sign). Exposed as CSS custom
// properties consumed by .graph-vote-cell / .graph-vote-net.
function voteColumnWidths(rows: VoteTypeRow[]): CSSProperties {
  let cellChars = 1; // widest unsigned count among all up/down values
  let netChars = 1;  // widest net value, including a leading "−" when negative
  for (const row of rows) {
    cellChars = Math.max(cellChars, String(row.up).length, String(row.down).length);
    netChars = Math.max(netChars, String(row.up - row.down).length);
  }
  return { "--vote-cell-chars": cellChars, "--vote-net-chars": netChars } as CSSProperties;
}

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
    </svg>
  );
}

/** The Street View pegman, in his signature yellow so the control reads as
 *  "Street View" at a glance even at 13px. */
function PegmanIcon({ size = 13 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="#FBBC04" aria-hidden="true"
      style={{ display: "block" }}>
      <circle cx="12" cy="4.2" r="2.7" />
      <path d="M12 7.6c-2.1 0-3.5 1.1-3.8 2.9l-.8 4.6h2.1l.7 6.9h1.2l.3-5.2h.6l.3 5.2h1.2l.7-6.9h2.1l-.8-4.6c-.3-1.8-1.7-2.9-3.8-2.9z" />
    </svg>
  );
}

/** Expand glyph — two diagonal corner brackets (top-right + bottom-left),
 *  straight and square-capped to match the app's CheckIcon aesthetic, but light
 *  enough to read as a subtle affordance. Inherits color via currentColor. */
function ExpandIcon({ size = 12 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="square" strokeLinejoin="miter" aria-hidden="true"
      style={{ display: "block" }}>
      <path d="M14 4 H20 V10" />
      <path d="M10 20 H4 V14" />
    </svg>
  );
}

interface ProposalCardProps {
  winner: VoteTypeWinner | null;
  /** Header eyebrow text naming the kind of selection: "Proposal" (plain
   *  point), "Top Proposal" (point on a PBTP winner), "Route Proposal" (plain
   *  route, or one that spills past a covered RBTP's blocks), "Top Route
   *  Proposal" (the selection IS the covered RBTP). Shown even without a
   *  `winner` on interactive cards; hover cards only show it with a winner. */
  eyebrow?: string;
  screenX: number;
  screenY: number;
  name: string;
  /** Replaces the default "N proposals" meta line — the route-summary card
   *  puts its block count here ("selects N blocks"). */
  metaText?: string | null;
  rows: VoteTypeRow[];
  interactive?: boolean;
  /** Lifts the card one z tier above sibling cards (route summary vs transient
   *  hover). Portals mount at different times, so DOM order can't order them. */
  elevated?: boolean;
  /** Called at positioning time for the current set of boxes the card should
   *  keep clear of (waypoint markers, an open modal). Its identity is a dep of
   *  the positioning effect, so a new callback (waypoints changed) re-places
   *  the card. */
  getAvoidRects?: () => AvoidRect[];
  /** Bumps the positioning effect when an avoid-rect source moves WITHOUT the
   *  card's own anchor moving — e.g. the open modal the hover card dodges
   *  re-anchors while the hover target stays put. */
  avoidKey?: string;
  edgeId?: number | null;
  /** The selection's touched blocks as materialized edge lists (docs §4.1) —
   *  drives the ± buttons' active/unvote state. Null/absent falls back to the
   *  edge-as-singleton block, i.e. the pre-blocks behavior. */
  blocks?: ArrayLike<number>[] | null;
  mode?: string;
  shareUrl?: string | null;
  /** Anchor coordinate for the Street View tool. Only the pinned POINT card
   *  passes it — a single place you can stand and look at. Route cards span
   *  many blocks, so a one-point pano would be misleading; they (and hover
   *  cards) omit it, which hides the pegman. */
  streetViewLatLng?: { lat: number; lng: number } | null;
  /** The active map's vote types, so the header icon resolves a custom vote-type
   *  set's own icon (matching markers and the selector) instead of falling back
   *  to the suggestion glyph. */
  voteTypes?: readonly { label: string; icon: string }[];
  onVote?: (edgeId: number | null, label: string, dir: VoteDirection) => void;
  onRemove?: () => void;
  /** Title/aria for the ✕ — the route-summary card deselects a whole route, not
   *  a point, so it says so. */
  removeLabel?: string;
  /** Notifies when the cursor enters/leaves the card (pinned modal only) so the
   *  map hover can yield beneath it. */
  onHoverChange?: (over: boolean) => void;
  /** Receives the card's root element so the host can hit-test the cursor
   *  against it (the body is pointer-events:none, so mouseenter is unreliable). */
  registerEl?: (el: HTMLDivElement | null) => void;
}

type CardPos = {
  left: number;
  top: number;
  originX: "left" | "right";
  originY: "top" | "bottom";
};

/** Viewport-space box a floating card should try not to cover (route
 *  waypoints, the open modal). Best-effort: when every placement collides,
 *  the least-covering one wins. */
type AvoidRect = { left: number; top: number; right: number; bottom: number };

function ProposalCard({
  winner, eyebrow = "Top Proposal", screenX, screenY, name, metaText = null, rows,
  interactive = false, elevated = false, getAvoidRects, avoidKey, edgeId = null, blocks = null, mode = "", shareUrl = null, streetViewLatLng = null, voteTypes, onVote, onRemove, removeLabel = "Remove this point", onHoverChange, registerEl,
}: ProposalCardProps) {
  const [copied, setCopied] = useState(false);
  // Interactive cards can collapse to a small pill (icon + label + expand) so a
  // pinned proposal stops covering the map on small screens. Hover cards never
  // minimize (they're transient and already dismiss on mouse-out).
  const [minimized, setMinimized] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const icon = winner ? iconForLabel(winner.label, voteTypes) : null;
  // Subscribe to the vote store so the +/- rows (which read getVote() below)
  // re-render whenever ANY vote changes — including the same edge being voted
  // from the top-bar banner. The value itself is unused; the subscription is.
  void useVotesVersion();

  // Anchor the card to the point and flip it into whichever quadrant has room,
  // then clamp so an oversized card never clips a viewport edge. Measured from
  // the card's real size (offsetWidth/Height ignore the CSS scale-in animation)
  // in a layout effect, so the corrected position is in place before first paint.
  const [pos, setPos] = useState<CardPos | null>(null);
  useLayoutEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const M = 8, GAP = 16; // viewport margin + gap between the card and the point
    const w = el.offsetWidth, h = el.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;

    // The topbar sits in normal flow above the map, so the usable top edge is the
    // banner's bottom — not the viewport top — otherwise a card flipped "above"
    // slides up under the banner and reads as cut off.
    const topbar = document.querySelector(".topbar");
    const topBound = (topbar ? topbar.getBoundingClientRect().bottom : 0) + M;

    // Try all four quadrants around the anchor, preferring right-of/above (the
    // historical default). Each candidate is clamped into the viewport, then
    // ranked by how much it would cover the avoid-rects (route waypoints, the
    // open modal), and among near-equals by how far clamping dragged it off its
    // natural spot — so with nothing to avoid this reduces to the old
    // flip-only-when-overflowing behavior. Best-effort: when every quadrant
    // collides (a waypoint hugging the anchor), the least-covering one wins.
    const avoid = getAvoidRects?.() ?? [];
    const coveredBy = (left: number, top: number) => {
      let sum = 0;
      for (const a of avoid) {
        sum += Math.max(0, Math.min(left + w, a.right) - Math.max(left, a.left))
          * Math.max(0, Math.min(top + h, a.bottom) - Math.max(top, a.top));
      }
      return sum;
    };

    let next: CardPos | null = null;
    let bestCovered = Infinity, bestDisplaced = Infinity;
    for (const placeBelow of [false, true]) {
      for (const placeLeft of [false, true]) {
        const rawLeft = placeLeft ? screenX - w - GAP : screenX + GAP;
        const rawTop = placeBelow ? screenY + GAP : screenY - h - GAP;
        const left = Math.max(M, Math.min(rawLeft, vw - M - w));
        const top = Math.max(topBound, Math.min(rawTop, vh - M - h));
        const covered = coveredBy(left, top);
        const displaced = Math.abs(left - rawLeft) + Math.abs(top - rawTop);
        // Strict margins keep the earlier (preferred) quadrant on ties, so the
        // card doesn't wander between equivalent spots as the anchor jitters.
        if (covered < bestCovered - 1
            || (covered < bestCovered + 1 && displaced < bestDisplaced - 1)) {
          bestCovered = covered;
          bestDisplaced = displaced;
          next = {
            left, top,
            originX: placeLeft ? "right" : "left",
            originY: placeBelow ? "top" : "bottom",
          };
        }
      }
    }
    if (!next) return;
    const chosen = next;
    setPos((prev) =>
      prev &&
      Math.abs(prev.left - chosen.left) < 0.5 && Math.abs(prev.top - chosen.top) < 0.5 &&
      prev.originX === chosen.originX && prev.originY === chosen.originY
        ? prev : chosen
    );
  }, [screenX, screenY, rows, minimized, getAvoidRects, avoidKey]);

  // Stop Leaflet from treating clicks/drags on the card as map interactions:
  // this keeps the modal from being cleared (a map click) and lets the user
  // select text and press buttons. Native-level handling (React's
  // stopPropagation doesn't reach Leaflet's own DOM listeners).
  useEffect(() => {
    if (!interactive || !cardRef.current) return;
    L.DomEvent.disableClickPropagation(cardRef.current);
    L.DomEvent.disableScrollPropagation(cardRef.current);
  }, [interactive]);

  // Release the hover flag if the card unmounts while the cursor is still over
  // it — e.g. deselecting via the ✕ button without the mouse leaving the modal.
  // Otherwise `onMouseLeave` never fires and the host's `overModalRef` stays
  // stuck `true`, suppressing all map hover. Mirrors the IndicatorMarker guard.
  const reportedHoverRef = useRef(false);
  const onHoverChangeRef = useRef(onHoverChange);
  useEffect(() => { onHoverChangeRef.current = onHoverChange; }, [onHoverChange]);
  useEffect(() => () => {
    if (reportedHoverRef.current) {
      reportedHoverRef.current = false;
      onHoverChangeRef.current?.(false);
    }
  }, []);

  const handleCopy = () => {
    if (!shareUrl) return;
    copyToClipboard(shareUrl).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  };

  const tally = (row: VoteTypeRow, dir: VoteDirection) => {
    const sign = dir === -1 ? "−" : "+";
    const count = dir === -1 ? row.down : row.up;
    const cls = `graph-vote-cell ${dir === -1 ? "is-down" : "is-up"}`;
    const inner = (
      <>
        <span className="graph-vote-sign">{sign}</span>
        <span className="graph-vote-count">{count}</span>
      </>
    );
    if (!interactive) {
      return <span className={cls}>{inner}</span>;
    }
    // Tri-state per docs §4.1: "active" (pressed, unvote affordance) only when
    // EVERY touched block already holds my vote in this direction; partial
    // coverage renders neutral (pressing still clears-then-casts).
    const pressed = edgeId != null
      && voteButtonState(
        blockCoverage(mode, blocks && blocks.length > 0 ? blocks : [[edgeId]], row.label),
        dir,
      ) === "active";
    return (
      <button
        type="button"
        className={`${cls} is-btn${pressed ? " is-mine" : ""}`}
        disabled={edgeId == null}
        aria-pressed={pressed}
        title={pressed
          ? "Remove your votes across this block"
          : dir === -1 ? "Downvote" : "Upvote"}
        onClick={() => onVote?.(edgeId, row.label, dir)}
      >
        {inner}
      </button>
    );
  };

  return (
    <div
      ref={(el) => { cardRef.current = el; registerEl?.(el); }}
      className={`graph-indicator-modal graph-proposal-card${interactive ? " is-interactive" : " is-hover"}${minimized ? " is-minimized" : ""}${elevated ? " is-elevated" : ""}`}
      style={{
        left: pos?.left ?? screenX,
        top: pos?.top ?? screenY,
        transformOrigin: pos ? `${pos.originY} ${pos.originX}` : "bottom left",
        // Hidden for the pre-measure frame so the card is never seen at the raw
        // anchor point; the layout effect reveals it at its final spot pre-paint.
        visibility: pos ? "visible" : "hidden",
      }}
      onMouseEnter={onHoverChange ? () => { reportedHoverRef.current = true; onHoverChange(true); } : undefined}
      onMouseLeave={onHoverChange ? () => { reportedHoverRef.current = false; onHoverChange(false); } : undefined}
    >
      {interactive && minimized ? (
        // The maximize control mirrors the ✕ (graph-proposal-close): the same
        // boxed graph-proposal-tool — identical size, border, and hover. The
        // collapsed card shrink-wraps it, so the maximize reads as the same
        // control the ✕ is, just standing alone where the card was.
        <button
          type="button"
          className="graph-proposal-tool graph-proposal-restore"
          title="Expand proposal"
          aria-label="Expand proposal"
          onClick={() => setMinimized(false)}
        >
          <ExpandIcon size={13} />
        </button>
      ) : (
        <>
          {/* Winner cards get the full header (glyph + label). Interactive
              cards WITHOUT a winner — a plain point or plain route selection —
              still show the eyebrow so the modal always names what's selected
              (Proposal / Route Proposal / Top … variants). Hover cards keep
              the winner-only header. */}
          {(winner || interactive) && (
            <div className="graph-indicator-modal-header">
              {winner && (
                <span className="graph-indicator-modal-glyph">
                  {icon ? (
                    <img className="graph-indicator-modal-icon" src={iconSrc(icon)} alt="" />
                  ) : (
                    <span
                      className="graph-indicator-modal-icon"
                      dangerouslySetInnerHTML={{ __html: suggestionGlyphForLabel(winner.label, 22) }}
                    />
                  )}
                </span>
              )}
              <div className="graph-indicator-modal-headtext">
                <div className="graph-indicator-modal-eyebrow">{eyebrow}</div>
                {winner && <div className="graph-indicator-modal-label">{winner.label}</div>}
              </div>
            </div>
          )}
          {interactive && (
            <div className="graph-proposal-tools">
              {streetViewLatLng && (
                <a
                  className="graph-proposal-tool graph-proposal-streetview"
                  // Maps URLs API: opens the Street View pano nearest this
                  // viewpoint (falls back to the map if none exists).
                  href={`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${streetViewLatLng.lat.toFixed(6)}%2C${streetViewLatLng.lng.toFixed(6)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open in Google Street View"
                  aria-label="Open this location in Google Street View"
                >
                  <PegmanIcon />
                </a>
              )}
              {shareUrl && (
                <button
                  type="button"
                  className="graph-proposal-tool"
                  title={copied ? "Link copied!" : "Copy link"}
                  aria-label="Copy link to this proposal"
                  onClick={handleCopy}
                >
                  {copied ? <CheckIcon size={13} /> : <LinkIcon />}
                </button>
              )}
              <button
                type="button"
                className="graph-proposal-tool graph-proposal-minimize"
                title="Minimize"
                aria-label="Minimize this proposal"
                onClick={() => setMinimized(true)}
              >–</button>
              {onRemove && (
                <button
                  type="button"
                  className="graph-proposal-tool graph-proposal-close"
                  title="Remove"
                  aria-label={removeLabel}
                  onClick={() => onRemove()}
                >✕</button>
              )}
            </div>
          )}
          <div className="graph-indicator-modal-body">
            {name && <div className="graph-tooltip-name">{name}</div>}
            <div className="graph-tooltip-meta">
              {metaText
                ?? (rows.length > 0
                  ? `${rows.length} proposal${rows.length !== 1 ? "s" : ""}`
                  : "No votes yet")}
            </div>
            {rows.length > 0 && (
              <div className="graph-proposal-rows" style={voteColumnWidths(rows)}>
                {rows.map((row) => (
                  <div className="graph-proposal-row" key={row.label}>
                    <span className="graph-proposal-row-label">{row.label}</span>
                    <span className="graph-vote" role="group" aria-label={`${row.label} votes`}>
                      {tally(row, -1)}
                      <span className="graph-vote-net" title="net votes (up − down)">{row.up - row.down}</span>
                      {tally(row, 1)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
