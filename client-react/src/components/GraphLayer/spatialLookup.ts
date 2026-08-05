import L from "leaflet";
import Flatbush from "flatbush";
import { pointToSegmentDist } from "./geometryHelpers";
import type { GraphData } from "../../types";
import type { GraphTopology } from "./graphTopology";
import {
  COORD_SCALE,
  type NodeAdj,
  nodeLat,
  nodeLon,
  edgeFrom,
  edgeTo,
  adjShortest,
} from "./graphTopology";
import { blockIdAtLatLng } from "../MapLibreBackground/MapLibreBackground";

// ---------------------------------------------------------------------------
// Constants & hit-testing
// ---------------------------------------------------------------------------

// Node/edge PARITY: the ends of every segment belong to its endpoint NODES.
// The nearest edge decides the hit, then the projection parameter t decides
// the final target — t in the outer NODE_END_SHARE of the segment resolves
// to that end's node instead. At 0.25 per end, nodes own half of every
// edge's catchment area (plus the clamped region beyond the endpoints), so
// random clicks split ~50/50 node/edge. This is deliberately the ONLY node
// rule — it's scale-invariant, so the split holds at every zoom:
//   - A pixel-radius node disc can never achieve parity: distance-to-nearest-
//     node is always >= distance-to-nearest-edge (nodes ARE edge endpoints),
//     so any "pick the closer" rule degenerates to all-edges.
//   - An absolute-priority disc overshoots the moment edges get short on
//     screen (park paths at z15 have geometry nodes every 4-16px, so an 8px
//     disc paves the whole line with node hits).
// Hovering exactly on a junction still yields the node: the nearest edge is
// an incident one and t clamps to its node end.
export const NODE_END_SHARE = 0.25;
// While a feature is pinned, a re-click within this screen distance of it keeps
// the SAME selection rather than re-resolving to a neighbour/endpoint. Stops the
// open card — which occludes its own icon — from drifting as you click near it.
export const STICKY_RESELECT_PX = 44;

// Highlight ring dimensions — matched to the desire path SVG filter so hover
// and pinned highlights look identical to the selected path:
//   - Edge ring: 7px wide stroke with a 4px hole = 1.5px white border each side
//   - Node ring: 5px outer radius with 3.5px hole = 1.5px white border
//   - Interior alpha: 0.12 (matches feColorMatrix in RouteLayer)
export const HIGHLIGHT_RING_WIDTH = 7;
export const HIGHLIGHT_INNER_WIDTH = 4;
export const HIGHLIGHT_NODE_OUTER_R = 5;
export const HIGHLIGHT_NODE_INNER_R = 3.5;
export const HIGHLIGHT_INTERIOR_ALPHA = 0.12;

// Hover target type
export interface HoverEdge { kind: "edge"; index: number }
export interface HoverNode { kind: "node"; index: number }
export type HoverTarget = HoverEdge | HoverNode;

// The single resolved selection shared by hover, click/pin, the point-vote
// handler, and top-proposal indicators. `voteEdgeId` is the edge a vote lands
// on (an edge target votes on itself; a node votes on its first adjacent edge),
// so the highlighted component always matches the vote target.
export interface ResolvedSelection {
  target: HoverTarget;
  snapLat: number;
  snapLng: number;
  voteEdgeId: number | null;
}

export interface HitResult {
  target: HoverTarget;
  snapLat: number;
  snapLng: number;
}

// A vote-type breakdown row shown in tooltips/modals.
export interface VoteTypeRow {
  label: string;
  up: number;
  down: number;
}

/** Decode a [legendIdx, up, down][] breakdown into labelled rows. */
export function decodeVoteTypes(
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
export const TOP_PROPOSAL_LIMIT = 20;

// "Spread to grid" interaction for crowded indicators. When a top-proposal
// icon is clicked and other icons sit within CLUSTER_RADIUS_PX of it on screen,
// the first click is swallowed: instead of selecting, the cluster fans out into
// a grid so each icon becomes individually clickable, then snaps back after
// SPREAD_DURATION_MS.
export const CLUSTER_RADIUS_PX = 26; // screen distance that counts as "clustered"
export const SPREAD_CELL_PX = 38;    // grid cell pitch when fanned out
export const SPREAD_DURATION_MS = 2200;
export const SPREAD_ANIM_MS = 280;   // keep in sync with the CSS transition

// Top-proposal recomputation is BATCHED off the vote path: a cast/delta only
// marks the lists dirty and repaints the heatmap; this sweep recomputes both
// proposal families (PBTP scan + RBTP clustering) in idle time on a minute
// cadence. On the 3.3M-edge NYC bike graph the recompute is far too heavy to
// run per vote — it froze the app for seconds on every cast.
export const PROPOSALS_REFRESH_INTERVAL_MS = 60_000;

// requestIdleCallback with a setTimeout fallback (Safari) — schedules the next
// slice of the route-proposal job so heavy vote types never monopolize a frame.
export function scheduleIdleSlice(cb: (deadline?: IdleDeadline) => void): void {
  if (typeof requestIdleCallback === "function") requestIdleCallback(cb, { timeout: 1000 });
  else window.setTimeout(() => cb(), 50);
}

// Movement (px²) before a press on an exploded proposal becomes a mid-drag rather
// than a tap (which selects). Mirrors the path-drag's tap-vs-drag intent.
export const MID_DRAG_THRESHOLD_SQ = 16; // (4px)²
// Dotted rubber-band for the mid being dragged out of a proposal — matches the
// path-drag and waypoint-drag trails (usePathDrag / RouteMarker).
export const MID_DRAG_TRAIL_STYLE: L.PolylineOptions = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round",
};

