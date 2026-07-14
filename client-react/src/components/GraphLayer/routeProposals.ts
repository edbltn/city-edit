// ==========================================================================
// Route-based top proposals (RBTPs) — client side
// ==========================================================================
// Terminology (docs/three-layer-model.md §3.1): an RBTP is a ROUTE-based top
// proposal — a hot corridor through the vote graph, shown as a diamond pin at
// its middle edge. Its point-based counterpart is the PBTP (one hot edge,
// square pin) in topProposals.ts.
//
// Pure logic (no React/Leaflet) for the server-computed ROUTE proposals served
// by /api/route-proposals. A route proposal is a high-vote corridor: a simple
// path through the intersection graph, expressed in BLOCK units (edge groups),
// with two anchor endpoints. This module covers:
//
//   - parsing the server wire shape → a typed RouteProposal
//   - the marker SHAPE class (route = diamond, point = square) — one icon area
//   - which edges a route HIGHLIGHTS on hover and VOTES on (all block edges)
//   - block-grained auto-select coverage (every block touched)
//   - de-duping point proposals subsumed by a route
//   - choosing the faster anchor insertion order for ghost-waypoint forcing
//
// Mirrors server/route_proposals.py; see docs/vote-system-design.md §2.
//
// It ALSO computes proposals client-side (computeRouteProposals below): a pure,
// deterministic function of (topology, vote state) per docs/three-layer-model.md
// §3 — recomputed in real time as vote deltas arrive, no server round-trip.

import type { LatLng } from "../../types";
import {
  adjEdgesOf,
  blockKeyOf,
  buildBlockIndex,
  edgeLengthMeters,
  edgesOfBlockKey,
  nodeLatLng,
  type BlockIndex,
  type GraphTopology,
  type NodeAdj,
} from "./graphTopology";
import type { VoteTypeKindResolver } from "./topProposals";

export interface RouteProposal {
  id: string;
  label: string;
  legendIdx: number;
  score: number;
  /** Ordered path edges (geometry / anchors). */
  edgeIds: number[];
  /** Distinct blocks along the path, each a group of edge ids. */
  blocks: number[][];
  /** Union of every edge in every block — the highlight + voting set. */
  blockEdgeIds: number[];
  /** Terminal intersection node indices. */
  anchors: [number, number];
  /** Coordinates of the two anchors. */
  anchorCoords: [LatLng, LatLng];
}

interface RouteProposalJson {
  id: string;
  label: string;
  legendIdx: number;
  score: number;
  edge_ids: number[];
  blocks: number[][];
  block_edge_ids: number[];
  anchors: [number, number];
  anchor_coords: [[number, number], [number, number]];
}

/** Map the server wire shape (snake_case, [lat,lng] tuples) to a RouteProposal. */
export function parseRouteProposal(j: RouteProposalJson): RouteProposal {
  const toLatLng = ([lat, lng]: [number, number]): LatLng => ({ lat, lng });
  return {
    id: j.id,
    label: j.label,
    legendIdx: j.legendIdx,
    score: j.score,
    edgeIds: j.edge_ids,
    blocks: j.blocks,
    blockEdgeIds: j.block_edge_ids,
    anchors: j.anchors,
    anchorCoords: [toLatLng(j.anchor_coords[0]), toLatLng(j.anchor_coords[1])],
  };
}

export function parseRouteProposals(body: { proposals?: RouteProposalJson[] } | null): RouteProposal[] {
  return (body?.proposals ?? []).map(parseRouteProposal);
}

// --------------------------------------------------------------------------
// Marker shape — one icon area, diamond for routes vs square for points.
// --------------------------------------------------------------------------
export type ProposalKind = "point" | "route";

export const PROPOSAL_SHAPE_BASE = "proposal-indicator";

/**
 * The CSS class for a proposal indicator's container. A route gets a DIAMOND
 * (the icon area rotated 45° to read as "directions"/a route); a point keeps the
 * SQUARE. Either way there is exactly one vote-type icon area — no second glyph.
 */
export function proposalShapeClass(kind: ProposalKind): string {
  return `${PROPOSAL_SHAPE_BASE} ${PROPOSAL_SHAPE_BASE}--${kind === "route" ? "diamond" : "square"}`;
}

// --------------------------------------------------------------------------
// Highlight / vote edge sets — both are the full block-edge union.
// --------------------------------------------------------------------------
/**
 * Every edge a route covers: the union of all its blocks' edges. Used both to
 * HIGHLIGHT all blocks on hover and to VOTE — selecting a route casts on every
 * underlying edge of every block, not just the path's representative edge.
 */
export function routeBlockEdges(p: RouteProposal): number[] {
  return p.blockEdgeIds;
}

// --------------------------------------------------------------------------
// Auto-select coverage (block-grained) — mirrors server is_route_covered.
// --------------------------------------------------------------------------
/** True iff the selection covers EVERY block (≥1 of each block's edges). */
export function isRouteCovered(blocks: number[][], selectedEdges: Iterable<number>): boolean {
  const sel = selectedEdges instanceof Set ? selectedEdges : new Set(selectedEdges);
  if (blocks.length === 0) return false;
  return blocks.every((block) => block.some((e) => sel.has(e)));
}

