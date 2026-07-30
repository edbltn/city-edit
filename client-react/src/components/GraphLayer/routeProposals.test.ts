import { describe, it, expect } from "vitest";
import {
  parseRouteProposal,
  proposalShapeClass,
  routeBlockEdges,
  isRouteCovered,
  expandSelectionToUndirected,
  dropPointsCoveredByRoutes,
  chooseAnchorOrder,
  chooseAnchorOrderBefore,
  computeRouteProposals,
  corridorCoordinates,
  corridorFromEdgeIds,
  corridorSliceBetween,
  dedupeRoutes,
  growCorridor,
  makeSegmentShortestCheck,
  routeLengthBudgetM,
  MAX_GHOST_WAYPOINTS,
  MIN_ROUTE_SCORE,
  MIN_ROUTE_EDGES,
  MIN_ROUTE_BLOCKS,
  ROUTE_LENGTH_BASE_M,
  ROUTE_LENGTH_PER_SQRT_SCORE_M,
  ROUTE_LENGTH_MAX_M,
  type RouteProposal,
  type RouteProposalOptions,
  type SegmentShortestCheck,
} from "./routeProposals";
import {
  topologyFromJson,
  buildNodeAdj,
  edgeLengthMeters,
  type GraphTopology,
} from "./graphTopology";
import type { LatLng } from "../../types";

const ll = (lat: number, lng: number): LatLng => ({ lat, lng });

function route(over: Partial<RouteProposal> = {}): RouteProposal {
  const base: RouteProposal = {
    id: "r1",
    label: "Bike lane",
    legendIdx: 0,
    score: 30,
    edgeIds: [0, 1, 2],
    blocks: [[0, 5], [1], [2]], // block 0 has two edges (both directions)
    blockEdgeIds: [0, 5, 1, 2],
    anchors: [10, 13],
    anchorCoords: [ll(40.70, -74.00), ll(40.70, -73.99)],
    waypointNodes: [10, 13],
    waypointCoords: [ll(40.70, -74.00), ll(40.70, -73.99)],
    segments: [[0, 1, 2]],
    ...over,
  };
  return base;
}

describe("parseRouteProposal", () => {
  it("maps the server wire shape to the client RouteProposal", () => {
    const p = parseRouteProposal({
      id: "abc", label: "Tree", legendIdx: 1, score: 12,
      edge_ids: [3, 4], blocks: [[3], [4]], block_edge_ids: [3, 4],
      anchors: [7, 9], anchor_coords: [[40.1, -74.1], [40.2, -74.2]],
    });
    expect(p.edgeIds).toEqual([3, 4]);
    expect(p.blockEdgeIds).toEqual([3, 4]);
    expect(p.anchorCoords[0]).toEqual({ lat: 40.1, lng: -74.1 });
    expect(p.legendIdx).toBe(1);
  });

  it("synthesizes anchor-only waypoints on legacy payloads", () => {
    const p = parseRouteProposal({
      id: "abc", label: "Tree", legendIdx: 1, score: 12,
      edge_ids: [3, 4], blocks: [[3], [4]], block_edge_ids: [3, 4],
      anchors: [7, 9], anchor_coords: [[40.1, -74.1], [40.2, -74.2]],
    });
    expect(p.waypointNodes).toEqual([7, 9]);
    expect(p.waypointCoords).toEqual([
      { lat: 40.1, lng: -74.1 }, { lat: 40.2, lng: -74.2 },
    ]);
    expect(p.segments).toEqual([[3, 4]]);
  });

  it("carries ghost waypoints + per-segment edges when the wire has them", () => {
    const p = parseRouteProposal({
      id: "abc", label: "Tree", legendIdx: 1, score: 12,
      edge_ids: [3, 4], blocks: [[3], [4]], block_edge_ids: [3, 4],
      anchors: [7, 9], anchor_coords: [[40.1, -74.1], [40.2, -74.2]],
      waypoint_nodes: [7, 8, 9],
      waypoint_coords: [[40.1, -74.1], [40.15, -74.15], [40.2, -74.2]],
      segments: [[3], [4]],
    });
    expect(p.waypointNodes).toEqual([7, 8, 9]);
    expect(p.waypointCoords[1]).toEqual({ lat: 40.15, lng: -74.15 });
    expect(p.segments).toEqual([[3], [4]]);
  });
});

describe("proposalShapeClass", () => {
  it("uses a diamond container for routes and a square for points", () => {
    expect(proposalShapeClass("route")).toContain("--diamond");
    expect(proposalShapeClass("point")).toContain("--square");
    // Exactly one icon area either way — the class differs only by shape modifier.
    expect(proposalShapeClass("route")).not.toContain("--square");
  });
});

describe("routeBlockEdges (hover highlight + vote set)", () => {
  it("returns every edge in every block, not just the path edges", () => {
    const p = route();
    // path edges are [0,1,2] but block 0 also carries edge 5 → all four highlight/vote.
    expect(new Set(routeBlockEdges(p))).toEqual(new Set([0, 5, 1, 2]));
  });

  it("the vote payload for a route spans all block edges", () => {
    // Selecting/voting a route casts on the whole block-edge union (the write
    // path accepts multi-edge edge_ids[]).
    const p = route();
    const voteEdgeIds = routeBlockEdges(p);
    expect(new Set(voteEdgeIds)).toEqual(new Set(p.blockEdgeIds));
  });
});

describe("isRouteCovered (auto-select)", () => {
  const blocks = [[0, 5], [1], [2]];

  it("fires only when every block has at least one selected edge", () => {
    expect(isRouteCovered(blocks, [5, 1, 2])).toBe(true); // one per block (5 covers block 0)
    expect(isRouteCovered(blocks, [0, 1])).toBe(false);   // block [2] uncovered
    expect(isRouteCovered(blocks, [])).toBe(false);
  });

  it("clears coverage when a block drops out of the selection", () => {
    expect(isRouteCovered(blocks, new Set([0, 1, 2]))).toBe(true);
    expect(isRouteCovered(blocks, new Set([0, 1]))).toBe(false);
  });
});

