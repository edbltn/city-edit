/**
 * Graph Layer - OSM Network Vote Visualization
 *
 * The network baseline renders from PMTiles (graph-edges layer) and voted
 * edges render as GL heat layers (maplibreHeat.ts); this component owns the
 * data + interaction side:
 *   - topology/vote loading, WebSocket deltas, optimistic vote application
 *   - Flatbush hit-testing (nearest-edge-anywhere fallback + node-over-edge
 *     priority that queryRenderedFeatures can't provide)
 *   - hover/pinned selection rings (pushed to the graph-highlight GL source)
 *   - proposal cards (hover + pinned) and top-proposal indicator markers
 */

import { useEffect, useLayoutEffect, useRef, useCallback, useState, useMemo } from "react";
import { createPortal } from "react-dom";
import Flatbush from "flatbush";
import { CONFIG } from "../../config";
import { withMap, getMapSlug } from "../../map/runtime";
import { useMapFacade } from "../../map/MapFacadeContext";
import type { MapFacade, MapMouseEvent } from "../../map/facade";
import { syncHeatToMapLibre, primeHeatFromServer } from "./maplibreHeat";
import { syncHighlightsToMapLibre } from "./maplibreHighlight";
import { useWebSocketContext } from "../../context/WebSocketContext";
import { useGraphSnap, useTheme, useHeatmap } from "../../context";
import type { GraphData } from "../../types";
import { hashLabelToColor, voteTypeIconHtml, VOTE_TYPE_ICON_SIZE } from "./voteTypeIcon";
import { selectTopProposals, type VoteTypeWinner } from "./topProposals";
import { applyEdgeVoteChange, applyAuthoritativeCounts } from "./voteApply";
import { iconForLabel, iconSrc } from "../../themes";
import {
  getCachedTopology,
  setCachedTopology,
  getCachedVotes,
  setCachedVotes,
} from "../../utils/graphCache";
import { getMyVote, setMyVote, reconcileEdge, type VoteDirection } from "../../utils/myVotes";
import { getVoterId } from "../../utils/voterIdentity";
import { buildSelectionUrl, copyToClipboard } from "../../utils/shareLink";
import { CheckIcon } from "../CheckIcon";
import { MapMarker } from "../MapMarker";

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

/** Distance from point (px,py) to line segment (ax,ay)-(bx,by), in pixels. */
function pointToSegmentDist(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) {
    const ex = px - ax;
    const ey = py - ay;
    return Math.sqrt(ex * ex + ey * ey);
  }
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx - px;
  const cy = ay + t * dy - py;
  return Math.sqrt(cx * cx + cy * cy);
}

// ---------------------------------------------------------------------------
// Reverse-geocode cache (module-level, survives re-renders)
// ---------------------------------------------------------------------------

const geocodeCache = new Map<string, string | null>();
const geocodeInFlight = new Set<string>();

function cacheKey(lat: number, lng: number): string {
  return `${lat.toFixed(5)},${lng.toFixed(5)}`;
}

function formatLatLng(lat: number, lng: number): string {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

/**
 * Return cached address, or a lat-lon placeholder while fetching.
 * Calls onResolved() when a fetch completes so the caller can re-render.
 * Does NOT cache failures so they can be retried.
 */
function resolveAddress(
  lat: number,
  lng: number,
  onResolved: () => void,
): string {
  const key = cacheKey(lat, lng);
  const cached = geocodeCache.get(key);
  if (cached !== undefined) return cached || formatLatLng(lat, lng);

  // Not cached — show placeholder and fire async fetch
  if (!geocodeInFlight.has(key)) {
    geocodeInFlight.add(key);
    fetch(withMap(`${CONFIG.apiUrl}/reverse-geocode?lat=${lat}&lng=${lng}`))
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        geocodeCache.set(key, data.address || null);
        onResolved();
      })
      .catch(() => {
        // Don't cache failures — allow retry on next hover
      })
      .finally(() => {
        geocodeInFlight.delete(key);
      });
  }
  return formatLatLng(lat, lng);
}


// ---------------------------------------------------------------------------
// Constants & hit-testing
// ---------------------------------------------------------------------------

// Unified radii — used for hover, snap, pinned highlight, and tooltip lookup
const SNAP_EDGE_PX = 4;   // hit-test radius for edges
const SNAP_NODE_PX = 3;   // node priority radius (wins over edges when very close)

// Hover target type
interface HoverEdge { kind: "edge"; index: number }
interface HoverNode { kind: "node"; index: number }
type HoverTarget = HoverEdge | HoverNode;

// The single resolved selection shared by hover, click/pin, the point-vote
// handler, and top-proposal indicators. `voteEdgeId` is the edge a vote lands
// on (an edge target votes on itself; a node votes on its first adjacent edge),
// so the highlighted component always matches the vote target.
interface ResolvedSelection {
  target: HoverTarget;
  snapLat: number;
  snapLng: number;
  voteEdgeId: number | null;
}

interface HitResult {
  target: HoverTarget;
  snapLat: number;
  snapLng: number;
}

// A vote-type breakdown row shown in tooltips/modals.
interface VoteTypeRow {
  label: string;
  up: number;
  down: number;
}

/** Decode a [legendIdx, up, down][] breakdown into labelled rows. */
function decodeVoteTypes(
  pairs: [number, number, number][] | undefined,
  legend: string[]
): VoteTypeRow[] {
  if (!pairs) return [];
  return pairs
    .map(([idx, up, down]) => ({ label: legend[idx], up: up ?? 0, down: down ?? 0 }))
    .filter((v) => v.label);
}

// Min zoom level at which to show vote-type indicator icons (avoids clutter
// when many edges crowd a small viewport). Leaflet-style zoom (see facade).
const INDICATOR_MIN_ZOOM = 13;

// Cap on how many top-proposal indicators show on the map. Each surviving
// EDGE consumes one slot (winners are deduped per edge first), so the 10 most
// net-voted segments survive — not one per vote type.
const TOP_PROPOSAL_LIMIT = 10;

// "Spread to grid" interaction for crowded indicators. When a top-proposal
// icon is clicked and other icons sit within CLUSTER_RADIUS_PX of it on screen,
// the first click is swallowed: instead of selecting, the cluster fans out into
// a grid so each icon becomes individually clickable, then snaps back after
// SPREAD_DURATION_MS.
const CLUSTER_RADIUS_PX = 26; // screen distance that counts as "clustered"
const SPREAD_CELL_PX = 38;    // grid cell pitch when fanned out
const SPREAD_DURATION_MS = 2200;
const SPREAD_ANIM_MS = 280;   // keep in sync with the CSS transition

// Number of bbox-nearest candidates to retrieve from the spatial index.
// 20 is enough for Manhattan-scale edges (~200m max length); the closest
// segment is always among the top few bbox-nearest in practice.
const INDEX_NEIGHBOR_K = 20;

/** Build a flatbush spatial index of node points. Done once per graph. */
function buildNodeIndex(data: Pick<GraphData, "nodes">): Flatbush | null {
  if (data.nodes.length === 0) return null;
  const idx = new Flatbush(data.nodes.length);
  for (let i = 0; i < data.nodes.length; i++) {
    const node = data.nodes[i];
    // Point bbox: min == max (lng,lat ordering matches the edge index)
    idx.add(node[1], node[0], node[1], node[0]);
  }
  idx.finish();
  return idx;
}