/**
 * Expand a selection's edge set to its DIRECTION TWINS among `candidateEdges`.
 * A two-way street stores each direction as its own edge, and a routed path
 * often traverses the twin of the edge a proposal's block recorded — a raw
 * edge-id intersection then misses coverage the user plainly traced. Any
 * candidate sharing an (undirected) node pair with a selected edge joins the
 * set, so isRouteCovered sees the street, not the direction.
 */
export function expandSelectionToUndirected(
  topo: { nEdges: number; ends: ArrayLike<number> },
  selected: Iterable<number>,
  candidateEdges: Iterable<number>,
): Set<number> {
  const sel = new Set(selected);
  const key = (e: number) => {
    const u = topo.ends[2 * e], v = topo.ends[2 * e + 1];
    return u < v ? `${u}|${v}` : `${v}|${u}`;
  };
  const pairs = new Set<string>();
  for (const e of sel) if (e < topo.nEdges) pairs.add(key(e));
  for (const e of candidateEdges) {
    if (e < topo.nEdges && !sel.has(e) && pairs.has(key(e))) sel.add(e);
  }
  return sel;
}

// --------------------------------------------------------------------------
// De-dupe point proposals subsumed by a route.
// --------------------------------------------------------------------------
/**
 * Drop point proposals whose edge already lies on a route proposal of the SAME
 * vote type — the route subsumes them, so they shouldn't also show a point pin.
 * Different vote types never suppress each other.
 */
export function dropPointsCoveredByRoutes<T extends { edgeIdx: number; legendIdx: number }>(
  points: T[],
  routes: RouteProposal[],
): T[] {
  const covered = new Map<number, Set<number>>(); // legendIdx → edge ids
  for (const r of routes) {
    let set = covered.get(r.legendIdx);
    if (!set) covered.set(r.legendIdx, (set = new Set()));
    for (const e of r.blockEdgeIds) set.add(e);
  }
  return points.filter((p) => !covered.get(p.legendIdx)?.has(p.edgeIdx));
}

// --------------------------------------------------------------------------
// Corridor geometry — the proposal's stored path as a coordinate chain.
// --------------------------------------------------------------------------
/**
 * The corridor's polyline as GeoJSON [lng, lat] pairs, walked from
 * `anchors[0]` to `anchors[1]` along the ordered `edgeIds`. This is what lets
 * a selected RBTP route the selection through the proposal VERBATIM instead of
 * whatever OSRM picks between the anchors. Returns null if the chain breaks
 * (stale topology / edge not incident to the walk) — callers fall back to OSRM.
 */
export function corridorCoordinates(
  topo: GraphTopology,
  p: RouteProposal,
): [number, number][] | null {
  if (p.edgeIds.length === 0) return null;
  let cur = p.anchors[0];
  if (cur >= topo.nNodes) return null;
  const coords: [number, number][] = [];
  const push = (n: number) => {
    const [lat, lng] = nodeLatLng(topo, n);
    coords.push([lng, lat]);
  };
  push(cur);
  for (const e of p.edgeIds) {
    if (e >= topo.nEdges) return null;
    const u = topo.ends[2 * e];
    const v = topo.ends[2 * e + 1];
    const next = u === cur ? v : v === cur ? u : null;
    if (next === null || next >= topo.nNodes) return null;
    push(next);
    cur = next;
  }
  return coords;
}

/**
 * Corridor geometry rebuilt from a bare ORDERED edge-id chain (a forced-corridor
 * snapshot — the proposal itself may no longer exist), oriented from the segment
 * point `a` to `b`. The chain's start node is inferred: for one edge either
 * endpoint works; otherwise it's the first edge's node NOT shared with the second
 * edge. The walked polyline is then reversed if its far end sits closer to `a`.
 * Null when the chain breaks (stale topology) — callers fall back to OSRM.
 */
export function corridorFromEdgeIds(
  topo: GraphTopology,
  edgeIds: number[],
  a: LatLng,
  b: LatLng,
): { coordinates: [number, number][]; edgeIds: number[] } | null {
  if (edgeIds.length === 0) return null;
  const e0 = edgeIds[0];
  if (e0 >= topo.nEdges) return null;
  const u0 = topo.ends[2 * e0];
  const v0 = topo.ends[2 * e0 + 1];
  let startNode = u0;
  if (edgeIds.length > 1) {
    const e1 = edgeIds[1];
    if (e1 >= topo.nEdges) return null;
    const u1 = topo.ends[2 * e1];
    const v1 = topo.ends[2 * e1 + 1];
    // Walk must LEAVE from the node the second edge doesn't touch.
    startNode = u0 === u1 || u0 === v1 ? v0 : u0;
  }
  // corridorCoordinates only reads `edgeIds` and `anchors[0]` — a minimal stub
  // stands in for the retired proposal.
  const coords = corridorCoordinates(topo, {
    edgeIds,
    anchors: [startNode, -1],
  } as RouteProposal);
  if (!coords || coords.length < 2) return null;
  const sq = (p: [number, number], q: LatLng) =>
    (p[1] - q.lat) ** 2 + (p[0] - q.lng) ** 2;
  const first = coords[0];
  const last = coords[coords.length - 1];
  // Orient a→b: keep whichever direction puts the near end at `a`.
  const forward = sq(first, a) + sq(last, b) <= sq(first, b) + sq(last, a);
  return {
    coordinates: forward ? coords : [...coords].reverse(),
    edgeIds,
  };
}

