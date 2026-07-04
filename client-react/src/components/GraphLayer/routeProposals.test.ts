import { describe, it, expect } from "vitest";
import {
  parseRouteProposal,
  proposalShapeClass,
  routeBlockEdges,
  isRouteCovered,
  dropPointsCoveredByRoutes,
  chooseAnchorOrder,
  type RouteProposal,
} from "./routeProposals";
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
    const end2 = ll(0, 12);
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
