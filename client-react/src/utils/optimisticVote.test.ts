// ==========================================================================
// Optimistic voting — prediction, reconciliation, rollback
// ==========================================================================
// castVote.test.ts covers the press matrix and the wire contract. This file
// covers the three things that make a press feel instant rather than merely
// look instant:
//
//   · the predicted BLOCK move (the number the heatmap paints) matches what
//     the server will count, so the confirming delta is a no-op and nothing
//     visibly corrects itself;
//   · an in-flight write survives a background refresh landing on top of it;
//   · a failure undoes itself, says so, and cannot undo a LATER press.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import {
  applyBlockCounts, applyMyBlockVoteChange,
} from "../components/GraphLayer/voteApply";
import type { GraphData } from "../types";
import { singletonBlocks, type TouchedBlock } from "./blockSelection";
import { blockVoteDeltas, castVotes, planBlockVote } from "./castVote";
import {
  _resetPendingCasts, pendingCastsFor, settlePendingCastsForDelta,
} from "./pendingVotes";
import {
  getVote, setVote, setVotes, setVoteTypeMap, reconcileEdge, resetMapVotes,
  _resetVoteStore,
} from "./voteStore";

const MODE = "walkways";
const LABEL = "Add bike lane";
const OTHER = "More trees";

const blk = (key: number, edges: number[]): TouchedBlock => ({ key, edges });

/** Events the cast path dispatched, in order. */
let events: { type: string; detail: unknown }[] = [];

beforeEach(() => {
  _resetVoteStore();
  _resetPendingCasts();
  setVoteTypeMap({ "10": LABEL, "11": OTHER });
  events = [];
  // Node has EventTarget/CustomEvent, so a real one gives real listener
  // semantics; localStorage is absent, which the store's try/catch expects.
  const win = new EventTarget() as unknown as Window & typeof globalThis;
  for (const type of ["optimistic-vote", "vote-rejected", "map-passcode-required"]) {
    win.addEventListener(type, (e) => {
      events.push({ type, detail: (e as CustomEvent).detail });
    });
  }
  vi.stubGlobal("window", win);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const optimistic = () => events.filter((e) => e.type === "optimistic-vote")
  .map((e) => e.detail as import("./castVote").OptimisticVoteDetail);
const rejections = () => events.filter((e) => e.type === "vote-rejected")
  .map((e) => (e.detail as { message: string }).message);

function okFetch(body: Record<string, unknown> = {}) {
  const mock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body });
  vi.stubGlobal("fetch", mock);
  return mock;
}

// ── The predicted block move ────────────────────────────────────────────────

describe("blockVoteDeltas — the server's dedupe rule, predicted", () => {
  // One block, five edges; a corridor vote lands on three of them.
  const BLOCK = blk(4, [1, 2, 3, 4, 5]);

  it("counts a person ONCE per block however many of its edges they voted", () => {
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: 1, blocks: [BLOCK],
    });
    expect(plan.blockDeltas).toEqual([{ block: 4, up: 1, down: 0 }]);
  });

  it("a second press on the held direction is −1 (an unvote), never +1", () => {
    setVotes(MODE, [1, 2, 3], LABEL, 1);
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: 1, blocks: [BLOCK],
    });
    expect(plan.targetDir).toBe(0);
    expect(plan.blockDeltas).toEqual([{ block: 4, up: -1, down: 0 }]);
  });

  it("a flip MOVES the person across arms rather than adding to the new one", () => {
    setVotes(MODE, [1, 2, 3], LABEL, 1);
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: -1, blocks: [BLOCK],
    });
    expect(plan.blockDeltas).toEqual([{ block: 4, up: -1, down: 1 }]);
  });

  it("extending a vote onto more edges of a block I already hold moves nothing", () => {
    setVote(MODE, 5, LABEL, 1); // already present in the block, off-selection…
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1, blocks: [BLOCK],
    });
    // …so this reads as full coverage and becomes an unvote of the whole block.
    expect(plan.targetDir).toBe(0);
    expect(plan.blockDeltas).toEqual([{ block: 4, up: -1, down: 0 }]);
  });

  it("a clear on one edge and a cast on another in the SAME block cancel out", () => {
    setVote(MODE, 5, LABEL, -1); // my down vote elsewhere in the block
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: [BLOCK],
    });
    expect(plan.castEdges).toEqual([1]);
    expect(plan.clearEdges).toEqual([5]);
    // The block gains an up and loses its down — one move each way, not +1/0.
    expect(plan.blockDeltas).toEqual([{ block: 4, up: 1, down: -1 }]);
  });

  it("moves each touched block exactly once across a multi-block corridor", () => {
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 7, 8], label: LABEL, direction: 1,
      blocks: [blk(4, [1, 2, 3]), blk(9, [7, 8])],
    });
    expect(plan.blockDeltas).toEqual([
      { block: 4, up: 1, down: 0 }, { block: 9, up: 1, down: 0 },
    ]);
  });

  it("predicts nothing without a block layer — a singleton has no aggregate", () => {
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1,
      blocks: singletonBlocks([1, 2]),
    });
    expect(plan.blockDeltas).toEqual([]);
  });

  it("ignores other vote types in the same block", () => {
    setVote(MODE, 5, OTHER, 1);
    const deltas = blockVoteDeltas(MODE, LABEL, [BLOCK], new Map([[1, 1 as const]]));
    expect(deltas).toEqual([{ block: 4, up: 1, down: 0 }]);
  });
});