describe("expandSelectionToUndirected", () => {
  // Edges 0/1 are direction twins (nodes 10↔11); edge 2 is a different street.
  const topo = {
    nEdges: 3,
    ends: Int32Array.from([10, 11, /* e0 */ 11, 10, /* e1 */ 11, 12 /* e2 */]),
  };

  it("adds candidate edges sharing a node pair with a selected edge", () => {
    const sel = expandSelectionToUndirected(topo, [0], [1, 2]);
    expect(sel.has(0)).toBe(true);
    expect(sel.has(1)).toBe(true);  // twin of selected 0 → joins
    expect(sel.has(2)).toBe(false); // different street → not added
  });

  it("makes twin-traversing routes read as covering the corridor", () => {
    // Corridor block recorded edge 1; the routed path traversed twin edge 0.
    const blocks = [[1]];
    expect(isRouteCovered(blocks, [0])).toBe(false); // raw ids miss
    expect(isRouteCovered(blocks, expandSelectionToUndirected(topo, [0], [1]))).toBe(true);
  });

  it("ignores out-of-range candidates and leaves the selection intact", () => {
    const sel = expandSelectionToUndirected(topo, [2], [99]);
    expect([...sel]).toEqual([2]);
  });
});

describe("dropPointsCoveredByRoutes", () => {
  it("removes same-type point proposals subsumed by a route, keeps others", () => {
    const routes = [route()]; // Bike lane covers edges {0,5,1,2}
    const points = [
      { edgeIdx: 1, legendIdx: 0 }, // Bike lane on edge 1 → subsumed → dropped
      { edgeIdx: 9, legendIdx: 0 }, // Bike lane elsewhere → kept
      { edgeIdx: 1, legendIdx: 1 }, // Tree on edge 1 → different type → kept
    ];
    const kept = dropPointsCoveredByRoutes(points, routes);
    expect(kept).toEqual([
      { edgeIdx: 9, legendIdx: 0 },
      { edgeIdx: 1, legendIdx: 1 },
    ]);
  });
});

describe("chooseAnchorOrder (ghost-waypoint forcing)", () => {
  const A = ll(0, 0);
  const B = ll(0, 10);
  const start = ll(0, -5);
  const end = ll(0, 15);
  // 1-D world: duration = absolute longitude gap.
  const durationOf: (a: LatLng, b: LatLng) => number = (a, b) => Math.abs(a.lng - b.lng);

  it("inserts anchors in the order that minimizes total routed duration", () => {
    // start(-5) → A(0) → B(10) → end(15): 5+10+5 = 20  (forward, best)
    // start(-5) → B(10) → A(0) → end(15): 15+10+15 = 40 (backward)
    expect(chooseAnchorOrder([start, end], A, B, durationOf)).toEqual([A, B]);
  });

  it("swaps to end/start when that is faster", () => {
    // Put the existing start on the far side so B-first is shorter.
    const farStart = ll(0, 20);
    // farStart(20)→A(0)→B(10)→end(15): 20+10+5=35 ; farStart→B→A→end: 10+10+15=35? tune:
    // farStart(20)→A(0)→B(10)→end(12): 20+10+2 = 32
    // farStart(20)→B(10)→A(0)→end(12): 10+10+12 = 32 → make B-first clearly shorter:
    const end3 = ll(0, 8);
    // fwd: 20+10+2(|10-8|)=32? A→B then end3=8: |10-8|=2 → 20+10+2=32
    // bwd: farStart→B(10):10, B→A:10, A→end3(8):8 → 28  → backward wins
    expect(chooseAnchorOrder([farStart, end3], A, B, durationOf)).toEqual([B, A]);
  });

  it("defaults to [a, b] when there is no existing route to order against", () => {
    expect(chooseAnchorOrder([], A, B, durationOf)).toEqual([A, B]);
  });
});

// ==========================================================================
// computeRouteProposals — deterministic client-side clustering
// (port of server/tests/unit/test_route_proposals.py, adapted to the
//  MIN_ROUTE_SCORE / MIN_ROUTE_EDGES activity gates)
// ==========================================================================

const BIKE = 0;
const TREE = 1;
const LEGEND = ["Bike lane", "Tree"];

type Evt = [number, number, number][][];

/** `evt([[BIKE, 5, 1]], [[TREE, 3, 0]], …)` → edge_vote_types (per edge). */
const evt = (...perEdge: [number, number, number][][]): Evt => perEdge;

function makeTopo(edges: [number, number][], blockIds?: number[]): GraphTopology {
  const nNodes = 1 + Math.max(...edges.flat());
  const nodes: [number, number][] = Array.from(
    { length: nNodes },
    (_, i) => [40.7, -74.0 + 0.001 * i],
  );
  const topo = topologyFromJson({ nodes, edges: edges.map(([u, v]) => [u, v, ""]) });
  if (blockIds) {
    topo.edgeBlockId = Int32Array.from(blockIds);
    topo.nBlocks = Math.max(...blockIds) + 1;
  }
  return topo;
}

function compute(
  edges: [number, number][],
  voteTypes: Evt,
  opts: RouteProposalOptions = {},
  blockIds?: number[],
): RouteProposal[] {
  const topo = makeTopo(edges, blockIds);
  // minRouteBlocks: 1 — these micro-graphs probe the clustering mechanics, not
  // the min-distance gate (which has its own suite below with the real default).
  return computeRouteProposals(topo, buildNodeAdj(topo), {
    edge_vote_types: voteTypes,
    vote_type_legend: LEGEND,
  }, { minRouteBlocks: 1, ...opts });
}

function bareRoute(over: Partial<RouteProposal>): RouteProposal {
  const merged = {
    id: "x", label: "Bike lane", legendIdx: BIKE, score: 0,
    edgeIds: [] as number[], blocks: [] as number[][], blockEdgeIds: [] as number[],
    anchors: [0, 0] as [number, number],
    anchorCoords: [ll(0, 0), ll(0, 0)] as [LatLng, LatLng],
    ...over,
  };
  return {
    // Waypoints default to the (possibly overridden) anchors, one segment.
    waypointNodes: [...merged.anchors],
    waypointCoords: [...merged.anchorCoords],
    segments: [merged.edgeIds],
    ...merged,
    ...over,
  };
}