// --------------------------------------------------------------------------
// Ghost-waypoint forcing — choose the faster anchor insertion order.
// --------------------------------------------------------------------------
export type DurationOf = (a: LatLng, b: LatLng) => number;

/**
 * Forcing a route through a proposal inserts its two anchors as waypoints. With
 * an existing selection the order matters (start→A→B→end vs start→B→A→end), so
 * pick the arrangement with the lower total routed duration. `existing` is the
 * current ordered waypoint coords (may be empty / single). Returns the two
 * anchors in insertion order.
 */
export function chooseAnchorOrder(
  existing: LatLng[],
  a: LatLng,
  b: LatLng,
  durationOf: DurationOf,
): [LatLng, LatLng] {
  const chainDuration = (pts: LatLng[]): number => {
    let total = 0;
    for (let i = 0; i < pts.length - 1; i++) total += durationOf(pts[i], pts[i + 1]);
    return total;
  };
  // Insert the pair between the existing start and end (or append when there's
  // no existing route to order against).
  const head = existing.length ? [existing[0]] : [];
  const tail = existing.length > 1 ? [existing[existing.length - 1]] : [];
  const forward = chainDuration([...head, a, b, ...tail]);
  const backward = chainDuration([...head, b, a, ...tail]);
  return backward < forward ? [b, a] : [a, b];
}

/**
 * Anchor order for a pair inserted BEFORE an existing point (dropping the START
 * onto a proposal: the chain becomes anchor→anchor→next). The corridor's own
 * length is the same either way, so the choice reduces to which anchor should
 * touch `next` — the head-side complement of `chooseAnchorOrder`.
 */
export function chooseAnchorOrderBefore(
  next: LatLng,
  a: LatLng,
  b: LatLng,
  durationOf: DurationOf,
): [LatLng, LatLng] {
  return durationOf(b, next) <= durationOf(a, next) ? [a, b] : [b, a];
}

// ==========================================================================
// Client-side deterministic clustering (port of server/route_proposals.py)
// ==========================================================================
// Pipeline per ROUTE-kind vote type (docs/three-layer-model.md §3.2; point-
// kind types are skipped — their votes surface as PBTP pins): net-positive
// subgraph → connected components (the deterministic replacement for the
// server's Leiden step — components localize; path peeling separates parallel
// corridors inside one component) → peel heaviest simple paths → split each at
// its loop-back points into straight-ish corridors (splitLoopyPath) → trim
// each to its support-earned meter budget (capPathToLengthBudget) → activity
// gates (score, edges, blocks) → block projection → same-type dedupe → rank +
// cap.
//
// Determinism contract: NO randomness, NO clock. Every iteration order and
// tie-break is by ascending edge/node id, so the same (topology, vote state)
// yields byte-identical proposals (ids and order) on every client.

/** Minimum net (up − down) for an edge to enter a type's subgraph. */
export const MIN_NET = 1;
/** Exact heaviest-simple-path search up to this many component vertices;
 *  greedy two-way extension above (longest path is NP-hard). */
export const EXACT_PATH_MAX_VERTICES = 12;
/** Peel at most this many paths out of one component. */
export const PEEL_MAX_PATHS = 8;
/** A peeled path survives only at ≥ this fraction of the component's first. */
export const PEEL_DOMINANCE = 0.25;
/** High-activity gate: minimum path score (sum of nets). */
export const MIN_ROUTE_SCORE = 3;
/** High-activity gate: minimum number of path edges. */
export const MIN_ROUTE_EDGES = 2;
/** Min-distance gate: a corridor must span at least this many BLOCKS. Anything
 *  shorter reads as a point, not a route — and its votes still surface as a
 *  PBTP pin, so nothing is lost by dropping the stub corridor. */
export const MIN_ROUTE_BLOCKS = 5;
/** Global cap on the ranked proposal list. */
export const DEFAULT_LIMIT = 20;
/** Same-type edge-set Jaccard at/above which two routes are duplicates. */
export const DEFAULT_JACCARD = 0.5;

// ── Corridor length budget ──────────────────────────────────────────────────
// A peeled path can snake for miles (greedy extension keeps going while ANY
// net-positive arc exists), which reads as an absurd proposal. Instead of a
// blunt truncation, each path gets a meter budget that GROWS with its support
// — a corridor earns length with votes — and is trimmed to its best-supported
// contiguous window under that budget (see capPathToLengthBudget).
/** Budget floor: every corridor may span at least this many meters. */
export const ROUTE_LENGTH_BASE_M = 2700;
/** Budget growth: meters added per √(path score). √ keeps a corridor with 4×
 *  the votes at 2× the earned length — support buys reach, sublinearly. */
export const ROUTE_LENGTH_PER_SQRT_SCORE_M = 660;
/** Budget ceiling, whatever the support. */
export const ROUTE_LENGTH_MAX_M = 10500;

