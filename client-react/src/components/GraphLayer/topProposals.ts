// ==========================================================================
// Top-proposal selection
// ==========================================================================
// Pure logic (no React/Leaflet) for choosing which segments get a "Top
// Proposal" indicator. The path is four explicit steps:
//
//   1. computeVoteTypeWinners — for each vote type, the top `perTypeLimit`
//      edges by NET (up − down) support. Net ≤ 0 is excluded.
//   2. dedupeWinnersByEdge   — collapse winners that share an edge to a single
//      representative (tiebreak), so one edge shows one indicator and occupies
//      one slot.
//   3. spaceOutWinners       — greedy per-type non-max suppression: keep a
//      winner only if no STRONGER same-type winner already sits within
//      `minSpacingMeters`. Collapses a hot corridor (its top edges are
//      adjacent segments) to a single pin instead of a stack of identical ones.
//   4. applyTopProposalLimit — sort by net (tiebreak) and cap at `limit`.
//
// `selectTopProposals` runs all four. See topProposals.test.ts.
//
// Steps 1–2 scan the full edge list; 3–4 operate only on the handful of
// surviving candidates, so the spacing pass is O(candidates²) over a few dozen
// — negligible even though selection re-runs on every incoming vote.

import type { GraphData } from "../../types";
import { type GraphTopology, nodeLat, nodeLon, edgeFrom, edgeTo } from "./graphTopology";

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
 * Step 1 — for each vote type, the top `perTypeLimit` edges by net support
 * (highest first). Vote types whose best edge is net ≤ 0 are dropped (a
 * net-downvoted proposal is not a "top proposal").
 */
export function computeVoteTypeWinners(
  legend: string[],
  edgeVoteTypes: [number, number, number][][],
  perTypeLimit = 1
): VoteTypeWinner[] {
  if (!legend.length || !edgeVoteTypes.length) return [];

  const edgesByType = new Map<number, { edgeIdx: number; count: number }[]>();
  for (let edgeIdx = 0; edgeIdx < edgeVoteTypes.length; edgeIdx++) {
    const pairs = edgeVoteTypes[edgeIdx];
    if (!pairs) continue;
    for (const [legendIdx, up, down] of pairs) {
      const count = up - down;
      if (count <= 0) continue;
      const list = edgesByType.get(legendIdx);
      if (list) list.push({ edgeIdx, count });
      else edgesByType.set(legendIdx, [{ edgeIdx, count }]);
    }
  }

  const winners: VoteTypeWinner[] = [];
  for (const [legendIdx, edges] of edgesByType) {
    const label = legend[legendIdx];
    if (!label) continue;
    edges.sort((a, b) => b.count - a.count);
    for (const { edgeIdx, count } of edges.slice(0, perTypeLimit)) {
      winners.push({ legendIdx, label, edgeIdx, count });
    }
  }
  return winners;
}

/**
 * The single highest net-voted (up − down) vote-type label among a set of edges
 * (a path), or null when none has positive net support. Used to pick a sensible
 * default vote type when a deep link's requested type isn't valid for the map —
 * independent of the display-oriented top-proposal selection above.
 */