describe("computeRouteProposals — net weighting + MIN_NET", () => {
  it("uses net (up − down) support and drops non-positive edges", () => {
    // Nets: edge0 = 5, edge1 = 4, edge2 = 1 − 4 = -3 (excluded).
    const ps = compute(
      [[0, 1], [1, 2], [2, 3]],
      evt([[BIKE, 5, 0]], [[BIKE, 4, 0]], [[BIKE, 1, 4]]),
    );
    expect(ps).toHaveLength(1);
    expect(new Set(ps[0].edgeIds)).toEqual(new Set([0, 1]));
    expect(ps[0].score).toBe(9);
  });

  it("returns nothing when no edge has positive net", () => {
    expect(compute([[0, 1], [1, 2]], evt([[BIKE, 0, 0]], [[BIKE, 1, 5]]))).toEqual([]);
  });
});

describe("computeRouteProposals — heaviest simple path", () => {
  it("matches brute force on a tiny graph (exact search)", () => {
    const edges: [number, number][] = [[0, 1], [1, 2], [2, 3], [0, 2]];
    const nets = [3, 3, 10, 1];
    const ps = compute(edges, evt(
      [[BIKE, 3, 0]], [[BIKE, 3, 0]], [[BIKE, 10, 0]], [[BIKE, 1, 0]],
    ));
    // Brute force the heaviest simple path over the same weighted edges.
    const adj = new Map<number, [number, number][]>();
    edges.forEach(([u, v], i) => {
      adj.set(u, [...(adj.get(u) ?? []), [v, i]]);
      adj.set(v, [...(adj.get(v) ?? []), [u, i]]);
    });
    let best = 0;
    const dfs = (node: number, seen: Set<number>, w: number) => {
      best = Math.max(best, w);
      for (const [nxt, eid] of adj.get(node) ?? []) {
        if (!seen.has(nxt)) dfs(nxt, new Set(seen).add(nxt), w + nets[eid]);
      }
    };
    for (const start of adj.keys()) dfs(start, new Set([start]), 0);
    expect(ps[0].score).toBe(best); // 16 = 0-1-2-3
  });

  it("returns a simple contiguous path (no repeated nodes, real edges only)", () => {
    const edges: [number, number][] = [[0, 1], [1, 2], [2, 3], [2, 4]];
    const p = compute(edges, evt(
      [[BIKE, 10, 0]], [[BIKE, 10, 0]], [[BIKE, 10, 0]], [[BIKE, 10, 0]],
    ))[0];
    // The Y-fork forces a choice: 3 path-eligible edges meet at node 2 but a
    // simple path can use at most 2 of them.
    expect(p.edgeIds.length).toBe(3);
    const edgeSet = new Set(p.edgeIds);
    expect(edgeSet.has(2) && edgeSet.has(3)).toBe(false);
  });
});

describe("computeRouteProposals — peeling separates parallel corridors", () => {
  it("peels two corridors crossing at one intersection out of one component", () => {
    // X crossing at node 4: hot arms 0-4-1 (10+10), warm arms 2-4-3 (5+5).
    const ps = compute(
      [[0, 4], [4, 1], [2, 4], [4, 3]],
      evt([[BIKE, 10, 0]], [[BIKE, 10, 0]], [[BIKE, 5, 0]], [[BIKE, 5, 0]]),
    );
    expect(ps).toHaveLength(2);
    expect(new Set(ps[0].edgeIds)).toEqual(new Set([0, 1]));
    expect(ps[0].score).toBe(20);
    expect(new Set(ps[1].edgeIds)).toEqual(new Set([2, 3]));
    expect(ps[1].score).toBe(10);
  });

  it("drops weak residue below the dominance fraction", () => {
    // Hot corridor 0-1-2 (score 40) + a weak crossing spur 3-1-4 (score 4 <
    // 0.25 × 40) — the spur is peel-eligible but under the dominance cut.
    const ps = compute(
      [[0, 1], [1, 2], [3, 1], [1, 4]],
      evt([[BIKE, 20, 0]], [[BIKE, 20, 0]], [[BIKE, 2, 0]], [[BIKE, 2, 0]]),
    );
    expect(ps).toHaveLength(1);
    expect(new Set(ps[0].edgeIds)).toEqual(new Set([0, 1]));
  });
});

describe("computeRouteProposals — per-type independence", () => {
  it("labels each corridor by its own type; types never mix in one route", () => {
    const ps = compute(
      [[0, 1], [1, 2], [10, 11], [11, 12]],
      evt([[BIKE, 9, 0]], [[BIKE, 8, 0]], [[TREE, 7, 0]], [[TREE, 6, 0]]),
    );
    const byLabel = new Map(ps.map((p) => [p.label, p]));
    expect(new Set(byLabel.keys())).toEqual(new Set(["Bike lane", "Tree"]));
    expect(new Set(byLabel.get("Bike lane")!.edgeIds)).toEqual(new Set([0, 1]));
    expect(new Set(byLabel.get("Tree")!.edgeIds)).toEqual(new Set([2, 3]));
    expect(byLabel.get("Bike lane")!.legendIdx).toBe(BIKE);
  });

  it("a bike and a tree corridor may ride the SAME street (no cross-type dedupe)", () => {
    const ps = compute(
      [[0, 1], [1, 2]],
      evt([[BIKE, 9, 0], [TREE, 7, 0]], [[BIKE, 8, 0], [TREE, 6, 0]]),
    );
    expect(ps).toHaveLength(2);
    expect(new Set(ps.map((p) => p.legendIdx))).toEqual(new Set([BIKE, TREE]));
  });
});

describe("dedupeRoutes — same-type Jaccard / containment", () => {
  it("collapses high-overlap same-type routes to the stronger", () => {
    const a = bareRoute({ id: "a", score: 30, edgeIds: [0, 1, 2] });
    const b = bareRoute({ id: "b", score: 10, edgeIds: [0, 1] });
    expect(dedupeRoutes([a, b]).map((p) => p.score)).toEqual([30]);
  });

  it("keeps crossing routes (low overlap is not a duplicate)", () => {
    const a = bareRoute({ id: "a", score: 20, edgeIds: [0, 1] });
    const b = bareRoute({ id: "b", score: 18, edgeIds: [2, 3] });
    expect(dedupeRoutes([a, b])).toHaveLength(2);
  });

  it("drops a subset route regardless of Jaccard (containment)", () => {
    const a = bareRoute({ id: "a", score: 30, edgeIds: [0, 1, 2, 3, 4, 5] });
    const b = bareRoute({ id: "b", score: 12, edgeIds: [1, 2] }); // jaccard 2/6 < 0.5
    expect(dedupeRoutes([a, b]).map((p) => p.id)).toEqual(["a"]);
  });

  it("never dedupes across vote types", () => {
    const a = bareRoute({ id: "a", score: 20, edgeIds: [0, 1], legendIdx: BIKE });
    const b = bareRoute({ id: "b", score: 18, edgeIds: [0, 1], legendIdx: TREE });
    expect(dedupeRoutes([a, b])).toHaveLength(2);
  });
});