// ── The invariant that keeps the vote from twitching ────────────────────────

describe("optimistic apply vs the server's authoritative SET", () => {
  function graph(): GraphData {
    return {
      block_votes: [0, 0, 0, 0, 0],
      block_vote_types: [[], [], [], [], []],
      block_vote_type_legend: [],
    } as unknown as GraphData;
  }

  it("a correct prediction makes the confirming delta a NO-OP", () => {
    const data = graph();
    expect(applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: 1, down: 0 }])).toBe(true);
    // The server's post-write truth for that block is the same 1 up.
    expect(applyBlockCounts(data, LABEL, { "4": [1, 0] })).toBe(false);
    expect(data.block_vote_types![4]).toEqual([[0, 1, 0]]);
    expect(data.block_votes![4]).toBe(1);
  });

  it("a flip predicted as a move lands as a no-op too", () => {
    const data = graph();
    applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: 1, down: 0 }]);
    applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: -1, down: 1 }]);
    expect(applyBlockCounts(data, LABEL, { "4": [0, 1] })).toBe(false);
    expect(data.block_vote_types![4]).toEqual([[0, 0, 1]]);
  });

  it("a WRONG prediction is corrected by the SET rather than compounded", () => {
    const data = graph();
    // Somebody else behind the same IP already held this block, so the real
    // count never moved — the blind spot the dedupe identity creates.
    applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: 1, down: 0 }]);
    expect(applyBlockCounts(data, LABEL, { "4": [1, 0] })).toBe(false);
    // A second, genuinely different truth still lands.
    expect(applyBlockCounts(data, LABEL, { "4": [2, 0] })).toBe(true);
    expect(data.block_vote_types![4]).toEqual([[0, 2, 0]]);
  });

  it("never drives a block count below zero", () => {
    const data = graph();
    expect(applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: -1, down: 0 }])).toBe(false);
    expect(data.block_votes![4]).toBe(0);
  });

  it("leaves blocks outside the prediction alone", () => {
    const data = graph();
    applyMyBlockVoteChange(data, LABEL, [{ block: 4, up: 1, down: 0 }]);
    expect(data.block_votes).toEqual([0, 0, 0, 0, 1]);
  });
});

// ── Surviving a background refresh ──────────────────────────────────────────

describe("an in-flight cast survives a background refresh", () => {
  it("keeps the vote when /api/my-votes lands mid-flight without it", async () => {
    // A snapshot READ before the cast reached the database: it knows nothing
    // about the vote, and used to delete it outright.
    let release!: (v: unknown) => void;
    const gate = new Promise((r) => { release = r; });
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => {
      await gate;
      return { ok: true, status: 200, json: async () => ({}) };
    }));

    const inFlight = castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: [blk(4, [1, 2])],
    });
    expect(getVote(MODE, 1, LABEL)).toBe(1);

    resetMapVotes(MODE, {}); // the stale snapshot: "you have voted on nothing"
    expect(getVote(MODE, 1, LABEL)).toBe(1); // …and the press stands

    release(null);
    await inFlight;
  });

  it("keeps the vote for a refresh that lands after the POST but before the echo", async () => {
    okFetch();
    await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: [blk(4, [1, 2])],
    });
    // The POST is done, but a my-votes request issued BEFORE it is only now
    // coming back — still blind to the row.
    resetMapVotes(MODE, {});
    expect(getVote(MODE, 1, LABEL)).toBe(1);
  });

  it("lets server truth win again once the cast's own delta has settled it", async () => {
    okFetch();
    await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: [blk(4, [1, 2])],
    });
    settlePendingCastsForDelta(MODE, LABEL, [1]);
    expect(pendingCastsFor(MODE)).toHaveLength(0);
    // No longer shielded: a genuine retraction elsewhere now applies.
    resetMapVotes(MODE, {});
    expect(getVote(MODE, 1, LABEL)).toBe(0);
  });

  it("shields the pending edge from reconcileEdge but not its neighbours", async () => {
    setVote(MODE, 2, LABEL, 1); // an older, settled vote on a sibling edge
    okFetch();
    await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: -1, blocks: [blk(4, [1, 2, 3])],
    });
    // Edge 2 was cleared by the plan and IS pending too, so both are shielded;
    // an unrelated edge the response does report still applies.
    reconcileEdge(MODE, 1, { [LABEL]: 1 });
    reconcileEdge(MODE, 3, { [LABEL]: 1 });
    expect(getVote(MODE, 1, LABEL)).toBe(-1); // in flight — untouched
    expect(getVote(MODE, 3, LABEL)).toBe(1); // not in flight — server wins
  });

  it("re-applies exactly what the optimistic press applied, over a fresh snapshot", async () => {
    okFetch();
    const blocks = [blk(4, [1, 2])];
    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks });

    // What the press did to the in-memory counts…
    const fresh = () => ({
      block_votes: [0, 0, 0, 0, 0], block_vote_types: [[], [], [], [], []],
      block_vote_type_legend: [],
    } as unknown as GraphData);
    const pressed = fresh();
    applyMyBlockVoteChange(pressed, LABEL, optimistic()[0].blockDeltas);

    // …a background /api/graph-votes install wipes, and the re-apply restores.
    const reinstalled = fresh();
    for (const cast of pendingCastsFor(MODE)) {
      applyMyBlockVoteChange(reinstalled, cast.label, cast.blockDeltas);
    }
    expect(reinstalled.block_votes).toEqual(pressed.block_votes);
    expect(reinstalled.block_vote_types).toEqual(pressed.block_vote_types);
  });
});