// Number of bbox-nearest candidates to retrieve from the spatial index.
// 20 is enough for Manhattan-scale edges (~200m max length); the closest
// segment is always among the top few bbox-nearest in practice.
export const INDEX_NEIGHBOR_K = 20;

// Cap on how many block edges the modal-open /my-votes reconcile requests. A
// merged foot-component block can hold thousands of edges — the URL (and the
// server scan) must stay bounded; coverage of a huge block degrades gracefully.
export const MY_VOTES_EDGE_CAP = 500;

// Cap + debounce for the route card's /api/route-votes fetch (distinct-voter
// rows). POSTed as JSON so it can carry far more edge ids than a URL, but still
// bounded; the debounce coalesces vote bursts into one refetch.
export const ROUTE_VOTES_EDGE_CAP = 4000;
export const ROUTE_VOTES_DEBOUNCE_MS = 350;

// /api/graph-votes can serve a snapshot older than the live revision (its
// debounce + stale-while-revalidate paths) and says so in a header. A live
// session doesn't care — WS deltas carry it forward — but a page load paints
// what it got, so it re-asks once the server's background rebuild has had time
// to finish. Bounded: a busy map is ALWAYS a revision or two behind, and an
// unbounded retry there would be a refetch loop of the largest body we serve.
export const STALE_VOTES_RETRY_MS = 2000;
export const STALE_VOTES_MAX_RETRIES = 2;

// Index builds yield to the main thread between batches so the multi-second
// NYC build doesn't jank the tile/heat animations happening at exactly this
// moment of the load. Until an index exists, hitTest simply returns no result
// (hover lights up ~a second later) — no brute-force fallback, which would
// scan the full 650k-edge array per mousemove.
export const INDEX_YIELD_BATCH = 120_000;
export const yieldToMain = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

/** Build a flatbush spatial index of node points. Done once per graph. */
export async function buildNodeIndex(data: GraphTopology): Promise<Flatbush | null> {
  const n = data.nNodes;
  if (n === 0) return null;
  const c = data.coords;
  const idx = new Flatbush(n);
  for (let i = 0; i < n; i++) {
    if (i > 0 && i % INDEX_YIELD_BATCH === 0) await yieldToMain();
    const lat = c[2 * i] / COORD_SCALE;
    const lng = c[2 * i + 1] / COORD_SCALE;
    // Point bbox: min == max (lng,lat ordering matches the edge index)
    idx.add(lng, lat, lng, lat);
  }
  idx.finish();
  return idx;
}

/** Build a flatbush spatial index of edge bounding boxes. Done once per graph. */
export async function buildEdgeIndex(data: GraphTopology): Promise<Flatbush | null> {
  const n = data.nEdges;
  if (n === 0) return null;
  const c = data.coords;
  const e = data.ends;
  const nNodes = data.nNodes;
  const idx = new Flatbush(n);
  for (let i = 0; i < n; i++) {
    if (i > 0 && i % INDEX_YIELD_BATCH === 0) await yieldToMain();
    const fromIdx = e[2 * i];
    const toIdx = e[2 * i + 1];
    // A malformed/stale topology can reference a node index that doesn't exist.
    // Index a degenerate (0,0) box for it instead of reading out of bounds —
    // Flatbush requires exactly nEdges adds, so we can't skip the entry.
    if (fromIdx >= nNodes || toIdx >= nNodes) {
      idx.add(0, 0, 0, 0);
      continue;
    }
    const fLat = c[2 * fromIdx] / COORD_SCALE, fLng = c[2 * fromIdx + 1] / COORD_SCALE;
    const tLat = c[2 * toIdx] / COORD_SCALE, tLng = c[2 * toIdx + 1] / COORD_SCALE;
    idx.add(
      Math.min(fLng, tLng), Math.min(fLat, tLat),
      Math.max(fLng, tLng), Math.max(fLat, tLat),
    );
  }
  idx.finish();
  return idx;
}

