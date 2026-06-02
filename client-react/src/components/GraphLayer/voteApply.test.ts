import { describe, it, expect } from "vitest";
import { applyEdgeVoteChange, applyAuthoritativeCounts } from "./voteApply";
import type { GraphData } from "../../types";

// Minimal graph: 2 edges sharing node 1.  edge0 = (0,1), edge1 = (1,2)
function makeData(): GraphData {
  return {
    nodes: [[0, 0], [0, 1], [0, 2]],
    edges: [[0, 1, ""], [1, 2, ""]],
    edge_votes: [0, 0],
    node_votes: [0, 0, 0],
    vote_type_legend: [],
    edge_vote_types: [[], []],
    node_vote_types: [[], []],
  };
}
const ADJ = [[0], [0, 1], [1]];

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
