import { describe, it, expect } from "vitest";
import { applyEdgeVoteChange, applyAuthoritativeCounts, applyBlockCounts, applyMyVoteChange } from "./voteApply";
import { topologyFromJson, buildNodeAdj } from "./graphTopology";
import type { GraphData } from "../../types";

// Minimal graph: 2 edges sharing node 1.  edge0 = (0,1), edge1 = (1,2)
function makeData(): GraphData {
  const topo = topologyFromJson({
    nodes: [[0, 0], [0, 1], [0, 2]],
    edges: [[0, 1, ""], [1, 2, ""]],
  });
  return {
    ...topo,
    edge_votes: [0, 0],
    node_votes: [0, 0, 0],
    vote_type_legend: [],
    edge_vote_types: [[], []],
    node_vote_types: [[], []],
  };
}
// CSR adjacency equivalent to [[0], [0, 1], [1]].
const ADJ = buildNodeAdj(topologyFromJson({
  nodes: [[0, 0], [0, 1], [0, 2]],
  edges: [[0, 1, ""], [1, 2, ""]],
}));

function row(data: GraphData, eid: number, label: string) {
  const li = (data.vote_type_legend ?? []).indexOf(label);
  const t = (data.edge_vote_types ?? [])[eid]?.find(([l]) => l === li);
  return t ? { up: t[1], down: t[2] } : null;
}

describe("applyEdgeVoteChange (optimistic increment)", () => {
  it("upvote then reversed downvote overwrites the up (no accumulation)", () => {
    const d = makeData();
    applyEdgeVoteChange(d, ADJ, [0], "X", 1, false);       // +
    expect(row(d, 0, "X")).toEqual({ up: 1, down: 0 });
    applyEdgeVoteChange(d, ADJ, [0], "X", -1, true);        // − (reversal)
    expect(row(d, 0, "X")).toEqual({ up: 0, down: 1 });     // up removed
    expect(d.edge_votes![0]).toBe(-1);                      // net flipped, not 0/2
  });

  it("the exploding-+ alternation stays bounded with correct reversed flags", () => {
    const d = makeData();
    applyEdgeVoteChange(d, ADJ, [0], "X", -1, false);       // −  (fresh)
    applyEdgeVoteChange(d, ADJ, [0], "X", 1, true);         // +  (reversal)
    applyEdgeVoteChange(d, ADJ, [0], "X", -1, true);        // −  (reversal)
    expect(row(d, 0, "X")).toEqual({ up: 0, down: 1 });     // NOT up:1, down:1
    expect(d.edge_votes![0]).toBe(-1);
  });
});

describe("applyMyVoteChange (optimistic transition: fresh / reverse / remove)", () => {
  it("fresh up (0 → +1) increments the up side", () => {
    const d = makeData();
    applyMyVoteChange(d, ADJ, [0], "X", 0, 1);
    expect(row(d, 0, "X")).toEqual({ up: 1, down: 0 });
    expect(d.edge_votes![0]).toBe(1);
  });

  it("reversal (+1 → -1) moves the vote across sides (net ±2)", () => {
    const d = makeData();
    applyMyVoteChange(d, ADJ, [0], "X", 0, 1); // up:1
    applyMyVoteChange(d, ADJ, [0], "X", 1, -1); // reverse to down
    expect(row(d, 0, "X")).toEqual({ up: 0, down: 1 });
    expect(d.edge_votes![0]).toBe(-1);
  });

  it("removal (+1 → 0) decrements and drops the type at 0/0", () => {
    const d = makeData();
    applyMyVoteChange(d, ADJ, [0], "X", 0, 1); // up:1
    applyMyVoteChange(d, ADJ, [0], "X", 1, 0); // remove
    expect(row(d, 0, "X")).toBeNull();
    expect(d.edge_votes![0]).toBe(0);
    expect((d.edge_vote_types ?? [])[0]).toEqual([]);
  });

  it("a no-op transition (same dir) changes nothing", () => {
    const d = makeData();
    applyMyVoteChange(d, ADJ, [0], "X", 1, 1);
    expect(d.edge_votes![0]).toBe(0);
  });

  it("re-derives the shared node after a transition", () => {
    const d = makeData();
    applyMyVoteChange(d, ADJ, [1], "X", 0, 1); // edge1 up:1 → node1 borders it
    expect(d.node_votes![1]).toBe(1);
  });
});

