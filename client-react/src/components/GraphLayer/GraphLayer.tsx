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
import { useGraphSnap, useTheme, useHeatmap, useGhostPin } from "../../context";
import type { GraphData, ProposalMatch } from "../../types";
import { makeVoteTypeIcon } from "./voteTypeIcon";
import { suggestionGlyphForLabel } from "../../utils/suggestionIcon";
import { selectTopProposals, topLabelForEdges, type VoteTypeWinner } from "./topProposals";
import { applyMyVoteChange, applyEdgeVoteChange, applyAuthoritativeCounts } from "./voteApply";
import { iconForLabel, iconSrc, mapStyleForTheme } from "../../themes";
import { buildHeatRampStops, sampleHeatRamp } from "../../mapStyles";
import {
  getCachedTopology,
  setCachedTopology,
  getCachedTopologyBin,
  setCachedTopologyBin,
  getCachedVotes,
  setCachedVotes,
  clearGraphCache,
} from "../../utils/graphCache";
import { getVote, reconcileEdge, setVoteTypeMap, type VoteDirection } from "../../utils/voteStore";
import { castVotes } from "../../utils/castVote";
import { useVotesVersion } from "../../utils/useVotesVersion";
import { getVoterId } from "../../utils/voterIdentity";
import { isHoverSuppressed } from "../../utils/touchHover";
import { buildSelectionUrl, copyToClipboard } from "../../utils/shareLink";
import { CheckIcon } from "../CheckIcon";

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

/** Great-circle distance between two lat/lng points, in meters. */
function haversineMeters(
  lat1: number, lng1: number, lat2: number, lng2: number
): number {
  const R = 6371000;
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLng = (lng2 - lng1) * toRad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Loop-based max to avoid stack overflow with large arrays. */
function arrayMax(arr: number[]): number {
  let max = 0;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > max) max = arr[i];
  }
  return max;
}

// Floor for the heat normalization denominator. Brightness is
// log(votes+1)/log(scale+1); using a map's own max as `scale` makes a quiet map
// (small max) blow out — log() pushes its 1–2-vote edges to near-full heat, so
// e.g. SF (max ~18) lit up almost every edge while NYC (max ~400) stayed mostly
// cool. Flooring the denominator at this many votes means a low-traffic map is
// measured against a fixed scale and renders proportionally cooler, while a
// busy map past the floor still uses its own (larger) max for full dynamic range.
const HEAT_FULL_SCALE = 50;

// ---------------------------------------------------------------------------
// Heatmap color stops — flame cross-section
// ---------------------------------------------------------------------------
// Each pass uses a different color and width so the gradient runs ACROSS the
// stroke (halo on the outside, hot core on the inside) rather than ALONG it.
// The ramp + blend mode come from the active map style (mapStyles.ts): dark
// styles blend additively (`lighter`/`screen`) for Strava-style intersection
// brightening; the light style blends via `multiply` so heat darkens the map.

// Slightly hold the heatmap back so the top-proposal pins read as the brightest
// thing on the map. Applied as canvas element opacity (atop the blend mode), so
// it dims the whole heat field — and its faint zero-vote network skeleton —
// uniformly without touching per-edge intensity math.
const HEATMAP_OPACITY = "0.72";

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
// While a feature is pinned, a re-click within this screen distance of it keeps
// the SAME selection rather than re-resolving to a neighbour/endpoint. Stops the
// open card — which occludes its own icon — from drifting as you click near it.
const STICKY_RESELECT_PX = 44;

// Highlight ring dimensions — matched to the desire path SVG filter so hover
// and pinned highlights look identical to the selected path:
//   - Edge ring: 7px wide stroke with a 4px hole = 1.5px white border each side
//   - Node ring: 5px outer radius with 3.5px hole = 1.5px white border
//   - Interior alpha: 0.12 (matches feColorMatrix in RouteLayer)
const HIGHLIGHT_RING_WIDTH = 7;
const HIGHLIGHT_INNER_WIDTH = 4;
const HIGHLIGHT_NODE_OUTER_R = 5;
const HIGHLIGHT_NODE_INNER_R = 3.5;
const HIGHLIGHT_INTERIOR_ALPHA = 0.12;

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

// Cap on how many top-proposal indicators show on the map. Each surviving
// EDGE consumes one slot (winners are deduped per edge first), so the 20 most
// net-voted segments survive — drawn from the top few edges per vote type.
const TOP_PROPOSAL_LIMIT = 20;

// "Spread to grid" interaction for crowded indicators. When a top-proposal
// icon is clicked and other icons sit within CLUSTER_RADIUS_PX of it on screen,
// the first click is swallowed: instead of selecting, the cluster fans out into
// a grid so each icon becomes individually clickable, then snaps back after
// SPREAD_DURATION_MS.
const CLUSTER_RADIUS_PX = 26; // screen distance that counts as "clustered"
const SPREAD_CELL_PX = 38;    // grid cell pitch when fanned out
const SPREAD_DURATION_MS = 2200;
const SPREAD_ANIM_MS = 280;   // keep in sync with the CSS transition

