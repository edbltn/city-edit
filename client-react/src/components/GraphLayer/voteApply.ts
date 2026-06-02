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
  adj: number[][],
  affected: Iterable<number>,
) {
  const edgeVotes = data.edge_votes ?? [];
  const nodeVotes = data.node_votes ?? [];
  const edgeVoteTypes = data.edge_vote_types ?? [];
  const nodeVoteTypes = data.node_vote_types ?? [];

  for (const nid of affected) {
    const nodeEdges = adj[nid];
    if (!nodeEdges) continue;
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
 * INCREMENT one vote on each edge. `dir` is +1 (up) / −1 (down); `reversed`
 * means a prior opposite vote is being flipped (net delta ±2).
 */
export function applyEdgeVoteChange(
  data: VoteData,
  adj: number[][],
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

    const edge = data.edges[eid];
    if (edge) { affected.add(edge[0]); affected.add(edge[1]); }
  }

  rederiveNodes(data, adj, affected);
}

/**
 * SET the authoritative [up, down] for `vtLabel` on each edge in `vtCounts`
 * (keyed by edge id). Idempotent: re-applying the same counts is a no-op, so
 * the caster's optimistic guess is corrected rather than compounded. A vote
 * type that drops to 0/0 is removed from the edge's breakdown.
 */
export function applyAuthoritativeCounts(
  data: VoteData,
  adj: number[][],
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

    const edge = data.edges[eid];
    if (edge) { affected.add(edge[0]); affected.add(edge[1]); }
  }

  rederiveNodes(data, adj, affected);
}