describe("computeRouteProposals — block grouping", () => {
  it("groups path edges into blocks and casts the whole block union", () => {
    // Edges 0 and 1 are the two directions of ONE street segment → one block.
    const ps = compute(
      [[0, 1], [1, 0], [1, 2]],
      evt([[BIKE, 9, 0]], [[BIKE, 9, 0]], [[BIKE, 8, 0]]),
      {},
      [0, 0, 1],
    );
    expect(ps).toHaveLength(1);
    const blockSets = ps[0].blocks.map((b) => new Set(b));
    expect(blockSets).toContainEqual(new Set([0, 1]));
    expect(blockSets).toContainEqual(new Set([2]));
    expect(ps[0].blocks).toHaveLength(2);
    expect(new Set(ps[0].blockEdgeIds)).toEqual(new Set([0, 1, 2]));
  });

  it("falls back to edge-as-singleton blocks without artifacts", () => {
    const ps = compute([[0, 1], [1, 2]], evt([[BIKE, 9, 0]], [[BIKE, 8, 0]]));
    expect(ps[0].blocks.map((b) => [...b].sort())).toEqual(
      expect.arrayContaining([[0], [1]]),
    );
    expect(ps[0].blocks).toHaveLength(2);
  });

  it("treats -1 (unmapped) edges as singletons inside a mapped city", () => {
    const ps = compute(
      [[0, 1], [1, 2]],
      evt([[BIKE, 9, 0]], [[BIKE, 8, 0]]),
      {},
      [3, -1],
    );
    const blockSets = ps[0].blocks.map((b) => new Set(b));
    expect(blockSets).toContainEqual(new Set([0]));
    expect(blockSets).toContainEqual(new Set([1]));
  });
});

describe("computeRouteProposals — activity gates", () => {
  it("drops single-edge paths (MIN_ROUTE_EDGES)", () => {
    expect(MIN_ROUTE_EDGES).toBe(2);
    expect(compute([[0, 1]], evt([[BIKE, 50, 0]]))).toEqual([]);
  });

  it("drops paths scoring under MIN_ROUTE_SCORE", () => {
    expect(MIN_ROUTE_SCORE).toBe(3);
    expect(compute([[0, 1], [1, 2]], evt([[BIKE, 1, 0]], [[BIKE, 1, 0]]))).toEqual([]);
    // …but exactly at the threshold survives.
    const ps = compute([[0, 1], [1, 2]], evt([[BIKE, 2, 0]], [[BIKE, 1, 0]]));
    expect(ps).toHaveLength(1);
    expect(ps[0].score).toBe(3);
  });
});

describe("computeRouteProposals — per-type diversity quota (MAX_PER_TYPE)", () => {
  // Six disjoint hot BIKE corridors (scores 60,58,…,50) + one weak TREE
  // corridor (score 9). Pure score ranking would fill any small limit with
  // BIKE alone; the quota admits at most 4 BIKEs before other types.
  const edges: [number, number][] = [];
  const votes: [number, number, number][][] = [];
  for (let i = 0; i < 6; i++) {
    const base = i * 10;
    edges.push([base, base + 1], [base + 1, base + 2]);
    votes.push([[BIKE, 30 - i, 0]], [[BIKE, 30 - i, 0]]);
  }
  edges.push([100, 101], [101, 102]);
  votes.push([[TREE, 5, 0]], [[TREE, 4, 0]]);
  edges.push([110, 111], [111, 112]);
  votes.push([[TREE, 4, 0]], [[TREE, 3, 0]]);

  it("admits at most 4 of one type, so a weaker type surfaces", () => {
    const ps = compute(edges, evt(...votes), { limit: 5 });
    const bikes = ps.filter((p) => p.legendIdx === BIKE);
    const trees = ps.filter((p) => p.legendIdx === TREE);
    expect(bikes).toHaveLength(4);
    expect(trees).toHaveLength(1);
    // The 4 admitted BIKEs are the strongest 4, and order is still by score.
    expect(bikes.map((p) => p.score)).toEqual([60, 58, 56, 54]);
    expect(ps.map((p) => p.score)).toEqual([60, 58, 56, 54, 9]);
  });

  it("backfills unused slots by pure score when types run out", () => {
    // Only BIKE corridors exist: the quota (4) can't fill limit 5, so the
    // 5th-best BIKE backfills rather than returning a short list.
    const bikeOnly = edges.slice(0, 12);
    const bikeVotes = votes.slice(0, 12);
    const ps = compute(bikeOnly, evt(...bikeVotes), { limit: 5 });
    expect(ps).toHaveLength(5);
    expect(ps.map((p) => p.score)).toEqual([60, 58, 56, 54, 52]);
  });

  it("is overridable via maxPerType", () => {
    // Quota 2: both TREEs are admitted (9 and 7); one BIKE backfills the
    // remaining slot by score.
    const ps = compute(edges, evt(...votes), { limit: 5, maxPerType: 2 });
    expect(ps.filter((p) => p.legendIdx === TREE)).toHaveLength(2);
    expect(ps.filter((p) => p.legendIdx === BIKE)).toHaveLength(3);
    expect(ps.map((p) => p.score)).toEqual([60, 58, 56, 9, 7]);
  });
});

describe("computeRouteProposals — ranking, cap, anchors", () => {
  it("ranks by score desc and caps at the limit", () => {
    const ps = compute(
      [[0, 1], [1, 2], [3, 4], [4, 5], [6, 7], [7, 8]],
      evt(
        [[BIKE, 15, 0]], [[BIKE, 15, 0]],
        [[BIKE, 10, 0]], [[BIKE, 10, 0]],
        [[BIKE, 5, 0]], [[BIKE, 5, 0]],
      ),
      { limit: 2 },
    );
    expect(ps.map((p) => p.score)).toEqual([30, 20]);
  });

  it("anchors are the path terminals with their node coordinates", () => {
    const p = compute([[0, 1], [1, 2]], evt([[BIKE, 9, 0]], [[BIKE, 8, 0]]))[0];
    expect(new Set(p.anchors)).toEqual(new Set([0, 2]));
    const [a, b] = p.anchors;
    expect(p.anchorCoords[0].lat).toBeCloseTo(40.7, 6);
    expect(p.anchorCoords[0].lng).toBeCloseTo(-74.0 + 0.001 * a, 6);
    expect(p.anchorCoords[1].lng).toBeCloseTo(-74.0 + 0.001 * b, 6);
  });
});

