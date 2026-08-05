import { describe, it, expect } from "vitest";
import { materializeBlocks, selectionVoteRows, selectionCoverage } from "./blockSelection";
import { buildBlockIndex, type GraphTopology } from "../components/GraphLayer/graphTopology";

// 4 nodes in a square; edges: e0(0-1)→block 0, e1(1-2)→block 0, e2(2-3)→block 1,
// e3(3-0)→unmapped (-1). Coordinates are irrelevant to block logic.
function topoWithBlocks(): GraphTopology {
  return {
    nNodes: 4,
    nEdges: 4,
    coords: new Int32Array(8),
    ends: Uint32Array.from([0, 1, 1, 2, 2, 3, 3, 0]),
    edgeBlockId: Int32Array.from([0, 0, 1, -1]),
    nBlocks: 2,
  };
}

function topoWithoutBlocks(): GraphTopology {
  return {
    nNodes: 4,
    nEdges: 4,
    coords: new Int32Array(8),
    ends: Uint32Array.from([0, 1, 1, 2, 2, 3, 3, 0]),
  };
}

describe("materializeBlocks", () => {
  it("expands a selection edge to its whole block", () => {
    const topo = topoWithBlocks();
    const index = buildBlockIndex(topo);
    const blocks = materializeBlocks(topo, index, [1]);
    expect(blocks.map((b) => Array.from(b))).toEqual([[0, 1]]);
  });

  it("dedupes edges of the same block and keeps blocks distinct", () => {
    const topo = topoWithBlocks();
    const index = buildBlockIndex(topo);
    const blocks = materializeBlocks(topo, index, [0, 1, 2]);
    expect(blocks.map((b) => Array.from(b))).toEqual([[0, 1], [2]]);
  });

  it("falls back to a singleton block for unmapped edges", () => {
    const topo = topoWithBlocks();
    const index = buildBlockIndex(topo);
    const blocks = materializeBlocks(topo, index, [3, 1]);
    // Singleton keys are negative → sorted before real block ids.
    expect(blocks.map((b) => Array.from(b))).toEqual([[3], [0, 1]]);
  });

  it("is all-singletons on a topology without block artifacts", () => {
    const topo = topoWithoutBlocks();
    const blocks = materializeBlocks(topo, null, [2, 0, 2]);
    expect(blocks.map((b) => Array.from(b))).toEqual([[2], [0]]);
  });
});

describe("selectionVoteRows", () => {
  const blockVoteTypes: [number, number, number][][] = [
    [[0, 3, 1], [1, 1, 0]], // block 0: Bike lane 3↑1↓, Bench 1↑
    [[0, 2, 0]],            // block 1: Bike lane 2↑
  ];
  const withVotes = () => ({
    ...topoWithBlocks(),
    block_vote_types: blockVoteTypes,
    block_vote_type_legend: ["Bike lane", "Bench"],
    edge_vote_types: [[], [], [], [[0, 1, 0]]] as [number, number, number][][],
    vote_type_legend: ["Crosswalk"],
  });

  it("returns null when the map has no block layer", () => {
    const data = { ...topoWithoutBlocks(), block_vote_types: blockVoteTypes };
    expect(selectionVoteRows(data, [0])).toBeNull();
    expect(selectionVoteRows({ ...topoWithBlocks() }, [0])).toBeNull();
  });

  it("reads one block's deduped counts for a single-edge selection", () => {
    expect(selectionVoteRows(withVotes(), [1])).toEqual([
      { label: "Bike lane", up: 3, down: 1 },
      { label: "Bench", up: 1, down: 0 },
    ]);
  });

  it("sums per label across a multi-block selection, once per block", () => {
    // Blocks 0 and 1 both carry "Bike lane"; e0+e1 share block 0 (no double count).
    expect(selectionVoteRows(withVotes(), [0, 1, 2])).toEqual([
      { label: "Bike lane", up: 5, down: 1 },
      { label: "Bench", up: 1, down: 0 },
    ]);
  });

  it("mixes in unmapped edges via their own edge breakdown", () => {
    expect(selectionVoteRows(withVotes(), [2, 3])).toEqual([
      { label: "Bike lane", up: 2, down: 0 },
      { label: "Crosswalk", up: 1, down: 0 },
    ]);
  });
});

describe("selectionCoverage", () => {
  const blockVoteTypes: [number, number, number][][] = [
    [[0, 3, 1], [1, 1, 0]], // block 0: Bike lane 3↑1↓, Bench 1↑
    [[0, 2, 0]],            // block 1: Bike lane 2↑
  ];
  const withVotes = () => ({
    ...topoWithBlocks(),
    block_vote_types: blockVoteTypes,
    block_vote_type_legend: ["Bike lane", "Bench"],
    edge_vote_types: [[], [], [], [[0, 1, 0]]] as [number, number, number][][],
    vote_type_legend: ["Crosswalk"],
  });

  it("counts a type once per block, however many of its edges are selected", () => {
    // e0 and e1 are both block 0 — coverage is 1 block, not 2 edges.
    const cov = selectionCoverage(withVotes(), [0, 1]);
    expect(cov.total).toBe(1);
    expect(cov.byLabel.get("Bike lane")).toBe(1);
    expect(cov.byLabel.get("Bench")).toBe(1);
  });

  it("reports partial reach across a multi-block selection", () => {
    // Both blocks carry Bike lane; only block 0 carries Bench.
    const cov = selectionCoverage(withVotes(), [0, 2]);
    expect(cov.total).toBe(2);
    expect(cov.byLabel.get("Bike lane")).toBe(2);
    expect(cov.byLabel.get("Bench")).toBe(1);
  });

  it("counts unmapped edges as their own unit, off their edge breakdown", () => {
    const cov = selectionCoverage(withVotes(), [2, 3]);
    expect(cov.total).toBe(2); // block 1 + the singleton for e3
    expect(cov.byLabel.get("Bike lane")).toBe(1);
    expect(cov.byLabel.get("Crosswalk")).toBe(1);
  });

  it("omits a type whose votes cancelled out — nobody's vote stands there", () => {
    const data = {
      ...topoWithBlocks(),
      block_vote_types: [[[0, 0, 0]], [[0, 2, 0]]] as [number, number, number][][],
      block_vote_type_legend: ["Bike lane"],
    };
    const cov = selectionCoverage(data, [0, 2]);
    expect(cov.total).toBe(2);
    expect(cov.byLabel.get("Bike lane")).toBe(1);
  });

  it("falls back to per-segment coverage without a block layer", () => {
    const data = {
      ...topoWithoutBlocks(),
      edge_vote_types: [
        [[0, 1, 0]], [], [[0, 0, 2]], [[1, 1, 0]],
      ] as [number, number, number][][],
      vote_type_legend: ["Bike lane", "Bench"],
    };
    const cov = selectionCoverage(data, [0, 1, 2, 3, 3]);
    expect(cov.total).toBe(4); // deduped: e3 twice is one segment
    // Downvotes count as coverage too — the type is under discussion there.
    expect(cov.byLabel.get("Bike lane")).toBe(2);
    expect(cov.byLabel.get("Bench")).toBe(1);
  });
});