describe("applyAuthoritativeCounts (SET, idempotent)", () => {
  it("sets up/down to the server truth", () => {
    const d = makeData();
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [3, 1] });
    expect(row(d, 0, "X")).toEqual({ up: 3, down: 1 });
    expect(d.edge_votes![0]).toBe(2); // net 3−1
  });

  it("is idempotent — re-applying the same delta does not change counts", () => {
    const d = makeData();
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [2, 0] });
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [2, 0] });
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [2, 0] });
    expect(row(d, 0, "X")).toEqual({ up: 2, down: 0 });
    expect(d.edge_votes![0]).toBe(2);
  });

  it("corrects a wrong optimistic guess instead of compounding it", () => {
    const d = makeData();
    // optimistic guessed a fresh downvote (wrong — it was a reversal)
    applyEdgeVoteChange(d, ADJ, [0], "X", -1, false); // up:0 down:1 (but truth is reversal)
    // server says the truth is up:0, down:1 after reversing a prior up... here
    // authoritative truth is the SET value, which overwrites the guess exactly:
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [0, 1] });
    expect(row(d, 0, "X")).toEqual({ up: 0, down: 1 });
    expect(d.edge_votes![0]).toBe(-1);
  });

  it("removes a vote type that drops to 0/0", () => {
    const d = makeData();
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [1, 0] });
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [0, 0] });
    expect(row(d, 0, "X")).toBeNull();
    expect(d.edge_votes![0]).toBe(0);
    expect((d.edge_vote_types ?? [])[0]).toEqual([]);
  });

  it("re-derives the shared node from the max-net adjacent edge", () => {
    const d = makeData();
    applyAuthoritativeCounts(d, ADJ, "X", { "0": [1, 0], "1": [5, 0] });
    // node 1 borders edge0 (net1) and edge1 (net5) → derives edge1's pair
    expect(d.node_votes![1]).toBe(5);
    const li = d.vote_type_legend!.indexOf("X");
    expect(d.node_vote_types![1].find(([l]) => l === li)).toEqual([li, 5, 0]);
  });
});

describe("applyBlockCounts (SET, idempotent, block twin of vtCounts)", () => {
  function makeBlockData(): GraphData {
    return {
      ...makeData(),
      block_votes: [0, 0, 0],
      block_vote_types: [[], [], []],
      block_vote_type_legend: [],
    };
  }

  it("SETs deduped counts, updates total activity, and registers the label", () => {
    const d = makeBlockData();
    expect(applyBlockCounts(d, "X", { "1": [3, 1] })).toBe(true);
    expect(d.block_vote_type_legend).toEqual(["X"]);
    expect(d.block_vote_types![1]).toEqual([[0, 3, 1]]);
    expect(d.block_votes![1]).toBe(4); // heat = up + down (downvotes read hot)
  });

  it("is idempotent and reports no change on re-apply", () => {
    const d = makeBlockData();
    applyBlockCounts(d, "X", { "1": [2, 0] });
    expect(applyBlockCounts(d, "X", { "1": [2, 0] })).toBe(false);
    expect(d.block_votes![1]).toBe(2);
  });

  it("drops a type that reaches 0/0 and adjusts the total down", () => {
    const d = makeBlockData();
    applyBlockCounts(d, "X", { "0": [1, 0] });
    applyBlockCounts(d, "X", { "0": [0, 0] });
    expect(d.block_vote_types![0]).toEqual([]);
    expect(d.block_votes![0]).toBe(0);
  });

  it("ignores out-of-range blocks and returns false without block arrays", () => {
    const d = makeBlockData();
    expect(applyBlockCounts(d, "X", { "99": [1, 0] })).toBe(false);
    const bare = makeData();
    expect(applyBlockCounts(bare, "X", { "0": [1, 0] })).toBe(false);
  });
});
