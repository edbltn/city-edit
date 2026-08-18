import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { planBlockVote, voteButtonState, castVotes } from "./castVote";
import { singletonBlocks, type TouchedBlock } from "./blockSelection";
import { _resetPendingCasts } from "./pendingVotes";
import {
  getVote, setVote, setVotes, setVoteTypeMap, blockCoverage, _resetVoteStore,
} from "./voteStore";

const MODE = "walkways";
const LABEL = "Add bike lane";

/** Singleton fallback: each selection edge is its own block. */
const singles = (edgeIds: number[]) => singletonBlocks(edgeIds);
/** A real, id-carrying block — the only kind that moves a block aggregate. */
const blk = (key: number, edges: number[]): TouchedBlock => ({ key, edges });

beforeEach(() => {
  _resetVoteStore();
  _resetPendingCasts();
  setVoteTypeMap({ "10": LABEL });
});

// ── planBlockVote — the docs §4.2 press matrix ───────────────────────────────

describe("planBlockVote — singleton blocks (per-edge fallback)", () => {
  it("fresh cast: none/none → casts on every selection edge, clears nothing", () => {
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: 1, blocks: singles([1, 2, 3]),
    });
    expect(plan).toEqual({ targetDir: 1, castEdges: [1, 2, 3], clearEdges: [], blockDeltas: [] });
  });

  it("unvote-all: pressing the active direction clears my votes, casts nothing", () => {
    setVotes(MODE, [1, 2, 3], LABEL, 1);
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: 1, blocks: singles([1, 2, 3]),
    });
    expect(plan).toEqual({ targetDir: 0, castEdges: [], clearEdges: [1, 2, 3], blockDeltas: [] });
  });

  it("flip: opposite active → reverses via castEdges, never via clearEdges", () => {
    setVotes(MODE, [1, 2], LABEL, 1);
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2], label: LABEL, direction: -1, blocks: singles([1, 2]),
    });
    expect(plan.targetDir).toBe(-1);
    expect(plan.castEdges).toEqual([1, 2]); // reversals ride the cast
    expect(plan.clearEdges).toEqual([]); // selection edges are never "cleared"
  });

  it("partial coverage stays a cast; edges already at the direction are no-ops", () => {
    setVote(MODE, 1, LABEL, 1); // already up — skipped
    setVote(MODE, 2, LABEL, -1); // opposite — reversal
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2, 3], label: LABEL, direction: 1, blocks: singles([1, 2, 3]),
    });
    expect(plan.targetDir).toBe(1);
    expect(plan.castEdges).toEqual([2, 3]);
    expect(plan.clearEdges).toEqual([]);
  });

  it("downvotes a whole stretch (the route-against gesture)", () => {
    const plan = planBlockVote({
      mode: MODE, edgeIds: [4, 5], label: LABEL, direction: -1, blocks: singles([4, 5]),
    });
    expect(plan).toEqual({ targetDir: -1, castEdges: [4, 5], clearEdges: [], blockDeltas: [] });
  });
});

describe("planBlockVote — real blocks (selection ⊂ touched blocks)", () => {
  // Selection = edge 1; its block also carries edges 10 and 11.
  const BLOCKS = [blk(7, [1, 10, 11])];

  it("coverage counts any block edge, so a vote elsewhere in the block activates unvote", () => {
    setVote(MODE, 10, LABEL, 1); // block held via a NON-selection edge
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: BLOCKS,
    });
    // coverage(+) = all → unvote everything of mine in the touched blocks.
    expect(plan).toEqual({
      targetDir: 0, castEdges: [], clearEdges: [10],
      blockDeltas: [{ block: 7, up: -1, down: 0 }],
    });
  });

  it("clear-then-cast: my scattered block votes are cleared, the selection cast", () => {
    setVote(MODE, 10, LABEL, -1);
    setVote(MODE, 11, LABEL, 1);
    // Block holds both up and down (local pre-invariant state); pressing down:
    // coverage(down) = all → unvote-all. Pressing up on a block that is
    // "all down" is the interesting flip:
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: [blk(7, [1, 10])],
    });
    expect(plan.targetDir).toBe(1);
    expect(plan.castEdges).toEqual([1]);
    expect(plan.clearEdges).toEqual([10]); // outside the selection → cleared
  });

  it("unvote-all clears BOTH directions of my votes across the touched blocks", () => {
    setVote(MODE, 1, LABEL, 1);
    setVote(MODE, 10, LABEL, -1); // stray opposite elsewhere in the block
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1], label: LABEL, direction: 1, blocks: BLOCKS,
    });
    expect(plan.targetDir).toBe(0);
    expect(plan.clearEdges).toEqual([1, 10]);
  });

  it("multi-block: 'all' requires every touched block, else clear-then-cast", () => {
    setVote(MODE, 10, LABEL, 1); // covers block A only
    const plan = planBlockVote({
      mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1,
      blocks: [blk(7, [1, 10]), blk(8, [2, 20])],
    });
    expect(plan.targetDir).toBe(1); // coverage = some → still a cast
    expect(plan.castEdges).toEqual([1, 2]);
    expect(plan.clearEdges).toEqual([10]);
  });
});