/** The meter budget a path of `score` (sum of nets) has earned. */
export function routeLengthBudgetM(score: number, maxM = ROUTE_LENGTH_MAX_M): number {
  const earned = ROUTE_LENGTH_BASE_M
    + ROUTE_LENGTH_PER_SQRT_SCORE_M * Math.sqrt(Math.max(score, 0));
  return Math.min(maxM, earned);
}

// ── Loop-back splitting ─────────────────────────────────────────────────────
// The peel extends by heaviest arc with no regard for direction, so a hot
// region yields corridors that snake or double back — and the length budget
// happily fills with the loop. A proposal should read as a corridor: a long,
// roughly straight line. Straightness of a stretch is measured as
// crow-flies(endpoints) / arc length (1.0 = ruler, ~0.33 = U-turn); a path is
// split where it turns back on itself and each side re-judged recursively, so
// one snake becomes several straight corridors that then earn their own
// length budgets and pass the activity gates independently.
/** Split when endpoint straightness of the whole stretch falls below this.
 *  Calibration: an L-corner or a grid staircase sits near 0.71, a half-circle
 *  arc at 0.64, a U-turn at ~0.33 — so 0.55 keeps corners and gentle arcs and
 *  splits anything that meaningfully comes back on itself. */
export const ROUTE_STRAIGHTNESS_MIN = 0.55;
/** Hairpin detector: slide a window of this arc length along the path… */
export const ROUTE_WINDOW_M = 800;
/** …and split when any window's straightness falls below this — catches a
 *  local double-back buried in an otherwise straight corridor, which the
 *  endpoint measure can't see. */
export const ROUTE_WINDOW_STRAIGHTNESS_MIN = 0.4;
/** Recursion bound (≤ 2^depth fragments per peeled path). */
export const ROUTE_SPLIT_MAX_DEPTH = 6;

/**
 * Split a peeled path at its loop-back points into straight-ish fragments.
 * Two triggers, checked per stretch: a WINDOW that doubles back (split at the
 * excursion apex — the window point farthest from its start) and a whole
 * stretch that ends near where it began (split at the point that maximizes the
 * weaker half's straightness). Fragments recurse until straight, too short to
 * judge (< 4 edges), or ROUTE_SPLIT_MAX_DEPTH. Deterministic: pure arithmetic
 * over node coordinates, ties keep the earliest index. Fragment weights are
 * recomputed from `weightOf` (simple paths — edges are distinct).
 */
export function splitLoopyPath(
  path: PathResult,
  lengthOf: (edgeId: number) => number,
  latLngOf: (nodeId: number) => [number, number],
  weightOf: (edgeId: number) => number,
): PathResult[] {
  const n = path.edges.length;
  if (n < 4) return [path];

  // Planar meters (equirectangular — fine at city scale, and deterministic).
  const lat0 = (latLngOf(path.nodes[0])[0] * Math.PI) / 180;
  const kx = 111320 * Math.cos(lat0);
  const ky = 110574;
  const xs = new Float64Array(n + 1);
  const ys = new Float64Array(n + 1);
  for (let i = 0; i <= n; i++) {
    const [lat, lng] = latLngOf(path.nodes[i]);
    xs[i] = lng * kx;
    ys[i] = lat * ky;
  }
  const arc = new Float64Array(n + 2);
  for (let i = 0; i < n; i++) arc[i + 1] = arc[i] + lengthOf(path.edges[i]);
  const crow = (i: number, j: number) => Math.hypot(xs[j] - xs[i], ys[j] - ys[i]);
  // Straightness of the stretch i..j; degenerate (zero-arc) stretches count as
  // straight so they never trigger a split.
  const straight = (i: number, j: number) => {
    const s = arc[j] - arc[i];
    return s > 0 ? crow(i, j) / s : 1;
  };

  const out: PathResult[] = [];
  const emit = (i0: number, i1: number) => {
    let w = 0;
    for (let i = i0; i < i1; i++) w += weightOf(path.edges[i]);
    out.push({
      edges: path.edges.slice(i0, i1),
      nodes: path.nodes.slice(i0, i1 + 1),
      weight: w,
    });
  };

  const rec = (i0: number, i1: number, depth: number) => {
    if (i1 - i0 < 4 || depth >= ROUTE_SPLIT_MAX_DEPTH) {
      emit(i0, i1);
      return;
    }
    // Worst window: for each end j, judge the longest window fitting the arc
    // budget. Windows under 60% full are skipped (edge-of-path stubs).
    let worst = 1;
    let wi = -1;
    let wj = -1;
    {
      let i = i0;
      for (let j = i0 + 1; j <= i1; j++) {
        while (arc[j] - arc[i] > ROUTE_WINDOW_M && i < j - 1) i++;
        if (arc[j] - arc[i] < ROUTE_WINDOW_M * 0.6) continue;
        const r = straight(i, j);
        if (r < worst) {
          worst = r;
          wi = i;
          wj = j;
        }
      }
    }
    let k = -1;
    if (worst < ROUTE_WINDOW_STRAIGHTNESS_MIN) {
      // Split at the excursion apex: the farthest point from the window start.
      let best = -1;
      for (let m = wi + 1; m < wj; m++) {
        const d = crow(wi, m);
        if (d > best) {
          best = d;
          k = m;
        }
      }
    }
    if (k <= i0 && straight(i0, i1) < ROUTE_STRAIGHTNESS_MIN) {
      // Whole stretch comes back on itself: split where both halves are
      // straightest (maximize the weaker half).
      let best = -1;
      for (let m = i0 + 1; m < i1; m++) {
        const v = Math.min(straight(i0, m), straight(m, i1));
        if (v > best) {
          best = v;
          k = m;
        }
      }
    }
    if (k <= i0 || k >= i1) {
      emit(i0, i1);
      return;
    }
    rec(i0, k, depth + 1);
    rec(k, i1, depth + 1);
  };

  rec(0, n, 0);
  return out;
}

