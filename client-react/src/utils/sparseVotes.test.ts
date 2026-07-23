import { describe, it, expect } from "vitest";
import { decodeSparseVotes, isSparseVotes, type SparseVotesPayload } from "./sparseVotes";

const payload: SparseVotesPayload = {
  sparse: 1,
  rev: 42,
  vote_types: { "7": "Add bike lane" },
  vote_type_legend: ["Add bike lane", "Add sharrow (shared lane markings)"],
  n_edges: 10,
  n_nodes: 6,
  topology_version: '"abc"',
  ev_idx: [2, 7],
  ev_val: [5, -3],
  nv_idx: [1],
  nv_val: [5],
  evt: { "2": [[0, 6, 1]], "7": [[1, 0, 3]] },
  nvt: { "1": [[0, 6, 1]] },
  n_blocks: 4,
  blocks_version: "1699",
  block_vote_type_legend: ["Add bike lane"],
  bv_idx: [3],
  bv_val: [7],
  bvt: { "3": [[0, 6, 1]] },
};

describe("isSparseVotes", () => {
  it("accepts the sparse payload and rejects dense/nullish bodies", () => {
    expect(isSparseVotes(payload)).toBe(true);
    expect(isSparseVotes({ edge_votes: [0, 1] })).toBe(false);
    expect(isSparseVotes(undefined)).toBe(false);
    expect(isSparseVotes(null)).toBe(false);
  });
});

describe("decodeSparseVotes", () => {
  const d = decodeSparseVotes(payload);

  it("rebuilds dense typed vote totals with zeros elsewhere", () => {
    expect(d.edge_votes).toBeInstanceOf(Int32Array);
    expect(d.edge_votes!.length).toBe(10);
    expect(d.edge_votes![2]).toBe(5);
    expect(d.edge_votes![7]).toBe(-3);
    expect(d.edge_votes![0]).toBe(0);
    expect(d.node_votes![1]).toBe(5);
    expect(d.block_votes![3]).toBe(7);
    expect(d.block_votes!.length).toBe(4);
  });

  it("rebuilds holey vote-type arrays sized to the graph", () => {
    expect(d.edge_vote_types!.length).toBe(10);
    expect(d.edge_vote_types![2]).toEqual([[0, 6, 1]]);
    expect(d.edge_vote_types![7]).toEqual([[1, 0, 3]]);
    expect(d.edge_vote_types![0]).toBeUndefined();
    expect(d.node_vote_types![1]).toEqual([[0, 6, 1]]);
    expect(d.block_vote_types![3]).toEqual([[0, 6, 1]]);
  });

  it("holes are independent — writing one edge's breakdown can't leak into another", () => {
    const fresh = decodeSparseVotes(payload);
    const evt = fresh.edge_vote_types!;
    // The voteApply mutation pattern: read-or-materialize, push, store back.
    const pairs = evt[4] || [];
    pairs.push([0, 1, 0]);
    evt[4] = pairs as [number, number, number][];
    expect(evt[5]).toBeUndefined();
    expect(evt[4]).toEqual([[0, 1, 0]]);
  });

  it("carries the dimension stamps and metadata for the mismatch guard", () => {
    expect(d.n_edges).toBe(10);
    expect(d.n_nodes).toBe(6);
    expect(d.n_blocks).toBe(4);
    expect(d.blocks_version).toBe("1699");
    expect(d.rev).toBe(42);
    expect(d.vote_type_legend).toEqual(payload.vote_type_legend);
  });

  it("omits block fields when the map has no block layer", () => {
    const noBlocks = decodeSparseVotes({ ...payload, n_blocks: undefined });
    expect(noBlocks.block_votes).toBeUndefined();
    expect(noBlocks.block_vote_types).toBeUndefined();
  });
});
