import { describe, it, expect } from "vitest";
import {
  shuffleKey,
  computeVoteTypeWinners,
  dedupeWinnersByEdge,
  dedupeWinnersByBlock,
  spaceOutWinners,
  edgeMidpointResolver,
  applyTopProposalLimit,
  selectTopProposals,
  selectTopProposalsFrom,
  computeWinnerCandidates,
  topLabelForEdges,
  type EdgePosition,
  type VoteTypeWinner,
} from "./topProposals";
import { topologyFromJson } from "./graphTopology";

const SALT = 12345;

// Build a typed-array topology from the legacy {nodes, edges} test shape.
const topo = (
  nodes: [number, number][],
  edges: [number, number, string][],
) => topologyFromJson({ nodes, edges });

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

  it("keeps up to perTypeLimit edges per vote type, highest net first", () => {
    const legend = ["Bike lane"];
    const evt: [number, number, number][][] = [
      [[0, 2, 0]], // net 2
      [[0, 9, 0]], // net 9
      [[0, 5, 0]], // net 5
      [[0, 7, 0]], // net 7
    ];
    const w = computeVoteTypeWinners(legend, evt, 3);
    expect(w.map((x) => x.count)).toEqual([9, 7, 5]);
    expect(w.map((x) => x.edgeIdx)).toEqual([1, 3, 2]);
  });

  it("defaults to a single edge per type", () => {
    const legend = ["Bike lane"];
    const evt: [number, number, number][][] = [[[0, 2, 0]], [[0, 9, 0]]];
    expect(computeVoteTypeWinners(legend, evt)).toHaveLength(1);
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

describe("spaceOutWinners", () => {
  const mk = (label: string, legendIdx: number, count: number, edgeIdx: number): VoteTypeWinner => ({
    legendIdx, label, edgeIdx, count,
  });

  // Two close points (~110m apart at this latitude) and one far away (~1.1km).
  const NEAR_A: [number, number] = [40.7000, -74.0000];
  const NEAR_B: [number, number] = [40.7010, -74.0000];
  const FAR: [number, number] = [40.7100, -74.0000];
  const posOf: EdgePosition = (edgeIdx) =>
    edgeIdx === 0 ? NEAR_A : edgeIdx === 1 ? NEAR_B : edgeIdx === 2 ? FAR : null;

  it("drops the weaker of two same-type winners within the spacing radius", () => {
    const winners = [mk("Bike", 0, 9, 0), mk("Bike", 0, 4, 1)]; // edges 0,1 ~110m apart
    const out = spaceOutWinners(winners, SALT, posOf, 300);
    expect(out.map((w) => w.edgeIdx)).toEqual([0]); // stronger (net 9) survives
  });

  it("keeps both when they are farther apart than the radius", () => {
    const winners = [mk("Bike", 0, 9, 0), mk("Bike", 0, 4, 2)]; // edges 0,2 ~1.1km apart
    const out = spaceOutWinners(winners, SALT, posOf, 300);
    expect(out.map((w) => w.edgeIdx).sort()).toEqual([0, 2]);
  });

  it("never suppresses across different vote types", () => {
    const winners = [mk("Bike", 0, 9, 0), mk("Tree", 1, 4, 1)]; // same spot, diff type
    const out = spaceOutWinners(winners, SALT, posOf, 300);
    expect(out).toHaveLength(2);
  });

  it("keeps winners whose position cannot be resolved", () => {
    const winners = [mk("Bike", 0, 9, 99), mk("Bike", 0, 4, 98)]; // posOf → null
    expect(spaceOutWinners(winners, SALT, posOf, 300)).toHaveLength(2);
  });

  it("is a no-op when spacing is zero", () => {
    const winners = [mk("Bike", 0, 9, 0), mk("Bike", 0, 4, 1)];
    expect(spaceOutWinners(winners, SALT, posOf, 0)).toHaveLength(2);
  });
});

describe("edgeMidpointResolver", () => {
  it("returns the mean of an edge's two endpoint nodes", () => {
    const data = topo([[40.70, -74.00], [40.72, -74.02]], [[0, 1, ""]]);
    const mid = edgeMidpointResolver(data)(0)!;
    expect(mid[0]).toBeCloseTo(40.71, 6);
    expect(mid[1]).toBeCloseTo(-74.01, 6);
  });

  it("returns null for missing edges, nodes, or null data", () => {
    expect(edgeMidpointResolver(null)(0)).toBeNull();
    expect(edgeMidpointResolver(topo([], []))(0)).toBeNull();
    // node 5 absent → clamped to 0; still resolves a (degenerate) midpoint, but
    // the edge index itself is out of range here, so it's null.
    expect(edgeMidpointResolver(topo([[40.7, -74]], []))(0)).toBeNull();
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
      { vote_type_legend: legend, edge_vote_types: evt, ...topo([], []) }, SALT, 2,
      600, undefined, 0,
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
      { vote_type_legend: legend, edge_vote_types: evt, ...topo([], []) }, SALT, 1,
      600, undefined, 0,
    );
    expect(out).toHaveLength(1);
    expect(out[0].edgeIdx).toBe(0);
  });

  it("collapses a same-type corridor to one pin but keeps a distant one", () => {
    // Three "Bike" edges: 0 and 1 are ~110m apart (one corridor), 2 is ~1.1km
    // away. Edge midpoints come from nodes/edges via edgeMidpointResolver.
    const nodes: [number, number][] = [
      [40.7000, -74.0000], [40.7000, -74.0000], // edge 0 midpoint ≈ NEAR_A
      [40.7010, -74.0000], [40.7010, -74.0000], // edge 1 midpoint ≈ NEAR_B (~110m)
      [40.7100, -74.0000], [40.7100, -74.0000], // edge 2 midpoint ≈ FAR (~1.1km)
    ];
    const edges: [number, number, string][] = [
      [0, 1, ""], [2, 3, ""], [4, 5, ""],
    ];
    const evt: [number, number, number][][] = [
      [[0, 9, 0]], // edge 0: Bike net 9
      [[0, 5, 0]], // edge 1: Bike net 5 (same corridor → suppressed)
      [[0, 4, 0]], // edge 2: Bike net 4 (far → survives)
    ];
    const out = selectTopProposals(
      { vote_type_legend: ["Bike"], edge_vote_types: evt, ...topo(nodes, edges) }, SALT, 10,
      600, undefined, 0,
    );
    expect(out.map((w) => w.edgeIdx).sort()).toEqual([0, 2]);
  });

  it("returns [] for null data", () => {
    expect(selectTopProposals(null, SALT, 10)).toEqual([]);
  });
});

describe("top-proposal support floor (TOP_PROPOSAL_MIN_NET)", () => {
  const legend = ["Rare", "Popular"];
  const evt: [number, number, number][][] = [
    [[0, 3, 0]],    // edge 0: Rare net 3 — below the floor
    [[1, 150, 20]], // edge 1: Popular net 130 — above it
    [[1, 100, 0]],  // edge 2: Popular net 100 — exactly at it (excluded: strictly greater)
  ];

  it("selectTopProposals defaults to the floor and drops low-support winners", () => {
    const out = selectTopProposals(
      { vote_type_legend: legend, edge_vote_types: evt, ...topo([], []) }, SALT, 10,
    );
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ label: "Popular", edgeIdx: 1, count: 130 });
  });

  it("requires STRICTLY more than minNet (net == floor is out)", () => {
    const w = computeVoteTypeWinners(legend, evt, 3, undefined, 100);
    expect(w.map((x) => x.edgeIdx)).toEqual([1]);
  });

  it("minNet 0 restores the any-positive-net behavior", () => {
    const w = computeVoteTypeWinners(legend, evt, 3, undefined, 0);
    expect(w).toHaveLength(3);
  });
});