/** Build a flatbush spatial index of edge bounding boxes. Done once per graph. */
function buildEdgeIndex(data: Pick<GraphData, "nodes" | "edges">): Flatbush | null {
  if (data.edges.length === 0) return null;
  const idx = new Flatbush(data.edges.length);
  for (let i = 0; i < data.edges.length; i++) {
    const [fromIdx, toIdx] = data.edges[i];
    const fromNode = data.nodes[fromIdx];
    const toNode = data.nodes[toIdx];
    const minLng = Math.min(fromNode[1], toNode[1]);
    const maxLng = Math.max(fromNode[1], toNode[1]);
    const minLat = Math.min(fromNode[0], toNode[0]);
    const maxLat = Math.max(fromNode[0], toNode[0]);
    idx.add(minLng, minLat, maxLng, maxLat);
  }
  idx.finish();
  return idx;
}

/**
 * Find the edge with the minimum pixel distance from (px,py).
 * No radius limit — used to guarantee hover/click always select *some* segment.
 *
 * With a spatial index: O(log n + k) — query the index for k nearest bboxes,
 * then compute exact projected-pixel distance for just those candidates.
 * Without: brute-force fallback used during the brief window before the
 * index is built on first topology load.
 */
function findNearestEdgeIndex(
  data: GraphData,
  map: MapFacade,
  px: number, py: number,
  index: Flatbush | null,
  queryLng: number,
  queryLat: number
): number | null {
  if (data.edges.length === 0) return null;

  const checkEdge = (i: number, currentBestDist: number, currentBestIdx: number): [number, number] => {
    const [fromIdx, toIdx] = data.edges[i];
    // Tombstones (retired eids) and self-loops aren't selectable geometry.
    if (fromIdx === toIdx) return [currentBestDist, currentBestIdx];
    const fromPt = map.latLngToContainerPoint([data.nodes[fromIdx][0], data.nodes[fromIdx][1]]);
    const toPt = map.latLngToContainerPoint([data.nodes[toIdx][0], data.nodes[toIdx][1]]);
    const dist = pointToSegmentDist(px, py, fromPt.x, fromPt.y, toPt.x, toPt.y);
    return dist < currentBestDist ? [dist, i] : [currentBestDist, currentBestIdx];
  };

  let bestDist = Infinity;
  let bestIdx = -1;

  if (index) {
    const candidates = index.neighbors(queryLng, queryLat, INDEX_NEIGHBOR_K);
    for (const i of candidates) {
      [bestDist, bestIdx] = checkEdge(i, bestDist, bestIdx);
    }
  } else {
    // Brief fallback while the index is being built
    for (let i = 0; i < data.edges.length; i++) {
      [bestDist, bestIdx] = checkEdge(i, bestDist, bestIdx);
    }
  }

  return bestIdx >= 0 ? bestIdx : null;
}

/**
 * Unified hit-test: find the nearest node or edge within snap radius.
 * Nodes within SNAP_NODE_PX win; otherwise edges within SNAP_EDGE_PX.
 * Returns the target and projected snap position.
 *
 * With spatial indices: each loop runs over the top-k bbox-nearest candidates
 * (typically <30 per call) instead of all nodes/edges. Path-drag fires this
 * on every mousemove via the snap closure, so this is the hot path.
 */
function hitTest(
  data: GraphData,
  map: MapFacade,
  px: number, py: number,
  lat: number, lng: number,
  edgeIndex: Flatbush | null,
  nodeIndex: Flatbush | null
): HitResult | null {
  // 1. Nodes — small radius, highest priority
  let bestNode: number | null = null;
  let bestNodeDist = SNAP_NODE_PX;

  const checkNode = (i: number) => {
    const node = data.nodes[i];
    const pt = map.latLngToContainerPoint([node[0], node[1]]);
    const dist = Math.sqrt((px - pt.x) ** 2 + (py - pt.y) ** 2);
    if (dist < bestNodeDist) {
      bestNodeDist = dist;
      bestNode = i;
    }
  };

  if (nodeIndex) {
    const candidates = nodeIndex.neighbors(lng, lat, INDEX_NEIGHBOR_K);
    for (const i of candidates) checkNode(i);
  } else {
    for (let i = 0; i < data.nodes.length; i++) checkNode(i);
  }

  if (bestNode !== null) {
    const n = data.nodes[bestNode];
    return { target: { kind: "node", index: bestNode }, snapLat: n[0], snapLng: n[1] };
  }

  // 2. Edges — project onto segment for snap position
  let bestEdge: number | null = null;
  let bestEdgeDist = SNAP_EDGE_PX;

  const checkEdge = (i: number) => {
    const [fromIdx, toIdx] = data.edges[i];
    // Tombstones (retired eids) and self-loops aren't selectable geometry.
    if (fromIdx === toIdx) return;
    const fromPt = map.latLngToContainerPoint([data.nodes[fromIdx][0], data.nodes[fromIdx][1]]);
    const toPt = map.latLngToContainerPoint([data.nodes[toIdx][0], data.nodes[toIdx][1]]);
    const dist = pointToSegmentDist(px, py, fromPt.x, fromPt.y, toPt.x, toPt.y);
    if (dist < bestEdgeDist) {
      bestEdgeDist = dist;
      bestEdge = i;
    }
  };

  if (edgeIndex) {
    const candidates = edgeIndex.neighbors(lng, lat, INDEX_NEIGHBOR_K);
    for (const i of candidates) checkEdge(i);
  } else {
    for (let i = 0; i < data.edges.length; i++) checkEdge(i);
  }

  if (bestEdge !== null) {
    const { lat: snapLat, lng: snapLng } = projectOntoEdge(data, bestEdge, lat, lng);
    return { target: { kind: "edge", index: bestEdge }, snapLat, snapLng };
  }

  return null;
}

/**
 * Project (lat,lng) onto edge `edgeIdx`, returning the closest point on the
 * segment (clamped to the endpoints). Shared by hitTest and the nearest-edge
 * fallback so both produce a real snap position.
 */
function projectOntoEdge(
  data: Pick<GraphData, "nodes" | "edges">,
  edgeIdx: number,
  lat: number, lng: number
): { lat: number; lng: number } {
  const [fromIdx, toIdx] = data.edges[edgeIdx];
  const fromNode = data.nodes[fromIdx];
  const toNode = data.nodes[toIdx];
  const dx = toNode[1] - fromNode[1];
  const dy = toNode[0] - fromNode[0];
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { lat: fromNode[0], lng: fromNode[1] };
  const t = Math.max(0, Math.min(1,
    ((lng - fromNode[1]) * dx + (lat - fromNode[0]) * dy) / lenSq
  ));
  return { lat: fromNode[0] + t * dy, lng: fromNode[1] + t * dx };
}

// ---------------------------------------------------------------------------
// Node adjacency builder — used to derive node votes from edges
// ---------------------------------------------------------------------------