/**
 * Unified hit-test: resolve (px,py) to a node or edge with node/edge PARITY.
 *
 * Hierarchy:
 *   1. The nearest edge within `maxEdgeDistPx` decides — but its ends belong
 *      to its endpoint nodes: a projection parameter t in the outer
 *      NODE_END_SHARE of the segment resolves to that end's node instead of
 *      the edge (see the constant's comment for why this is the ONLY node
 *      rule). Degenerate self-edges (e-bike stations) are never split —
 *      they stay edge targets.
 *   2. maxEdgeDistPx === Infinity (always-resolve callers) with no eligible
 *      edge: the nearest eligible node, uncapped — a block whose only
 *      member is a node is still selectable from anywhere in its polygon.
 *
 * Every live caller resolves through resolveSelection, which passes Infinity
 * so a selection ALWAYS resolves; maxEdgeDistPx remains a parameter for
 * radius-bounded callers.
 *
 * With spatial indices each scan touches only the top-k bbox-nearest
 * candidates (typically <30 per call) instead of all nodes/edges. Path-drag
 * fires this on every mousemove via the snap closure, so this is the hot path.
 */
export function hitTest(
  data: GraphData,
  map: L.Map,
  px: number, py: number,
  lat: number, lng: number,
  edgeIndex: Flatbush | null,
  nodeIndex: Flatbush | null,
  allowEdge?: (i: number) => boolean,
  allowNode?: (i: number) => boolean,
  maxEdgeDistPx: number = Infinity
): HitResult | null {
  const nodeResult = (i: number): HitResult => ({
    target: { kind: "node", index: i },
    snapLat: nodeLat(data, i), snapLng: nodeLon(data, i),
  });

  // 1. Nearest edge within maxEdgeDistPx.
  let bestEdge: number | null = null;
  let bestEdgeDist = maxEdgeDistPx;

  const checkEdge = (i: number) => {
    const fromIdx = edgeFrom(data, i), toIdx = edgeTo(data, i);
    const fromPt = map.latLngToContainerPoint([nodeLat(data, fromIdx), nodeLon(data, fromIdx)]);
    const toPt = map.latLngToContainerPoint([nodeLat(data, toIdx), nodeLon(data, toIdx)]);
    const dist = pointToSegmentDist(px, py, fromPt.x, fromPt.y, toPt.x, toPt.y);
    if (dist < bestEdgeDist) {
      bestEdgeDist = dist;
      bestEdge = i;
    }
  };

  if (edgeIndex) {
    // flatbush applies the filter BEFORE counting results, so a constrained
    // query still returns up to K matching candidates however sparse they are.
    const candidates = edgeIndex.neighbors(lng, lat, INDEX_NEIGHBOR_K, Infinity, allowEdge);
    for (const i of candidates) checkEdge(i);
  }
  // No else: while the index is still building (async, yielding) edges simply
  // don't hit — brute-forcing all edges per mousemove froze the NYC map.

  if (bestEdge !== null) {
    // Parity split: t in the outer NODE_END_SHARE hands the hit to that
    // end's node. t is computed in PIXEL space (the same space the distance
    // ranking uses) — lat/lng space is anisotropic at NYC latitudes.
    const fromIdx = edgeFrom(data, bestEdge), toIdx = edgeTo(data, bestEdge);
    const fromPt = map.latLngToContainerPoint([nodeLat(data, fromIdx), nodeLon(data, fromIdx)]);
    const toPt = map.latLngToContainerPoint([nodeLat(data, toIdx), nodeLon(data, toIdx)]);
    const dx = toPt.x - fromPt.x, dy = toPt.y - fromPt.y;
    const lenSq = dx * dx + dy * dy;
    if (lenSq > 0) {
      const t = Math.max(0, Math.min(1,
        ((px - fromPt.x) * dx + (py - fromPt.y) * dy) / lenSq
      ));
      if (t < NODE_END_SHARE || t > 1 - NODE_END_SHARE) {
        const nid = t < 0.5 ? fromIdx : toIdx;
        if (!allowNode || allowNode(nid)) return nodeResult(nid);
      }
    }
    const { lat: snapLat, lng: snapLng } = projectOntoEdge(data, bestEdge, lat, lng);
    return { target: { kind: "edge", index: bestEdge }, snapLat, snapLng };
  }

  // 2. Always-resolve mode with no eligible edge (a capture-only node cell):
  // the nearest eligible node, uncapped. Node scan only runs on this cold
  // path — the hot path above never touches the node index.
  if (maxEdgeDistPx === Infinity) {
    let bestNode: number | null = null;
    let bestNodeDist = Infinity;
    const checkNode = (i: number) => {
      const pt = map.latLngToContainerPoint([nodeLat(data, i), nodeLon(data, i)]);
      const dist = (px - pt.x) ** 2 + (py - pt.y) ** 2;
      if (dist < bestNodeDist) {
        bestNodeDist = dist;
        bestNode = i;
      }
    };
    if (nodeIndex) {
      const candidates = nodeIndex.neighbors(lng, lat, INDEX_NEIGHBOR_K, Infinity, allowNode);
      for (const i of candidates) checkNode(i);
    }
    // No else — same index-only rule as edges above.
    if (bestNode !== null) return nodeResult(bestNode);
  }

  return null;
}