describe("computeRouteProposals — determinism", () => {
  const EDGES: [number, number][] = [
    [0, 1], [1, 2], [2, 3], [0, 2], [3, 4], [10, 11], [11, 12], [2, 12],
  ];
  const VOTES = evt(
    [[BIKE, 5, 0], [TREE, 2, 0]], [[BIKE, 4, 1]], [[BIKE, 7, 0]], [[BIKE, 2, 0]],
    [[BIKE, 3, 0]], [[TREE, 6, 0]], [[TREE, 5, 0]], [[TREE, 2, 0]],
  );

  it("two runs over freshly built inputs are deep-equal (ids and order)", () => {
    const run = () => compute(EDGES, VOTES);
    expect(JSON.parse(JSON.stringify(run()))).toEqual(JSON.parse(JSON.stringify(run())));
  });

  it("ids are content-derived: same path ⇒ same id across topological rebuilds", () => {
    const a = compute(EDGES, VOTES);
    const b = compute(EDGES, VOTES);
    expect(a.map((p) => p.id)).toEqual(b.map((p) => p.id));
    expect(a.every((p) => /^[0-9a-f]{8}$/.test(p.id))).toBe(true);
  });
});

describe("corridorCoordinates (verbatim corridor routing)", () => {
  it("walks the ordered edges from anchors[0] into a [lng, lat] chain", () => {
    // makeTopo places node i at [40.7, -74.0 + 0.001*i].
    const topo = makeTopo([[0, 1], [1, 2], [2, 3]]);
    const p = bareRoute({ edgeIds: [0, 1, 2], anchors: [0, 3] });
    expect(corridorCoordinates(topo, p)).toEqual([
      [-74.0, 40.7], [-73.999, 40.7], [-73.998, 40.7], [-73.997, 40.7],
    ]);
  });

  it("chains through edges stored in either direction", () => {
    // Edge 1 is stored REVERSED (2→1): the walk must still traverse 0→1→2→3.
    const topo = makeTopo([[0, 1], [2, 1], [2, 3]]);
    const p = bareRoute({ edgeIds: [0, 1, 2], anchors: [0, 3] });
    expect(corridorCoordinates(topo, p)?.length).toBe(4);
    expect(corridorCoordinates(topo, p)?.[3]).toEqual([-73.997, 40.7]);
  });

  it("returns null when the chain breaks (edge not incident to the walk)", () => {
    const topo = makeTopo([[0, 1], [2, 3]]);
    const p = bareRoute({ edgeIds: [0, 1], anchors: [0, 3] }); // edge 1 doesn't touch node 1
    expect(corridorCoordinates(topo, p)).toBeNull();
  });

  it("returns null on out-of-range edges or an empty path (stale topology)", () => {
    const topo = makeTopo([[0, 1]]);
    expect(corridorCoordinates(topo, bareRoute({ edgeIds: [7], anchors: [0, 1] }))).toBeNull();
    expect(corridorCoordinates(topo, bareRoute({ edgeIds: [], anchors: [0, 1] }))).toBeNull();
  });
});

describe("corridorFromEdgeIds (forced-corridor snapshot fallback)", () => {
  // makeTopo places node i at [40.7, -74.0 + 0.001*i] → node 0 west, node 3 east.
  const nodeLL = (i: number) => ll(40.7, -74.0 + 0.001 * i);

  it("rebuilds the chain and orients it a→b", () => {
    const topo = makeTopo([[0, 1], [1, 2], [2, 3]]);
    const fwd = corridorFromEdgeIds(topo, [0, 1, 2], nodeLL(0), nodeLL(3));
    expect(fwd?.coordinates).toEqual([
      [-74.0, 40.7], [-73.999, 40.7], [-73.998, 40.7], [-73.997, 40.7],
    ]);
    // Same snapshot queried in the opposite direction reverses the polyline.
    const bwd = corridorFromEdgeIds(topo, [0, 1, 2], nodeLL(3), nodeLL(0));
    expect(bwd?.coordinates).toEqual([...fwd!.coordinates].reverse());
    expect(bwd?.edgeIds).toEqual([0, 1, 2]);
  });

  it("infers the start node when the first edge is stored reversed", () => {
    // Edge 0 stored 1→0 (walk must still leave from node 0, which edge 1 doesn't touch).
    const topo = makeTopo([[1, 0], [1, 2], [2, 3]]);
    const c = corridorFromEdgeIds(topo, [0, 1, 2], nodeLL(0), nodeLL(3));
    expect(c?.coordinates[0]).toEqual([-74.0, 40.7]);
    expect(c?.coordinates[3]).toEqual([-73.997, 40.7]);
  });

  it("handles a single-edge snapshot (either endpoint works, oriented to a)", () => {
    const topo = makeTopo([[0, 1]]);
    const c = corridorFromEdgeIds(topo, [0], nodeLL(1), nodeLL(0));
    expect(c?.coordinates).toEqual([[-73.999, 40.7], [-74.0, 40.7]]);
  });

  it("returns null on a broken chain or empty/stale snapshot", () => {
    const topo = makeTopo([[0, 1], [2, 3]]);
    expect(corridorFromEdgeIds(topo, [0, 1], nodeLL(0), nodeLL(3))).toBeNull();
    expect(corridorFromEdgeIds(topo, [], nodeLL(0), nodeLL(3))).toBeNull();
    expect(corridorFromEdgeIds(topo, [9], nodeLL(0), nodeLL(3))).toBeNull();
  });
});