describe("voteButtonState", () => {
  it("is active only when coverage of that direction is 'all'", () => {
    expect(voteButtonState({ up: "all", down: "none" }, 1)).toBe("active");
    expect(voteButtonState({ up: "all", down: "none" }, -1)).toBe("neutral");
    expect(voteButtonState({ up: "some", down: "none" }, 1)).toBe("neutral");
    expect(voteButtonState({ up: "none", down: "all" }, -1)).toBe("active");
  });

  it("agrees with blockCoverage end-to-end", () => {
    setVotes(MODE, [1, 2], LABEL, -1);
    const cov = blockCoverage(MODE, singles([1, 2]).map((b) => b.edges), LABEL);
    expect(voteButtonState(cov, -1)).toBe("active");
    expect(voteButtonState(cov, 1)).toBe("neutral");
  });
});

// ── castVotes — wire contract + local-store effects (fetch stubbed) ─────────

describe("castVotes", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  function respond(body: Record<string, unknown> = {}) {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => body });
  }

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    respond();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function postedBody(): Record<string, unknown> {
    expect(fetchMock).toHaveBeenCalledTimes(1);
    return JSON.parse(fetchMock.mock.calls[0][1].body as string);
  }

  it("POSTs the ORIGINAL selection edges + direction and applies locally", async () => {
    const res = await castVotes({ mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1 });
    expect(res.ok).toBe(true);
    expect(res.targetDir).toBe(1);
    const body = postedBody();
    expect(body.edge_ids).toEqual([1, 2]);
    expect(body.direction).toBe(1);
    expect(body.vote_type).toBe(LABEL);
    expect(getVote(MODE, 1, LABEL)).toBe(1);
    expect(getVote(MODE, 2, LABEL)).toBe(1);
  });

  it("unvote-all posts direction 0 with the selection and clears locally", async () => {
    setVotes(MODE, [1, 2], LABEL, 1);
    const res = await castVotes({ mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1 });
    expect(res.targetDir).toBe(0);
    const body = postedBody();
    expect(body.edge_ids).toEqual([1, 2]);
    expect(body.direction).toBe(0);
    expect(getVote(MODE, 1, LABEL)).toBe(0);
    expect(getVote(MODE, 2, LABEL)).toBe(0);
  });

  it("with blocks: clears my block votes outside the selection, casts the selection", async () => {
    setVote(MODE, 10, LABEL, 1); // elsewhere in the touched block
    const res = await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: -1, blocks: [blk(7, [1, 10])],
    });
    expect(res.targetDir).toBe(-1);
    expect(postedBody().edge_ids).toEqual([1]); // selection only — server expands
    expect(getVote(MODE, 1, LABEL)).toBe(-1);
    expect(getVote(MODE, 10, LABEL)).toBe(0); // optimistic clear
  });

  it("reconciles the server's `cleared` edges into the local store", async () => {
    setVote(MODE, 42, LABEL, 1); // a vote the server clears beyond our plan
    respond({ cleared: [42] });
    await castVotes({ mode: MODE, edgeIds: [1], label: LABEL, direction: 1 });
    expect(getVote(MODE, 42, LABEL)).toBe(0);
    expect(getVote(MODE, 1, LABEL)).toBe(1);
  });

  it("rolls back casts AND clears when the request fails", async () => {
    setVote(MODE, 10, LABEL, 1);
    fetchMock.mockRejectedValue(new Error("network down"));
    const res = await castVotes({
      mode: MODE, edgeIds: [1], label: LABEL, direction: -1, blocks: [blk(7, [1, 10])],
    });
    expect(res.ok).toBe(false);
    expect(getVote(MODE, 1, LABEL)).toBe(0); // cast rolled back
    expect(getVote(MODE, 10, LABEL)).toBe(1); // clear rolled back
  });

  it("rolls back only the edges the server declined under the cap", async () => {
    respond({ capped: [2] });
    const res = await castVotes({ mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1 });
    expect(res.ok).toBe(true);
    expect(res.changedEdges).toEqual([1]);
    expect(getVote(MODE, 1, LABEL)).toBe(1);
    expect(getVote(MODE, 2, LABEL)).toBe(0);
  });

  it("keeps an evicted takeover's local vote at the server's direction", async () => {
    respond({ evicted: { "2": 1 } });
    await castVotes({ mode: MODE, edgeIds: [1, 2], label: LABEL, direction: 1 });
    expect(getVote(MODE, 1, LABEL)).toBe(1);
    expect(getVote(MODE, 2, LABEL)).toBe(1); // vote is real for us
  });

  it("no-ops (no POST) on an empty selection", async () => {
    const res = await castVotes({ mode: MODE, edgeIds: [], label: LABEL, direction: 1 });
    expect(res.ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