export function topLabelForEdges(
  legend: string[],
  edgeVoteTypes: [number, number, number][][],
  edgeIds: number[]
): string | null {
  if (!legend.length || !edgeIds.length || !edgeVoteTypes.length) return null;

  const netByType = new Map<number, number>();
  for (const edgeId of edgeIds) {
    const pairs = edgeVoteTypes[edgeId];
    if (!pairs) continue;
    for (const [legendIdx, up, down] of pairs) {
      netByType.set(legendIdx, (netByType.get(legendIdx) ?? 0) + up - down);
    }
  }

  let bestIdx = -1;
  let bestNet = 0; // strictly positive net required
  for (const [legendIdx, net] of netByType) {
    if (net > bestNet) {
      bestNet = net;
      bestIdx = legendIdx;
    }
  }
  return bestIdx >= 0 ? (legend[bestIdx] ?? null) : null;
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

/**
 * Maps an edge index to its midpoint `[lat, lng]`, or null when the edge/nodes
 * can't be resolved. `spaceOutWinners` uses this to measure spacing without the
 * pure module needing to know the GraphData layout.
 */
export type EdgePosition = (edgeIdx: number) => [number, number] | null;

const EARTH_RADIUS_M = 6_371_000;

/** Fast equirectangular distance in meters — exact enough at city scale. */
function metersBetween(
  a: [number, number],
  b: [number, number]
): number {
  const meanLatRad = (((a[0] + b[0]) / 2) * Math.PI) / 180;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLng = (((b[1] - a[1]) * Math.PI) / 180) * Math.cos(meanLatRad);
  return EARTH_RADIUS_M * Math.hypot(dLat, dLng);
}

/**
 * Builds an `EdgePosition` from a typed-array topology: an edge's midpoint is
 * the mean of its two endpoint nodes.
 */
export function edgeMidpointResolver(
  data: GraphTopology | null
): EdgePosition {
  return (edgeIdx) => {
    if (!data || edgeIdx >= data.nEdges) return null;
    const from = edgeFrom(data, edgeIdx);
    const to = edgeTo(data, edgeIdx);
    if (from >= data.nNodes || to >= data.nNodes) return null;
    return [
      (nodeLat(data, from) + nodeLat(data, to)) / 2,
      (nodeLon(data, from) + nodeLon(data, to)) / 2,
    ];
  };
}

/**
 * Step 3 — greedy per-type non-max suppression. Processes winners strongest
 * first (net, then shuffle) and drops any whose edge midpoint lies within
 * `minSpacingMeters` of an already-kept winner of the SAME vote type. Different
 * types never suppress each other (a bike-lane pin and a tree pin can sit side
 * by side — they're genuinely distinct proposals). Winners whose position can't
 * be resolved are kept (never silently dropped on missing geometry).
 */
export function spaceOutWinners(
  winners: VoteTypeWinner[],
  salt: number,
  positionOf: EdgePosition,
  minSpacingMeters: number
): VoteTypeWinner[] {
  if (minSpacingMeters <= 0) return winners.slice();

  const ordered = winners
    .slice()
    .sort((a, b) => compareWinners(a, b, salt));

  // Accepted midpoints grouped by vote type, so each candidate only checks
  // same-type neighbors.
  const keptPosByType = new Map<number, [number, number][]>();
  const kept: VoteTypeWinner[] = [];

  for (const w of ordered) {
    const pos = positionOf(w.edgeIdx);
    if (!pos) {
      kept.push(w);
      continue;
    }
    const neighbors = keptPosByType.get(w.legendIdx);
    const tooClose = neighbors?.some(
      (p) => metersBetween(p, pos) < minSpacingMeters
    );
    if (tooClose) continue;
    if (neighbors) neighbors.push(pos);
    else keptPosByType.set(w.legendIdx, [pos]);
    kept.push(w);
  }
  return kept;
}

/** Step 4 — sort by net (tiebreak shuffle) and cap at `limit`. */
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

// Up to this many edges per vote type feed the spacing step, so a popular type
// can surface several distinct LOCATIONS (not just its single best edge). The
// spacing pass then collapses any that land on the same corridor.
export const TOP_PROPOSALS_PER_TYPE = 6;

// Two top proposals of the same vote type closer than this collapse to the
// stronger one — kills the "3 identical pins stacked on one avenue" look. ~one
// long NYC avenue block; tune for denser/sparser networks.
export const TOP_PROPOSAL_MIN_SPACING_M = 300;

/**
 * Full selection path: top-N-per-type winners → one per edge (tiebreak) →
 * per-type spatial spacing → top `limit` by net. Each surviving edge appears
 * once and consumes one slot.
 */
export function selectTopProposals(
  data:
    | (Pick<GraphData, "vote_type_legend" | "edge_vote_types"> & GraphTopology)
    | null,
  salt: number,
  limit: number,
  minSpacingMeters = TOP_PROPOSAL_MIN_SPACING_M
): VoteTypeWinner[] {
  if (!data) return [];
  const perType = computeVoteTypeWinners(
    data.vote_type_legend ?? [],
    data.edge_vote_types ?? [],
    TOP_PROPOSALS_PER_TYPE
  );
  const perEdge = dedupeWinnersByEdge(perType, salt);
  const spaced = spaceOutWinners(
    perEdge, salt, edgeMidpointResolver(data), minSpacingMeters
  );
  return applyTopProposalLimit(spaced, salt, limit);
}