function buildNodeAdj(topology: Pick<GraphData, "nodes" | "edges">): number[][] {
  const adj: number[][] = new Array(topology.nodes.length);
  for (let i = 0; i < adj.length; i++) adj[i] = [];
  for (let i = 0; i < topology.edges.length; i++) {
    // Skip degenerates (retired-eid tombstones point 0→0) so node 0 doesn't
    // accumulate them as "adjacent" edges.
    if (topology.edges[i][0] === topology.edges[i][1]) continue;
    adj[topology.edges[i][0]].push(i);
    adj[topology.edges[i][1]].push(i);
  }
  return adj;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface GraphLayerProps {
  onSnap?: (pos: { lat: number; lng: number } | null) => void;
  /** When set, a tooltip is pinned at this point showing nearest node vote data. */
  pinnedPoint?: { lat: number; lng: number } | null;
  /** Called when a user clicks/taps a top-proposal indicator. Receives the
   *  edge midpoint of the indicator's segment. Hosts use this to place a
   *  start point at that location so the segment becomes selected. */
  onIndicatorClick?: (latlng: { lat: number; lng: number }) => void;
  /** Removes the currently-selected point. Wired to the same handler as the
   *  start marker's delete so the modal's X is functionally identical. */
  onRemoveSelected?: () => void;
  /** True while the cursor is over the route/desire path. The graph hover yields
   *  (no card/highlight) so the path's own midwaypoint grab affordance isn't
   *  competed with by a graph proposal tooltip. */
  suppressHover?: boolean;
}

export function GraphLayer({ onSnap, pinnedPoint, onIndicatorClick, onRemoveSelected, suppressHover = false }: GraphLayerProps) {
  const map = useMapFacade();
  const { subscribeToDelta } = useWebSocketContext();
  const { setSnapFn, setCurrentSnap, isDraggingRef: graphDraggingRef } = useGraphSnap();
  const { setHeatmapLoaded, isHeatmapLoading } = useHeatmap();
  const theme = useTheme();
  const themeMode = theme.mode;
  const graphDataRef = useRef<GraphData | null>(null);
  const topologyRef = useRef<Pick<GraphData, "nodes" | "edges"> | null>(null);
  const edgeIndexRef = useRef<Flatbush | null>(null);
  const nodeIndexRef = useRef<Flatbush | null>(null);

  // Node adjacency list — node index → [edge indices]. Built once from topology,
  // used to derive node votes from edges (max of adjacent edges).
  const nodeAdjRef = useRef<number[][] | null>(null);

  // Last-seen revision for gap detection
  const lastRevRef = useRef(0);

  // Deltas received before the initial vote fetch completes
  const pendingDeltasRef = useRef<import("../../types").VoteDelta[]>([]);

  // Optimistic increments not yet reconciled by their own WebSocket delta.
  // Keyed by `${edgeId}:${voteType}` → count. When a vote is cast we apply +1
  // optimistically and record it here; the server then broadcasts the same vote
  // as a delta, which would double-count. We absorb that delta against this
  // ledger so the caster's own vote is counted exactly once. Other users'
  // coincident votes on the same edge aren't in the ledger and still apply.
  const pendingOptimisticRef = useRef<Map<string, number>>(new Map());

  // Stable ref for onSnap callback
  const onSnapRef = useRef(onSnap);
  useEffect(() => { onSnapRef.current = onSnap; }, [onSnap]);

  // Unified selection resolver — the single source of truth for "what graph
  // component does this point map to". Used by hover, the pinned/selected
  // effect, the point-vote handler, and top-proposal indicators so the
  // highlighted component always equals the vote target. Hierarchy:
  //   1. overrideEdgeIdx (a top-proposal icon) dominates everything.
  //   2. a node within SNAP_NODE_PX wins (same radius for hover and click).
  //   3. otherwise the nearest edge (in-radius via hitTest, else globally).
  // Pulls indices/data from refs so the closure stays stable across renders.
  const resolveSelection = useCallback((
    lat: number, lng: number, overrideEdgeIdx?: number | null
  ): ResolvedSelection | null => {
    const data = graphDataRef.current;
    if (!data?.edges?.length) return null;

    // 1. Indicator override — select that edge directly.
    if (overrideEdgeIdx != null) {
      const snap = projectOntoEdge(data, overrideEdgeIdx, lat, lng);
      return {
        target: { kind: "edge", index: overrideEdgeIdx },
        snapLat: snap.lat, snapLng: snap.lng, voteEdgeId: overrideEdgeIdx,
      };
    }

    const pt = map.latLngToContainerPoint([lat, lng]);

    // 2. Node-then-edge within radius.
    const hit = hitTest(
      data, map, pt.x, pt.y, lat, lng,
      edgeIndexRef.current, nodeIndexRef.current
    );
    if (hit) {
      const voteEdgeId = hit.target.kind === "edge"
        ? hit.target.index
        : nodeAdjRef.current?.[hit.target.index]?.[0] ?? null;
      return { target: hit.target, snapLat: hit.snapLat, snapLng: hit.snapLng, voteEdgeId };
    }

    // 3. Nearest-edge fallback (no radius) so a selection always resolves.
    const nearestEdgeIdx = findNearestEdgeIndex(
      data, map, pt.x, pt.y, edgeIndexRef.current, lng, lat
    );
    if (nearestEdgeIdx !== null) {
      const snap = projectOntoEdge(data, nearestEdgeIdx, lat, lng);
      return {
        target: { kind: "edge", index: nearestEdgeIdx },
        snapLat: snap.lat, snapLng: snap.lng, voteEdgeId: nearestEdgeIdx,
      };
    }

    return null;
  }, [map]);

  // Ref so stable effects (hover, point-vote listener) can call the resolver
  // without re-subscribing when it changes.
  const resolveSelectionRef = useRef(resolveSelection);
  useEffect(() => { resolveSelectionRef.current = resolveSelection; }, [resolveSelection]);

  // Register graph snap function for use by path/marker drag. Uses hitTest
  // directly (in-radius only, null when out of range) so dragging stays free
  // when far from the graph — unlike resolveSelection, which always resolves.
  useEffect(() => {
    setSnapFn((lat: number, lng: number) => {
      const data = graphDataRef.current;
      if (!data) return null;
      const pt = map.latLngToContainerPoint([lat, lng]);
      const result = hitTest(
        data, map, pt.x, pt.y, lat, lng,
        edgeIndexRef.current, nodeIndexRef.current
      );
      if (!result) return null;
      return { lat: result.snapLat, lng: result.snapLng };
    });
  }, [setSnapFn, map]);

  // Hover state
  const [hoverTarget, setHoverTarget] = useState<HoverTarget | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoverTargetRef = useRef<HoverTarget | null>(null);
  const hoverRafRef = useRef<number | null>(null);
  // True while the cursor is over a top-proposal icon, so the map hover handler
  // yields the highlight to the icon (hierarchy rule #1).
  const overIndicatorRef = useRef(false);
  // True while the cursor is over the pinned/selected modal, so the map hover
  // handler doesn't fire (and surface other hover modals) underneath it.
  const overModalRef = useRef(false);
  // Mirrors the `suppressHover` prop (cursor over the route path) for the
  // stable mousemove handler to read without re-subscribing.
  const suppressHoverRef = useRef(suppressHover);
  useEffect(() => { suppressHoverRef.current = suppressHover; }, [suppressHover]);

  // Pinned tooltip screen position (follows start pin on map pan/zoom)
  const [pinnedScreenPos, setPinnedScreenPos] = useState<{ x: number; y: number } | null>(null);
  const pinnedRafRef = useRef<number | null>(null);
  // Pinned target (node or edge) for highlight and tooltip
  const pinnedTargetRef = useRef<HoverTarget | null>(null);
  // When an indicator is clicked, store the exact edge index so the
  // pinnedPoint effect uses it directly instead of re-running hitTest
  // (which can snap to a neighboring edge).
  const pinnedEdgeOverrideRef = useRef<number | null>(null);

  // Push the current pinned/hover rings to the GL highlight source. The
  // hover-suppressed-when-pinned rule is applied here so the sync module
  // stays dumb. Stable (reads refs only).
  const syncHighlights = useCallback(() => {
    const pinned = pinnedTargetRef.current;
    const hover = hoverTargetRef.current;
    const hoverIsPinned = pinned && hover
      && hover.kind === pinned.kind && hover.index === pinned.index;
    syncHighlightsToMapLibre(graphDataRef.current, pinned, hoverIsPinned ? null : hover);
  }, []);

  // Increments when a geocode resolves, forcing tooltip re-render
  const [geocodeVersion, setGeocodeVersion] = useState(0);
  const bumpGeocode = useCallback(() => setGeocodeVersion((v) => v + 1), []);

  // Increments when graph vote data mutates, forcing tooltip re-render. The
  // value is read so the vote-type decode memos re-run in lockstep with the
  // winners recompute (both happen in refreshGraphDisplay) — avoiding a stale
  // winner indexing into emptied vote data ("no votes yet" on a top proposal).
  const [graphVoteVersion, setGraphVoteVersion] = useState(0);
  void graphVoteVersion; // read so decode memos/winners re-render in lockstep

  // Increments when the user's own votes change, forcing the proposal modal to
  // re-evaluate +/- button state (myVotes lives outside React state).
  const [myVotesVersion, setMyVotesVersion] = useState(0);

  // Vote-type indicator markers — top-voted segment per vote type
  const [winners, setWinners] = useState<VoteTypeWinner[]>([]);
  const [currentZoom, setCurrentZoom] = useState<number>(() => map.getZoom());
  const iconHtmlCacheRef = useRef<Map<string, string>>(new Map());

  // Temporary "fan out crowded icons into a grid" state. Maps a winner's
  // legendIdx -> overridden [lat, lng] while the spread is active; null when
  // icons sit at their natural edge midpoints. spreadActiveRef mirrors this for
  // synchronous reads inside click handlers (state is async).
  const [spreadPositions, setSpreadPositions] =
    useState<Map<number, [number, number]> | null>(null);
  const spreadActiveRef = useRef(false);
  const spreadTimeoutRef = useRef<number | null>(null);

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

  const refreshGraphDisplay = useCallback(() => {
    const data = graphDataRef.current;
    if (!data) return;
    const legendLen = data.vote_type_legend?.length ?? 0;
    const legendChanged = legendLen !== lastLegendLenRef.current;
    lastLegendLenRef.current = legendLen;
    setStableWinners(selectTopProposals(
      data, tiebreakSaltRef.current, TOP_PROPOSAL_LIMIT,
    ), legendChanged);
    setGraphVoteVersion((v) => v + 1);
    // Voted edges render as MapLibre heat layers; ring highlights re-sync in
    // case the underlying data object was replaced.
    syncHeatToMapLibre(data);
    syncHighlights();
  }, [setStableWinners, syncHighlights]);

  const refreshGraphDisplayRef = useRef(refreshGraphDisplay);
  useEffect(() => { refreshGraphDisplayRef.current = refreshGraphDisplay; }, [refreshGraphDisplay]);

  // Stable ref for the indicator-click callback (avoids re-running the
  // useMemo that builds marker components every time the parent re-renders).
  const onIndicatorClickRef = useRef(onIndicatorClick);
  useEffect(() => { onIndicatorClickRef.current = onIndicatorClick; }, [onIndicatorClick]);

  const onRemoveSelectedRef = useRef(onRemoveSelected);
  useEffect(() => { onRemoveSelectedRef.current = onRemoveSelected; }, [onRemoveSelected]);

  // Reset icon cache + winners when theme switches (different vote namespace)
  useEffect(() => {
    iconHtmlCacheRef.current.clear();
    pendingOptimisticRef.current.clear();
    setWinners([]);
  }, [themeMode]);

  // Full vote fetch — used on initial load and revision-gap recovery.
  const fetchVotes = useCallback(async () => {
    if (!topologyRef.current) return;
    try {
      const url = `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}`;
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`Vote fetch failed: ${response.status}`);
      const voteData = await response.json();
      graphDataRef.current = { ...topologyRef.current!, ...voteData };
      lastRevRef.current = voteData.rev ?? 0;
      // Authoritative snapshot — any unreconciled optimistic increments are now
      // baked into (or superseded by) this data, so drop the ledger.
      pendingOptimisticRef.current.clear();

      // Replay any deltas that arrived while the fetch was in flight
      const pending = pendingDeltasRef.current;
      if (pending.length > 0) {
        pendingDeltasRef.current = [];
        for (const d of pending) {
          if (d.rev <= lastRevRef.current) continue;
          applyDeltaToGraphData(d);
        }
      }

      refreshGraphDisplayRef.current();
    } catch (error) {
      console.error("Failed to fetch graph votes:", error);
    }
  }, [themeMode]);

  const fetchVotesRef = useRef(fetchVotes);
  useEffect(() => { fetchVotesRef.current = fetchVotes; }, [fetchVotes]);

  // Apply a WebSocket delta to graphDataRef.
  //
  // Directional (modal +/−) deltas carry `vtCounts` — the server's authoritative
  // [up, down] for the changed proposals — which we SET (idempotent), so the
  // caster's optimistic guess is corrected, never compounded. Legacy bulk-cast
  // deltas have no vtCounts: they INCREMENT, with the optimistic ledger
  // absorbing the caster's own echo so it isn't double-counted.
  const applyDeltaToGraphData = useCallback((delta: import("../../types").VoteDelta) => {
    const data = graphDataRef.current;
    const adj = nodeAdjRef.current;
    if (!data?.edge_votes || !adj) { lastRevRef.current = delta.rev; return; }
    const vtLabel = delta.vtLabel ?? "";

    if (delta.vtCounts) {
      applyAuthoritativeCounts(data, adj, vtLabel, delta.vtCounts);
      lastRevRef.current = delta.rev;
      return;
    }

    const dir = delta.dir ?? 1;
    const reversed = delta.reversed ?? false;
    const pending = pendingOptimisticRef.current;
    const toApply: number[] = [];
    for (const eid of delta.edges) {
      const key = `${eid}:${vtLabel}:${dir}:${reversed ? 1 : 0}`;
      const optimistic = pending.get(key) ?? 0;
      if (optimistic > 0) {
        if (optimistic === 1) pending.delete(key);
        else pending.set(key, optimistic - 1);
      } else {
        toApply.push(eid);
      }
    }

    if (toApply.length > 0) applyEdgeVoteChange(data, adj, toApply, vtLabel, dir, reversed);
    lastRevRef.current = delta.rev;
  }, []);

  // Load topology + votes on mount, preferring persisted caches.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      // 0a. Heatmap fast path: the server-built voted-edges GeoJSON paints the
      //     heat as soon as the GL map is up, long before the topology
      //     download completes. Superseded by the locally-built collection
      //     once topology + votes land (see maplibreHeat.primeHeatFromServer).
      fetch(`${CONFIG.apiUrl}/heat?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((fc) => {
          if (!cancelled && fc) primeHeatFromServer(fc as GeoJSON.FeatureCollection);
        })
        .catch(() => {});

      // 0b. Kick off the authoritative vote fetch immediately — it doesn't
      //    depend on topology, so it downloads in parallel with the (much
      //    larger) topology fetch instead of after it.
      const votesPromise = fetch(
        `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}`
      ).then((r) => {
        if (!r.ok) throw new Error(`Vote fetch failed: ${r.status}`);
        return r.json();
      });
      votesPromise.catch(() => {}); // handled below; avoid unhandled rejection

      // 1. Resolve the graph version, then load topology from IndexedDB when it
      //    matches — skipping the multi-MB download and JSON parse entirely.
      let topology: GraphData | null = null;
      let version: string | null = null;
      let usedCachedTopology = false;
      try {
        try {
          const vr = await fetch(withMap(`${CONFIG.apiUrl}/graph-version`));
          if (vr.ok) version = (await vr.json()).version ?? null;
        } catch {
          // Version probe failed — fall back to a direct topology fetch.
        }

        if (version) {
          const cached = await getCachedTopology<GraphData>(version);
          if (cached) {
            topology = cached;
            usedCachedTopology = true;
          }
        }
        if (!topology) {
          const r = await fetch(withMap(`${CONFIG.apiUrl}/graph-topology`));
          if (!r.ok) throw new Error(`Topology fetch failed: ${r.status}`);
          topology = await r.json();
          if (version && topology) setCachedTopology(version, topology);
        }
      } catch (error) {
        console.error("Failed to load graph topology:", error);
        return;
      }
      if (cancelled || !topology) return;

      topologyRef.current = topology;
      graphDataRef.current = topology;
      edgeIndexRef.current = buildEdgeIndex(topology);
      nodeIndexRef.current = buildNodeIndex(topology);
      nodeAdjRef.current = buildNodeAdj(topology);

      // 2. Paint immediately from cached votes (same graph version only) so the
      //    heatmap appears without waiting on the network. Skipped when the
      //    topology was freshly downloaded, since edge indices may have shifted.
      if (usedCachedTopology && version) {
        const cachedVotes = await getCachedVotes<Partial<GraphData>>(getMapSlug() || themeMode, version);
        if (cancelled) return;
        if (cachedVotes) {
          graphDataRef.current = { ...topology, ...cachedVotes };
          lastRevRef.current = cachedVotes.rev ?? 0;
          refreshGraphDisplayRef.current();
          setHeatmapLoaded();
        }
      }

      // 3. Authoritative vote fetch (started in step 0) — replaces the cached
      //    snapshot and replays any deltas that arrived while it was in flight.
      try {
        const voteData = await votesPromise;
        if (cancelled) return;
        graphDataRef.current = { ...topologyRef.current!, ...voteData };
        lastRevRef.current = voteData.rev ?? 0;
        pendingOptimisticRef.current.clear();
        if (version) setCachedVotes(getMapSlug() || themeMode, version, voteData);

        // Replay any deltas that arrived while waiting for the fetch
        const pending = pendingDeltasRef.current;
        if (pending.length > 0) {
          pendingDeltasRef.current = [];
          for (const d of pending) {
            if (d.rev <= lastRevRef.current) continue;
            applyDeltaToGraphData(d);
          }
        }

        refreshGraphDisplayRef.current();
        setHeatmapLoaded();
      } catch (error) {
        console.error("Failed to fetch graph votes:", error);
      }
    })();
    return () => { cancelled = true; };
  }, [applyDeltaToGraphData, setHeatmapLoaded, themeMode]);

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

      // Gap detection: if we missed revisions, full refetch
      if (lastRevRef.current > 0 && delta.rev > lastRevRef.current + 1) {
        fetchVotesRef.current();
        return;
      }

      // Skip duplicates
      if (delta.rev <= lastRevRef.current) {
        return;
      }

      applyDeltaToGraphData(delta);
      refreshGraphDisplayRef.current();
    });
    return unsubscribe;
  }, [subscribeToDelta, themeMode, applyDeltaToGraphData]);

  // Optimistic vote — apply edge increments immediately on cast so the heatmap
  // and top-proposal counts update before the server round-trip completes. The
  // increments are recorded in pendingOptimisticRef; when the server's matching
  // WebSocket delta arrives it is absorbed (see applyDeltaToGraphData) so the
  // caster's own vote is counted exactly once instead of being double-counted.
  // Accepts either {edgeIds} (path votes) or {point} (single-location votes).
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      const { voteType: vtLabel, mode } = detail;
      if (mode !== themeMode) return;

      const data = graphDataRef.current;
      const adj = nodeAdjRef.current;
      if (!data?.edge_votes || !adj) return;

      // Direction defaults to up (route/point casts); modal +/- supplies it.
      const dir: number = detail.direction ?? 1;
      const reversed: boolean = detail.reversed ?? false;

      let edgeIds: number[] | undefined = detail.edgeIds;

      // Point vote: resolve the point through the SAME hierarchy as hover/click
      // so the vote lands on exactly the component that was highlighted (an
      // edge votes on itself; a node on its first adjacent edge).
      if (!edgeIds && detail.point) {
        const sel = resolveSelectionRef.current(detail.point.lat, detail.point.lng);
        if (sel?.voteEdgeId == null) return;
        edgeIds = [sel.voteEdgeId];
      }

      if (!edgeIds?.length) return;

      applyEdgeVoteChange(data, adj, edgeIds, vtLabel, dir, reversed);

      // Directional (modal +/−) votes are `authoritative`: their confirming
      // delta carries vtCounts and is applied as an idempotent SET, so we do
      // NOT ledger them (no echo to absorb). Legacy bulk casts increment, so we
      // ledger them to absorb the caster's own echo and avoid double-counting.
      if (!detail.authoritative) {
        const pending = pendingOptimisticRef.current;
        for (const eid of edgeIds) {
          const key = `${eid}:${vtLabel ?? ""}:${dir}:${reversed ? 1 : 0}`;
          pending.set(key, (pending.get(key) ?? 0) + 1);
        }
      }

      refreshGraphDisplayRef.current();
    };
    window.addEventListener("optimistic-vote", handler);
    return () => window.removeEventListener("optimistic-vote", handler);
  }, [themeMode]);

  // Map event listeners — drop the hover ring when a zoom starts (its target
  // is stale the moment the zoom starts); the pinned ring stays, as a GL layer
  // it tracks the camera for free. Track zoom for the indicator threshold.
  useEffect(() => {
    const handleZoomStart = () => {
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
        syncHighlights();
      }
    };
    const handleZoomEnd = () => {
      setCurrentZoom(map.getZoom());
    };

    map.on("zoomstart", handleZoomStart);
    map.on("zoomend", handleZoomEnd);
    return () => {
      map.off("zoomstart", handleZoomStart);
      map.off("zoomend", handleZoomEnd);
    };
  }, [map, syncHighlights]);

  // Hover detection and snap — uses hitTest for unified logic.
  useEffect(() => {
    const canHover = window.matchMedia("(hover: hover)").matches;
    if (!canHover) return;

    const handleMouseMove = (e: MapMouseEvent) => {
      if (hoverRafRef.current) return;
      hoverRafRef.current = requestAnimationFrame(() => {
        hoverRafRef.current = null;
        const data = graphDataRef.current;
        if (!data?.edges) {
          if (hoverTargetRef.current) {
            hoverTargetRef.current = null;
            setHoverTarget(null);
            syncHighlights();
          }
          onSnapRef.current?.(null);
          setCurrentSnap(null);
          return;
        }

        // A top-proposal icon owns the highlight while hovered (hierarchy rule
        // #1) — it sets the hover target itself, so just yield without clearing.
        if (overIndicatorRef.current) return;

        // The pinned modal and the route path (mid-waypoint grab) both suppress
        // the graph hover beneath them; clear any active card and yield.
        if (overModalRef.current || suppressHoverRef.current) {
          if (hoverTargetRef.current) {
            hoverTargetRef.current = null;
            setHoverTarget(null);
            syncHighlights();
          }
          return;
        }

        const dragging = graphDraggingRef.current;

        // During drag: suppress hover highlight and tooltip, but still compute
        // snap below. Otherwise resolve the hover target through the unified
        // hierarchy so hover reflects exactly the component a click would pin.
        let newTarget: HoverTarget | null = null;
        if (!dragging) {
          const sel = resolveSelectionRef.current(e.latlng.lat, e.latlng.lng);
          newTarget = sel?.target ?? null;
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
          syncHighlights();
        }

        if (newTarget) {
          setTooltipPos({ x: e.originalEvent.clientX, y: e.originalEvent.clientY });
        }

        // Snap position: only compute when actually dragging (path-drag system
        // is the only consumer). Skipping when idle saves the hit-test work
        // on every mousemove. Uses hitTest directly (in-radius only) so the drag
        // stays free when far from the graph — not resolveSelection's edge fallback.
        if (!dragging) return;

        const hit = hitTest(
          data, map, e.containerPoint.x, e.containerPoint.y, e.latlng.lat, e.latlng.lng,
          edgeIndexRef.current, nodeIndexRef.current
        );
        if (hit) {
          const snapPos = { lat: hit.snapLat, lng: hit.snapLng };
          onSnapRef.current?.(snapPos);
          setCurrentSnap(snapPos);
        } else {
          // Fallback: nearest node out of range. Use the node index when
          // available (top-1 by bbox distance — for points that's exact).
          const nodeIdx = nodeIndexRef.current;
          let nearestIdx = -1;
          if (nodeIdx) {
            const candidates = nodeIdx.neighbors(e.latlng.lng, e.latlng.lat, 1);
            if (candidates.length > 0) nearestIdx = candidates[0];
          } else {
            let nearestDist = Infinity;
            for (let i = 0; i < data.nodes.length; i++) {
              const n = data.nodes[i];
              const d = (n[0] - e.latlng.lat) ** 2 + (n[1] - e.latlng.lng) ** 2;
              if (d < nearestDist) { nearestDist = d; nearestIdx = i; }
            }
          }
          if (nearestIdx >= 0) {
            const n = data.nodes[nearestIdx];
            const snapPos = { lat: n[0], lng: n[1] };
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
        syncHighlights();
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
  }, [map, setCurrentSnap, graphDraggingRef, syncHighlights]);

  // Track pinned tooltip screen position on map pan/zoom
  const pinnedLat = pinnedPoint?.lat ?? null;
  const pinnedLng = pinnedPoint?.lng ?? null;

  useEffect(() => {
    if (pinnedLat === null || pinnedLng === null) {
      setPinnedScreenPos(null);
      pinnedTargetRef.current = null;
      syncHighlights();
      return;
    }

    // Resolve through the unified hierarchy. An indicator click pre-sets the
    // edge via pinnedEdgeOverrideRef (single-shot), which dominates; otherwise
    // it's node-within-radius then nearest edge — same logic as hover.
    const sel = resolveSelection(pinnedLat, pinnedLng, pinnedEdgeOverrideRef.current);
    pinnedEdgeOverrideRef.current = null;
    pinnedTargetRef.current = sel?.target ?? null;
    syncHighlights();

    const update = () => {
      const pt = map.latLngToContainerPoint([pinnedLat, pinnedLng]);
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
  }, [map, pinnedLat, pinnedLng, resolveSelection, isHeatmapLoading, syncHighlights]);

  // -------------------------------------------------------------------------
  // Tooltip content — hover
  // -------------------------------------------------------------------------

  const data = graphDataRef.current;
  const legend = data?.vote_type_legend ?? [];
  let tooltipName = "";
  let hoverVoteTypes: VoteTypeRow[] = [];

  if (hoverTarget && data) {
    if (hoverTarget.kind === "edge") {
      const edge = data.edges[hoverTarget.index];
      if (edge) {
        const [fromIdx, toIdx] = edge;
        const fromNode = data.nodes[fromIdx];
        const toNode = data.nodes[toIdx];
        const midLat = (fromNode[0] + toNode[0]) / 2;
        const midLng = (fromNode[1] + toNode[1]) / 2;

        tooltipName = edge[2] || resolveAddress(midLat, midLng, bumpGeocode);
        hoverVoteTypes = decodeVoteTypes((data.edge_vote_types ?? [])[hoverTarget.index], legend);
      }
    } else {
      const node = data.nodes[hoverTarget.index];
      if (node) {
        tooltipName = resolveAddress(node[0], node[1], bumpGeocode);
        hoverVoteTypes = decodeVoteTypes((data.node_vote_types ?? [])[hoverTarget.index], legend);
      }
    }
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
      const edge = data.edges[pinnedTarget.index];
      if (edge) {
        const [fromIdx, toIdx] = edge;
        const fromNode = data.nodes[fromIdx];
        const toNode = data.nodes[toIdx];
        const midLat = (fromNode[0] + toNode[0]) / 2;
        const midLng = (fromNode[1] + toNode[1]) / 2;
        pinnedName = edge[2] || resolveAddress(midLat, midLng, bumpGeocode);
        pinnedVoteTypes = decodeVoteTypes((data.edge_vote_types ?? [])[pinnedTarget.index], legend);
        pinnedVoteEdgeId = pinnedTarget.index;
        pinnedPointLatLng = { lat: midLat, lng: midLng };
      }
    } else {
      const node = data.nodes[pinnedTarget.index];
      if (node) {
        pinnedName = resolveAddress(node[0], node[1], bumpGeocode);
        pinnedVoteTypes = decodeVoteTypes((data.node_vote_types ?? [])[pinnedTarget.index], legend);
        pinnedVoteEdgeId = nodeAdjRef.current?.[pinnedTarget.index]?.[0] ?? null;
        pinnedPointLatLng = { lat: node[0], lng: node[1] };
      }
    }
  }

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
  const showHoverTooltip = hoverTarget !== null && !hoverMatchesPinned;
  const hoverWinner = (hoverTarget?.kind === "edge")
    ? winners.find(w => w.edgeIdx === hoverTarget.index) ?? null
    : null;

  const pinnedWinner = (showPinned && pinnedTarget?.kind === "edge")
    ? winners.find(w => w.edgeIdx === pinnedTarget.index) ?? null
    : null;

  // Safety net for a momentary winners/data desync: if an edge is a current
  // winner but its live breakdown decoded empty, show the winner's own proposal
  // so a top-proposal card never reads "no proposals yet". Net support maps to
  // up (positive) so the row's net (up − down) matches the winner's count.
  if (hoverWinner && hoverVoteTypes.length === 0) {
    hoverVoteTypes = [{ label: hoverWinner.label, up: hoverWinner.count, down: 0 }];
  }
  if (pinnedWinner && pinnedVoteTypes.length === 0) {
    pinnedVoteTypes = [{ label: pinnedWinner.label, up: pinnedWinner.count, down: 0 }];
  }

  // geocodeVersion used to re-render when async geocode completes
  void geocodeVersion;

  // Build indicator markers for the top-voted segment per vote type.
  // Position = edge midpoint. Hover/click sets the same hoverTarget the
  // map mousemove handler would, so the existing tooltip lights up.
  // Cancel any in-flight spread and snap icons back to their edge midpoints.
  const clearSpread = useCallback(() => {
    if (spreadTimeoutRef.current) {
      clearTimeout(spreadTimeoutRef.current);
      spreadTimeoutRef.current = null;
    }
    spreadActiveRef.current = false;
    setSpreadPositions(null);
    // Leave the transition class on briefly so the snap-back animates too.
    const container = map.getContainer();
    window.setTimeout(
      () => container.classList.remove("votes-spreading"),
      SPREAD_ANIM_MS,
    );
  }, [map]);

  // Edge index of the currently selected top proposal (if any), so its icon
  // can float above the others.
  const selectedEdgeIdx =
    pinnedTarget?.kind === "edge" ? pinnedTarget.index : null;

  const indicatorMarkers = useMemo(() => {
    if (currentZoom < INDICATOR_MIN_ZOOM) return null;
    const topology = topologyRef.current;
    if (!topology || winners.length === 0) return null;

    // Resolve every winner to its edge-midpoint once up front so click handlers
    // can measure on-screen distance between icons for cluster detection.
    const placed = winners
      .map((w) => {
        const edge = topology.edges[w.edgeIdx];
        if (!edge || edge[0] === edge[1]) return null;
        const [fromIdx, toIdx] = edge;
        const fromNode = topology.nodes[fromIdx];
        const toNode = topology.nodes[toIdx];
        if (!fromNode || !toNode) return null;
        const midLat = (fromNode[0] + toNode[0]) / 2;
        const midLng = (fromNode[1] + toNode[1]) / 2;
        return { w, midLat, midLng };
      })
      .filter(Boolean) as Array<{
      w: VoteTypeWinner;
      midLat: number;
      midLng: number;
    }>;

    // Fan a cluster of icons out into a centered grid of cells around their
    // shared anchor point, then schedule a snap-back. Each icon's overridden
    // position is stored by legendIdx for the render below to pick up.
    const spreadCluster = (
      members: typeof placed,
      anchor: { x: number; y: number },
    ) => {
      const cols = Math.ceil(Math.sqrt(members.length));
      const rows = Math.ceil(members.length / cols);
      const next = new Map<number, [number, number]>();
      members.forEach((m, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const offsetX = (col - (cols - 1) / 2) * SPREAD_CELL_PX;
        const offsetY = (row - (rows - 1) / 2) * SPREAD_CELL_PX;
        const ll = map.containerPointToLatLng([
          anchor.x + offsetX,
          anchor.y + offsetY,
        ]);
        next.set(m.w.legendIdx, [ll.lat, ll.lng]);
      });

      map.getContainer().classList.add("votes-spreading");
      spreadActiveRef.current = true;
      setSpreadPositions(next);
      if (spreadTimeoutRef.current) clearTimeout(spreadTimeoutRef.current);
      spreadTimeoutRef.current = window.setTimeout(
        clearSpread,
        SPREAD_DURATION_MS,
      );
    };

    return placed.map(({ w, midLat, midLng }) => {
      const override = spreadPositions?.get(w.legendIdx);
      const posLat = override ? override[0] : midLat;
      const posLng = override ? override[1] : midLng;

      let iconHtml = iconHtmlCacheRef.current.get(w.label);
      if (!iconHtml) {
        iconHtml = voteTypeIconHtml(w.label);
        iconHtmlCacheRef.current.set(w.label, iconHtml);
      }

      const activateIndicator = () => {
        // Hovering the icon sets the same hoverTarget the map mousemove would,
        // anchoring the hover card near the icon. The card derives "top proposal"
        // from this edge being a winner, so the markup matches a segment hover.
        // overIndicatorRef tells the map hover handler to yield (hierarchy #1).
        overIndicatorRef.current = true;
        const target: HoverTarget = { kind: "edge", index: w.edgeIdx };
        const iconPt = map.latLngToContainerPoint([posLat, posLng]);
        const rect = map.getContainer().getBoundingClientRect();
        setTooltipPos({ x: rect.left + iconPt.x, y: rect.top + iconPt.y });
        hoverTargetRef.current = target;
        setHoverTarget(target);
        syncHighlights();
      };

      const deactivateIndicator = () => {
        overIndicatorRef.current = false;
        hoverTargetRef.current = null;
        setHoverTarget(null);
        syncHighlights();
      };

      const handleClick = () => {
        // When icons are already fanned out, a click selects normally.
        if (!spreadActiveRef.current) {
          const selfPt = map.latLngToContainerPoint([midLat, midLng]);
          const cluster = placed.filter(({ midLat: la, midLng: ln }) => {
            const p = map.latLngToContainerPoint([la, ln]);
            const dx = p.x - selfPt.x;
            const dy = p.y - selfPt.y;
            return dx * dx + dy * dy <= CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX;
          });
          // Crowded: swallow this click and fan the cluster out instead.
          if (cluster.length > 1) {
            spreadCluster(cluster, selfPt);
            return;
          }
        }

        clearSpread();
        activateIndicator();
        pinnedEdgeOverrideRef.current = w.edgeIdx;
        // Lock the start point to the icon's true edge midpoint, not its
        // temporary fanned-out grid cell — the spread offset is display-only.
        onIndicatorClickRef.current?.({ lat: midLat, lng: midLng });
      };

      return (
        <MapMarker
          key={w.legendIdx}
          position={{ lat: posLat, lng: posLng }}
          html={iconHtml}
          className="vote-type-indicator-wrapper"
          size={VOTE_TYPE_ICON_SIZE}
          anchor="center"
          zIndex={w.edgeIdx === selectedEdgeIdx ? 2000 : 1000}
          onMouseOver={activateIndicator}
          onMouseOut={deactivateIndicator}
          onClick={handleClick}
        />
      );
    });
  }, [winners, currentZoom, map, spreadPositions, clearSpread, selectedEdgeIdx, syncHighlights]);

  // Cast a directional vote on a single proposal (edge, vote type). Optimistic:
  // applies immediately via the same event the route cast uses, records the
  // local "my vote" for instant button state, then persists to the server.
  const castProposalVote = useCallback((
    edgeId: number | null, label: string, newDir: VoteDirection,
  ) => {
    if (edgeId == null || !label) return;
    const prev = getMyVote(themeMode, edgeId, label);
    if (prev === newDir) return; // already cast this direction — button is greyed
    const reversed = prev !== 0;

    window.dispatchEvent(new CustomEvent("optimistic-vote", {
      detail: {
        edgeIds: [edgeId], voteType: label, mode: themeMode,
        direction: newDir, reversed, authoritative: true,
      },
    }));
    setMyVote(themeMode, edgeId, label, newDir);
    setMyVotesVersion((v) => v + 1);

    fetch(`${CONFIG.apiUrl}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        map: getMapSlug(), edge_id: edgeId, mode: themeMode,
        vote_type: label, direction: newDir, voter_id: getVoterId(),
      }),
    }).catch((err) => console.error("Proposal vote failed:", err));
  }, [themeMode]);

  // Reconcile local "my votes" against the server when a proposal modal opens
  // (authoritative across devices). Keyed on the pinned edge so it refetches
  // whenever the selection changes.
  useEffect(() => {
    if (pinnedVoteEdgeId == null) return;
    let cancelled = false;
    const url = `${CONFIG.apiUrl}/my-votes?map=${encodeURIComponent(getMapSlug())}`
      + `&mode=${encodeURIComponent(themeMode)}&edge_ids=${pinnedVoteEdgeId}`
      + `&voter_id=${encodeURIComponent(getVoterId())}`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (cancelled || !j?.votes) return;
        const labels = j.votes[String(pinnedVoteEdgeId)];
        if (labels) {
          reconcileEdge(themeMode, pinnedVoteEdgeId, labels);
          setMyVotesVersion((v) => v + 1);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pinnedVoteEdgeId, themeMode]);

  return (
    <>
      {indicatorMarkers}
      {showPinned && pinnedScreenPos && createPortal(
        <ProposalCard
          winner={pinnedWinner}
          screenX={pinnedScreenPos.x}
          screenY={pinnedScreenPos.y}
          name={pinnedName}
          rows={pinnedVoteTypes}
          interactive
          edgeId={pinnedVoteEdgeId}
          mode={themeMode}
          myVotesVersion={myVotesVersion}
          shareUrl={pinnedPointLatLng
            ? buildSelectionUrl(pinnedPointLatLng, pinnedWinner?.label ?? pinnedVoteTypes[0]?.label)
            : null}
          onVote={castProposalVote}
          onRemove={onRemoveSelectedRef.current}
          onHoverChange={(over) => {
            overModalRef.current = over;
            // Clear any transient hover card when entering the pinned modal so
            // it doesn't linger over the selection.
            if (over && hoverTargetRef.current) {
              hoverTargetRef.current = null;
              setHoverTarget(null);
              syncHighlights();
            }
          }}
        />,
        mapContainer
      )}
      {showHoverTooltip && createPortal(
        <ProposalCard
          winner={hoverWinner}
          screenX={tooltipPos.x}
          screenY={tooltipPos.y}
          name={tooltipName}
          rows={hoverVoteTypes}
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

function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5" />
    </svg>
  );
}

interface ProposalCardProps {
  winner: VoteTypeWinner | null;
  screenX: number;
  screenY: number;
  name: string;
  rows: VoteTypeRow[];
  interactive?: boolean;
  edgeId?: number | null;
  mode?: string;
  myVotesVersion?: number;
  shareUrl?: string | null;
  onVote?: (edgeId: number | null, label: string, dir: VoteDirection) => void;
  onRemove?: () => void;
  /** Notifies when the cursor enters/leaves the card (pinned modal only) so the
   *  map hover can yield beneath it. */
  onHoverChange?: (over: boolean) => void;
}

function ProposalCard({
  winner, screenX, screenY, name, rows,
  interactive = false, edgeId = null, mode = "", myVotesVersion, shareUrl = null, onVote, onRemove, onHoverChange,
}: ProposalCardProps) {
  const [copied, setCopied] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  const flipped = screenX > window.innerWidth / 2;
  const icon = winner ? iconForLabel(winner.label) : null;
  void myVotesVersion; // read so the card re-renders when my votes change

  // If the card unmounts with the cursor still inside it (e.g. clicking its
  // own X), no mouseleave ever fires — release the hover claim explicitly so
  // the map hover doesn't stay suppressed.
  const onHoverChangeRef = useRef(onHoverChange);
  useEffect(() => { onHoverChangeRef.current = onHoverChange; }, [onHoverChange]);
  useEffect(() => () => { onHoverChangeRef.current?.(false); }, []);

  // Keep the card fully on-screen: from its untransformed size (offsetWidth/Height
  // ignore the CSS scale-in animation) + the known anchor transform, compute where
  // each edge lands and nudge it back in from any viewport edge it overflows.
  const [corr, setCorr] = useState({ x: 0, y: 0 });
  useLayoutEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const M = 8, GAP = 16; // viewport margin + the anchor offset baked into the CSS transform
    const w = el.offsetWidth, h = el.offsetHeight;
    const baseLeft = flipped ? screenX - w - GAP : screenX + GAP;
    const baseTop = screenY - h - GAP; // card sits above the point
    let x = 0, y = 0;
    if (baseLeft < M) x = M - baseLeft;
    else if (baseLeft + w > window.innerWidth - M) x = window.innerWidth - M - (baseLeft + w);
    if (baseTop < M) y = M - baseTop;
    else if (baseTop + h > window.innerHeight - M) y = window.innerHeight - M - (baseTop + h);
    setCorr((prev) => (Math.abs(prev.x - x) > 0.5 || Math.abs(prev.y - y) > 0.5 ? { x, y } : prev));
  }, [screenX, screenY, flipped, rows]);

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
    const myVote = edgeId != null ? getMyVote(mode, edgeId, row.label) : 0;
    return (
      <button
        type="button"
        className={`${cls} is-btn`}
        disabled={myVote === dir || edgeId == null}
        aria-pressed={myVote === dir}
        title={dir === -1 ? "Downvote" : "Upvote"}
        onClick={() => onVote?.(edgeId, row.label, dir)}
      >
        {inner}
      </button>
    );
  };

  return (
    <div
      ref={cardRef}
      className={`graph-indicator-modal graph-proposal-card${interactive ? " is-interactive" : " is-hover"}${flipped ? " modal-flipped" : ""}`}
      style={{ left: screenX + corr.x, top: screenY + corr.y }}
      onMouseEnter={onHoverChange ? () => onHoverChange(true) : undefined}
      onMouseLeave={onHoverChange ? () => onHoverChange(false) : undefined}
    >
      {winner && (
        <div className="graph-indicator-modal-header">
          <span className="graph-indicator-modal-glyph">
            {icon ? (
              <img className="graph-indicator-modal-icon" src={iconSrc(icon)} alt="" />
            ) : (
              <span className="graph-indicator-modal-disc" style={{ background: hashLabelToColor(winner.label) }} />
            )}
          </span>
          <div className="graph-indicator-modal-headtext">
            <div className="graph-indicator-modal-eyebrow">Top Proposal</div>
            <div className="graph-indicator-modal-label">{winner.label}</div>
          </div>
        </div>
      )}
      {interactive && (shareUrl || onRemove) && (
        <div className="graph-proposal-tools">
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
          {onRemove && (
            <button
              type="button"
              className="graph-proposal-tool graph-proposal-close"
              title="Remove"
              aria-label="Remove this point"
              onClick={() => onRemove()}
            >✕</button>
          )}
        </div>
      )}
      <div className="graph-indicator-modal-body">
        {name && <div className="graph-tooltip-name">{name}</div>}
        <div className="graph-tooltip-meta">
          {rows.length > 0
            ? `${rows.length} proposal${rows.length !== 1 ? "s" : ""}`
            : "no proposals yet"}
        </div>
        {rows.length > 0 && (
          <div className="graph-proposal-rows">
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
    </div>
  );
}
