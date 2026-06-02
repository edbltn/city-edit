import { describe, it, expect } from "vitest";
import {
  shuffleKey,
  computeVoteTypeWinners,
  dedupeWinnersByEdge,
  applyTopProposalLimit,
  selectTopProposals,
  type VoteTypeWinner,
} from "./topProposals";

const SALT = 12345;

describe("computeVoteTypeWinners", () => {
  it("picks the edge with the highest net per vote type", () => {
    const legend = ["Bike lane", "Tree"];
    const evt: [number, number, number][][] = [
      [[0, 5, 1]],            // edge 0: Bike lane net 4
      [[0, 2, 0], [1, 3, 0]], // edge 1: Bike lane net 2, Tree net 3
    ];
    const w = computeVoteTypeWinners(legend, evt);
    expect(w.find((x) => x.label === "Bike lane")).toMatchObject({ edgeIdx: 0, count: 4 });
    expect(w.find((x) => x.label === "Tree")).toMatchObject({ edgeIdx: 1, count: 3 });
  });

  it("counts net = up − down so downvotes reduce support", () => {
    const legend = ["X"];
    const evt: [number, number, number][][] = [[[0, 3, 5]]]; // net -2
    expect(computeVoteTypeWinners(legend, evt)).toHaveLength(0);
  });

  it("excludes net <= 0 proposals", () => {
    const legend = ["X", "Y"];
    const evt: [number, number, number][][] = [[[0, 2, 2], [1, 1, 0]]]; // X net 0, Y net 1
    expect(computeVoteTypeWinners(legend, evt).map((x) => x.label)).toEqual(["Y"]);
  });

  it("returns [] for empty inputs", () => {
    expect(computeVoteTypeWinners([], [])).toEqual([]);
    expect(computeVoteTypeWinners(["A"], [])).toEqual([]);
  });
});

describe("dedupeWinnersByEdge", () => {
  it("keeps a single winner per edge (higher net wins)", () => {
    const winners: VoteTypeWinner[] = [
      { legendIdx: 0, label: "A", edgeIdx: 7, count: 3 },
      { legendIdx: 1, label: "B", edgeIdx: 7, count: 8 },
      { legendIdx: 2, label: "C", edgeIdx: 9, count: 2 },
    ];
    const out = dedupeWinnersByEdge(winners, SALT);
    expect(out).toHaveLength(2);
    expect(out.find((w) => w.edgeIdx === 7)!.label).toBe("B");
  });

  it("tiebreaks equal-net same-edge winners deterministically by salt", () => {
    const winners: VoteTypeWinner[] = [
      { legendIdx: 0, label: "Alpha", edgeIdx: 1, count: 5 },
      { legendIdx: 1, label: "Bravo", edgeIdx: 1, count: 5 },
    ];
    const first = dedupeWinnersByEdge(winners, SALT)[0].label;
    expect(dedupeWinnersByEdge(winners, SALT)[0].label).toBe(first); // stable
    const expected = shuffleKey("Alpha", SALT) <= shuffleKey("Bravo", SALT) ? "Alpha" : "Bravo";
    expect(first).toBe(expected);
  });

  it("keeps winners on distinct edges", () => {
    const winners: VoteTypeWinner[] = [
      { legendIdx: 0, label: "A", edgeIdx: 1, count: 3 },
      { legendIdx: 1, label: "B", edgeIdx: 2, count: 3 },
    ];
    expect(dedupeWinnersByEdge(winners, SALT)).toHaveLength(2);
  });
});

describe("applyTopProposalLimit", () => {
  const mk = (label: string, count: number, edgeIdx: number): VoteTypeWinner => ({
    legendIdx: 0, label, edgeIdx, count,
  });

  it("sorts by net descending and caps at limit", () => {
    const winners = [mk("A", 1, 1), mk("B", 9, 2), mk("C", 5, 3)];
    expect(applyTopProposalLimit(winners, SALT, 2).map((w) => w.label)).toEqual(["B", "C"]);
  });

  it("returns all when under the limit", () => {
    expect(applyTopProposalLimit([mk("A", 3, 1)], SALT, 10)).toHaveLength(1);
  });
});

describe("selectTopProposals (full path)", () => {
  it("shows a shared edge once and does NOT consume two limit slots", () => {
    // edge 0 has two type-winners (net 8 and 6); edge 1 has one (net 5).
    const legend = ["A", "B", "C"];
    const evt: [number, number, number][][] = [
      [[0, 8, 0], [1, 6, 0]],
      [[2, 5, 0]],
    ];
    // Naive (per-type) logic with limit 2 would pick A and B (both edge 0),
    // dropping C. Correct: edge 0 collapses to one (A) + edge 1 (C).
    const out = selectTopProposals(
      { vote_type_legend: legend, edge_vote_types: evt }, SALT, 2,
    );
    expect(out.map((w) => w.edgeIdx).sort()).toEqual([0, 1]);
    expect(out.find((w) => w.edgeIdx === 0)!.label).toBe("A");
  });

  it("each edge consumes exactly one slot under a tight limit", () => {
    const legend = ["A", "B", "C"];
    const evt: [number, number, number][][] = [
      [[0, 8, 0], [1, 7, 0]], // edge 0: two winners
      [[2, 1, 0]],            // edge 1: one winner
    ];
    const out = selectTopProposals(
      { vote_type_legend: legend, edge_vote_types: evt }, SALT, 1,
    );
    expect(out).toHaveLength(1);
    expect(out[0].edgeIdx).toBe(0);
  });

  it("returns [] for null data", () => {
    expect(selectTopProposals(null, SALT, 10)).toEqual([]);
  });
});
