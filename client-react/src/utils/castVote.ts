// ==========================================================================
// The single vote-cast path — block-scoped clear-then-cast
// ==========================================================================
// Both the top-bar route cast and the in-map proposal +/- buttons call
// castVotes(). Semantics per docs/three-layer-model.md §4: coverage is computed
// over the selection's TOUCHED BLOCKS (edge-as-singleton fallback when no block
// artifacts exist). Pressing an already-active direction unvotes the whole
// touched-block set; anything else clears my same-type votes across those
// blocks and casts on exactly the selection edges. Optimistic (instant heatmap
// update via the `optimistic-vote` event GraphLayer applies) and self-healing
// (rolls back on failure; the server's authoritative deltas + `cleared`
// response reconcile the rest).

import { CONFIG } from "../config";
import { getMapSlug, getPasscodeToken } from "../map/runtime";
import { dlog, derror } from "./debugLog";
import { getVoterId } from "./voterIdentity";
import {
  blockCoverage,
  getVote,
  myVotesInBlocks,
  setVotes,
  type BlockCoverage,
  type VoteDirection,
} from "./voteStore";

/** Detail of the `optimistic-vote` window event GraphLayer listens for. */
export interface OptimisticVoteDetail {
  mode: string;
  label: string;
  edgeIds: number[];
  prevDir: number; // -1 | 0 | 1
  newDir: number; // -1 | 0 | 1
}

export interface CastResult {
  ok: boolean;
  /** The direction actually applied: the request direction, or 0 if unvoting. */
  targetDir: VoteDirection | 0;
  /** Edges whose state changed (cast + cleared). Empty if nothing to do. */
  changedEdges: number[];
}

export interface TransitionGroup {
  edges: number[];
  prevDir: number;
  newDir: number;
}

export interface BlockVotePlan {
  /** Direction actually applied: the request direction, or 0 when unvoting. */
  targetDir: VoteDirection | 0;
  /** Selection edges to set to targetDir (fresh casts + reversals; excludes
   *  edges already at targetDir — those are no-ops). */
  castEdges: number[];
  /** My existing same-type votes (in the touched blocks) to remove: everything
   *  on an unvote, everything outside the selection on a cast. */
  clearEdges: number[];
}

/**
 * Decide what a press should do — the pure heart of the block-scoped rule
 * (docs §4.1–4.2), extracted so it can be unit-tested without network/DOM.
 * `blocks` is the materialized touched-block edge lists; pass
 * `edgeIds.map((e) => [e])` for the no-blocks singleton fallback.
 *
 *   - coverage(direction) === 'all' → UNVOTE: clear every one of my `label`
 *     votes across the touched blocks, cast nothing (wire direction 0).
 *   - else → CLEAR-THEN-CAST: cast `direction` on the selection edges (edges
 *     already there are no-ops; opposites are reversals, NOT clears), and clear
 *     my `label` votes on touched-block edges outside the selection.
 */
export function planBlockVote(params: {
  mode: string;
  edgeIds: number[];
  label: string;
  direction: VoteDirection;
  blocks: ArrayLike<number>[];
}): BlockVotePlan {
  const { mode, edgeIds, label, direction, blocks } = params;
  const cov = blockCoverage(mode, blocks, label);
  const mine = myVotesInBlocks(mode, blocks, label);

  const active = (direction === 1 ? cov.up : cov.down) === "all";
  if (active) {
    return {
      targetDir: 0,
      castEdges: [],
      clearEdges: [...mine.keys()].sort((a, b) => a - b),
    };
  }
  const selection = new Set(edgeIds);
  return {
    targetDir: direction,
    castEdges: edgeIds.filter((e) => getVote(mode, e, label) !== direction),
    clearEdges: [...mine.keys()].filter((e) => !selection.has(e)).sort((a, b) => a - b),
  };
}

/** Button render state for a direction (docs §4.1): active iff every touched
 *  block already holds my vote in that direction (pressing = unvote). */
export function voteButtonState(coverage: BlockCoverage, dir: VoteDirection): "active" | "neutral" {
  return (dir === 1 ? coverage.up : coverage.down) === "all" ? "active" : "neutral";
}

function dispatchOptimistic(detail: OptimisticVoteDetail) {
  if (typeof window === "undefined" || detail.edgeIds.length === 0) return;
  window.dispatchEvent(new CustomEvent("optimistic-vote", { detail }));
}

/** Group edges by their CURRENT stored direction so each optimistic event (and
 *  its rollback) carries an accurate prevDir snapshot. */
function groupByPrevDir(
  mode: string,
  edgeIds: number[],
  label: string,
  newDir: number,
): TransitionGroup[] {
  const byPrev = new Map<number, number[]>();
  for (const e of edgeIds) {
    const prev = getVote(mode, e, label);
    if (prev === newDir) continue; // no-op — nothing to apply or roll back
    let bucket = byPrev.get(prev);
    if (!bucket) byPrev.set(prev, (bucket = []));
    bucket.push(e);
  }
  return [...byPrev.entries()].map(([prevDir, edges]) => ({ edges, prevDir, newDir }));
}

/**
 * Cast `direction` for `label` across `edgeIds` (the selection), with block
 * semantics from `blocks` (the touched blocks' materialized edge lists; omitted
 * → per-edge singleton blocks, identical to the old per-edge behavior).
 *
 * The POST always carries the ORIGINAL selection edges + the resolved direction
 * (0 = unvote); the server expands selection → touched blocks itself and
 * reports any extra edges it unvoted in `cleared`, which we fold back into the
 * local store (counts self-heal via the authoritative delta broadcast).
 */