describe("chooseAnchorOrderBefore (start dropped onto a diamond)", () => {
  // 1-D world matching the chooseAnchorOrder tests: duration = longitude gap.
  const durationOf: (a: LatLng, b: LatLng) => number = (a, b) => Math.abs(a.lng - b.lng);
  const A = ll(0, 0);
  const B = ll(0, 10);

  it("puts the anchor nearer the next fixed point SECOND", () => {
    // next(15) is nearer B(10) → chain A→B→next: [A, B].
    expect(chooseAnchorOrderBefore(ll(0, 15), A, B, durationOf)).toEqual([A, B]);
    // next(-5) is nearer A(0) → chain B→A→next: [B, A].
    expect(chooseAnchorOrderBefore(ll(0, -5), A, B, durationOf)).toEqual([B, A]);
  });
});

// ==========================================================================
// Corridor length budget — routeLengthBudgetM + capPathToLengthBudget
// ==========================================================================

describe("routeLengthBudgetM", () => {
  it("grows with support from the base, sublinearly", () => {
    expect(routeLengthBudgetM(0)).toBe(ROUTE_LENGTH_BASE_M);
    expect(routeLengthBudgetM(4)).toBe(ROUTE_LENGTH_BASE_M + 2 * ROUTE_LENGTH_PER_SQRT_SCORE_M);
    expect(routeLengthBudgetM(16)).toBe(ROUTE_LENGTH_BASE_M + 4 * ROUTE_LENGTH_PER_SQRT_SCORE_M);
  });

  it("is clamped to the ceiling regardless of support", () => {
    expect(routeLengthBudgetM(1e9)).toBe(ROUTE_LENGTH_MAX_M);
    expect(routeLengthBudgetM(1e9, 1000)).toBe(1000);
  });
});

describe("computeRouteProposals — corridor length cap", () => {
  it("caps a long corridor to its best-supported stretch", () => {
    // A 12-edge chain (~84m/edge ≈ 1.0km) with a hot 4-edge core. A 400m
    // ceiling keeps only the core (4 edges ≈ 338m; a 5th would exceed 400m).
    const edges: [number, number][] = Array.from({ length: 12 }, (_, i) => [i, i + 1]);
    const votes = edges.map((_, i): [number, number, number][] =>
      [[BIKE, i >= 4 && i <= 7 ? 5 : 1, 0]]);
    const ps = compute(edges, evt(...votes), { maxRouteLengthM: 400 });
    expect(ps).toHaveLength(1);
    expect(ps[0].edgeIds).toEqual([4, 5, 6, 7]);
    expect(ps[0].score).toBe(20);
    // Anchors follow the trimmed window, not the original path ends.
    expect(ps[0].anchors).toEqual([4, 8]);
  });

  it("leaves short corridors untouched by the default budget", () => {
    const ps = compute(
      [[0, 1], [1, 2], [2, 3]],
      evt([[BIKE, 5, 0]], [[BIKE, 4, 0]], [[BIKE, 3, 0]]),
    );
    expect(ps).toHaveLength(1);
    expect(ps[0].edgeIds).toEqual(expect.arrayContaining([0, 1, 2]));
  });
});

describe("computeRouteProposals — route/point kind filter", () => {
  const kindOf = (label: string) =>
    label === "Bike lane" ? "point" as const
    : label === "Tree" ? "route" as const
    : null;

  it("skips POINT-kind vote types (their votes are PBTPs, not corridors)", () => {
    const ps = compute(
      [[0, 1], [1, 2], [10, 11], [11, 12]],
      evt([[BIKE, 9, 0]], [[BIKE, 8, 0]], [[TREE, 7, 0]], [[TREE, 6, 0]]),
      { kindOf },
    );
    expect(ps.map((p) => p.label)).toEqual(["Tree"]);
  });

  it("keeps unknown-kind labels eligible and admits all without a resolver", () => {
    const both = compute(
      [[0, 1], [1, 2]],
      evt([[BIKE, 9, 0]], [[BIKE, 8, 0]]),
    );
    expect(both.map((p) => p.label)).toEqual(["Bike lane"]);
    const unknownKind = compute(
      [[0, 1], [1, 2]],
      evt([[BIKE, 9, 0]], [[BIKE, 8, 0]]),
      { kindOf: () => null },
    );
    expect(unknownKind.map((p) => p.label)).toEqual(["Bike lane"]);
  });
});

// ==========================================================================
// Min-blocks gate — a corridor must span at least MIN_ROUTE_BLOCKS blocks
// ==========================================================================

describe("computeRouteProposals — min-blocks gate", () => {
  const hot = (n: number): Evt =>
    evt(...Array.from({ length: n }, (): [number, number, number][] => [[BIKE, 5, 0]]));
  const chain = (n: number): [number, number][] =>
    Array.from({ length: n }, (_, i) => [i, i + 1]);
  // Direct call — no helper — so the REAL default gate applies.
  const computeDefault = (edges: [number, number][], votes: Evt, blockIds?: number[]) => {
    const topo = makeTopo(edges, blockIds);
    return computeRouteProposals(topo, buildNodeAdj(topo), {
      edge_vote_types: votes,
      vote_type_legend: LEGEND,
    });
  };

  it("drops corridors spanning fewer than MIN_ROUTE_BLOCKS blocks by default", () => {
    expect(MIN_ROUTE_BLOCKS).toBe(5);
    // 4 singleton blocks — hot, but reads as a point, not a route.
    expect(computeDefault(chain(4), hot(4))).toEqual([]);
    // 5 blocks is exactly enough.
    const ps = computeDefault(chain(5), hot(5));
    expect(ps).toHaveLength(1);
    expect(ps[0].blocks).toHaveLength(5);
  });

  it("counts BLOCKS, not edges — a many-edge path over few blocks is dropped", () => {
    // 6 chain edges mapped into 3 blocks: plenty of edges, too little distance.
    expect(computeDefault(chain(6), hot(6), [0, 0, 1, 1, 2, 2])).toEqual([]);
  });

  it("is overridable via minRouteBlocks", () => {
    const ps = compute(chain(2), hot(2), { minRouteBlocks: 1 });
    expect(ps).toHaveLength(1);
  });
});

// ==========================================================================
// Routing-consistent growth — growCorridor + makeSegmentShortestCheck
// ==========================================================================

// A tiny 2-D topology builder (makeTopo's nodes are colinear, which makes
// every corridor trivially shortest — useless for consistency tests).
function makeTopo2D(
  nodes: [number, number][],
  edges: [number, number][],
): GraphTopology {
  return topologyFromJson({ nodes, edges: edges.map(([u, v]) => [u, v, ""]) });
}

