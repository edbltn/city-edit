// ==========================================================================
// Route-based top proposals (client side)
// ==========================================================================
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

import type { LatLng } from "../../types";

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