export interface RouteProposalOptions {
  limit?: number;
  jaccardThreshold?: number;
  minNet?: number;
  minRouteScore?: number;
  minRouteEdges?: number;
  /** Min-distance gate: minimum BLOCKS a corridor must span (MIN_ROUTE_BLOCKS). */
  minRouteBlocks?: number;
  /** Hard ceiling override for the corridor length budget (meters). */
  maxRouteLengthM?: number;
  /** Prebuilt edge→block index for `topo` (GraphLayer already holds one).
   *  Omitted, one is built here — an O(nEdges) pass worth skipping per call. */
  blockIndex?: BlockIndex | null;
  /** Label → route/point kind. POINT-kind vote types are skipped — their votes
   *  surface as PBTP pins (topProposals.ts), not corridors. Unknown (null)
   *  kinds stay eligible. Omit to admit every type. */
  kindOf?: VoteTypeKindResolver;
}

/** Arc of the per-type subgraph: neighbor node, original edge id, net weight. */
interface Arc {
  n: number;
  e: number;
  w: number;
}

/** Adjacency of the per-type subgraph. Keys are inserted in ascending node-id
 *  order and arcs in ascending edge-id order — the determinism substrate. */
type TypeAdj = Map<number, Arc[]>;

/** Deterministic proposal id: FNV-1a over `${legendIdx}:${sorted edge ids}`. */
function proposalIdOf(legendIdx: number, pathEdgeIds: number[]): string {
  const raw = `${legendIdx}:${[...pathEdgeIds].sort((a, b) => a - b).join(",")}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < raw.length; i++) {
    h ^= raw.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

/** Per-type sparse nets (up − down), built in ONE pass over the vote table.
 *  The table has an nEdges-long slot array but nearly every slot is empty, so
 *  the old per-type full rescan (an O(nEdges) Int32Array per legend entry)
 *  dominated recompute time on big graphs — ~2s on the 3.3M-edge NYC bike
 *  graph. Keys insert in ascending-eid scan order, so iterating a type's map
 *  preserves the determinism contract's ascending-edge-id iteration. */
function netsByType(
  edgeVoteTypes: [number, number, number][][],
  nEdges: number,
  nTypes: number,
): Map<number, number>[] {
  const nets: Map<number, number>[] = Array.from({ length: nTypes }, () => new Map());
  const n = Math.min(edgeVoteTypes.length, nEdges);
  for (let eid = 0; eid < n; eid++) {
    const pairs = edgeVoteTypes[eid];
    if (!pairs) continue;
    for (const [t, up, dn] of pairs) {
      if (t >= 0 && t < nTypes) {
        const m = nets[t];
        m.set(eid, (m.get(eid) ?? 0) + (up - dn));
      }
    }
  }
  return nets;
}

/** The net-positive subgraph for one type (mirrors build_type_graph + _adj_of):
 *  nodes = touched intersections, arcs weighted by net; self-loops excluded. */
function buildTypeAdj(
  topo: GraphTopology,
  adj: NodeAdj,
  nets: Map<number, number>,
  minNet: number,
): TypeAdj {
  const { ends } = topo;
  const nodeIds: number[] = [];
  const seen = new Set<number>();
  // nets iterates in ascending edge id (see netsByType) — the same order the
  // old full scan visited, so nodeIds collect (then sort) identically.
  for (const [e, w] of nets) {
    if (w < minNet) continue;
    const u = ends[2 * e];
    const v = ends[2 * e + 1];
    if (u === v) continue;
    if (!seen.has(u)) {
      seen.add(u);
      nodeIds.push(u);
    }
    if (!seen.has(v)) {
      seen.add(v);
      nodeIds.push(v);
    }
  }
  nodeIds.sort((a, b) => a - b);

  const typeAdj: TypeAdj = new Map();
  for (const nid of nodeIds) {
    const row = adjEdgesOf(adj, nid);
    const arcs: Arc[] = [];
    for (let i = 0; i < row.length; i++) {
      const e = row[i];
      const w = nets.get(e) ?? 0;
      if (w < minNet) continue;
      const u = ends[2 * e];
      const v = ends[2 * e + 1];
      if (u === v) continue;
      arcs.push({ n: u === nid ? v : u, e, w });
    }
    if (arcs.length) typeAdj.set(nid, arcs);
  }
  return typeAdj;
}

/** Connected components (≥ 2 nodes), each as its own adjacency map with nodes
 *  in ascending order. Components are yielded by ascending smallest node id. */
function connectedComponents(typeAdj: TypeAdj): TypeAdj[] {
  const comps: TypeAdj[] = [];
  const visited = new Set<number>();
  for (const start of typeAdj.keys()) {
    if (visited.has(start)) continue;
    visited.add(start);
    const nodes: number[] = [start];
    for (let head = 0; head < nodes.length; head++) {
      for (const arc of typeAdj.get(nodes[head]) ?? []) {
        if (!visited.has(arc.n)) {
          visited.add(arc.n);
          nodes.push(arc.n);
        }
      }
    }
    if (nodes.length < 2) continue;
    nodes.sort((a, b) => a - b);
    const sub: TypeAdj = new Map();
    for (const n of nodes) {
      const arcs = typeAdj.get(n);
      if (arcs) sub.set(n, arcs);
    }
    comps.push(sub);
  }
  return comps;
}

export type PathResult = { edges: number[]; nodes: number[]; weight: number };

/** Exact heaviest simple path by DFS from every node (small components only).
 *  Strictly-greater comparisons keep the FIRST best found — ties resolve to the
 *  lowest start node / lowest edge id, matching the ascending iteration order. */
function exactHeaviestPath(adj: TypeAdj): PathResult {
  let bestW = -1;
  let bestNodes: number[] = [];
  let bestEdges: number[] = [];
  const seen = new Set<number>();
  const nodes: number[] = [];
  const edges: number[] = [];

  const dfs = (node: number, weight: number) => {
    if (weight > bestW) {
      bestW = weight;
      bestNodes = [...nodes];
      bestEdges = [...edges];
    }
    for (const arc of adj.get(node) ?? []) {
      if (seen.has(arc.n)) continue;
      seen.add(arc.n);
      nodes.push(arc.n);
      edges.push(arc.e);
      dfs(arc.n, weight + arc.w);
      seen.delete(arc.n);
      nodes.pop();
      edges.pop();
    }
  };

  for (const start of adj.keys()) {
    seen.clear();
    seen.add(start);
    nodes.length = 0;
    nodes.push(start);
    edges.length = 0;
    dfs(start, 0);
  }
  return { edges: bestEdges, nodes: bestNodes, weight: Math.max(bestW, 0) };
}

/** Sum of distinct edge weights along a (possibly overlapping) edge list. */
function pathWeight(edgeIds: number[], adj: TypeAdj): number {
  const wanted = new Set(edgeIds);
  const counted = new Set<number>();
  let total = 0;
  for (const arcs of adj.values()) {
    for (const arc of arcs) {
      if (wanted.has(arc.e) && !counted.has(arc.e)) {
        counted.add(arc.e);
        total += arc.w;
      }
    }
  }
  return total;
}

/** Greedy heaviest path: seed at the heaviest edge, extend both ways picking
 *  the heaviest unvisited arc, splice the halves. Cheap and path-valued for
 *  large components (port of _greedy_heaviest_path). */
function greedyHeaviestPath(adj: TypeAdj): PathResult {
  let seedA = -1;
  let seedB = -1;
  let seedE = -1;
  let seedW = -1;
  for (const [a, arcs] of adj) {
    for (const arc of arcs) {
      if (arc.w > seedW) {
        seedA = a;
        seedB = arc.n;
        seedE = arc.e;
        seedW = arc.w;
      }
    }
  }
  if (seedA < 0) return { edges: [], nodes: [], weight: 0 };

  const extend = (frm: number, to: number, firstEid: number) => {
    const nodes = [frm, to];
    const edges = [firstEid];
    const seen = new Set([frm, to]);
    let cur = to;
    for (;;) {
      let nxt: Arc | null = null;
      for (const arc of adj.get(cur) ?? []) {
        if (seen.has(arc.n)) continue;
        if (!nxt || arc.w > nxt.w) nxt = arc;
      }
      if (!nxt) break;
      seen.add(nxt.n);
      nodes.push(nxt.n);
      edges.push(nxt.e);
      cur = nxt.n;
    }
    return { nodes, edges };
  };

  // Extend from b away from a, and from a away from b, then splice into one
  // path joined on the seed edge (the bwd half reversed, seed edge kept once).
  // The bwd half contributes everything BEFORE seedA (drop its trailing
  // seedA+seedB after reversing — fwd starts at seedA), keeping nodes aligned
  // with edges (nodes[i] is the node entering edges[i]) for window slicing.
  const fwd = extend(seedA, seedB, seedE);
  const bwd = extend(seedB, seedA, seedE);
  const leftNodes = [...bwd.nodes].reverse().slice(0, -2);
  const leftEdges = [...bwd.edges.slice(1)].reverse(); // edges past the seed, reversed
  const nodes = [...leftNodes, ...fwd.nodes];
  const edges = [...leftEdges, ...fwd.edges];
  return { edges, nodes, weight: pathWeight(edges, adj) };
}

function heaviestPathFromAdj(adj: TypeAdj): PathResult {
  let arcCount = 0;
  for (const arcs of adj.values()) arcCount += arcs.length;
  if (arcCount === 0) return { edges: [], nodes: [], weight: 0 };
  return adj.size <= EXACT_PATH_MAX_VERTICES ? exactHeaviestPath(adj) : greedyHeaviestPath(adj);
}

/** Adjacency with `eids` removed (drops now-isolated nodes, keeps key order). */
function removeEdges(adj: TypeAdj, eids: Set<number>): TypeAdj {
  const out: TypeAdj = new Map();
  for (const [n, arcs] of adj) {
    const kept = arcs.filter((arc) => !eids.has(arc.e));
    if (kept.length) out.set(n, kept);
  }
  return out;
}

/** Successively pull the heaviest simple path out of a component, removing its
 *  edges each round — a region's real parallel corridors surface while weak
 *  dead-end residue (below PEEL_DOMINANCE × the first path) is dropped. */
function peelPaths(adj: TypeAdj): PathResult[] {
  const paths: PathResult[] = [];
  let first: number | null = null;
  let work = adj;
  while (work.size && paths.length < PEEL_MAX_PATHS) {
    const path = heaviestPathFromAdj(work);
    if (path.edges.length === 0) break;
    if (first === null) first = path.weight;
    else if (path.weight < PEEL_DOMINANCE * first) break;
    paths.push(path);
    work = removeEdges(work, new Set(path.edges));
  }
  return paths;
}

/**
 * Trim an ordered path to its best-supported contiguous window within a meter
 * budget. Slides a window over the path's edges keeping total length ≤
 * `budgetM` and returns the window with the highest weight sum — the hottest
 * stretch survives, straggly low-support reach is dropped. Deterministic ties:
 * equal weight prefers the shorter window, then the earliest along the path.
 * A single edge longer than the whole budget is kept (a corridor is never
 * trimmed to nothing); paths already within budget return unchanged.
 */
export function capPathToLengthBudget(
  path: PathResult,
  budgetM: number,
  lengthOf: (edgeId: number) => number,
  weightOf: (edgeId: number) => number,
): PathResult {
  const n = path.edges.length;
  if (n <= 1) return path;
  const lens = path.edges.map(lengthOf);
  if (lens.reduce((a, b) => a + b, 0) <= budgetM) return path;

  let bestI = 0;
  let bestJ = 0;
  let bestW = -Infinity;
  let bestLen = Infinity;
  let i = 0;
  let sumW = 0;
  let sumL = 0;
  for (let j = 0; j < n; j++) {
    sumW += weightOf(path.edges[j]);
    sumL += lens[j];
    // Shrink from the left until within budget — but never below one edge, so
    // an over-budget single edge (a long bridge) still yields a window.
    while (sumL > budgetM && i < j) {
      sumW -= weightOf(path.edges[i]);
      sumL -= lens[i];
      i++;
    }
    if (sumW > bestW || (sumW === bestW && sumL < bestLen)) {
      bestI = i;
      bestJ = j;
      bestW = sumW;
      bestLen = sumL;
    }
  }
  return {
    edges: path.edges.slice(bestI, bestJ + 1),
    nodes: path.nodes.slice(bestI, bestJ + 2),
    weight: bestW,
  };
}

/** Expand an ordered path into its distinct blocks (in path order) + the union
 *  of every block edge — the voting set. Singleton fallback where unmapped. */
function groupBlocks(
  pathEdgeIds: number[],
  topo: GraphTopology,
  blockIndex: BlockIndex | null,
): { blocks: number[][]; blockEdgeIds: number[] } {
  const blocks: number[][] = [];
  const seenKeys = new Set<number>();
  const union: number[] = [];
  const unionSeen = new Set<number>();
  for (const eid of pathEdgeIds) {
    const key = blockKeyOf(topo, eid);
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    const found = edgesOfBlockKey(topo, blockIndex, key);
    const members = found.length ? Array.from(found) : [eid];
    blocks.push(members);
    for (const m of members) {
      if (!unionSeen.has(m)) {
        unionSeen.add(m);
        union.push(m);
      }
    }
  }
  return { blocks, blockEdgeIds: union };
}

/**
 * Drop near-duplicate SAME-TYPE routes, keeping the higher score. Overlap is
 * path-edge-set Jaccard; a subset also counts as a duplicate (containment == 1).
 * Different vote types never suppress each other. Input order is irrelevant —
 * candidates are ranked by score (id-ascending tiebreak) first.
 */
export function dedupeRoutes(
  proposals: RouteProposal[],
  jaccardThreshold: number = DEFAULT_JACCARD,
): RouteProposal[] {
  const ordered = [...proposals].sort(
    (a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  const kept: RouteProposal[] = [];
  const keptSets: { legendIdx: number; edges: Set<number> }[] = [];
  for (const p of ordered) {
    const s = new Set(p.edgeIds);
    let dup = false;
    for (const k of keptSets) {
      if (k.legendIdx !== p.legendIdx) continue;
      let inter = 0;
      for (const e of s) if (k.edges.has(e)) inter++;
      if (!inter) continue;
      const jaccard = inter / (s.size + k.edges.size - inter);
      const containment = inter / Math.min(s.size, k.edges.size);
      if (jaccard >= jaccardThreshold || containment >= 0.999) {
        dup = true;
        break;
      }
    }
    if (!dup) {
      kept.push(p);
      keptSets.push({ legendIdx: p.legendIdx, edges: s });
    }
  }
  return kept;
}

/**
 * A resumable route-proposal extraction: `types` lists every eligible legend
 * index (ascending), `step(legendIdx)` runs the full pipeline for one vote
 * type, and `finish(perType)` assembles the ranked, capped list. Splitting on
 * the type boundary lets the app spread the recompute across idle slices —
 * one type per slice — instead of freezing for the whole walk; running every
 * step back-to-back is byte-identical to the old single-pass loop, so the
 * determinism contract is unchanged (computeRouteProposals below does exactly
 * that and remains the reference/entry point for tests and one-shot callers).
 */
export interface RouteProposalJob {
  types: number[];
  step(legendIdx: number): RouteProposal[];
  finish(perType: RouteProposal[][]): RouteProposal[];
}

export function createRouteProposalJob(
  topo: GraphTopology,
  adj: NodeAdj,
  data: {
    edge_vote_types?: [number, number, number][][];
    vote_type_legend?: string[];
  },
  opts: RouteProposalOptions = {},
): RouteProposalJob {
  const legend = data.vote_type_legend ?? [];
  const edgeVoteTypes = data.edge_vote_types ?? [];
  const limit = opts.limit ?? DEFAULT_LIMIT;
  const jaccardThreshold = opts.jaccardThreshold ?? DEFAULT_JACCARD;
  const minNet = opts.minNet ?? MIN_NET;
  const minRouteScore = opts.minRouteScore ?? MIN_ROUTE_SCORE;
  const minRouteEdges = opts.minRouteEdges ?? MIN_ROUTE_EDGES;
  const minRouteBlocks = opts.minRouteBlocks ?? MIN_ROUTE_BLOCKS;
  const maxRouteLengthM = opts.maxRouteLengthM ?? ROUTE_LENGTH_MAX_M;
  const blockIndex = opts.blockIndex !== undefined ? opts.blockIndex : buildBlockIndex(topo);
  const netsPerType = netsByType(edgeVoteTypes, topo.nEdges, legend.length);

  const types: number[] = [];
  for (let legendIdx = 0; legendIdx < legend.length; legendIdx++) {
    const label = legend[legendIdx];
    if (!label) continue;
    // POINT-kind vote types never form corridors — their votes are PBTP pins
    // (topProposals.ts). Unknown kind (null) stays eligible for both families.
    if (opts.kindOf && opts.kindOf(label) === "point") continue;
    if (netsPerType[legendIdx].size === 0) continue;
    types.push(legendIdx);
  }

  const step = (legendIdx: number): RouteProposal[] => {
    const label = legend[legendIdx];
    const nets = netsPerType[legendIdx];
    const typeAdj = buildTypeAdj(topo, adj, nets, minNet);
    const typeProposals: RouteProposal[] = [];
    const lengthOf = (e: number) => edgeLengthMeters(topo, e);
    const weightOf = (e: number) => nets.get(e) ?? 0;
    const latLngOf = (nid: number) => nodeLatLng(topo, nid);
    for (const compAdj of connectedComponents(typeAdj)) {
      for (const peeled of peelPaths(compAdj)) {
        // Split loop-backs FIRST (a snake becomes several straight corridors),
        // then trim each fragment to the meter budget ITS OWN support earned,
        // BEFORE the activity gates — the gates judge the corridor that will
        // actually be shown.
        for (const frag of splitLoopyPath(peeled, lengthOf, latLngOf, weightOf)) {
          const path = capPathToLengthBudget(
            frag,
            routeLengthBudgetM(frag.weight, maxRouteLengthM),
            lengthOf,
            weightOf,
          );
          if (path.weight < minRouteScore || path.edges.length < minRouteEdges) continue;
          const { blocks, blockEdgeIds } = groupBlocks(path.edges, topo, blockIndex);
          if (blocks.length < minRouteBlocks) continue;
          const a = path.nodes[0];
          const b = path.nodes[path.nodes.length - 1];
          const [aLat, aLng] = nodeLatLng(topo, a);
          const [bLat, bLng] = nodeLatLng(topo, b);
          typeProposals.push({
            id: proposalIdOf(legendIdx, path.edges),
            label,
            legendIdx,
            score: path.weight,
            edgeIds: path.edges,
            blocks,
            blockEdgeIds,
            anchors: [a, b],
            anchorCoords: [{ lat: aLat, lng: aLng }, { lat: bLat, lng: bLng }],
          });
        }
      }
    }
    return dedupeRoutes(typeProposals, jaccardThreshold);
  };

  const finish = (perType: RouteProposal[][]): RouteProposal[] => {
    const all = perType.flat();
    all.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    return all.slice(0, limit);
  };

  return { types, step, finish };
}

/**
 * Full deterministic route-proposal extraction from the current vote state.
 * Same (topology, vote state) ⇒ identical output (ids and order) on every
 * client — see the determinism contract above.
 */
export function computeRouteProposals(
  topo: GraphTopology,
  adj: NodeAdj,
  data: {
    edge_vote_types?: [number, number, number][][];
    vote_type_legend?: string[];
  },
  opts: RouteProposalOptions = {},
): RouteProposal[] {
  const job = createRouteProposalJob(topo, adj, data, opts);
  return job.finish(job.types.map(job.step));
}
