import { describe, it, expect } from "vitest";
import { materializeBlocks, selectionVoteRows } from "./blockSelection";
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