describe("topLabelForEdges (vote-type fallback ranking)", () => {
  const legend = ["Bike", "Trees", "Sidewalk"];
  // edge 0: Bike net 3 (5-2), Trees net 4; edge 1: Bike net 6; edge 2: Sidewalk net 9
  const evt: [number, number, number][][] = [
    [[0, 5, 2], [1, 4, 0]],
    [[0, 6, 0]],
    [[2, 9, 0]],
  ];

  it("returns the highest net label across the given edges only", () => {
    // Edges 0+1: Bike = 3+6 = 9, Trees = 4 → Bike wins.
    expect(topLabelForEdges(legend, evt, [0, 1])).toBe("Bike");
    // Edge 2 only: Sidewalk.
    expect(topLabelForEdges(legend, evt, [2])).toBe("Sidewalk");
  });

  it("ignores edges not in the set", () => {
    // Only edge 0: Trees (4) beats Bike (3).
    expect(topLabelForEdges(legend, evt, [0])).toBe("Trees");
  });

  it("returns null when no edge has positive net support", () => {
    const downvoted: [number, number, number][][] = [[[0, 1, 5]]];
    expect(topLabelForEdges(legend, downvoted, [0])).toBeNull();
  });

  it("returns null for empty inputs", () => {
    expect(topLabelForEdges(legend, evt, [])).toBeNull();
    expect(topLabelForEdges([], evt, [0])).toBeNull();
  });
});

