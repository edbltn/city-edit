// ==========================================================================
// Top-proposal selection
// ==========================================================================
// Pure logic (no React/Leaflet) for choosing which segments get a "Top
// Proposal" indicator. The path is three explicit steps:
//
//   1. computeVoteTypeWinners — for each vote type, the edge where it has the
//      highest NET (up − down) support. Net ≤ 0 is excluded.
//   2. dedupeWinnersByEdge   — collapse winners that share an edge to a single
//      representative (tiebreak), so one edge shows one indicator and occupies
//      one slot.
//   3. applyTopProposalLimit — sort by net (tiebreak) and cap at `limit`.
//
// `selectTopProposals` runs all three. See topProposals.test.ts.

import type { GraphData } from "../../types";

export interface VoteTypeWinner {
  legendIdx: number;
  label: string;
  edgeIdx: number;
  count: number; // net (up − down) for this vote type on the winning edge
}

/**
 * Deterministic tiebreak key, stable for a given (label, salt). A fresh salt
 * per page load gives equal-net proposals different exposure across visits
 * without reshuffling within a session.
 */
export function shuffleKey(label: string, salt: number): number {
  let h = salt | 0;
  for (let i = 0; i < label.length; i++) {
    h = ((h * 31) + label.charCodeAt(i)) | 0;
  }
  return h;
}

/** Order winners: higher net first, then deterministic shuffle by label. */
export function compareWinners(
  a: VoteTypeWinner,
  b: VoteTypeWinner,
  salt: number
): number {
  if (b.count !== a.count) return b.count - a.count;
  return shuffleKey(a.label, salt) - shuffleKey(b.label, salt);
}

/**
 * Step 1 — for each vote type, the single edge where its net support is
 * highest. Vote types whose best edge is net ≤ 0 are dropped (a net-downvoted
 * proposal is not a "top proposal").
 */
export function computeVoteTypeWinners(
  legend: string[],
  edgeVoteTypes: [number, number, number][][]
): VoteTypeWinner[] {
  if (!legend.length || !edgeVoteTypes.length) return [];

  const bestByType = new Map<number, { edgeIdx: number; count: number }>();
  for (let edgeIdx = 0; edgeIdx < edgeVoteTypes.length; edgeIdx++) {
    const pairs = edgeVoteTypes[edgeIdx];
    if (!pairs) continue;
    for (const [legendIdx, up, down] of pairs) {
      const count = up - down;
      const existing = bestByType.get(legendIdx);
      if (!existing || count > existing.count) {
        bestByType.set(legendIdx, { edgeIdx, count });
      }
    }
  }

  const winners: VoteTypeWinner[] = [];
  for (const [legendIdx, { edgeIdx, count }] of bestByType) {
    const label = legend[legendIdx];
    if (!label || count <= 0) continue;
    winners.push({ legendIdx, label, edgeIdx, count });
  }
  return winners;
}

/**
 * Step 2 — collapse winners on the SAME edge to one representative (tiebreak:
 * higher net, then shuffle). Guarantees one indicator per edge so a single hot
 * edge can't occupy multiple slots in the limit.
 */
export function dedupeWinnersByEdge(
  winners: VoteTypeWinner[],
  salt: number
): VoteTypeWinner[] {
  const bestByEdge = new Map<number, VoteTypeWinner>();
  for (const w of winners) {
    const cur = bestByEdge.get(w.edgeIdx);
    if (!cur || compareWinners(w, cur, salt) < 0) {
      bestByEdge.set(w.edgeIdx, w);
    }
  }
  return [...bestByEdge.values()];
}

/** Step 3 — sort by net (tiebreak shuffle) and cap at `limit`. */
export function applyTopProposalLimit(
  winners: VoteTypeWinner[],
  salt: number,
  limit: number
): VoteTypeWinner[] {
  return winners
    .slice()
    .sort((a, b) => compareWinners(a, b, salt))
    .slice(0, limit);
}

/**
 * Full selection path: per-type winners → one per edge (tiebreak) → top
 * `limit` by net. Each surviving edge appears once and consumes one slot.
 */
export function selectTopProposals(
  data: Pick<GraphData, "vote_type_legend" | "edge_vote_types"> | null,
  salt: number,
  limit: number
): VoteTypeWinner[] {
  if (!data) return [];
  const perType = computeVoteTypeWinners(
    data.vote_type_legend ?? [],
    data.edge_vote_types ?? []
  );
  const perEdge = dedupeWinnersByEdge(perType, salt);
  return applyTopProposalLimit(perEdge, salt, limit);
}