// Movement (px²) before a press on an exploded proposal becomes a mid-drag rather
// than a tap (which selects). Mirrors the path-drag's tap-vs-drag intent.
const MID_DRAG_THRESHOLD_SQ = 16; // (4px)²
// Dotted rubber-band for the mid being dragged out of a proposal — matches the
// path-drag and waypoint-drag trails (usePathDrag / RouteMarker).
const MID_DRAG_TRAIL_STYLE: L.PolylineOptions = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round",
};

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
    // A malformed/stale topology can reference a node index that doesn't exist.
    // Index a degenerate (0,0) box for it instead of dereferencing undefined —
    // Flatbush requires exactly edges.length adds, so we can't skip the entry.
    if (!fromNode || !toNode) {
      idx.add(0, 0, 0, 0);
      continue;
    }
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
  map: L.Map,
  px: number, py: number,
  index: Flatbush | null,
  queryLng: number,
  queryLat: number
): number | null {
  if (data.edges.length === 0) return null;

  const checkEdge = (i: number, currentBestDist: number, currentBestIdx: number): [number, number] => {
    const [fromIdx, toIdx] = data.edges[i];
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
  map: L.Map,
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

/**
 * The representative on-graph coordinate for a resolved target: an edge's
 * midpoint (where its indicator icon sits) or a node's position. This is the
 * ONE place that maps a target to a point — the pinned modal uses it as both
 * its anchor AND its React key, so the card tracks the selected FEATURE and is
 * never positioned by the raw click coordinates.
 */
function targetLatLng(
  target: HoverTarget | null,
  data: Pick<GraphData, "nodes" | "edges"> | null
): { lat: number; lng: number } | null {
  if (!target || !data) return null;
  if (target.kind === "edge") {
    const edge = data.edges[target.index];
    if (!edge) return null;
    const from = data.nodes[edge[0]];
    const to = data.nodes[edge[1]];
    if (!from || !to) return null;
    return { lat: (from[0] + to[0]) / 2, lng: (from[1] + to[1]) / 2 };
  }
  const node = data.nodes[target.index];
  return node ? { lat: node[0], lng: node[1] } : null;
}

// ---------------------------------------------------------------------------
// Tooltip content helper (shared by hover and pinned tooltips)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Node adjacency builder — used to derive node votes from edges
// ---------------------------------------------------------------------------

/**
 * Decode the binary topology (server graph_registry.topology_binary) into the same
 * {nodes, edges} shape the JSON path yields — WITHOUT JSON.parse-ing a ~150MB
 * string, the allocation that OOM-crashes mobile Safari on the NYC graph. Layout:
 * 12-byte header [magic, uint32 nNodes, uint32 nEdges], then nNodes×2 int32
 * (lat, lon ×1e7), then nEdges×2 uint32 (from_idx, to_idx). Edge names are dropped
 * (street tooltips reverse-geocode instead); the edges index is the canonical edge id.
 */
// 'GTB1' little-endian as a uint32 (G=0x47, T=0x54, B=0x42, '1'=0x31).
const TOPOLOGY_BIN_MAGIC = 0x31425447;

function decodeTopologyBin(buf: ArrayBuffer): Pick<GraphData, "nodes" | "edges"> {
  // Validate the header BEFORE allocating. A stale/truncated/corrupt cached blob
  // (or a non-binary error body that slipped through) would otherwise be read as
  // garbage nNodes/nEdges — allocating multi-GB arrays that OOM-crash the tab, or
  // building edges that reference nonexistent nodes (the downstream crash). On any
  // inconsistency we throw, and callers fall back to a fresh fetch / JSON.
  if (buf.byteLength < 12) {
    throw new Error(`topology blob too small (${buf.byteLength} bytes)`);
  }
  const head = new Uint32Array(buf, 0, 3); // [magic, nNodes, nEdges]
  if (head[0] !== TOPOLOGY_BIN_MAGIC) {
    throw new Error(`bad topology magic 0x${head[0].toString(16)}`);
  }
  const nNodes = head[1];
  const nEdges = head[2];
  const expected = 12 + nNodes * 8 + nEdges * 8;
  if (buf.byteLength !== expected) {
    throw new Error(
      `topology size mismatch: ${buf.byteLength} bytes, expected ${expected} ` +
        `(nNodes=${nNodes}, nEdges=${nEdges})`,
    );
  }
  const coords = new Int32Array(buf, 12, nNodes * 2);
  const ends = new Uint32Array(buf, 12 + nNodes * 8, nEdges * 2);
  const nodes: [number, number][] = new Array(nNodes);
  for (let i = 0; i < nNodes; i++) {
    nodes[i] = [coords[2 * i] / 1e7, coords[2 * i + 1] / 1e7];
  }
  const edges: [number, number, string][] = new Array(nEdges);
  for (let i = 0; i < nEdges; i++) {
    // Clamp out-of-range node indices to 0 rather than leaving a dangling ref
    // that crashes buildEdgeIndex/redraw — a degenerate self-edge is harmless.
    const a = ends[2 * i] < nNodes ? ends[2 * i] : 0;
    const b = ends[2 * i + 1] < nNodes ? ends[2 * i + 1] : 0;
    edges[i] = [a, b, ""];
  }
  return { nodes, edges };
}

/**
 * True when a vote payload is indexed against the SAME topology we hold. Vote
 * arrays are sized to the server's current graph; the server stamps n_edges /
 * n_nodes onto the payload so the client can reject votes that don't line up
 * with its (possibly day-old cached) topology — painting a mismatch is what
 * indexed past the array ends and crashed mobile Safari. Falls back to the
 * edge_votes length when the dimensions aren't present (older server).
 */
function votesMatchTopology(
  voteData: { n_edges?: number; n_nodes?: number; edge_votes?: unknown[] },
  topology: Pick<GraphData, "nodes" | "edges"> | null,
): boolean {
  if (!topology) return false;
  const nEdges = voteData.n_edges ?? voteData.edge_votes?.length;
  if (nEdges != null && nEdges !== topology.edges.length) return false;
  if (voteData.n_nodes != null && voteData.n_nodes !== topology.nodes.length) return false;
  return true;
}

function buildNodeAdj(topology: Pick<GraphData, "nodes" | "edges">): number[][] {
  const adj: number[][] = new Array(topology.nodes.length);
  for (let i = 0; i < adj.length; i++) adj[i] = [];
  for (let i = 0; i < topology.edges.length; i++) {
    adj[topology.edges[i][0]].push(i);
    adj[topology.edges[i][1]].push(i);
  }
  return adj;
}

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
  position, icon, zIndexOffset, interactive = true, onActivate, onDeactivate, onClick, onMidDragDown,
}: {
  position: [number, number];
  icon: L.DivIcon;
  zIndexOffset: number;
  /** When false the marker is a passive visual (no events) — used for a linked
   *  or on-path proposal so the path/kite underneath takes the drag/click. */
  interactive?: boolean;
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
    <Marker
      position={position}
      icon={icon}
      zIndexOffset={zIndexOffset}
      interactive={interactive}
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

export function GraphLayer({ onSnap, pinnedPoint, startPoint, endPoint, ghostWaypoints = EMPTY_WAYPOINTS, dragPoint = null, hoverProposalPoint = null, onWaypointMatch, onIndicatorClick, clusterExploderRef, onRemoveProposal, onRemoveSelected, suppressHover = false, pathEdgeIds = null, onProposalDrop, onPinnedResolve }: GraphLayerProps) {
  const map = useMap();
  const { subscribeToDelta } = useWebSocketContext();
  const { setSnapFn, setResolveVoteEdgeId, setResolveTopLabelForPath, setCurrentSnap, isDraggingRef: graphDraggingRef, snapToGraph, setDragging } = useGraphSnap();
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
  const topologyRef = useRef<Pick<GraphData, "nodes" | "edges"> | null>(null);
  const edgeIndexRef = useRef<Flatbush | null>(null);
  const nodeIndexRef = useRef<Flatbush | null>(null);
  const redrawTimeoutRef = useRef<number | null>(null);
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
  const maxVotesRevRef = useRef(-1);

  // Node adjacency list — node index → [edge indices]. Built once from topology,
  // used to derive node votes from edge totals (max of adjacent edges).
  const nodeAdjRef = useRef<number[][] | null>(null);

  // Last-seen revision for gap detection
  const lastRevRef = useRef(0);

  // Deltas received before the initial vote fetch completes
  const pendingDeltasRef = useRef<import("../../types").VoteDelta[]>([]);

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

  // Register graph snap function for use by path drag. Uses hitTest directly
  // (in-radius only, null when out of range) so dragging stays free when far
  // from the graph — unlike resolveSelection, which always resolves to an edge.
  useEffect(() => {
    setSnapFn((m: L.Map, lat: number, lng: number) => {
      // Proposals are sticky drop targets: a drop near one links to its midpoint.
      const sticky = stickyProposalSnapRef.current(lat, lng);
      if (sticky) return { lat: sticky.lat, lng: sticky.lng };
      const data = graphDataRef.current;
      if (!data) return null;
      const pt = m.latLngToContainerPoint([lat, lng]);
      const result = hitTest(
        data, m, pt.x, pt.y, lat, lng,
        edgeIndexRef.current, nodeIndexRef.current
      );
      if (!result) return null;
      return { lat: result.snapLat, lng: result.snapLng };
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
  // Pinned target (node or edge) for highlight and tooltip
  const pinnedTargetRef = useRef<HoverTarget | null>(null);
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

  // Increments when graph vote data mutates, forcing tooltip re-render. The
  // value is read so the vote-type decode memos re-run in lockstep with the
  // winners recompute (both happen in refreshGraphDisplay) — avoiding a stale
  // winner indexing into emptied vote data ("no votes yet" on a top proposal).
  const [graphVoteVersion, setGraphVoteVersion] = useState(0);
  void graphVoteVersion; // read so decode memos/winners re-render in lockstep

  // Increments on any vote change (this map's proposal modal, the top-bar
  // banner, or server reconciliation) so the pinned-selection resolve effect
  // re-runs and the modal re-evaluates +/- state. The store is the single
  // signal — see useVotesVersion.
  const votesVersion = useVotesVersion();

  // Vote-type indicator markers — top-voted segment per vote type
  const [winners, setWinners] = useState<VoteTypeWinner[]>([]);
  const [currentZoom, setCurrentZoom] = useState<number>(() => map.getZoom());
  const iconCacheRef = useRef<Map<string, L.DivIcon>>(new Map());

  // "Fan out crowded icons into a grid" state. Only ONE cluster is ever fanned
  // out at a time — exploding a new cluster replaces (collapses) whatever was
  // open. The map keys a winner's EDGE index -> overridden [lat, lng] and the
  // render reads it as a per-icon position override. (Edge, not legendIdx: a vote
  // type can win on several edges — top-N-per-type — so legendIdx is no longer
  // unique per icon, but edgeIdx always is.)
  //
  // A spread starts TRANSIENT — a hover/snap-back timer collapses it. Picking one
  // of its boxes LOCKS it (`spreadLockedRef`), so it persists with no timer until
  // the selection clears or the map pans/zooms. Locked vs transient is the ONLY
  // difference between the two; the position map is identical, so the lock is just
  // a ref flag. Refs mirror the state for synchronous reads in handlers.
  type SpreadMap = Map<number, [number, number]>;
  const [spread, setSpread] = useState<SpreadMap | null>(null);
  const spreadRef = useRef<SpreadMap | null>(null);
  const spreadLockedRef = useRef(false);
  const spreadTimeoutRef = useRef<number | null>(null);
  // collapseSpread is defined far below; this ref lets earlier effects call it.
  const collapseSpreadRef = useRef<() => void>(() => {});

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
    scheduleRedrawRef.current();
  }, [setStableWinners]);

  const refreshGraphDisplayRef = useRef(refreshGraphDisplay);
  useEffect(() => { refreshGraphDisplayRef.current = refreshGraphDisplay; }, [refreshGraphDisplay]);

  // Stable ref for the indicator-click callback (avoids re-running the
  // useMemo that builds marker components every time the parent re-renders).
  const onIndicatorClickRef = useRef(onIndicatorClick);
  useEffect(() => { onIndicatorClickRef.current = onIndicatorClick; }, [onIndicatorClick]);

  const onRemoveSelectedRef = useRef(onRemoveSelected);
  useEffect(() => { onRemoveSelectedRef.current = onRemoveSelected; }, [onRemoveSelected]);

  const onRemoveProposalRef = useRef(onRemoveProposal);
  useEffect(() => { onRemoveProposalRef.current = onRemoveProposal; }, [onRemoveProposal]);

  // One delegated handler for every proposal's [×] badge, in CAPTURE phase on the
  // map container so it pre-empts both Leaflet's map click and the indicator
  // marker's own click (which would otherwise also restart/select). The badge is
  // pointer-events:auto inside an icon that may be pointer-events:none, so without
  // this stop the clickthrough trap (see modal_clickthrough memory) would fire the
  // map click. data-x-edge tells us which proposal's waypoint to drop.
  useEffect(() => {
    const container = map.getContainer();
    const onCaptureClick = (e: MouseEvent) => {
      const badge = (e.target as HTMLElement | null)?.closest?.(".vote-type-indicator-x");
      if (!badge) return;
      e.stopPropagation();
      e.preventDefault();
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
      ? g.edges.map((_, i) => i)
      : winners.map((w) => w.edgeIdx);
    let best: number | null = null;
    let bestDist = Infinity;
    for (const edgeIdx of candidates) {
      const edge = g.edges[edgeIdx];
      if (!edge) continue;
      const [fromIdx, toIdx] = edge;
      const fromNode = g.nodes[fromIdx];
      const toNode = g.nodes[toIdx];
      if (!fromNode || !toNode) continue;
      const midLat = (fromNode[0] + toNode[0]) / 2;
      const midLng = (fromNode[1] + toNode[1]) / 2;
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
    const adj = nodeAdjRef.current?.[nodeIdx];
    if (!g?.edge_vote_types || !adj) return null;
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
      const e = g.edges[ei];
      if (e) pathPairs.add(pairKey(e[0], e[1]));
    }
    const next = new Set<number>();
    for (const w of winners) {
      // Skip proposals that ARE waypoints — `role` styles those.
      if (w.edgeIdx === startEdgeIdx || w.edgeIdx === endEdgeIdx || midEdgeSet.has(w.edgeIdx)) continue;
      const e = g.edges[w.edgeIdx];
      if (!e) continue;
      if (pathPairs.has(pairKey(e[0], e[1]))) next.add(w.edgeIdx);
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
      const edge = g.edges[w.edgeIdx];
      if (!edge) continue;
      const a = g.nodes[edge[0]], b = g.nodes[edge[1]];
      if (!a || !b) continue;
      const lat = (a[0] + b[0]) / 2, lng = (a[1] + b[1]) / 2;
      // Hit-test at where the icon is actually drawn; snap to the real midpoint.
      const override = spread?.get(w.edgeIdx);
      const mp = map.latLngToContainerPoint(override ? [override[0], override[1]] : [lat, lng]);
      if (pt.x < mp.x - ICON_HALF_W || pt.x > mp.x + ICON_HALF_W
          || pt.y < mp.y - ICON_TOP_PX || pt.y > mp.y + ICON_BOTTOM_PX) continue;
      const dx = pt.x - mp.x, dy = pt.y - (mp.y - 18);
      const d = dx * dx + dy * dy;
      if (d < bestD) { bestD = d; best = { edgeIdx: w.edgeIdx, lat, lng }; }
    }
    return best;
  }, [isStationNetwork, map]);

  // Sticky snap: a drag on/near a proposal icon snaps to its exact midpoint so
  // the waypoint links cleanly. Consulted by the registered snapFn (the committed
  // drop) and the mousemove drag branch (the live trail).
  const stickyProposalSnap = useCallback(
    (lat: number, lng: number) => proposalIconAt(map.latLngToContainerPoint([lat, lng])),
    [proposalIconAt, map]
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

  // A waypoint drag whose live position hovers over a crowded proposal cluster
  // fans it out — the SAME exploder a tap uses — so you can drop onto a specific
  // icon instead of blindly hitting whichever sits on top. The exploder no-ops
  // once a spread is open (and when not over a cluster), so firing it on every
  // drag frame is cheap. clusterExploderRef.current is assigned during the
  // indicatorMarkers render above, so it's set by the time this effect runs.
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
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      if (hoverCanvas.parentNode) hoverCanvas.parentNode.removeChild(hoverCanvas);
    };
  }, [map, mapStyle.heatBlend]);

  // Full vote fetch — used on initial load and revision-gap recovery.
  const fetchVotes = useCallback(async () => {
    if (!topologyRef.current) return;
    try {
      const url = `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}`;
      const response = await fetch(url, { cache: "no-store", headers: passcodeHeaders() });
      if (!response.ok) throw new Error(`Vote fetch failed: ${response.status}`);
      const voteData = await response.json();
      // If the graph was rebuilt mid-session, these votes no longer line up with
      // our topology. Don't paint the mismatch (it would crash); leave the current
      // heatmap and let the next full page load reconcile against fresh topology.
      if (!votesMatchTopology(voteData, topologyRef.current)) {
        console.warn("Skipping vote refresh: topology/vote dimension mismatch");
        return;
      }
      graphDataRef.current = { ...topologyRef.current!, ...voteData };
      lastRevRef.current = voteData.rev ?? 0;
      // Teach the vote store this map's label→id map so packed lookups resolve.
      setVoteTypeMap(voteData.vote_types);

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
    lastRevRef.current = delta.rev;
  }, []);

  // Load topology + votes on mount, preferring persisted caches.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      // 1. Resolve the graph version, then load topology from IndexedDB when it
      //    matches — skipping the multi-MB download and JSON parse entirely.
      let topology: GraphData | null = null;
      let version: string | null = null;
      let usedCachedTopology = false;
      // Assigned inside the try below; the step-3 stale-topology guard reuses it.
      let fetchFreshTopology: ((forceReload?: boolean) => Promise<GraphData | null>) | null = null;
      try {
        try {
          const vr = await fetch(withMap(`${CONFIG.apiUrl}/graph-version`), { headers: passcodeHeaders() });
          if (vr.ok) version = (await vr.json()).version ?? null;
        } catch {
          // Version probe failed — fall back to a direct topology fetch.
        }

        // Cache-bust the topology URL by graph version. /graph-topology is served
        // with max-age=86400, so after a deploy that renumbers edge ids the browser
        // HTTP cache would otherwise serve a STALE topology under the same URL —
        // which, paired with fresh (new-edge-id) votes, mismatches and crashes
        // mobile Safari. A version-scoped URL guarantees a fresh fetch on change
        // while still caching repeat loads of the same version.
        const withVersion = (url: string) =>
          version ? url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(version) : url;

        // Fetch the topology from the NETWORK (not IndexedDB). `forceReload` adds
        // cache:"reload" so the browser HTTP cache is bypassed too — used by the
        // stale-topology recovery path in step 3, where even the version-busted
        // URL may have been served stale from Safari's HTTP cache.
        const fetchTopologyFromNetwork = async (forceReload = false): Promise<GraphData | null> => {
          const init: RequestInit = forceReload
            ? { headers: passcodeHeaders(), cache: "reload" }
            : { headers: passcodeHeaders() };
          // Station networks (tiny, names matter) use the JSON topology; large
          // street graphs use the binary topology so a phone never JSON.parse-es a
          // ~150MB string (the OOM that crashed mobile Safari on the NYC graph).
          if (isStationNetwork) {
            const r = await fetch(withVersion(withMap(`${CONFIG.apiUrl}/graph-topology`)), init);
            if (!r.ok) throw new Error(`Topology fetch failed: ${r.status}`);
            const t = await r.json();
            if (version && t) setCachedTopology(version, t);
            return t;
          }
          try {
            const r = await fetch(
              withVersion(withMap(`${CONFIG.apiUrl}/graph-topology?format=bin`)),
              init,
            );
            if (!r.ok) throw new Error(`Binary topology fetch failed: ${r.status}`);
            const buf = await r.arrayBuffer();
            const t = decodeTopologyBin(buf) as GraphData;
            if (version) setCachedTopologyBin(version, buf);
            return t;
          } catch (binErr) {
            // Older server without the binary endpoint — fall back to JSON.
            console.warn("Binary topology unavailable, using JSON:", binErr);
            const r = await fetch(withVersion(withMap(`${CONFIG.apiUrl}/graph-topology`)), init);
            if (!r.ok) throw new Error(`Topology fetch failed: ${r.status}`);
            const t = await r.json();
            if (version && t) setCachedTopology(version, t);
            return t;
          }
        };
        fetchFreshTopology = fetchTopologyFromNetwork;

        if (version) {
          if (isStationNetwork) {
            const cached = await getCachedTopology<GraphData>(version);
            if (cached) { topology = cached; usedCachedTopology = true; }
          } else {
            const buf = await getCachedTopologyBin(version);
            // Decode can throw on a corrupt/truncated cached blob — ignore the
            // cache and fall through to a fresh network fetch rather than aborting.
            if (buf) {
              try {
                topology = decodeTopologyBin(buf) as GraphData;
                usedCachedTopology = true;
              } catch (decodeErr) {
                console.warn("Cached binary topology was corrupt, refetching:", decodeErr);
              }
            }
          }
        }
        if (!topology) {
          topology = await fetchTopologyFromNetwork();
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
      scheduleRedrawRef.current();

      // 2. Paint immediately from cached votes (same graph version only) so the
      //    heatmap appears without waiting on the network. Skipped when the
      //    topology was freshly downloaded, since edge indices may have shifted.
      if (usedCachedTopology && version) {
        const cachedVotes = await getCachedVotes<Partial<GraphData>>(getMapSlug() || themeMode, version);
        if (cancelled) return;
        if (cachedVotes) {
          graphDataRef.current = { ...topology, ...cachedVotes };
          lastRevRef.current = cachedVotes.rev ?? 0;
          setVoteTypeMap(cachedVotes.vote_types);
          refreshGraphDisplayRef.current();
          setHeatmapLoaded();
        }
      }

      // 3. Authoritative vote fetch — replaces the cached snapshot and replays
      //    any deltas that arrived while the fetch was in flight.
      try {
        const r = await fetch(
          `${CONFIG.apiUrl}/graph-votes?map=${getMapSlug()}&mode=${encodeURIComponent(themeMode)}`,
          { headers: passcodeHeaders() }
        );
        if (!r.ok) throw new Error(`Vote fetch failed: ${r.status}`);
        const voteData = await r.json();
        if (cancelled) return;

        // Stale-topology guard. The vote arrays are indexed against the server's
        // CURRENT graph. If our topology (often from the day-long cache) has a
        // different node/edge count, painting these votes indexes past the array
        // ends — the mobile-Safari crash. Refetch the topology fresh (bypassing
        // both the IndexedDB and HTTP caches), rebuild the indices, and only apply
        // the votes once they line up. If still mismatched, drop the persisted
        // caches and bail rather than paint a crash.
        if (!votesMatchTopology(voteData, topologyRef.current)) {
          console.warn("Topology/vote mismatch — refetching fresh topology", {
            topoEdges: topologyRef.current?.edges.length,
            voteEdges: voteData.n_edges ?? voteData.edge_votes?.length,
          });
          let fresh: GraphData | null = null;
          try {
            fresh = fetchFreshTopology ? await fetchFreshTopology(true) : null;
          } catch (refetchErr) {
            console.error("Fresh topology refetch failed:", refetchErr);
          }
          if (cancelled) return;
          if (fresh && votesMatchTopology(voteData, fresh)) {
            topology = fresh;
            topologyRef.current = fresh;
            edgeIndexRef.current = buildEdgeIndex(fresh);
            nodeIndexRef.current = buildNodeIndex(fresh);
            nodeAdjRef.current = buildNodeAdj(fresh);
          } else {
            await clearGraphCache();
            setHeatmapLoaded();
            return;
          }
        }

        graphDataRef.current = { ...topologyRef.current!, ...voteData };
        lastRevRef.current = voteData.rev ?? 0;
        setVoteTypeMap(voteData.vote_types);
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
  }, [applyDeltaToGraphData, setHeatmapLoaded]);

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

      applyMyVoteChange(data, adj, detail.edgeIds, detail.label, detail.prevDir, detail.newDir);
      refreshGraphDisplayRef.current();
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
      const node = data.nodes[nodeIndex];
      if (!node) return;
      const pt = map.latLngToContainerPoint([node[0], node[1]]);

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
      const edge = data.edges[edgeIndex];
      if (!edge) return;
      const [fromIdx, toIdx] = edge;
      const fromScreen = map.latLngToContainerPoint([data.nodes[fromIdx][0], data.nodes[fromIdx][1]]);
      const toScreen = map.latLngToContainerPoint([data.nodes[toIdx][0], data.nodes[toIdx][1]]);

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

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!data.edges || !data.nodes) return;

    const nodes = data.nodes;
    const edges = data.edges;
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
      const n = nodes.length;
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
      const p = map.latLngToLayerPoint([nodes[i][0], nodes[i][1]]);
      xs[i] = p.x;
      ys[i] = p.y;
      done[i] = 1;
    };

    const drawSeg = (i: number) => {
      const a = edges[i][0];
      const b = edges[i][1];
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
    const visible = edgeIndex
      ? edgeIndex.search(
          bounds.getWest() - mLng,
          bounds.getSouth() - mLat,
          bounds.getEast() + mLng,
          bounds.getNorth() + mLat,
        )
      : null;
    const count = visible ? visible.length : edges.length;
    const edgeAt = (k: number): number => (visible ? visible[k] : k);

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
    const rampStops = buildHeatRampStops(heat);
    const sampleRamp = (t: number): string => sampleHeatRamp(rampStops, t);

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
        ctx.strokeStyle = heat.peak;
        drawSeg(i);
      }
    }

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1.0;

    // Record the map state this bitmap was painted at, so a subsequent zoom
    // animation can scale/translate the existing pixels to the target zoom.
    drawStateRef.current = { zoom, nw: map.containerPointToLatLng([0, 0]) };

    redrawHoverHighlightRef.current();
  }, [map, mapStyle.heat, mapStyle.heatComposite, mapStyle.selection]);

  // Schedule redraw
  const scheduleRedraw = useCallback(() => {
    if (redrawTimeoutRef.current) cancelAnimationFrame(redrawTimeoutRef.current);
    redrawTimeoutRef.current = requestAnimationFrame(redraw);
  }, [redraw]);

  const scheduleRedrawRef = useRef(scheduleRedraw);
  useEffect(() => { scheduleRedrawRef.current = scheduleRedraw; }, [scheduleRedraw]);

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

    map.on("zoomstart", handleZoomStart);
    map.on("zoomanim", handleZoomAnim);
    map.on("zoomend", handleZoomEnd);
    map.on("moveend", handleMoveEnd);
    map.on("resize", handleResize);
    return () => {
      map.off("zoomstart", handleZoomStart);
      map.off("zoomanim", handleZoomAnim);
      map.off("zoomend", handleZoomEnd);
      map.off("moveend", handleMoveEnd);
      map.off("resize", handleResize);
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
        if (!data?.edges) {
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
            const sel = resolveSelectionRef.current(e.latlng.lat, e.latlng.lng);
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
          redrawHoverHighlightRef.current();
        }

        if (newTarget) {
          setTooltipPos({ x: e.originalEvent.clientX, y: e.originalEvent.clientY });
        }

        // Snap position: only compute when actually dragging (path-drag system
        // is the only consumer). Skipping when idle saves ~100k iterations
        // on every mousemove. Uses hitTest directly (in-radius only) so the drag
        // stays free when far from the graph — not resolveSelection's edge fallback.
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
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
        redrawHoverHighlightRef.current();
      }
    };
    map.on("click", clearStaleHover);
    return () => { map.off("click", clearStaleHover); };
  }, [map]);

  // Track pinned tooltip screen position on map pan/zoom
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
    const sel = resolveSelection(pinnedLat, pinnedLng, pinnedEdgeOverrideRef.current);
    pinnedEdgeOverrideRef.current = null;
    let target = sel?.target ?? null;

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
    // Skip entirely when an override was set: an indicator click and the
    // re-pin in castProposalVote both name an EXACT edge to keep selected. A
    // vote can momentarily drop that edge out of the spaced `winners` list, so
    // without this guard isProposalTarget reads false and reconciliation re-snaps
    // the modal onto a neighbouring winner — the selection appears to jump to a
    // different proposal the instant you vote.
    const isProposalTarget = target?.kind === "edge"
      && (isStationNetwork || winners.some((w) => w.edgeIdx === target!.index));
    if (!hadOverride && !isProposalTarget) {
      const snapped = nearestProposalEdgeIndex(pinnedLat, pinnedLng, 8);
      if (snapped !== null) target = { kind: "edge", index: snapped };
    }

    // Node → proposal-edge upgrade. A node modal merges the proposals of every
    // incident edge, but a vote lands on one edge — so selecting an intersection
    // (a map click that snaps to a node, or a deep link whose slat/slng IS a
    // node's coords) voted an arbitrary incident edge and then collapsed onto it.
    // Resolve a node that has proposals to the edge owning its strongest one, so
    // the selection is an edge throughout. Bare intersections (no proposals) stay
    // a node — their modal is just an unvotable "no proposals yet" preview.
    let didNodeUpgrade = false;
    if (target?.kind === "node") {
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
    if (!hadOverride && prevTarget && target &&
        (prevTarget.kind !== target.kind || prevTarget.index !== target.index)) {
      const gd = graphDataRef.current;
      const onFeature = gd && (prevTarget.kind === "edge"
        ? projectOntoEdge(gd, prevTarget.index, pinnedLat, pinnedLng)
        : (gd.nodes[prevTarget.index]
            ? { lat: gd.nodes[prevTarget.index][0], lng: gd.nodes[prevTarget.index][1] }
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
    if (!hadOverride && lockNow && graphDataRef.current?.edges[lockNow.edgeIdx]
        && (target?.kind !== "edge" || target.index !== lockNow.edgeIdx)) {
      target = { kind: "edge", index: lockNow.edgeIdx };
    }

    pinnedTargetRef.current = target;
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
    // A station carries its name on its self-edge (index == node index); use it
    // instead of a (disabled) reverse-geocode regardless of node/edge resolution.
    if (isStationNetwork) tooltipName = data.edges[hoverTarget.index]?.[2] || tooltipName;
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
      }
    } else {
      const node = data.nodes[pinnedTarget.index];
      if (node) {
        pinnedName = resolveAddress(node[0], node[1], bumpGeocode);
        pinnedVoteTypes = decodeVoteTypes((data.node_vote_types ?? [])[pinnedTarget.index], legend);
        pinnedVoteEdgeId = nodeAdjRef.current?.[pinnedTarget.index]?.[0] ?? null;
      }
    }
    // Anchor + share-link coord come from the SAME helper the position effect
    // uses, so the card's key and its on-screen anchor can never drift apart.
    pinnedPointLatLng = targetLatLng(pinnedTarget, data);
    // Stations carry their name on the self-edge (index == node index).
    if (isStationNetwork) pinnedName = data.edges[pinnedTarget.index]?.[2] || pinnedName;
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
  const indicatorMarkers = useMemo(() => {
    const topology = topologyRef.current;
    if (!topology) return null;

    // Station networks: draw EVERY point as a permanent marker (a self-edge per
    // station), all sharing one icon — "as though they were top proposals". The
    // legendIdx (== edge index) keys the marker. Streets: the vote winners. A
    // winner that hosts a waypoint stays drawn (it's the fixed pin), just tinted
    // + click-through (see the icon opts below).
    const source: VoteTypeWinner[] = isStationNetwork
      ? topology.edges.map((_, i) => ({ legendIdx: i, label: stationLabel, edgeIdx: i, count: 0 }))
      : winners;
    if (source.length === 0) return null;

    // Per-proposal "heat": normalize each winner's net votes against the hottest
    // visible proposal (log scale, matching the canvas heatmap so a busy map
    // doesn't peg everything to max), then sample the map's ramp at that
    // intensity. The pin glows + tints to that color, as hot as its votes. The
    // bucketed color keys the icon cache so equally-hot pins of a label still
    // share one divIcon (and so a re-render doesn't churn setIcon every frame).
    const rampStops = buildHeatRampStops(mapStyle.heat);
    const maxCount = source.reduce((m, w) => Math.max(m, w.count), 0);
    const heatOf = (count: number): number =>
      maxCount > 0 && count > 0
        ? Math.log(count + 1) / Math.log(maxCount + 1)
        : 0;

    // Resolve every marker to its edge-midpoint once up front so click handlers
    // can measure on-screen distance between icons for cluster detection.
    const placed = source
      .map((w) => {
        const edge = topology.edges[w.edgeIdx];
        if (!edge) return null;
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

    // The snap-back countdown governs a TRANSIENT (not yet locked) spread: it's
    // paused while the cursor is over one of its fanned-out icons and (re)started
    // when it leaves, so the cluster stays open as long as you hover it and
    // collapses once you move away. A locked spread has no timer.
    const armSpreadTimer = () => {
      clearSpreadTimer();
      spreadTimeoutRef.current = window.setTimeout(collapseSpread, SPREAD_DURATION_MS);
    };

    // The crowded proposal cluster around a screen anchor: the 2+ icons within
    // CLUSTER_RADIUS_PX of it. Shared by both explode entry points (browse click
    // and path/drag tap), so cluster detection lives in exactly one place.
    const clusterAround = (anchor: L.Point) =>
      placed.filter((m) => {
        const p = map.latLngToContainerPoint([m.midLat, m.midLng]);
        const dx = p.x - anchor.x, dy = p.y - anchor.y;
        return dx * dx + dy * dy <= CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX;
      });

    // Fan a cluster of icons out into a centered grid of cells around their shared
    // anchor point, REPLACING any currently-open spread (only one cluster is
    // fanned out at a time, so a fresh fanout collapses whatever was open), then
    // schedule the snap-back. The new spread is transient until a box is picked.
    const spreadCluster = (
      members: typeof placed,
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
        next.set(m.w.edgeIdx, [ll.lat, ll.lng]);
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
    if (clusterExploderRef) {
      clusterExploderRef.current = (latlng) => {
        const tapPt = map.latLngToContainerPoint([latlng.lat, latlng.lng]);
        let anchor: L.Point | null = null;
        let nearestSq = Infinity;
        for (const m of placed) {
          const p = map.latLngToContainerPoint([m.midLat, m.midLng]);
          const d = (p.x - tapPt.x) ** 2 + (p.y - tapPt.y) ** 2;
          if (d < nearestSq) { nearestSq = d; anchor = p; }
        }
        if (!anchor || nearestSq > CLUSTER_RADIUS_PX * CLUSTER_RADIUS_PX) return false;
        const cluster = clusterAround(anchor);
        if (cluster.length <= 1) return false;
        // Explode this cluster UNLESS it's already the open one — so a drag calling
        // this every frame doesn't re-fan the cluster it's already hovering.
        const alreadyOpen = cluster.some((m) => spreadRef.current?.has(m.w.edgeIdx));
        if (!alreadyOpen) spreadCluster(cluster, anchor);
        return true;
      };
    }

    return placed.map(({ w, midLat, midLng }) => {
      const override = spread?.get(w.edgeIdx);
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
      const heatColor = heat > 0 ? sampleHeatRamp(rampStops, heat) : undefined;
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
        if (!spreadLockedRef.current && spreadRef.current?.has(w.edgeIdx)) clearSpreadTimer();
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
        if (!spreadLockedRef.current && spreadRef.current?.has(w.edgeIdx)) armSpreadTimer();
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
          // Stacking order, highest first:
          //   3000  fanned-out (spread override) — the exploded cluster reads as
          //         one group on top of everything else.
          //   2500  a MATCHED waypoint (start/end/mid). It carries the [×] badge,
          //         so it must sit above any overlapping non-matched sibling — else
          //         when two proposals stack and the lower one is the waypoint, the
          //         upper icon conceals its badge.
          //   2000  the selected (open-modal) proposal.
          //   1000  everything else.
          zIndexOffset={
            override ? 3000
            : role !== null ? 2500
            : w.edgeIdx === selectedEdgeIdx ? 2000
            : 1000
          }
          interactive={!passthrough}
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
    // isStationNetwork/stationLabel select the marker source; isHeatmapLoading
    // re-runs once topology+votes arrive so stations appear even with zero votes.
  }, [winners, currentZoom, map, spread, collapseSpread, clearSpreadTimer, applySpread,
      selectedEdgeIdx, startEdgeIdx, endEdgeIdx, midEdgeSet, onPathEdgeSet, dropTargetEdgeIdx,
      isRouteMode, beginProposalMidDrag, mapStyle.selection, mapStyle.heat, isStationNetwork, stationLabel, isHeatmapLoading, canHover]);

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
    castVotes({ mode: themeMode, edgeIds: [edgeId], label, direction: newDir });
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
        // reconcileEdge bumps votesVersion itself when the server's truth
        // differs from local, which re-runs the resolve effect + re-renders the
        // modal. No manual bump.
        if (labels) reconcileEdge(themeMode, pinnedVoteEdgeId, labels);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [pinnedVoteEdgeId, themeMode]);

  return (
    <>
      {indicatorMarkers}
      {showPinned && pinnedScreenPos && createPortal(
        <ProposalCard
          // Key by the selected feature's identity (kind + index), stable across
          // pan/zoom and click jitter. Selecting a DIFFERENT feature remounts the
          // card fresh — always expanded, never inheriting the previous
          // selection's minimized state — while re-clicking the SAME feature
          // keeps the existing card in place.
          key={pinnedTarget ? `${pinnedTarget.kind}:${pinnedTarget.index}` : "pinned"}
          winner={isStationNetwork ? null : pinnedWinner}
          screenX={pinnedScreenPos.x}
          screenY={pinnedScreenPos.y}
          name={pinnedName}
          rows={pinnedVoteTypes}
          interactive
          edgeId={pinnedVoteEdgeId}
          mode={themeMode}
          voteTypes={theme.suggestions}
          shareUrl={pinnedPointLatLng
            ? buildSelectionUrl(pinnedPointLatLng, pinnedWinner?.label ?? pinnedVoteTypes[0]?.label)
            : null}
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
  screenX: number;
  screenY: number;
  name: string;
  rows: VoteTypeRow[];
  interactive?: boolean;
  edgeId?: number | null;
  mode?: string;
  shareUrl?: string | null;
  /** The active map's vote types, so the header icon resolves a custom vote-type
   *  set's own icon (matching markers and the selector) instead of falling back
   *  to the suggestion glyph. */
  voteTypes?: readonly { label: string; icon: string }[];
  onVote?: (edgeId: number | null, label: string, dir: VoteDirection) => void;
  onRemove?: () => void;
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

function ProposalCard({
  winner, screenX, screenY, name, rows,
  interactive = false, edgeId = null, mode = "", shareUrl = null, voteTypes, onVote, onRemove, onHoverChange, registerEl,
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

    // Prefer right-of / above the point; flip to the opposite side only when the
    // preferred side would overflow and the opposite side actually has room.
    const placeLeft = screenX + GAP + w > vw - M && screenX - GAP - w >= M;
    const placeBelow = screenY - GAP - h < topBound && screenY + GAP + h <= vh - M;

    let left = placeLeft ? screenX - w - GAP : screenX + GAP;
    let top = placeBelow ? screenY + GAP : screenY - h - GAP;
    left = Math.max(M, Math.min(left, vw - M - w));
    top = Math.max(topBound, Math.min(top, vh - M - h));

    const next: CardPos = {
      left, top,
      originX: placeLeft ? "right" : "left",
      originY: placeBelow ? "top" : "bottom",
    };
    setPos((prev) =>
      prev &&
      Math.abs(prev.left - left) < 0.5 && Math.abs(prev.top - top) < 0.5 &&
      prev.originX === next.originX && prev.originY === next.originY
        ? prev : next
    );
  }, [screenX, screenY, rows, minimized]);

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
    const myVote = edgeId != null ? getVote(mode, edgeId, row.label) : 0;
    const pressed = myVote === dir;
    return (
      <button
        type="button"
        className={`${cls} is-btn${pressed ? " is-mine" : ""}`}
        disabled={edgeId == null}
        aria-pressed={pressed}
        title={pressed ? "Remove your vote" : dir === -1 ? "Downvote" : "Upvote"}
        onClick={() => onVote?.(edgeId, row.label, dir)}
      >
        {inner}
      </button>
    );
  };

  return (
    <div
      ref={(el) => { cardRef.current = el; registerEl?.(el); }}
      className={`graph-indicator-modal graph-proposal-card${interactive ? " is-interactive" : " is-hover"}${minimized ? " is-minimized" : ""}`}
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
          {winner && (
            <div className="graph-indicator-modal-header">
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
              <div className="graph-indicator-modal-headtext">
                <div className="graph-indicator-modal-eyebrow">Top Proposal</div>
                <div className="graph-indicator-modal-label">{winner.label}</div>
              </div>
            </div>
          )}
          {interactive && (
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
