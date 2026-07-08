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
  dedupeRoutes,
  capPathToLengthBudget,
  routeLengthBudgetM,
  MIN_ROUTE_SCORE,
  MIN_ROUTE_EDGES,
  ROUTE_LENGTH_BASE_M,
  ROUTE_LENGTH_PER_SQRT_SCORE_M,
  ROUTE_LENGTH_MAX_M,
  type PathResult,
  type RouteProposal,
  type RouteProposalOptions,
} from "./routeProposals";
import {
  topologyFromJson,
  buildNodeAdj,
  type GraphTopology,
} from "./graphTopology";
import type { LatLng } from "../../types";

const ll = (lat: number, lng: number): LatLng => ({ lat, lng });

function route(over: Partial<RouteProposal> = {}): RouteProposal {
  return {
    id: "r1",
    label: "Bike lane",
    legendIdx: 0,
    score: 30,
    edgeIds: [0, 1, 2],
    blocks: [[0, 5], [1], [2]], // block 0 has two edges (both directions)
    blockEdgeIds: [0, 5, 1, 2],
    anchors: [10, 13],
    anchorCoords: [ll(40.70, -74.00), ll(40.70, -73.99)],
    ...over,
  };
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
  return computeRouteProposals(topo, buildNodeAdj(topo), {
    edge_vote_types: voteTypes,
    vote_type_legend: LEGEND,
  }, opts);
}

function bareRoute(over: Partial<RouteProposal>): RouteProposal {
  return {
    id: "x", label: "Bike lane", legendIdx: BIKE, score: 0,
    edgeIds: [], blocks: [], blockEdgeIds: [], anchors: [0, 0],
    anchorCoords: [ll(0, 0), ll(0, 0)],
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

describe("capPathToLengthBudget", () => {
  // Path of 5 edges, 100m each; per-edge weights below.
  const path = (weights: number[]): { p: PathResult; weightOf: (e: number) => number } => ({
    p: {
      edges: weights.map((_, i) => i),
      nodes: weights.map((_, i) => i).concat(weights.length),
      weight: weights.reduce((a, b) => a + b, 0),
    },
    weightOf: (e: number) => weights[e],
  });
  const len100 = () => 100;

  it("returns the path unchanged when it fits the budget", () => {
    const { p, weightOf } = path([1, 1, 1]);
    expect(capPathToLengthBudget(p, 300, len100, weightOf)).toBe(p);
  });

  it("trims to the hottest contiguous window under the budget", () => {
    // 500m total, budget 200m (2 edges). Hot stretch is edges 2–3.
    const { p, weightOf } = path([1, 1, 9, 8, 1]);
    const out = capPathToLengthBudget(p, 200, len100, weightOf);
    expect(out.edges).toEqual([2, 3]);
    expect(out.nodes).toEqual([2, 3, 4]); // endpoints of the kept window
    expect(out.weight).toBe(17);
  });

  it("prefers the shorter window on equal weight, then the earliest", () => {
    const { p, weightOf } = path([5, 0, 5, 5]);
    // Budget 200: windows [2,3] (weight 10, 200m) beat [0,1] (5) and [1,2] (5).
    expect(capPathToLengthBudget(p, 200, len100, weightOf).edges).toEqual([2, 3]);
    // Budget 100: [0], [2], [3] all weigh 5 — earliest wins.
    expect(capPathToLengthBudget(p, 100, len100, weightOf).edges).toEqual([0]);
  });

  it("keeps a single over-budget edge rather than trimming to nothing", () => {
    const { p, weightOf } = path([1, 50, 1]);
    const out = capPathToLengthBudget(p, 60, len100, weightOf);
    expect(out.edges).toEqual([1]);
    expect(out.weight).toBe(50);
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