export async function castVotes(params: {
  mode: string;
  edgeIds: number[];
  label: string;
  direction: VoteDirection;
  blocks?: ArrayLike<number>[];
  /** Kind of the selection being voted ("route" corridor vs single "point").
   *  Sent so the server can record the kind of a brand-new suggestion label —
   *  the creating cast is the only witness. Optional: casts on existing labels
   *  (proposal pins, route cards) don't need it. */
  pointType?: "route" | "point";
}): Promise<CastResult> {
  const { mode, label, direction } = params;
  const edges = [...new Set(params.edgeIds)].filter((e) => e != null);
  if (edges.length === 0 || !label) {
    return { ok: false, targetDir: direction, changedEdges: [] };
  }

  const blocks = params.blocks ?? edges.map((e) => [e]);
  const { targetDir, castEdges, clearEdges } = planBlockVote({
    mode, edgeIds: edges, label, direction, blocks,
  });
  dlog("cast", `press ${direction > 0 ? "+" : "−"} "${label}" →`,
    targetDir === 0 ? "UNVOTE-ALL" : `cast ${targetDir}`,
    { selection: edges.length, blocks: blocks.length,
      cast: castEdges.length, clear: clearEdges.length });

  // Optimistic transitions: clears (prev → 0) then casts (prev → targetDir),
  // grouped by prevDir so rollback can restore the exact prior state.
  const groups = [
    ...groupByPrevDir(mode, clearEdges, label, 0),
    ...groupByPrevDir(mode, castEdges, label, targetDir),
  ];
  const changedEdges = groups.flatMap((g) => g.edges);
  if (changedEdges.length === 0) {
    return { ok: true, targetDir, changedEdges: [] };
  }

  // Optimistic apply + local store update.
  for (const g of groups) {
    dispatchOptimistic({ mode, label, edgeIds: g.edges, prevDir: g.prevDir, newDir: g.newDir });
    setVotes(mode, g.edges, label, g.newDir as VoteDirection | 0);
  }

  const slug = getMapSlug();
  try {
    const body: Record<string, unknown> = {
      map: slug,
      mode,
      vote_type: label,
      direction: targetDir,
      edge_ids: edges, // the selection — the server expands to blocks itself
      voter_id: getVoterId(),
    };
    if (params.pointType) body.point_type = params.pointType;
    const token = getPasscodeToken(slug);
    if (token) body.passcode_token = token;

    const res = await fetch(`${CONFIG.apiUrl}/vote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) {
      window.dispatchEvent(new CustomEvent("map-passcode-required", { detail: { slug } }));
      throw new Error("Passcode required");
    }
    if (!res.ok) throw new Error(`Vote failed: ${res.status}`);

    const result = await res.json();
    dlog("cast", "server:", {
      changed: result?.changed?.length ?? 0, cleared: result?.cleared?.length ?? 0,
      capped: result?.capped?.length ?? 0, evicted: Object.keys(result?.evicted ?? {}).length,
    });

    // The server may decline some edges under the per-IP abuse cap, returning
    // them in `capped` (absent from `changed`, with no count broadcast). Roll
    // back our optimistic apply for those so the heatmap/button can't show a
    // vote the server never recorded.
    const declined = new Set<number>(result?.capped ?? []);
    if (declined.size > 0) {
      for (const g of groups) {
        const edges = g.edges.filter((e) => declined.has(e));
        if (edges.length === 0) continue;
        dispatchOptimistic({ mode, label, edgeIds: edges, prevDir: g.newDir, newDir: g.prevDir });
        setVotes(mode, edges, label, g.prevDir as VoteDirection | 0);
      }
    }

    // At the cap the server took over an existing vote instead of adding one
    // (`evicted`: edge → direction now owned by this device). The vote is real
    // for us — keep the local "you voted" state — but the TOTAL didn't move, so
    // undo the optimistic count bump (no server delta corrects it otherwise).
    const evicted: Record<string, number> = result?.evicted ?? {};
    for (const g of groups) {
      const edges = g.edges.filter((e) => evicted[String(e)] !== undefined);
      if (edges.length === 0) continue;
      dispatchOptimistic({ mode, label, edgeIds: edges, prevDir: g.newDir, newDir: g.prevDir });
      for (const e of edges) {
        setVotes(mode, [e], label, evicted[String(e)] as VoteDirection | 0);
      }
    }

    // The server expanded the selection to its touched blocks and reports every
    // edge it unvoted beyond the selection in `cleared`. Our optimistic plan
    // usually predicted these, but the server is authoritative (another tab may
    // hold votes this store never saw) — fold them in. Counts self-heal via the
    // authoritative [up, down] delta broadcast, so no optimistic event here.
    const cleared: number[] = Array.isArray(result?.cleared) ? result.cleared : [];
    if (cleared.length > 0) {
      setVotes(mode, cleared, label, 0);
    }

    return { ok: true, targetDir, changedEdges: changedEdges.filter((e) => !declined.has(e)) };
  } catch (err) {
    // Roll back the optimistic apply and the local store so a failed sync can't
    // leave the heatmap or button state showing a vote the server never took.
    for (const g of groups) {
      dispatchOptimistic({ mode, label, edgeIds: g.edges, prevDir: g.newDir, newDir: g.prevDir });
      setVotes(mode, g.edges, label, g.prevDir as VoteDirection | 0);
    }
    derror("cast", "failed, rolled back:", err);
    return { ok: false, targetDir, changedEdges: [] };
  }
}