// ── Rollback ───────────────────────────────────────────────────────────────

describe("rollback", () => {
  it("undoes the store, the edge transitions AND the block move", async () => {
    setVote(MODE, 2, LABEL, 1); // will be cleared by the plan
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const res = await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: -1, blocks: [blk(4, [1, 2])],
    });
    expect(res.ok).toBe(false);
    expect(getVote(MODE, 1, LABEL)).toBe(0); // cast undone
    expect(getVote(MODE, 2, LABEL)).toBe(1); // clear undone

    const [forward, back] = optimistic();
    expect(forward.blockDeltas).toEqual([{ block: 4, up: -1, down: 1 }]);
    expect(back.blockDeltas).toEqual([{ block: 4, up: 1, down: -1 }]);
    // Applying both to a graph leaves it exactly where it started.
    const data = {
      block_votes: [0, 0, 0, 0, 5], block_vote_types: [[], [], [], [], [[0, 5, 0]]],
      block_vote_type_legend: [LABEL],
    } as unknown as GraphData;
    applyMyBlockVoteChange(data, LABEL, forward.blockDeltas);
    applyMyBlockVoteChange(data, LABEL, back.blockDeltas);
    expect(data.block_votes![4]).toBe(5);
    expect(data.block_vote_types![4]).toEqual([[0, 5, 0]]);
  });

  it("tells the person, rather than reverting in silence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1 });
    expect(rejections()).toHaveLength(1);
    expect(rejections()[0]).toMatch(/couldn't save your vote/i);
  });

  it("uses the server's own reason once, not twice", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 507, json: async () => ({ error: "Too many vote types" }),
    }));
    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1 });
    expect(rejections()).toEqual(["Too many vote types"]);
  });

  it("stays quiet behind the passcode gate, which is its own message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 401, json: async () => ({}),
    }));
    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1 });
    expect(rejections()).toEqual([]);
    expect(events.some((e) => e.type === "map-passcode-required")).toBe(true);
  });

  it("does NOT undo a later press on the same control", async () => {
    // Press 1 hangs, then fails. Press 2 lands cleanly in the meantime — its
    // state is what the user asked for last and must survive press 1's failure.
    let failFirst!: (e: unknown) => void;
    const first = new Promise((_, reject) => { failFirst = reject; });
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => first)
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    const blocks = [blk(4, [1])];
    const pending = castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks });
    expect(getVote(MODE, 1, LABEL)).toBe(1);

    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: -1, blocks });
    expect(getVote(MODE, 1, LABEL)).toBe(-1); // the newer press

    failFirst(new Error("timed out"));
    await pending;
    expect(getVote(MODE, 1, LABEL)).toBe(-1); // …still the newer press
    expect(rejections()).toEqual([]); // and no toast about a press since replaced
  });

  it("rolls back only the edges the server declined under the cap", async () => {
    okFetch({ capped: [2] });
    await castVotes({
      mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1,
      blocks: [blk(4, [1]), blk(5, [2])],
    });
    expect(getVote(MODE, 1, LABEL)).toBe(1);
    expect(getVote(MODE, 2, LABEL)).toBe(0);
    // Block 5's optimistic +1 is taken back; block 4's stands.
    const [forward, back] = optimistic();
    expect(forward.blockDeltas).toEqual([
      { block: 4, up: 1, down: 0 }, { block: 5, up: 1, down: 0 },
    ]);
    expect(back.blockDeltas).toEqual([{ block: 5, up: -1, down: 0 }]);
    // …and the declined edge stops shielding, since the server never took it.
    expect([...pendingCastsFor(MODE)[0].edges.keys()]).toEqual([1]);
  });
});
