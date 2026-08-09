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
  EARTH_RADIUS_M,
  buildBlockIndex,
  edgeLengthMeters,
  edgesOfBlockKey,
  nodeLatLng,
  type BlockIndex,
  type GraphTopology,
  type NodeAdj,
} from "./graphTopology";
import type { VoteTypeKindResolver, VoteTypeVisibility } from "./topProposals";

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
  /** Route waypoints along the path: [anchor A, ghost mids…, anchor B] as
   *  node indices. Ghosts mark where growth had to PIN the corridor because
   *  routing between the surrounding waypoints would otherwise leave it (at
   *  most MAX_GHOST_WAYPOINTS). Selecting the proposal threads ALL of these
   *  into the selection — and thus the URL — so a shared link re-routes into
   *  (approximately) this corridor even after the proposal retires. */
  waypointNodes: number[];
  /** Coordinates of `waypointNodes` (same order). */
  waypointCoords: LatLng[];
  /** Path edge ids per waypoint segment: segments[i] joins waypointNodes[i] →
   *  waypointNodes[i+1]; concatenated they equal `edgeIds`. */
  segments: number[][];
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
  /** Optional ghost-waypoint fields; absent on legacy payloads (then the
   *  waypoints are just the two anchors and the path is one segment). */
  waypoint_nodes?: number[];
  waypoint_coords?: [number, number][];
  segments?: number[][];
}