describe("computeVoteTypeWinners — route/point kind filter", () => {
  const legend = ["Add bike greenway", "Add bike parking", "Fix the thing"];
  const evt: [number, number, number][][] = [
    [[0, 9, 0]], // edge 0: greenway (route kind) net 9
    [[1, 5, 0]], // edge 1: parking (point kind) net 5
    [[2, 3, 0]], // edge 2: unknown-kind custom label net 3
  ];
  const kindOf = (label: string) =>
    label === "Add bike greenway" ? "route" as const
    : label === "Add bike parking" ? "point" as const
    : null;

  it("excludes ROUTE-kind vote types from point-based winners", () => {
    const out = computeVoteTypeWinners(legend, evt, 1, kindOf);
    expect(out.map((w) => w.label).sort()).toEqual(["Add bike parking", "Fix the thing"]);
  });

  it("keeps unknown-kind labels eligible (legacy suggestions)", () => {
    const out = computeVoteTypeWinners(legend, evt, 1, kindOf);
    expect(out.some((w) => w.label === "Fix the thing")).toBe(true);
  });

  it("admits every kind when no resolver is given", () => {
    expect(computeVoteTypeWinners(legend, evt, 1)).toHaveLength(3);
  });
});

describe("computeVoteTypeWinners — legend visibility filter", () => {
  const legend = ["Bike lane", "Tree", "Bench"];
  const evt: [number, number, number][][] = [
    [[0, 9, 0]],
    [[1, 5, 0]],
    [[2, 3, 0]],
  ];

  it("drops vote types toggled off in the legend", () => {
    const out = computeVoteTypeWinners(legend, evt, 1, undefined, 0,
      (label) => label !== "Tree");
    expect(out.map((w) => w.label).sort()).toEqual(["Bench", "Bike lane"]);
  });

  it("admits every type when no resolver is given", () => {
    expect(computeVoteTypeWinners(legend, evt, 1)).toHaveLength(3);
  });

  it("frees ranked slots for the survivors (filter, THEN limit)", () => {
    // Two types, three edges each. With a limit of 2 and both types visible,
    // one slot goes to each; hiding one type must promote the other's
    // runner-up rather than leave the second slot empty.
    const two = ["Bike lane", "Tree"];
    const rows: [number, number, number][][] = [
      [[0, 9, 0]], [[0, 8, 0]], [[1, 7, 0]],
    ];
    const visible = computeVoteTypeWinners(two, rows, 2, undefined, 0,
      (label) => label === "Bike lane");
    expect(applyTopProposalLimit(visible, SALT, 2).map((w) => w.count)).toEqual([9, 8]);
  });
});

describe("dedupeWinnersByBlock", () => {
  const mk = (label: string, legendIdx: number, count: number, edgeIdx: number): VoteTypeWinner => ({
    legendIdx, label, edgeIdx, count,
  });

  it("keeps a single winner per block ACROSS vote types (strongest wins)", () => {
    // Edges 0 and 1 share block 4; different vote types.
    const keyOf = (e: number) => (e === 0 || e === 1 ? 4 : 7);
    const out = dedupeWinnersByBlock(
      [mk("Bike", 0, 3, 0), mk("Tree", 1, 8, 1), mk("Bench", 2, 2, 2)],
      SALT,
      keyOf,
    );
    expect(out).toHaveLength(2);
    expect(out.find((w) => keyOf(w.edgeIdx) === 4)!.label).toBe("Tree");
    expect(out.some((w) => w.edgeIdx === 2)).toBe(true);
  });

  it("keeps winners on unmapped edges (negative keys) unconstrained", () => {
    // Singleton keys (≤ -2) mean "no block layer" — nothing collapses.
    const out = dedupeWinnersByBlock(
      [mk("Bike", 0, 3, 0), mk("Tree", 1, 8, 1)],
      SALT,
      (e) => -(e + 2),
    );
    expect(out).toHaveLength(2);
  });

  it("tiebreaks equal-net same-block winners deterministically by salt", () => {
    const winners = [mk("Alpha", 0, 5, 0), mk("Bravo", 1, 5, 1)];
    const first = dedupeWinnersByBlock(winners, SALT, () => 1)[0].label;
    expect(dedupeWinnersByBlock(winners, SALT, () => 1)[0].label).toBe(first);
    const expected = shuffleKey("Alpha", SALT) <= shuffleKey("Bravo", SALT) ? "Alpha" : "Bravo";
    expect(first).toBe(expected);
  });
});

