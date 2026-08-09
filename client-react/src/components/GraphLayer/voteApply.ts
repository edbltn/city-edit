// Referenced by: docs/algorithms/05-heat-coloring.md
//   (topProposalDiffs — the signed number behind every block's colour).
// ==========================================================================
// Vote application (pure)
// ==========================================================================
// Mutates the in-memory GraphData vote arrays. Two entry points:
//
//   applyEdgeVoteChange      — INCREMENT a vote (optimistic update + legacy
//                              bulk-cast WS deltas). `reversed` moves one vote
//                              across directions (net ±2).
//   applyAuthoritativeCounts — SET a vote type's [up, down] on edges to the
//                              server's post-write truth. Idempotent, so a
//                              caster's optimistic guess can't drift or
//                              double-count — re-applying the same delta is a
//                              no-op. This is the authoritative path for
//                              directional (modal +/−) votes.
//
// Per-type breakdown is [legendIdx, up, down]; `edge_votes`/`node_votes` are
// net (up − down). Node values are derived (per type: the adjacent edge with
// the largest net; node total: the largest adjacent edge net). See
// voteApply.test.ts.

import type { GraphData } from "../../types";
import { type NodeAdj, edgeFrom, edgeTo, adjEdgesOf } from "./graphTopology";

type VoteData = GraphData;

/** Index of `vtLabel` in the legend, appending it if missing (−1 if blank). */
function legendIndex(data: VoteData, vtLabel: string): number {
  if (!vtLabel) return -1;
  const legend = data.vote_type_legend ?? (data.vote_type_legend = []);
  let idx = legend.indexOf(vtLabel);
  if (idx === -1) {
    idx = legend.length;
    legend.push(vtLabel);
  }
  return idx;
}

/** Re-derive node totals + per-type breakdown for the affected nodes. */
export function rederiveNodes(
  data: VoteData,
  adj: NodeAdj,
  affected: Iterable<number>,
) {
  const edgeVotes = data.edge_votes ?? [];
  const nodeVotes = data.node_votes ?? [];
  const edgeVoteTypes = data.edge_vote_types ?? [];
  const nodeVoteTypes = data.node_vote_types ?? [];

  for (const nid of affected) {
    if (nid >= adj.start.length - 1) continue;
    const nodeEdges = adjEdgesOf(adj, nid);
    let best = 0;
    const merged = new Map<number, [number, number]>();
    for (const eid of nodeEdges) {
      const v = edgeVotes[eid] || 0;
      if (v > best) best = v;
      const evtPairs = edgeVoteTypes[eid];
      if (evtPairs) {
        for (const [li, u, d] of evtPairs) {
          const cur = merged.get(li);
          if (!cur || u - d > cur[0] - cur[1]) merged.set(li, [u, d]);
        }
      }
    }
    nodeVotes[nid] = best;
    nodeVoteTypes[nid] = [...merged.entries()]
      .sort((a, b) => b[1][0] - b[1][1] - (a[1][0] - a[1][1]))
      .map(([li, [u, d]]) => [li, u, d] as [number, number, number]);
  }
}

/**
 * Apply THIS user's vote transition (`prevDir` → `newDir`) on each edge to the
 * global in-memory counts. Generalizes applyEdgeVoteChange to the full set of
 * transitions a unified cast can produce:
 *   0 → ±1   fresh vote      (increment one side)
 *  ±1 → 0    remove vote     (decrement one side)
 *  ±1 → ∓1   reversal        (move one vote across sides)
 * Optimistic only — the server's authoritative `vtCounts` SET later corrects
 * this to truth, so a wrong guess can't persist.
 */