/** Map the server wire shape (snake_case, [lat,lng] tuples) to a RouteProposal. */
export function parseRouteProposal(j: RouteProposalJson): RouteProposal {
  const toLatLng = ([lat, lng]: [number, number]): LatLng => ({ lat, lng });
  const anchorCoords: [LatLng, LatLng] =
    [toLatLng(j.anchor_coords[0]), toLatLng(j.anchor_coords[1])];
  const hasWaypoints =
    j.waypoint_nodes && j.waypoint_coords &&
    j.waypoint_nodes.length === j.waypoint_coords.length &&
    j.waypoint_nodes.length >= 2;
  return {
    id: j.id,
    label: j.label,
    legendIdx: j.legendIdx,
    score: j.score,
    edgeIds: j.edge_ids,
    blocks: j.blocks,
    blockEdgeIds: j.block_edge_ids,
    anchors: j.anchors,
    anchorCoords,
    waypointNodes: hasWaypoints ? j.waypoint_nodes! : [...j.anchors],
    waypointCoords: hasWaypoints ? j.waypoint_coords!.map(toLatLng) : [...anchorCoords],
    segments: j.segments && j.segments.length ? j.segments : [j.edge_ids],
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

/**
 * The corridor's sub-chain between the proposal WAYPOINTS nearest `a` and `b`,
 * oriented a→b — the per-segment corridor resolver for a selection that
 * threads a multi-waypoint (ghosted) proposal: each selection segment between
 * two consecutive proposal waypoints resolves to exactly its slice of the
 * corridor. With a two-waypoint proposal this degenerates to the whole
 * corridor. Null when the chain breaks (stale topology), the segment shape
 * doesn't match the path, or `a`/`b` land on the same waypoint.
 */
export function corridorSliceBetween(
  topo: GraphTopology,
  p: RouteProposal,
  a: LatLng,
  b: LatLng,
): { coordinates: [number, number][]; edgeIds: number[] } | null {
  if (p.edgeIds.length === 0) return null;
  // Node chain of the full corridor, anchors[0] → anchors[1].
  let cur = p.anchors[0];
  if (cur >= topo.nNodes) return null;
  const chain: number[] = [cur];
  for (const e of p.edgeIds) {
    if (e >= topo.nEdges) return null;
    const u = topo.ends[2 * e];
    const v = topo.ends[2 * e + 1];
    const next = u === cur ? v : v === cur ? u : null;
    if (next === null || next >= topo.nNodes) return null;
    chain.push(next);
    cur = next;
  }
  // Waypoint positions along the chain, from the per-segment edge counts.
  const wpPos: number[] = [0];
  for (const seg of p.segments) wpPos.push(wpPos[wpPos.length - 1] + seg.length);
  if (wpPos.length !== p.waypointNodes.length || wpPos[wpPos.length - 1] !== p.edgeIds.length) {
    return null;
  }
  const sq = (n: number, q: LatLng) => {
    const [lat, lng] = nodeLatLng(topo, n);
    return (lat - q.lat) ** 2 + (lng - q.lng) ** 2;
  };
  const nearestWp = (q: LatLng) => {
    let bi = 0;
    let bd = Infinity;
    for (let i = 0; i < wpPos.length; i++) {
      const d = sq(chain[wpPos[i]], q);
      if (d < bd) { bd = d; bi = i; }
    }
    return bi;
  };
  const ia = nearestWp(a);
  const ib = nearestWp(b);
  if (ia === ib) return null;
  const [lo, hi] = ia < ib ? [ia, ib] : [ib, ia];
  const edgeIds = p.edgeIds.slice(wpPos[lo], wpPos[hi]);
  const coords = chain.slice(wpPos[lo], wpPos[hi] + 1).map((n) => {
    const [lat, lng] = nodeLatLng(topo, n);
    return [lng, lat] as [number, number];
  });
  return { coordinates: ia < ib ? coords : coords.reverse(), edgeIds };
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
// Client-side deterministic clustering
// ==========================================================================
// Pipeline per ROUTE-kind vote type (docs/three-layer-model.md §3.2; point-
// kind types are skipped — their votes surface as PBTP pins): net-positive
// subgraph → connected components (components localize; corridor peeling
// separates parallel corridors inside one component) → grow a corridor from
// the heaviest edge by ROUTING-CONSISTENT extension (growCorridor: an
// extension must keep the open segment a shortest path, or it pins the
// previous endpoint as a GHOST WAYPOINT — at most MAX_GHOST_WAYPOINTS, then
// growth ends) → peel its edges out and repeat → activity gates (score,
// edges, blocks) → block projection → same-type dedupe → rank with a
// per-type diversity quota (MAX_PER_TYPE) + cap.
//
// Why routing-consistent: a proposal's waypoints (anchors + ghosts) go into
// the selection URL when it's picked, so the link must ROUTE back into the
// corridor even after the proposal retires. Growth therefore only accepts
// extensions the router would reproduce, and spends the 3-ghost budget where
// it wouldn't — which also bounds how roundabout a corridor can get (the old
// straightness-splitting + budget-window trimming this replaces).
//
// Why pins get re-examined: shortest-ness is only a proxy for "routing hands
// this stretch back", and a pessimistic one — a two-metre-shorter alternative
// that runs along the same blocks fails it. So growth asks a second oracle
// (makeSegmentRecoveryCheck: route it and compare BLOCKS) before spending a
// ghost, and prunes ghosts that stopped being needed as the corridor grew past
// them. Reclaimed pins go straight back into the budget, so a corridor that
// spent its three early can still reach further.
//
// Determinism contract: NO randomness, NO clock. Every iteration order and
// tie-break is by ascending edge/node id (the A* check breaks heap ties by
// node id), so the same (topology, vote state) yields byte-identical
// proposals (ids and order) on every client.

/** Minimum net (up − down) for an edge to enter a type's subgraph. */
export const MIN_NET = 1;
/** Peel at most this many corridors out of one component. */
export const PEEL_MAX_PATHS = 8;
/** A peeled corridor survives only at ≥ this fraction of the component's first. */
export const PEEL_DOMINANCE = 0.25;
/** Ghost-waypoint budget: growth may pin the corridor at most this many times
 *  (so a proposal carries at most 2 anchors + 3 ghosts = 5 waypoints). The 3rd
 *  pin ends growth — "3 path modifications and we're done". */
export const MAX_GHOST_WAYPOINTS = 3;
/** An alternate path must be shorter than the corridor segment by MORE than
 *  this to count as "routing would leave the corridor". Absorbs float noise
 *  and meaningless sub-meter shortcuts (grid tie-paths stay ties). */
export const ROUTE_CONSISTENCY_EPS_M = 1;
/** A* node-pop cap per consistency check. The search explores the ellipse
 *  {x : d(seg start,x)+crow(x,seg end) ≤ segment length}, razor-thin for the
 *  near-straight segments consistent growth produces — this cap only bites on
 *  pathological geometry, where the check FAILS OPEN (treats the corridor as
 *  shortest) to keep recompute time bounded. Deterministic either way. */
export const ROUTE_CHECK_MAX_POPS = 30000;
/** Fraction of a corridor stretch's BLOCKS that plain routing between its two
 *  bounding waypoints must hand back for the stretch to count as "recovered" —
 *  i.e. for the ghost pinning it to be unnecessary. Not 1.0: a shortcut that
 *  clips a corner off one block in twenty still reproduces the corridor for
 *  every purpose a proposal has (its blocks are the display and voting grain),
 *  and demanding perfection would keep pins that buy nothing. */
export const RECOVERY_MIN_BLOCK_COVERAGE = 0.85;
/** Recovery-check budget per grown corridor (they cost an A* apiece, path
 *  reconstruction included). Growth spends them on strict-check failures that
 *  might not need a pin; the prune pass spends them on ghosts that might no
 *  longer be needed. Exhausted, both fall back to the plain strict behaviour. */
export const MAX_RECOVERY_CHECKS = 16;
/** How many times one corridor may re-examine its ghosts. A pass runs when
 *  growth stalls or spends its last pin: any ghost the router no longer needs
 *  is dropped, which hands its budget back and lets growth continue. Capped
 *  so a corridor can't ping-pong between pinning and pruning forever. */
export const MAX_PRUNE_PASSES = 3;
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
/** Type-diversity quota: at most this many proposals of ONE vote type in the
 *  ranked list. Without it, bulk-imported types (scores in the tens of
 *  thousands) fill every slot and organic types never surface — Broadway's
 *  net-strongest corridor ("Add sharrow", score 237) ranked #87 behind 86
 *  imported-type corridors. Slots the quota can't fill backfill by pure score. */
export const MAX_PER_TYPE = 4;
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

export interface RouteProposalOptions {
  limit?: number;
  /** Max proposals of one vote type in the ranked list (MAX_PER_TYPE). */
  maxPerType?: number;
  jaccardThreshold?: number;
  minNet?: number;
  minRouteScore?: number;
  minRouteEdges?: number;
  /** Min-distance gate: minimum BLOCKS a corridor must span (MIN_ROUTE_BLOCKS). */
  minRouteBlocks?: number;
  /** Hard ceiling override for the corridor length budget (meters). */
  maxRouteLengthM?: number;
  /** Ghost-waypoint budget for corridor growth (MAX_GHOST_WAYPOINTS). */
  maxGhostWaypoints?: number;
  /** Routing-consistency oracle override (tests inject fakes). Default:
   *  makeSegmentShortestCheck(topo, adj) — bounded A* over the full graph. */
  segmentShortestCheck?: SegmentShortestCheck;
  /** Corridor-recovery oracle override — the second opinion that keeps growth
   *  from pinning (and lets it un-pin) ghosts routing doesn't need. Default:
   *  makeSegmentRecoveryCheck(topo, adj); pass null to disable pruning. */
  segmentRecoveryCheck?: SegmentRecoveryCheck | null;
  /** Recovery-check budget per corridor (MAX_RECOVERY_CHECKS). 0 leaves only
   *  the reserved final pass: pins still get cleaned off finished corridors,
   *  but none are reclaimed early enough to buy more reach. */
  maxRecoveryChecks?: number;
  /** Mid-growth prune passes per corridor (MAX_PRUNE_PASSES). */
  maxPrunePasses?: number;
  /** Prebuilt edge→block index for `topo` (GraphLayer already holds one).
   *  Omitted, one is built here — an O(nEdges) pass worth skipping per call. */
  blockIndex?: BlockIndex | null;
  /** Label → route/point kind. POINT-kind vote types are skipped — their votes
   *  surface as PBTP pins (topProposals.ts), not corridors. Unknown (null)
   *  kinds stay eligible. Omit to admit every type. */
  kindOf?: VoteTypeKindResolver;
  /** Label → toggled on in the legend (map/voteTypeFilter). Hidden types are
   *  skipped before any clustering runs, so filtering the map also SPEEDS UP
   *  the recompute — and frees ranked slots for the types left on. Omit to
   *  admit every type. */
  isVisible?: VoteTypeVisibility;
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

/** A grown corridor: the path plus the route waypoints that reproduce it —
 *  [tip A, ghost pins…, tip B] in path order — and the path edges of each
 *  waypoint-to-waypoint segment (concatenated they equal `edges`). */
export interface GrownCorridor extends PathResult {
  waypointNodes: number[];
  segments: number[][];
}

/**
 * Routing-consistency oracle: is the corridor segment from `fromNode` to
 * `toNode` of length `corridorLenM` (still) a shortest path through the FULL
 * graph? "Shortest" tolerates ties and sub-eps shortcuts
 * (ROUTE_CONSISTENCY_EPS_M): routing may pick an equal-length alternative,
 * but that is a tie the block-grain display forgives, not a detour.
 */
export type SegmentShortestCheck = (
  fromNode: number,
  toNode: number,
  corridorLenM: number,
) => boolean;

/**
 * Bounded A* from `start` to `goal` over the FULL topology, refusing any path
 * longer than `limit` meters. The heuristic is crow-flies (equirectangular,
 * scaled ×0.999 to stay admissible under the per-edge mean-latitude lengths),
 * so the explored region is exactly the ellipse of paths that could come in
 * under `limit` — razor-thin for the near-straight segments consistent growth
 * produces. `reached` is true only when a path of length ≤ limit EXISTS;
 * exhausting the frontier, a crow distance already over the limit, or the pop
 * cap all report unreached (`capped` distinguishes the last). With
 * `wantPath`, `pathEdges` carries the optimal path's edge ids (goal→start
 * order). Deterministic: heap ties break by node id.
 */
function boundedAStar(
  topo: GraphTopology,
  adj: NodeAdj,
  start: number,
  goal: number,
  limit: number,
  maxPops: number,
  wantPath: boolean,
): { reached: boolean; capped: boolean; pathEdges: number[] } {
  const { ends } = topo;
  const unreached = { reached: false, capped: false, pathEdges: [] as number[] };
  // Crow-flies distance in the SAME earth model edgeLengthMeters uses — mixing
  // models (the WGS84 metres-per-degree constants against its spherical R) put
  // the heuristic ~0.1% ABOVE the graph's own edge lengths, i.e. inadmissible,
  // which reads a straight corridor as unreachable inside its own length.
  const [tLat, tLng] = nodeLatLng(topo, goal);
  const degM = (EARTH_RADIUS_M * Math.PI) / 180;
  const ky = degM;
  const kx = degM * Math.cos((tLat * Math.PI) / 180);
  const crowToTarget = (n: number): number => {
    const [lat, lng] = nodeLatLng(topo, n);
    return Math.hypot((lat - tLat) * ky, (lng - tLng) * kx) * 0.999;
  };
  if (crowToTarget(start) > limit) return unreached;

  // Binary min-heap of (f, node) pairs; lazy deletes via the closed set.
  const heapF: number[] = [];
  const heapN: number[] = [];
  const less = (i: number, j: number) =>
    heapF[i] < heapF[j] || (heapF[i] === heapF[j] && heapN[i] < heapN[j]);
  const swap = (i: number, j: number) => {
    const f = heapF[i]; heapF[i] = heapF[j]; heapF[j] = f;
    const n = heapN[i]; heapN[i] = heapN[j]; heapN[j] = n;
  };
  const push = (f: number, n: number) => {
    heapF.push(f); heapN.push(n);
    let i = heapF.length - 1;
    while (i > 0) {
      const par = (i - 1) >> 1;
      if (!less(i, par)) break;
      swap(i, par); i = par;
    }
  };
  const pop = (): number => {
    const top = heapN[0];
    const lastF = heapF.pop()!;
    const lastN = heapN.pop()!;
    if (heapF.length) {
      heapF[0] = lastF; heapN[0] = lastN;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < heapF.length && less(l, m)) m = l;
        if (r < heapF.length && less(r, m)) m = r;
        if (m === i) break;
        swap(i, m); i = m;
      }
    }
    return top;
  };

  const g = new Map<number, number>([[start, 0]]);
  const parentEdge = wantPath ? new Map<number, number>() : null;
  const closed = new Set<number>();
  push(crowToTarget(start), start);
  let pops = 0;
  while (heapF.length) {
    const f = heapF[0];
    const n = pop();
    if (closed.has(n)) continue;
    closed.add(n);
    if (f > limit) return unreached;   // best possible remaining path > limit
    if (n === goal) {
      const pathEdges: number[] = [];
      if (parentEdge) {
        let cur = goal;
        while (cur !== start) {
          const e = parentEdge.get(cur);
          if (e === undefined) break;
          pathEdges.push(e);
          const u = ends[2 * e];
          cur = u === cur ? ends[2 * e + 1] : u;
        }
      }
      return { reached: true, capped: false, pathEdges };
    }
    if (++pops > maxPops) return { reached: false, capped: true, pathEdges: [] };
    const gn = g.get(n)!;
    const row = adjEdgesOf(adj, n);
    for (let i = 0; i < row.length; i++) {
      const e = row[i];
      const u = ends[2 * e];
      const v = ends[2 * e + 1];
      const other = u === n ? v : u;
      if (other === n || closed.has(other)) continue;
      const ng = gn + edgeLengthMeters(topo, e);
      if (ng > limit) continue;
      const cur = g.get(other);
      if (cur !== undefined && cur <= ng) continue;
      g.set(other, ng);
      parentEdge?.set(other, e);
      push(ng + crowToTarget(other), other);
    }
  }
  return unreached; // frontier exhausted: nothing comes in under the limit
}

/**
 * The default oracle: bounded A* over the full topology, searching from
 * `toNode` toward `fromNode` under a budget of (corridor length − eps).
 * Returns false only when a strictly (> eps) shorter path EXISTS; exhausting
 * the frontier, a crow distance already at/over the corridor, or the pop cap
 * (fail open, bounded work) all return true.
 */
export function makeSegmentShortestCheck(
  topo: GraphTopology,
  adj: NodeAdj,
  opts: { epsM?: number; maxPops?: number } = {},
): SegmentShortestCheck {
  const eps = opts.epsM ?? ROUTE_CONSISTENCY_EPS_M;
  const maxPops = opts.maxPops ?? ROUTE_CHECK_MAX_POPS;
  return (fromNode, toNode, corridorLenM) => {
    if (fromNode === toNode) return true;
    const limit = corridorLenM - eps;
    if (limit <= 0) return true;
    // Pushes are pruned at g > limit, so reaching the target means a path
    // strictly shorter (by > eps) than the corridor exists.
    return !boundedAStar(topo, adj, toNode, fromNode, limit, maxPops, false).reached;
  };
}

/**
 * Corridor-recovery oracle: routing plainly from `fromNode` to `toNode` — no
 * waypoint in between — does it come back with (essentially) the corridor
 * stretch `corridorEdges`?
 *
 * This is the question a ghost waypoint actually exists to answer, and it is
 * WEAKER than shortest-ness in the direction that matters: a corridor can lose
 * a metres-long race to a parallel alternative and still be what the router
 * returns, and an alternative that shaves a corner off one block still hands
 * back the same corridor for display purposes. Growth consults it before
 * spending a ghost, and the prune pass consults it to hand ghosts back.
 */
export type SegmentRecoveryCheck = (
  fromNode: number,
  toNode: number,
  corridorEdges: number[],
) => boolean;

/**
 * The default recovery oracle: shortest-path A* between the two waypoints
 * (bounded by the corridor's own length — the corridor IS a path, so nothing
 * longer can win), then a BLOCK-grain comparison of what came back against
 * what the corridor covers. Block grain is the point: blocks are the display
 * and voting unit, and a routed path routinely rides the direction twin of the
 * edge the corridor recorded. Recovered at ≥ `minCoverage` of the corridor's
 * blocks. Fails CLOSED — an unreachable goal or the pop cap keeps the pin.
 */
export function makeSegmentRecoveryCheck(
  topo: GraphTopology,
  adj: NodeAdj,
  opts: { maxPops?: number; minCoverage?: number } = {},
): SegmentRecoveryCheck {
  const maxPops = opts.maxPops ?? ROUTE_CHECK_MAX_POPS;
  const minCoverage = opts.minCoverage ?? RECOVERY_MIN_BLOCK_COVERAGE;
  const { ends } = topo;
  // An edge's corridor UNIT: its block where it has one, else its undirected
  // node pair — either way a direction twin counts as the same unit.
  const unitOf = (e: number): number => {
    const key = blockKeyOf(topo, e);
    if (key >= 0) return key;
    const u = ends[2 * e], v = ends[2 * e + 1];
    const lo = u < v ? u : v;
    const hi = u < v ? v : u;
    return -1 - (lo * topo.nNodes + hi);
  };
  return (fromNode, toNode, corridorEdges) => {
    if (fromNode === toNode || corridorEdges.length === 0) return false;
    const units = new Set<number>();
    let corridorLenM = 0;
    for (const e of corridorEdges) {
      units.add(unitOf(e));
      corridorLenM += edgeLengthMeters(topo, e);
    }
    // Slack absorbs float-summation order: the corridor must fit its own limit.
    const found = boundedAStar(
      topo, adj, toNode, fromNode, corridorLenM + 0.001, maxPops, true,
    );
    if (!found.reached) return false;
    const routed = new Set<number>();
    for (const e of found.pathEdges) routed.add(unitOf(e));
    let hit = 0;
    for (const u of units) if (routed.has(u)) hit++;
    return hit / units.size >= minCoverage - 1e-9;
  };
}

/**
 * Grow ONE corridor from the component's heaviest edge by routing-consistent
 * extension. Each step considers the net-positive arcs leaving EITHER tip and
 * takes the heaviest that fits the support-earned length budget (ties: lowest
 * edge id). The open segment between a tip and its nearest inner waypoint
 * must remain a shortest path (`isSegmentShortest`): an extension that keeps
 * it so is taken outright; one that breaks it PINS the previous tip as a
 * GHOST WAYPOINT — the route now passes through it, so a link routing through
 * the waypoints still reproduces the corridor — and the 3rd pin ends growth
 * ("3 path modifications and we're done"). With the ghost budget spent, only
 * still-consistent extensions are taken. The seed edge itself is accepted
 * unchecked: there is nothing to pin between two adjacent nodes, and the
 * forced-corridor flag still pins exact geometry while the proposal lives.
 *
 * Pins are not final. Strict shortest-ness is a PROXY for the real question —
 * would routing hand this stretch back? — and it is a pessimistic one: an
 * alternative two metres shorter fails the proxy while still returning the
 * same corridor. So, given a `recoversCorridor` oracle, growth (a) asks it
 * before spending a ghost on a strict-check failure, and (b) runs a PRUNE PASS
 * whenever it stalls or spends its last pin, re-testing every ghost against
 * the stretch between its NEIGHBOURING waypoints. A ghost pinned early often
 * turns unnecessary once the far end has moved on — routing to the more
 * distant target no longer takes the shortcut that forced the pin. Every ghost
 * dropped is a waypoint off the shared URL and a pin handed back to growth, so
 * the corridor can reach further on the same budget.
 */
export function growCorridor(
  adj: TypeAdj,
  lengthOf: (edgeId: number) => number,
  isSegmentShortest: SegmentShortestCheck,
  opts: {
    maxGhosts?: number;
    budgetOf?: (weight: number) => number;
    /** Oracle for "routing reproduces this stretch" (makeSegmentRecoveryCheck).
     *  Omitted, growth keeps every pin the strict check asks for. */
    recoversCorridor?: SegmentRecoveryCheck | null;
    maxRecoveryChecks?: number;
    maxPrunePasses?: number;
  } = {},
): GrownCorridor | null {
  const maxGhosts = opts.maxGhosts ?? MAX_GHOST_WAYPOINTS;
  const budgetOf = opts.budgetOf ?? ((w: number) => routeLengthBudgetM(w));
  const recoversCorridor = opts.recoversCorridor ?? null;
  let recoveryChecksLeft = opts.maxRecoveryChecks ?? MAX_RECOVERY_CHECKS;
  let prunePassesLeft = opts.maxPrunePasses ?? MAX_PRUNE_PASSES;

  // Seed: the heaviest arc; strict > keeps the first found (lowest node id,
  // then the row's ascending edge order) — the old greedy's exact seed rule.
  let seedA = -1, seedB = -1, seedE = -1, seedW = -1;
  for (const [a, arcs] of adj) {
    for (const arc of arcs) {
      if (arc.w > seedW) { seedA = a; seedB = arc.n; seedE = arc.e; seedW = arc.w; }
    }
  }
  if (seedA < 0) return null;

  const seedLen = lengthOf(seedE);
  const nodes: number[] = [seedA, seedB];
  const edges: number[] = [seedE];
  const seen = new Set<number>([seedA, seedB]);
  const ghostSet = new Set<number>();
  let weight = seedW;
  let totalLen = seedLen;
  // Arcs that would need a pin after the budget was spent — not reconsidered
  // until a prune pass hands a pin back (the state that rejected them changed).
  const rejected = new Set<number>();
  // The path has changed since the last prune pass, so ghosts are worth
  // re-testing. Cleared by a pass, set by every accepted extension.
  let pathDirty = true;

  // Open-segment lengths are re-summed from the path (rather than carried
  // incrementally) so a pruned pin needs no bookkeeping fixups — but every
  // length is a trig call, so memoise per edge. Summation order is the path's,
  // so the sums stay bit-identical run to run.
  const lenCache = new Map<number, number>();
  const lenOf = (e: number): number => {
    let v = lenCache.get(e);
    if (v === undefined) lenCache.set(e, (v = lengthOf(e)));
    return v;
  };
  const sumLen = (eids: number[]): number => {
    let total = 0;
    for (const e of eids) total += lenOf(e);
    return total;
  };
  /** Positions of the ghost pins along `nodes`, in path order. */
  const ghostIndices = (): number[] => {
    const out: number[] = [];
    for (let i = 1; i < nodes.length - 1; i++) if (ghostSet.has(nodes[i])) out.push(i);
    return out;
  };
  /**
   * The OPEN segment ending at `side`'s tip (0 = front, 1 = back): the inner
   * waypoint bounding it and the path edges in between. With no ghosts pinned
   * the segment is the whole path — both tips move and the bounds chase them.
   */
  const openSegment = (side: 0 | 1): { bound: number; edges: number[] } => {
    const ghosts = ghostIndices();
    if (side === 0) {
      const hi = ghosts.length ? ghosts[0] : nodes.length - 1;
      return { bound: nodes[hi], edges: edges.slice(0, hi) };
    }
    const lo = ghosts.length ? ghosts[ghosts.length - 1] : 0;
    return { bound: nodes[lo], edges: edges.slice(lo) };
  };
  const recovers = (from: number, to: number, stretch: number[]): boolean => {
    if (!recoversCorridor || recoveryChecksLeft <= 0) return false;
    recoveryChecksLeft--;
    return recoversCorridor(from, to, stretch);
  };

  /** One growth step off either tip. */
  type StepResult = "took" | "stalled" | "spent";
  const extendOnce = (): StepResult => {
    const tipA = nodes[0];
    const tipB = nodes[nodes.length - 1];
    // Candidates off both tips, heaviest first (ties: lowest edge id).
    const cands: { side: 0 | 1; n: number; e: number; w: number; len: number }[] = [];
    for (const [side, tip] of [[0, tipA], [1, tipB]] as const) {
      for (const arc of adj.get(tip) ?? []) {
        if (seen.has(arc.n) || rejected.has(arc.e)) continue;
        cands.push({ side, n: arc.n, e: arc.e, w: arc.w, len: lenOf(arc.e) });
      }
    }
    cands.sort((x, y) => y.w - x.w || x.e - y.e);

    for (const c of cands) {
      // Support-earned length budget — support buys reach as it accumulates.
      // A skipped candidate is retried next round (more weight, more budget).
      if (totalLen + c.len > budgetOf(weight + c.w)) continue;
      const prevTip = c.side === 0 ? tipA : tipB;
      const seg = openSegment(c.side);
      let consistent = isSegmentShortest(seg.bound, c.n, sumLen(seg.edges) + c.len);
      if (!consistent) {
        // Beaten on length, but is the corridor still what routing returns? If
        // so the pin would buy nothing — take the extension and keep the ghost.
        const stretch = c.side === 0 ? [c.e, ...seg.edges] : [...seg.edges, c.e];
        consistent = recovers(seg.bound, c.n, stretch);
      }
      if (!consistent && ghostSet.size >= maxGhosts) {
        rejected.add(c.e);
        continue;
      }
      // Accept the extension.
      if (c.side === 0) { nodes.unshift(c.n); edges.unshift(c.e); }
      else { nodes.push(c.n); edges.push(c.e); }
      seen.add(c.n);
      weight += c.w;
      totalLen += c.len;
      pathDirty = true;
      if (!consistent) {
        // Pin the previous tip: routing from the bound would leave the
        // corridor here, so the URL must carry this point.
        ghostSet.add(prevTip);
        if (ghostSet.size >= maxGhosts) return "spent";
      }
      return "took";
    }
    return "stalled";
  };

  /**
   * Re-test every ghost against the stretch between its NEIGHBOURING
   * waypoints and drop the ones routing no longer needs, walking outward-in in
   * path order so each test sees the survivors on its left. Returns whether
   * anything was dropped (growth then resumes with the reclaimed budget).
   */
  const pruneGhosts = (): boolean => {
    if (!recoversCorridor || !pathDirty || prunePassesLeft <= 0) return false;
    if (ghostSet.size === 0) return false;
    prunePassesLeft--;
    pathDirty = false;
    const ghosts = ghostIndices();
    let dropped = false;
    let prev = 0; // index of the last surviving waypoint
    for (let k = 0; k < ghosts.length; k++) {
      const i = ghosts[k];
      const next = k + 1 < ghosts.length ? ghosts[k + 1] : nodes.length - 1;
      if (recovers(nodes[prev], nodes[next], edges.slice(prev, next))) {
        ghostSet.delete(nodes[i]);
        dropped = true;
      } else {
        prev = i;
      }
    }
    // A reclaimed pin makes the arcs rejected for want of one worth another look.
    if (dropped) rejected.clear();
    return dropped;
  };

  let spent = false;
  for (;;) {
    const step = spent ? "spent" : extendOnce();
    if (step === "took") continue;
    spent = step === "spent";
    // Stalled or out of pins: reclaim what the router doesn't need and retry.
    if (!pruneGhosts()) break;
    spent = false;
  }
  // The path is final — one last pass, on a RESERVED allowance the growth loop
  // can't have spent. Mid-growth passes are budgeted (they can restart growth,
  // so they compound); this one only deletes waypoints from a finished
  // corridor, and skipping it would ship URLs carrying pins that stopped
  // mattering several extensions ago. Cost is bounded by the pin count.
  prunePassesLeft = Math.max(prunePassesLeft, 1);
  recoveryChecksLeft = Math.max(recoveryChecksLeft, ghostSet.size);
  pathDirty = true;
  pruneGhosts();

  // Waypoints in path order + the per-segment edge slices they delimit.
  const waypointNodes: number[] = [nodes[0]];
  const segments: number[][] = [];
  let segStart = 0;
  for (let i = 1; i < nodes.length - 1; i++) {
    if (ghostSet.has(nodes[i])) {
      waypointNodes.push(nodes[i]);
      segments.push(edges.slice(segStart, i));
      segStart = i;
    }
  }
  waypointNodes.push(nodes[nodes.length - 1]);
  segments.push(edges.slice(segStart));
  return { edges, nodes, weight, waypointNodes, segments };
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

/** Successively grow-and-remove corridors out of a component — a region's
 *  real parallel corridors surface while weak dead-end residue (below
 *  PEEL_DOMINANCE × the first corridor's weight) is dropped. */
function peelCorridors(
  adj: TypeAdj,
  grow: (work: TypeAdj) => GrownCorridor | null,
): GrownCorridor[] {
  const out: GrownCorridor[] = [];
  let first: number | null = null;
  let work = adj;
  while (work.size && out.length < PEEL_MAX_PATHS) {
    const grown = grow(work);
    if (!grown || grown.edges.length === 0) break;
    if (first === null) first = grown.weight;
    else if (grown.weight < PEEL_DOMINANCE * first) break;
    out.push(grown);
    work = removeEdges(work, new Set(grown.edges));
  }
  return out;
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
  const maxPerType = opts.maxPerType ?? MAX_PER_TYPE;
  const jaccardThreshold = opts.jaccardThreshold ?? DEFAULT_JACCARD;
  const minNet = opts.minNet ?? MIN_NET;
  const minRouteScore = opts.minRouteScore ?? MIN_ROUTE_SCORE;
  const minRouteEdges = opts.minRouteEdges ?? MIN_ROUTE_EDGES;
  const minRouteBlocks = opts.minRouteBlocks ?? MIN_ROUTE_BLOCKS;
  const maxRouteLengthM = opts.maxRouteLengthM ?? ROUTE_LENGTH_MAX_M;
  const maxGhosts = opts.maxGhostWaypoints ?? MAX_GHOST_WAYPOINTS;
  const segmentShortest =
    opts.segmentShortestCheck ?? makeSegmentShortestCheck(topo, adj);
  const segmentRecovery = opts.segmentRecoveryCheck !== undefined
    ? opts.segmentRecoveryCheck
    : makeSegmentRecoveryCheck(topo, adj);
  const blockIndex = opts.blockIndex !== undefined ? opts.blockIndex : buildBlockIndex(topo);
  const netsPerType = netsByType(edgeVoteTypes, topo.nEdges, legend.length);

  const types: number[] = [];
  for (let legendIdx = 0; legendIdx < legend.length; legendIdx++) {
    const label = legend[legendIdx];
    if (!label) continue;
    // POINT-kind vote types never form corridors — their votes are PBTP pins
    // (topProposals.ts). Unknown kind (null) stays eligible for both families.
    if (opts.kindOf && opts.kindOf(label) === "point") continue;
    if (opts.isVisible && !opts.isVisible(label)) continue;
    if (netsPerType[legendIdx].size === 0) continue;
    types.push(legendIdx);
  }

  const step = (legendIdx: number): RouteProposal[] => {
    const label = legend[legendIdx];
    const nets = netsPerType[legendIdx];
    const typeAdj = buildTypeAdj(topo, adj, nets, minNet);
    const typeProposals: RouteProposal[] = [];
    const lengthOf = (e: number) => edgeLengthMeters(topo, e);
    const budgetOf = (w: number) => routeLengthBudgetM(w, maxRouteLengthM);
    const grow = (work: TypeAdj) =>
      growCorridor(work, lengthOf, segmentShortest, {
        maxGhosts,
        budgetOf,
        recoversCorridor: segmentRecovery,
        maxRecoveryChecks: opts.maxRecoveryChecks,
        maxPrunePasses: opts.maxPrunePasses,
      });
    for (const compAdj of connectedComponents(typeAdj)) {
      // No corridor can outscore its component's total support, so cold
      // components skip growth (and its A* checks) entirely. With the
      // top-proposal floor as minRouteScore this prunes almost everything.
      let compWeight = 0;
      {
        const counted = new Set<number>();
        for (const arcs of compAdj.values()) {
          for (const arc of arcs) {
            if (!counted.has(arc.e)) {
              counted.add(arc.e);
              compWeight += arc.w;
            }
          }
        }
      }
      if (compWeight < minRouteScore) continue;
      for (const path of peelCorridors(compAdj, grow)) {
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
          waypointNodes: path.waypointNodes,
          waypointCoords: path.waypointNodes.map((n) => {
            const [lat, lng] = nodeLatLng(topo, n);
            return { lat, lng };
          }),
          segments: path.segments,
        });
      }
    }
    return dedupeRoutes(typeProposals, jaccardThreshold);
  };

  const finish = (perType: RouteProposal[][]): RouteProposal[] => {
    const byScore = (a: RouteProposal, b: RouteProposal) =>
      b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    const all = perType.flat();
    all.sort(byScore);
    // Diversity pass: walk by score, admitting at most `maxPerType` per vote
    // type; leftover slots backfill from the skipped (5th-and-up of their type)
    // by score. Final list re-sorted so callers still see pure score order.
    const taken: RouteProposal[] = [];
    const skipped: RouteProposal[] = [];
    const perTypeCount = new Map<number, number>();
    for (const p of all) {
      const c = perTypeCount.get(p.legendIdx) ?? 0;
      if (c < maxPerType) {
        perTypeCount.set(p.legendIdx, c + 1);
        taken.push(p);
      } else {
        skipped.push(p);
      }
    }
    const ranked = taken.slice(0, limit);
    for (const p of skipped) {
      if (ranked.length >= limit) break;
      ranked.push(p);
    }
    ranked.sort(byScore);
    return ranked;
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