describe("selectTopProposals — block uniqueness + kind filter (full path)", () => {
  it("allows at most one pin per street block, across vote types", () => {
    // Edges 0 and 1 both map to block 0 (a two-direction street); edge 2 is
    // its own block. Bike (net 8) and Tree (net 6) sit on the same block →
    // only Bike survives there; Bench keeps its own block.
    const nodes: [number, number][] = [
      [40.7000, -74.0000], [40.7001, -74.0000],
      [40.7000, -74.0001], [40.7001, -74.0001],
      [40.7100, -74.0000], [40.7101, -74.0000],
    ];
    const edges: [number, number, string][] = [[0, 1, ""], [2, 3, ""], [4, 5, ""]];
    const data = { 
      vote_type_legend: ["Bike", "Tree", "Bench"],
      edge_vote_types: [
        [[0, 8, 0]], [[1, 6, 0]], [[2, 4, 0]],
      ] as [number, number, number][][],
      ...topo(nodes, edges),
    };
    data.edgeBlockId = Int32Array.from([0, 0, 1]);
    data.nBlocks = 2;
    const out = selectTopProposals(data, SALT, 10, 600, undefined, 0);
    expect(out.map((w) => w.label).sort()).toEqual(["Bench", "Bike"]);
  });

  it("keeps ROUTE-kind labels out of the point-based top proposals", () => {
    const data = {
      vote_type_legend: ["Add bike greenway", "Add bike parking"],
      edge_vote_types: [
        [[0, 9, 0]], // greenway on edge 0 — route kind, must not pin
        [[1, 5, 0]], // parking on edge 1 — point kind, pins
      ] as [number, number, number][][],
      ...topo([], []),
    };
    const kindOf = (label: string) =>
      label === "Add bike greenway" ? "route" as const : "point" as const;
    const out = selectTopProposals(data, SALT, 10, 600, kindOf, 0);
    expect(out.map((w) => w.label)).toEqual(["Add bike parking"]);
  });
});

describe("selectTopProposalsFrom — the legend-toggle contract", () => {
  // GraphLayer caches the candidate scan for a vote epoch and re-selects from
  // it whenever the legend toggles. That is only sound while re-selecting from
  // cached candidates gives exactly what a fresh filtered scan would.
  const legend = ["Bike", "Tree", "Bench"];
  const evt: [number, number, number][][] = [
    [[0, 9, 0]],
    [[1, 8, 0]],
    [[2, 7, 0]],
    [[0, 3, 0], [1, 2, 0]],
  ];
  const nodes: [number, number][] = [
    [40.700, -74.00], [40.700, -74.00],
    [40.710, -74.00], [40.710, -74.00],
    [40.720, -74.00], [40.720, -74.00],
    [40.730, -74.00], [40.730, -74.00],
  ];
  const edges: [number, number, string][] = [[0, 1, ""], [2, 3, ""], [4, 5, ""], [6, 7, ""]];
  const data = { vote_type_legend: legend, edge_vote_types: evt, ...topo(nodes, edges) };

  const hideTree = (l: string) => l !== "Tree";

  it("re-selecting from cached candidates equals a fresh filtered scan", () => {
    const candidates = computeWinnerCandidates(legend, evt, 6, undefined, 0);
    expect(selectTopProposalsFrom(data, candidates, SALT, 10, 600, hideTree))
      .toEqual(selectTopProposals(data, SALT, 10, 600, undefined, 0, hideTree));
  });

  it("hiding a type frees its slot for another type's runner-up", () => {
    const candidates = computeWinnerCandidates(legend, evt, 6, undefined, 0);
    const all = selectTopProposalsFrom(data, candidates, SALT, 2, 600);
    const filtered = selectTopProposalsFrom(data, candidates, SALT, 2, 600, hideTree);
    expect(all.map((w) => w.label)).toEqual(["Bike", "Tree"]);
    expect(filtered.map((w) => w.label)).toEqual(["Bike", "Bench"]);
  });

  it("the candidate scan itself ignores visibility", () => {
    expect(computeWinnerCandidates(legend, evt, 6, undefined, 0).map((w) => w.label))
      .toContain("Tree");
  });
});