export function applyMyVoteChange(
  data: VoteData,
  adj: NodeAdj,
  edgeIds: number[],
  vtLabel: string,
  prevDir: number,
  newDir: number,
) {
  if (prevDir === newDir || !data.edge_votes) return;
  const edgeVotes = data.edge_votes;
  const edgeVoteTypes = data.edge_vote_types ?? (data.edge_vote_types = []);
  const li = legendIndex(data, vtLabel);
  if (li < 0) return;

  // Per-edge delta to the [up, down] pair for this transition.
  let upDelta = 0;
  let downDelta = 0;
  if (prevDir > 0) upDelta -= 1;
  else if (prevDir < 0) downDelta -= 1;
  if (newDir > 0) upDelta += 1;
  else if (newDir < 0) downDelta += 1;
  const netDelta = upDelta - downDelta;

  const affected = new Set<number>();
  for (const eid of edgeIds) {
    if (eid >= edgeVotes.length) continue;
    edgeVotes[eid] = (edgeVotes[eid] || 0) + netDelta;

    const pairs = edgeVoteTypes[eid] || [];
    let triple = pairs.find(([l]) => l === li);
    if (!triple) { triple = [li, 0, 0]; pairs.push(triple); }
    triple[1] = Math.max(0, triple[1] + upDelta);
    triple[2] = Math.max(0, triple[2] + downDelta);
    edgeVoteTypes[eid] = pairs.filter((t) => t[1] !== 0 || t[2] !== 0)
      .sort((a, b) => b[1] - b[2] - (a[1] - a[2]));

    if (eid < (data.nEdges ?? 0)) { affected.add(edgeFrom(data, eid)); affected.add(edgeTo(data, eid)); }
  }

  rederiveNodes(data, adj, affected);
}

/**
 * INCREMENT one vote on each edge. `dir` is +1 (up) / −1 (down); `reversed`
 * means a prior opposite vote is being flipped (net delta ±2).
 */
export function applyEdgeVoteChange(
  data: VoteData,
  adj: NodeAdj,
  edgeIds: number[],
  vtLabel: string,
  dir: number = 1,
  reversed: boolean = false,
) {
  if (!data.edge_votes) return;
  const edgeVotes = data.edge_votes;
  const edgeVoteTypes = data.edge_vote_types ?? (data.edge_vote_types = []);
  const li = legendIndex(data, vtLabel);

  const up = dir >= 0;
  const netDelta = (up ? 1 : -1) * (reversed ? 2 : 1);
  const affected = new Set<number>();

  for (const eid of edgeIds) {
    if (eid >= edgeVotes.length) continue;
    edgeVotes[eid] = (edgeVotes[eid] || 0) + netDelta;

    if (li >= 0) {
      const pairs = edgeVoteTypes[eid] || [];
      let triple = pairs.find(([l]) => l === li);
      if (!triple) { triple = [li, 0, 0]; pairs.push(triple); }
      if (up) { triple[1] += 1; if (reversed) triple[2] -= 1; }
      else { triple[2] += 1; if (reversed) triple[1] -= 1; }
      if (triple[1] < 0) triple[1] = 0;
      if (triple[2] < 0) triple[2] = 0;
      pairs.sort((a, b) => b[1] - b[2] - (a[1] - a[2]));
      edgeVoteTypes[eid] = pairs;
    }

    if (eid < (data.nEdges ?? 0)) { affected.add(edgeFrom(data, eid)); affected.add(edgeTo(data, eid)); }
  }

  rederiveNodes(data, adj, affected);
}

/**
 * SET the authoritative [up, down] for `vtLabel` on each edge in `vtCounts`
 * (keyed by edge id). Idempotent: re-applying the same counts is a no-op, so
 * the caster's optimistic guess is corrected rather than compounded. A vote
 * type that drops to 0/0 is removed from the edge's breakdown.
 */
/**
 * SET the authoritative deduped [up, down] for `vtLabel` on each BLOCK in
 * `blockCounts` (the delta's block twin of vtCounts — see
 * docs/three-layer-model.md §2.4). Idempotent like the edge SET. Returns true
 * when anything changed so the caller can re-broadcast the block heat.
 */
/**
 * Per-block SIGNED heat value: the vote differential (up − down) of the
 * block's top-ranked proposal, ranked BY differential — i.e. the max
 * differential across the block's vote types. Negative when even the block's
 * best proposal is net-against; zero (invisible) when signal cancels out.
 * Blocks with activity but no per-type breakdown (legacy untyped votes) fall
 * back to their deduped total. Returns the array plus both arm ceilings for
 * the renderer's two-sided log normalization.
 *
 * `allowed` is the legend-visibility mask from the vote-type filter (see
 * map/voteTypeFilter.legendVisibilityMask): 1 = the type is toggled on, 0 =
 * off. Pass null (the default, and what an unfiltered map always passes) to
 * rank across every type. With a mask, a block ranks only its visible types
 * and goes cold (0) when none of them is on — and the untyped fallback is
 * suppressed too: a legacy block's total can't be attributed to any type, so
 * showing it while the user has filtered would be heat they didn't ask for.
 */
