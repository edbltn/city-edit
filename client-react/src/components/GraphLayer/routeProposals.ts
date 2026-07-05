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
//
// It ALSO computes proposals client-side (computeRouteProposals below): a pure,
// deterministic function of (topology, vote state) per docs/three-layer-model.md
// §3 — recomputed in real time as vote deltas arrive, no server round-trip.

import type { LatLng } from "../../types";
import {
  adjEdgesOf,
  blockKeyOf,
  buildBlockIndex,
  edgesOfBlockKey,
  nodeLatLng,
  type BlockIndex,
  type GraphTopology,
  type NodeAdj,
} from "./graphTopology";

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

// ==========================================================================
// Client-side deterministic clustering (port of server/route_proposals.py)
// ==========================================================================
// Pipeline per vote type (docs/three-layer-model.md §3.2): net-positive
// subgraph → connected components (the deterministic replacement for the
// server's Leiden step — components localize; path peeling separates parallel
// corridors inside one component) → peel heaviest simple paths → activity
// gates → block projection → same-type dedupe → rank + cap.
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
/** Global cap on the ranked proposal list. */
export const DEFAULT_LIMIT = 20;
/** Same-type edge-set Jaccard at/above which two routes are duplicates. */
export const DEFAULT_JACCARD = 0.5;

export interface RouteProposalOptions {
  limit?: number;
  jaccardThreshold?: number;
  minNet?: number;
  minRouteScore?: number;
  minRouteEdges?: number;
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

/** Per-edge net (up − down) for one legend index, as a typed array. */
function netsForType(
  edgeVoteTypes: [number, number, number][][],
  nEdges: number,
  legendIdx: number,
): Int32Array {
  const nets = new Int32Array(nEdges);
  const n = Math.min(edgeVoteTypes.length, nEdges);
  for (let eid = 0; eid < n; eid++) {
    const pairs = edgeVoteTypes[eid];
    if (!pairs) continue;
    for (const [t, up, dn] of pairs) {
      if (t === legendIdx) nets[eid] += up - dn;
    }
  }
  return nets;
}

/** The net-positive subgraph for one type (mirrors build_type_graph + _adj_of):
 *  nodes = touched intersections, arcs weighted by net; self-loops excluded. */
function buildTypeAdj(
  topo: GraphTopology,
  adj: NodeAdj,
  nets: Int32Array,
  minNet: number,
): TypeAdj {
  const { nEdges, ends } = topo;
  const nodeIds: number[] = [];
  const seen = new Set<number>();
  for (let e = 0; e < nEdges; e++) {
    if (nets[e] < minNet) continue;
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
      if (nets[e] < minNet) continue;
      const u = ends[2 * e];
      const v = ends[2 * e + 1];
      if (u === v) continue;
      arcs.push({ n: u === nid ? v : u, e, w: nets[e] });
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

type PathResult = { edges: number[]; nodes: number[]; weight: number };

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
  const fwd = extend(seedA, seedB, seedE);
  const bwd = extend(seedB, seedA, seedE);
  const leftNodes = [...bwd.nodes].reverse().slice(0, -1); // … → seedA (drop trailing seedB)
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
  const legend = data.vote_type_legend ?? [];
  const edgeVoteTypes = data.edge_vote_types ?? [];
  const limit = opts.limit ?? DEFAULT_LIMIT;
  const jaccardThreshold = opts.jaccardThreshold ?? DEFAULT_JACCARD;
  const minNet = opts.minNet ?? MIN_NET;
  const minRouteScore = opts.minRouteScore ?? MIN_ROUTE_SCORE;
  const minRouteEdges = opts.minRouteEdges ?? MIN_ROUTE_EDGES;
  const blockIndex = buildBlockIndex(topo);

  const all: RouteProposal[] = [];
  for (let legendIdx = 0; legendIdx < legend.length; legendIdx++) {
    const label = legend[legendIdx];
    if (!label) continue;
    const nets = netsForType(edgeVoteTypes, topo.nEdges, legendIdx);
    const typeAdj = buildTypeAdj(topo, adj, nets, minNet);
    const typeProposals: RouteProposal[] = [];
    for (const compAdj of connectedComponents(typeAdj)) {
      for (const path of peelPaths(compAdj)) {
        if (path.weight < minRouteScore || path.edges.length < minRouteEdges) continue;
        const { blocks, blockEdgeIds } = groupBlocks(path.edges, topo, blockIndex);
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
    all.push(...dedupeRoutes(typeProposals, jaccardThreshold));
  }

  all.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return all.slice(0, limit);
}