describe("makeSegmentShortestCheck (A* consistency oracle)", () => {
  // Right triangle: hypotenuse 0–2 is the direct edge; the corridor 0–1–2
  // takes the two legs, which are strictly longer.
  //   node0 (40.700, -74.000), node1 (40.701, -74.000), node2 (40.700, -73.999)
  const nodes: [number, number][] = [
    [40.700, -74.000], [40.701, -74.000], [40.700, -73.999],
  ];
  const legs: [number, number][] = [[0, 1], [1, 2]];

  it("accepts a corridor that is the only path", () => {
    const topo = makeTopo2D(nodes, legs);
    const check = makeSegmentShortestCheck(topo, buildNodeAdj(topo));
    // Corridor 0→2 over the two legs, at its EXACT length (the contract:
    // growth passes the sum of the segment's own edge lengths).
    const len = edgeLengthMeters(topo, 0) + edgeLengthMeters(topo, 1);
    expect(check(0, 2, len)).toBe(true);
  });

  it("rejects a corridor when a strictly shorter path exists", () => {
    const topo = makeTopo2D(nodes, [...legs, [0, 2]]); // add the hypotenuse
    const check = makeSegmentShortestCheck(topo, buildNodeAdj(topo));
    // Hypotenuse ≈ 84.7m beats the ~250m legs.
    expect(check(0, 2, 250)).toBe(false);
  });

  it("tolerates ties (an equal-length alternative is not a detour)", () => {
    // A grid square: two equal L-shaped paths 0–1–3 and 0–2–3.
    const sq: [number, number][] = [
      [40.700, -74.000], [40.701, -74.000], [40.700, -73.999], [40.701, -73.999],
    ];
    const topo = makeTopo2D(sq, [[0, 1], [1, 3], [0, 2], [2, 3]]);
    const check = makeSegmentShortestCheck(topo, buildNodeAdj(topo));
    const lenL = 110.6 + 84.4; // ≈ leg lengths; the corridor's own length
    expect(check(0, 3, lenL)).toBe(true);
  });

  it("fails open at the pop cap (bounded work, corridor kept)", () => {
    const topo = makeTopo2D(nodes, [...legs, [0, 2]]);
    const check = makeSegmentShortestCheck(topo, buildNodeAdj(topo), { maxPops: 0 });
    expect(check(0, 2, 250)).toBe(true);
  });
});

describe("growCorridor (routing-consistent extension + ghost waypoints)", () => {
  // Chain-of-arcs adjacency helper over edge list with weights.
  const typeAdjOf = (
    edges: [number, number][],
    weights: number[],
  ) => {
    const adj = new Map<number, { n: number; e: number; w: number }[]>();
    const nodeIds = [...new Set(edges.flat())].sort((a, b) => a - b);
    for (const nid of nodeIds) {
      const arcs: { n: number; e: number; w: number }[] = [];
      edges.forEach(([u, v], e) => {
        if (u === nid) arcs.push({ n: v, e, w: weights[e] });
        else if (v === nid) arcs.push({ n: u, e, w: weights[e] });
      });
      adj.set(nid, arcs);
    }
    return adj;
  };
  const len100 = () => 100;
  const always: SegmentShortestCheck = () => true;
  const never: SegmentShortestCheck = () => false;

  it("with a consistent route: no ghosts, two waypoints, one segment", () => {
    const adj = typeAdjOf([[0, 1], [1, 2], [2, 3]], [5, 9, 5]);
    const g = growCorridor(adj, len100, always)!;
    expect(g.edges).toEqual([0, 1, 2]);
    expect(g.weight).toBe(19);
    expect(g.waypointNodes).toEqual([0, 3]);
    expect(g.segments).toEqual([[0, 1, 2]]);
  });

  it("pins the previous endpoint as a ghost when the route would change", () => {
    const adj = typeAdjOf([[0, 1], [1, 2]], [9, 5]);
    // Seed 0–1 accepted unchecked; extending to 2 breaks consistency → node 1
    // (the previous endpoint) becomes a ghost waypoint.
    const g = growCorridor(adj, len100, never)!;
    expect(g.edges).toEqual([0, 1]);
    expect(g.waypointNodes).toEqual([0, 1, 2]);
    expect(g.segments).toEqual([[0], [1]]);
  });

  it("stops at MAX_GHOST_WAYPOINTS pins (up to 5 waypoints total)", () => {
    expect(MAX_GHOST_WAYPOINTS).toBe(3);
    // A long hot chain where EVERY extension changes the route: each accepted
    // extension pins a ghost; the 3rd pin ends growth.
    const chain: [number, number][] = Array.from({ length: 8 }, (_, i) => [i, i + 1]);
    const g = growCorridor(typeAdjOf(chain, chain.map(() => 5)), len100, never)!;
    expect(g.waypointNodes.length).toBe(5); // 2 anchors + 3 ghosts
    expect(g.edges.length).toBe(4);         // seed + one edge per pin
    expect(g.segments.map((s) => s.length)).toEqual([1, 1, 1, 1]);
    expect(g.segments.flat()).toEqual(g.edges);
  });

  it("with the ghost budget spent, only still-consistent extensions continue", () => {
    // Consistency flips false only when the segment grows past 300m — so the
    // corridor picks up ghosts as it lengthens, and once 3 are spent it keeps
    // growing while segments stay short… but the pins land where needed.
    const chain: [number, number][] = Array.from({ length: 20 }, (_, i) => [i, i + 1]);
    const check: SegmentShortestCheck = (_f, _t, len) => len <= 300;
    const g = growCorridor(typeAdjOf(chain, chain.map(() => 5)), len100, check)!;
    // Segments between waypoints never exceed 300m + the pinned edge.
    expect(g.waypointNodes.length).toBeLessThanOrEqual(5);
    expect(g.segments.flat()).toEqual(g.edges);
  });

  it("skips candidates that would blow the support-earned length budget", () => {
    const adj = typeAdjOf([[0, 1], [1, 2], [2, 3]], [5, 5, 5]);
    // Budget so small only the seed fits.
    const g = growCorridor(adj, len100, always, { budgetOf: () => 150 })!;
    expect(g.edges.length).toBe(1);
    expect(g.waypointNodes.length).toBe(2);
  });

  it("is deterministic (ties by edge id)", () => {
    const edges: [number, number][] = [[0, 1], [1, 2], [1, 3]];
    const adj = () => typeAdjOf(edges, [5, 5, 5]);
    const a = growCorridor(adj(), len100, always)!;
    const b = growCorridor(adj(), len100, always)!;
    expect(a).toEqual(b);
    // Equal-weight fork at node 1 → lower edge id (1→2) wins.
    expect(a.edges).toEqual([0, 1]);
  });
});