/**
 * The block-constraint filters for a point: which block polygon is under it,
 * and the edge/node eligibility predicates for that block. ONE construction
 * shared by hover/selection (resolveSelection) AND the waypoint snap — the
 * two must agree or a click selects something different from what hover
 * showed. Null when no block is under the point (or MapLibre isn't ready).
 */
export function blockFiltersAt(
  data: GraphData,
  adj: NodeAdj | null,
  lat: number,
  lng: number,
): { blockId: number; edgeInBlock: (i: number) => boolean; nodeInBlock: (n: number) => boolean } | null {
  const ebi = data.edgeBlockId;
  if (!ebi) return null;
  const blockId = blockIdAtLatLng(lat, lng);
  if (blockId == null) return null;
  return {
    blockId,
    edgeInBlock: (i: number) => ebi[i] === blockId,
    // A node belongs to the hovered block if an adjacent edge is a member OR
    // the node physically sits inside the block's polygon. The geometric arm
    // is what makes the junction node at the CENTER of a circular cell
    // hoverable — its incident roadway edges can all belong to neighbouring
    // street blocks, so adjacency alone rejected the very node under the
    // cursor. (Short-circuited: the MapLibre point query only runs for nodes
    // that fail adjacency, i.e. near junction cells.)
    nodeInBlock: (n: number) => {
      if (adj) {
        for (let k = adj.start[n]; k < adj.start[n + 1]; k++) {
          if (ebi[adj.edges[k]] === blockId) return true;
        }
      }
      return blockIdAtLatLng(nodeLat(data, n), nodeLon(data, n)) === blockId;
    },
  };
}

/**
 * The shortest edge adjacent to `nid` that belongs to `blockId` — the
 * block-constrained twin of graphTopology.adjShortest, so a node picked under
 * a hovered block votes/highlights within THAT block, not a neighbour the
 * unconstrained shortest-edge rule might prefer. Falls back to the
 * unconstrained pick when no adjacent edge is in the block.
 */
export function adjShortestInBlock(
  data: GraphData,
  adj: NodeAdj | null,
  nid: number,
  blockId: number,
): number | null {
  if (!adj || !data.edgeBlockId) return adjShortest(data, adj, nid);
  const cosLat = Math.cos(nodeLat(data, nid) * (Math.PI / 180));
  let best = -1;
  let bestD2 = Infinity;
  for (let k = adj.start[nid]; k < adj.start[nid + 1]; k++) {
    const eid = adj.edges[k];
    if (data.edgeBlockId[eid] !== blockId) continue;
    const a = edgeFrom(data, eid), b = edgeTo(data, eid);
    const dLat = nodeLat(data, a) - nodeLat(data, b);
    const dLon = (nodeLon(data, a) - nodeLon(data, b)) * cosLat;
    const d2 = dLat * dLat + dLon * dLon;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = eid;
    }
  }
  return best >= 0 ? best : adjShortest(data, adj, nid);
}

/**
 * Project (lat,lng) onto edge `edgeIdx`, returning the closest point on the
 * segment (clamped to the endpoints). Shared by hitTest and the nearest-edge
 * fallback so both produce a real snap position.
 */
export function projectOntoEdge(
  data: GraphTopology,
  edgeIdx: number,
  lat: number, lng: number
): { lat: number; lng: number } {
  const fromIdx = edgeFrom(data, edgeIdx), toIdx = edgeTo(data, edgeIdx);
  const fLat = nodeLat(data, fromIdx), fLng = nodeLon(data, fromIdx);
  const tLat = nodeLat(data, toIdx), tLng = nodeLon(data, toIdx);
  const dx = tLng - fLng;
  const dy = tLat - fLat;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { lat: fLat, lng: fLng };
  const t = Math.max(0, Math.min(1,
    ((lng - fLng) * dx + (lat - fLat) * dy) / lenSq
  ));
  return { lat: fLat + t * dy, lng: fLng + t * dx };
}