export function topProposalDiffs(
  blockVotes: ArrayLike<number>,
  blockVoteTypes?: ([number, number, number][] | undefined)[] | null,
  allowed?: Uint8Array | null,
): { diff: Int32Array; maxPos: number; maxNeg: number } {
  const n = blockVotes.length;
  const diff = new Int32Array(n);
  let maxPos = 0;
  let maxNeg = 0;
  for (let b = 0; b < n; b++) {
    const entries = blockVoteTypes?.[b];
    let d = 0;
    if (entries && entries.length > 0) {
      let found = false;
      for (let i = 0; i < entries.length; i++) {
        // Out-of-range index = a type appended to the legend after the mask was
        // built (an optimistic cast); nothing has hidden it, so it stays visible.
        const li = entries[i][0];
        if (allowed && li < allowed.length && !allowed[li]) continue;
        const v = entries[i][1] - entries[i][2];
        if (!found || v > d) { d = v; found = true; }
      }
      if (!found) d = 0;
    } else if (!allowed) {
      d = blockVotes[b] || 0;
    }
    diff[b] = d;
    if (d > maxPos) maxPos = d;
    else if (-d > maxNeg) maxNeg = -d;
  }
  return { diff, maxPos, maxNeg };
}

export function applyBlockCounts(
  data: VoteData,
  vtLabel: string,
  blockCounts: Record<string, [number, number]>,
): boolean {
  const blockVotes = data.block_votes;
  const blockVoteTypes = data.block_vote_types;
  if (!blockVotes || !blockVoteTypes) return false;
  const legend = data.block_vote_type_legend ?? (data.block_vote_type_legend = []);
  let li = legend.indexOf(vtLabel);
  if (li < 0) {
    li = legend.length;
    legend.push(vtLabel);
  }

  let changed = false;
  for (const [bidStr, counts] of Object.entries(blockCounts)) {
    const bid = Number(bidStr);
    if (bid < 0 || bid >= blockVotes.length) continue;
    const up = counts[0] ?? 0;
    const down = counts[1] ?? 0;

    const pairs = blockVoteTypes[bid] || [];
    const existing = pairs.find(([l]) => l === li);
    // block_votes stays TOTAL deduped activity (up + down), mirroring server
    // block_votes.build_block_arrays — it's the legacy-fallback heat for blocks
    // without a per-type breakdown. Display heat itself is the top-proposal
    // DIFFERENTIAL, derived from blockVoteTypes in broadcastBlockVotes.
    const oldTotal = existing ? existing[1] + existing[2] : 0;
    if (existing && existing[1] === up && existing[2] === down) continue;

    let next = pairs.filter(([l]) => l !== li);
    if (up !== 0 || down !== 0) next.push([li, up, down]);
    next.sort((a, b) => b[1] + b[2] - (a[1] + a[2]));
    blockVoteTypes[bid] = next;
    blockVotes[bid] = (blockVotes[bid] || 0) + (up + down - oldTotal);
    changed = true;
  }
  return changed;
}

export function applyAuthoritativeCounts(
  data: VoteData,
  adj: NodeAdj,
  vtLabel: string,
  vtCounts: Record<string, [number, number]>,
) {
  if (!data.edge_votes) return;
  const edgeVotes = data.edge_votes;
  const edgeVoteTypes = data.edge_vote_types ?? (data.edge_vote_types = []);
  const li = legendIndex(data, vtLabel);
  if (li < 0) return;

  const affected = new Set<number>();
  for (const [eidStr, counts] of Object.entries(vtCounts)) {
    const eid = Number(eidStr);
    if (eid >= edgeVotes.length) continue;
    const up = counts[0] ?? 0;
    const down = counts[1] ?? 0;

    const pairs = edgeVoteTypes[eid] || [];
    const existing = pairs.find(([l]) => l === li);
    const oldNet = existing ? existing[1] - existing[2] : 0;

    let next = pairs.filter(([l]) => l !== li);
    if (up !== 0 || down !== 0) next.push([li, up, down]);
    next.sort((a, b) => b[1] - b[2] - (a[1] - a[2]));
    edgeVoteTypes[eid] = next;

    edgeVotes[eid] = (edgeVotes[eid] || 0) + (up - down - oldNet);

    if (eid < (data.nEdges ?? 0)) { affected.add(edgeFrom(data, eid)); affected.add(edgeTo(data, eid)); }
  }

  rederiveNodes(data, adj, affected);
}