describe("computeRouteProposals — ghost waypoints end-to-end", () => {
  it("pins a ghost where votes leave the shortest path", () => {
    // Right triangle: hot corridor rides the two legs 0–1–2 while the
    // unvoted hypotenuse 0–2 is shorter — routing 0→2 would cut the corner,
    // so the corner (node 1) must become a ghost waypoint.
    const nodes: [number, number][] = [
      [40.700, -74.000], [40.701, -74.000], [40.700, -73.999],
    ];
    const topo = makeTopo2D(nodes, [[0, 1], [1, 2], [0, 2]]);
    const ps = computeRouteProposals(topo, buildNodeAdj(topo), {
      edge_vote_types: [[[BIKE, 9, 0]], [[BIKE, 8, 0]], []],
      vote_type_legend: LEGEND,
    }, { minRouteBlocks: 1 });
    expect(ps).toHaveLength(1);
    expect(ps[0].edgeIds).toEqual(expect.arrayContaining([0, 1]));
    expect(ps[0].waypointNodes).toEqual([0, 1, 2]);
    expect(ps[0].waypointCoords[1]).toEqual({ lat: 40.701, lng: -74.000 });
    expect(ps[0].segments.map((s) => s.length)).toEqual([1, 1]);
  });

  it("keeps a genuinely-only-path corridor whole with anchor-only waypoints", () => {
    // A U-shaped chain with NO shortcut in the graph: routing reproduces the
    // whole U, so no ghosts are needed and the corridor stays one piece.
    const nodes: [number, number][] = [];
    for (let i = 0; i <= 4; i++) nodes.push([40.7 + 0.001 * (4 - i), -74.0]);   // west leg ↓
    nodes.push([40.7, -73.999]);                                                 // bottom →
    for (let i = 0; i <= 4; i++) nodes.push([40.7 + 0.001 * i, -73.998]);        // east leg ↑
    const edges: [number, number][] =
      Array.from({ length: nodes.length - 1 }, (_, i) => [i, i + 1]);
    const topo = makeTopo2D(nodes, edges);
    const ps = computeRouteProposals(topo, buildNodeAdj(topo), {
      edge_vote_types: edges.map((): [number, number, number][] => [[BIKE, 5, 0]]),
      vote_type_legend: LEGEND,
    });
    expect(ps).toHaveLength(1);
    expect(ps[0].edgeIds.length).toBe(edges.length);
    expect(ps[0].waypointNodes).toEqual([ps[0].anchors[0], ps[0].anchors[1]]);
  });

  it("waypoint segments always partition the path edges", () => {
    const edges: [number, number][] = [
      [0, 1], [1, 2], [2, 3], [0, 2], [3, 4], [10, 11], [11, 12], [2, 12],
    ];
    const ps = compute(edges, evt(
      [[BIKE, 5, 0]], [[BIKE, 4, 1]], [[BIKE, 7, 0]], [[BIKE, 2, 0]],
      [[BIKE, 3, 0]], [[TREE, 6, 0]], [[TREE, 5, 0]], [[TREE, 2, 0]],
    ));
    for (const p of ps) {
      expect(p.segments.flat()).toEqual(p.edgeIds);
      expect(p.waypointNodes.length).toBe(p.segments.length + 1);
      expect(p.waypointNodes.length).toBeLessThanOrEqual(2 + MAX_GHOST_WAYPOINTS);
      expect(p.waypointNodes[0]).toBe(p.anchors[0]);
      expect(p.waypointNodes[p.waypointNodes.length - 1]).toBe(p.anchors[1]);
      expect(p.waypointCoords.length).toBe(p.waypointNodes.length);
    }
  });
});

describe("corridorSliceBetween (per-segment corridor resolution)", () => {
  // Chain 0–1–2–3–4 with a ghost at node 2: segments [[0,1],[2,3]].
  const nodeLL = (i: number) => ll(40.7, -74.0 + 0.001 * i);
  const topo = makeTopo([[0, 1], [1, 2], [2, 3], [3, 4]]);
  const p = bareRoute({
    edgeIds: [0, 1, 2, 3],
    anchors: [0, 4],
    anchorCoords: [nodeLL(0), nodeLL(4)],
    waypointNodes: [0, 2, 4],
    waypointCoords: [nodeLL(0), nodeLL(2), nodeLL(4)],
    segments: [[0, 1], [2, 3]],
  });

  it("resolves the slice between two consecutive waypoints, oriented a→b", () => {
    const s = corridorSliceBetween(topo, p, nodeLL(2), nodeLL(4));
    expect(s?.edgeIds).toEqual([2, 3]);
    expect(s?.coordinates[0]).toEqual([-73.998, 40.7]);
    expect(s?.coordinates[2]).toEqual([-73.996, 40.7]);
    const rev = corridorSliceBetween(topo, p, nodeLL(4), nodeLL(2));
    expect(rev?.coordinates).toEqual([...s!.coordinates].reverse());
  });

  it("degenerates to the whole corridor for a two-waypoint proposal", () => {
    const whole = bareRoute({
      edgeIds: [0, 1, 2, 3],
      anchors: [0, 4],
      anchorCoords: [nodeLL(0), nodeLL(4)],
    });
    const s = corridorSliceBetween(topo, whole, nodeLL(0), nodeLL(4));
    expect(s?.edgeIds).toEqual([0, 1, 2, 3]);
    expect(s?.coordinates).toHaveLength(5);
  });

  it("returns null when both points resolve to the same waypoint or the shape is stale", () => {
    expect(corridorSliceBetween(topo, p, nodeLL(2), nodeLL(2))).toBeNull();
    const stale = bareRoute({
      edgeIds: [0, 1, 2, 3],
      anchors: [0, 4],
      waypointNodes: [0, 2, 4],
      waypointCoords: [nodeLL(0), nodeLL(2), nodeLL(4)],
      segments: [[0, 1]], // doesn't cover the path
    });
    expect(corridorSliceBetween(topo, stale, nodeLL(0), nodeLL(2))).toBeNull();
  });
});
