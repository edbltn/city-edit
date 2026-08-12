// Algorithm doc: docs/algorithms/02-top-proposals.md
// ==========================================================================
// Point-based top proposals (PBTPs) — selection
// ==========================================================================
// Terminology (docs/three-layer-model.md §3.1): a PBTP is a POINT-based top
// proposal — one hot block, shown as a square pin on the block's strongest
// edge. Its route-based counterpart is the RBTP (a hot corridor, diamond pin)
// in routeProposals.ts.
//
// Pure logic (no React/Leaflet) for choosing which segments get a "Top
// Proposal" indicator. The path is five explicit steps:
//
//   1. computeVoteTypeWinners — for each POINT-kind vote type, the top
//      `perTypeLimit` BLOCKS by DEDUPED net (up − down) support. Votes are
//      STORED on edges but COUNTED on blocks (docs/three-layer-model.md §2),
//      and the count that matters is DISTINCT DEVICES per (block, vote type,
//      direction) — `block_vote_types`, built server-side by block_votes.py.
//      Summing the block's edge nets instead would rank a block by how many
//      edges one person's route happened to cover (docs/algorithms/07-counts.md
//      — "why summing these across a selection over-counts people"); the edge
//      scan here therefore enumerates which (block, type) pairs exist and picks
//      each block's REPRESENTATIVE edge — its strongest for that type, so the
//      pin has a real location — while the ranking value comes from the deduped
//      arrays. Block net ≤ 0 is excluded, and so are ROUTE-kind types (they
//      surface as RBTPs; a route type's hot edge is a corridor fragment, not a
//      point proposal). Types of unknown kind (legacy suggestions never
//      flagged) stay eligible.
//   2. dedupeWinnersByEdge   — collapse winners that share an edge to a single
//      representative (tiebreak), so one edge shows one indicator and occupies
//      one slot.
//   3. dedupeWinnersByBlock  — at most ONE pin per street block, across ALL
//      vote types (tiebreak keeps the strongest). Step 1 already gives each
//      type one candidate per block; this is the cross-TYPE half of the same
//      rule — two pins on one block read as clutter even when their types
//      differ.
//   4. spaceOutWinners       — greedy per-type non-max suppression: keep a
//      winner only if no STRONGER same-type winner already sits within
//      `minSpacingMeters`. Collapses a hot corridor (its top edges are
//      adjacent segments) to a single pin instead of a stack of identical ones.
//      Same-type spacing is deliberately WIDER than the cross-type block grain.
//   5. applyTopProposalLimit — sort by net (tiebreak) and cap at `limit`.
//
// `selectTopProposals` runs all five. See topProposals.test.ts.
//
// Step 1 makes ONE pass over the edge list, tallying into a per-type map; 2–5
// operate only on the handful of surviving candidates, so the spacing pass is
// O(candidates²) over a few dozen — negligible even though selection re-runs on
// every incoming vote.

import type { GraphData } from "../../types";
import { type GraphTopology, blockKeyOf, nodeLat, nodeLon, edgeFrom, edgeTo } from "./graphTopology";

export interface VoteTypeWinner {
  legendIdx: number;
  label: string;
  /** Where the pin is drawn: the winning block's strongest edge for this type. */
  edgeIdx: number;
  count: number; // deduped net (up − down) for this vote type on the whole block
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
 * A label's route/point kind, or null when unknown. Point-based selection
 * excludes "route" labels (they belong to the RBTP family); route-based
 * extraction excludes "point" labels. Null (a legacy suggestion the DB never
 * flagged) stays eligible for BOTH — dropping it from both would hide real
 * votes from every proposal surface.
 */
export type VoteTypeKindResolver = (label: string) => "route" | "point" | null;

/**
 * Whether a vote type is toggled ON in the legend (map/voteTypeFilter). A type
 * toggled off is excluded from selection entirely rather than filtered out of
 * the finished list, so hiding nine of ten types PROMOTES the tenth's runners-up
 * into the freed slots instead of leaving the map near-empty.
 */
export type VoteTypeVisibility = (label: string) => boolean;

/**
 * Support floor for a PBTP pin: an edge counts as a top proposal with STRICTLY
 * MORE than this many net votes. Zero means any positive net qualifies — a
 * single vote earns a pin. The ranking (net descending) plus TOP_PROPOSAL_LIMIT
 * is what keeps the map legible now, rather than a threshold: the strongest
 * proposals win the slots, and a quiet map still shows what little it has.
 *
 * A map may raise it for itself via `topProposalMinNet` (map/runtime.ts).
 *
 * This is the PIN floor only. Corridors have their own, much higher default —
 * ROUTE_PROPOSAL_MIN_NET below — because a route drawn from one vote is noise,
 * not a proposal.
 */
export const TOP_PROPOSAL_MIN_NET = 0;

/**
 * Support floor for an RBTP corridor, as a path score (sum of edge nets). Kept
 * where the shared floor used to sit: corridor growth off one or two votes
 * wanders, and the 2026-07-30 noise-proposal fix depends on this bar. A map's
 * `topProposalMinNet` override still wins when it is set (the imported
 * nyc-proposals map sets 0, since each official project is one authoritative
 * vote); see GraphLayer's createRouteProposalJob call site.
 */
export const ROUTE_PROPOSAL_MIN_NET = 100;

/**
 * Maps an edge index to its street-block key, or a negative (singleton) value
 * when the edge is unmapped — no block layer, or an edge outside every block.
 * `graphTopology.blockKeyOf` is the implementation; the pure steps take it as a
 * function so they never need to know the topology layout.
 */
export type EdgeBlockKey = (edgeIdx: number) => number;

/**
 * The fallback block mapping: `graphTopology.blockKeyOf`'s own encoding for an
 * unmapped edge. "No block layer" therefore means "every edge is its own
 * block", and block-grain aggregation degrades to the edge grain it replaced
 * rather than lumping an entire map into one proposal.
 */
const singletonBlockKey: EdgeBlockKey = (edgeIdx) => -(edgeIdx + 2);

/**
 * The DEDUPED net (up − down) for one (block, vote type), or null when there is
 * no deduped row to read. See `dedupedBlockNetResolver`.
 */
export type BlockNetResolver = (blockKey: number, legendIdx: number) => number | null;

/**
 * The honest per-(block, vote type) net: `block_vote_types`, which the server
 * builds by counting DISTINCT DEVICES per (block, vote type, direction) —
 * server/block_votes.py, docs/algorithms/07-counts.md. Summing edge nets over a
 * block would instead count one person once per edge their route happened to
 * cover, so a single lengthwise cast on a long block would outrank a short
 * block three separate people asked for.
 *
 * The two legends are DIFFERENT INDEX SPACES (`block_vote_type_legend` is built
 * from whichever types have block rows, in their own order), so they are
 * bridged by LABEL — once, here, into a lookup array, never per edge.
 *
 * Returns null, meaning "fall back to the edge sum", when:
 *   • the deduped arrays are absent (station networks, pre-block maps);
 *   • the key is a singleton — an unmapped edge is not a block and has no row;
 *   • the block carries no deduped row for this type yet. The block arrays lag
 *     an optimistic cast by one round trip (applyMyVoteChange writes edges;
 *     only the server's delta reaches applyBlockCounts), and a caster must see
 *     their own vote land.
 */
export function dedupedBlockNetResolver(
  data: Pick<GraphData, "vote_type_legend" | "block_vote_types" | "block_vote_type_legend">
): BlockNetResolver | undefined {
  const blockVoteTypes = data.block_vote_types;
  const blockLegend = data.block_vote_type_legend;
  const legend = data.vote_type_legend;
  if (!blockVoteTypes?.length || !blockLegend?.length || !legend?.length) return undefined;

  const blockLegendIdxOf = legend.map((label) => blockLegend.indexOf(label));
  return (blockKey, legendIdx) => {
    if (blockKey < 0) return null;
    const bli = blockLegendIdxOf[legendIdx];
    if (bli === undefined || bli < 0) return null;
    const entries = blockVoteTypes[blockKey];
    if (!entries) return null;
    for (const [li, up, down] of entries) {
      if (li === bli) return up - down;
    }
    return null;
  };
}

/** A block's running tally for one vote type, during the step-1 scan. */
interface BlockTally {
  net: number;      // Σ (up − down) over the block's edges, for this type
  edgeIdx: number;  // the block's strongest edge so far — the pin's home
  edgeNet: number;  // that edge's own net, kept only to compare the next one
}

/**
 * Step 1 — for each vote type, the top `perTypeLimit` blocks by DEDUPED net
 * support (highest first). Vote types with no block above max(0, minNet) are
 * dropped (a net-downvoted proposal is not a "top proposal", and below the
 * support floor it isn't "top" either), as are ROUTE-kind types when a `kindOf`
 * resolver is supplied — their corridors surface as RBTPs — and types toggled
 * off in the legend when an `isVisible` resolver is supplied.
 */
export function computeVoteTypeWinners(
  legend: string[],
  edgeVoteTypes: [number, number, number][][],
  perTypeLimit = 1,
  kindOf?: VoteTypeKindResolver,
  minNet = 0,
  isVisible?: VoteTypeVisibility,
  blockKeyOfEdge: EdgeBlockKey = singletonBlockKey,
  blockNetOf?: BlockNetResolver
): VoteTypeWinner[] {
  const candidates = computeWinnerCandidates(
    legend, edgeVoteTypes, perTypeLimit, kindOf, minNet, blockKeyOfEdge, blockNetOf
  );
  return isVisible ? candidates.filter((w) => isVisible(w.label)) : candidates;
}

/**
 * Step 1 WITHOUT the legend-visibility filter — the part that costs an O(edges)
 * scan, and the part that does not depend on which types are toggled on.
 *
 * Visibility is a display gesture, not a vote-state change: which blocks win for
 * a given type is identical whether its neighbours in the legend are shown or
 * hidden. Keeping the scan separate lets the caller cache it for a vote state
 * and re-select in microseconds when the legend toggles (GraphLayer's PBTP
 * candidate cache) instead of rescanning three million edges to redraw the same
 * pins. Filter the result with `filterWinnersByVisibility` before steps 2–5 —
 * pruning here rather than from the finished list is what lets a hidden type's
 * slot be taken by someone else's runner-up.
 */
export function computeWinnerCandidates(
  legend: string[],
  edgeVoteTypes: [number, number, number][][],
  perTypeLimit = 1,
  kindOf?: VoteTypeKindResolver,
  minNet = 0,
  blockKeyOfEdge: EdgeBlockKey = singletonBlockKey,
  blockNetOf?: BlockNetResolver
): VoteTypeWinner[] {
  if (!legend.length || !edgeVoteTypes.length) return [];

  const floor = Math.max(0, minNet);
  // legendIdx → blockKey → tally. Two maps rather than one keyed on a composite
  // because the per-type map is exactly what the ranking below consumes.
  const blocksByType = new Map<number, Map<number, BlockTally>>();
  // Label/kind rejection is memoized per legend index and applied INSIDE the
  // scan: a route-kind type on the NYC graph carries hundreds of thousands of
  // edges, and tallying blocks we are about to discard is the only part of this
  // loop big enough to notice.
  const eligible: (boolean | undefined)[] = [];

  for (let edgeIdx = 0; edgeIdx < edgeVoteTypes.length; edgeIdx++) {
    const pairs = edgeVoteTypes[edgeIdx];
    if (!pairs || !pairs.length) continue;
    const key = blockKeyOfEdge(edgeIdx);
    for (const [legendIdx, up, down] of pairs) {
      const net = up - down;
      // A perfectly cancelled edge moves neither the block's total nor its
      // choice of representative, so it never needs a tally entry.
      if (net === 0) continue;
      let ok = eligible[legendIdx];
      if (ok === undefined) {
        const label = legend[legendIdx];
        ok = !!label && !(kindOf && kindOf(label) === "route");
        eligible[legendIdx] = ok;
      }
      if (!ok) continue;

      // The tally exists to (a) enumerate which (block, type) pairs carry any
      // votes at all and (b) pick the block's representative edge. Its running
      // `net` is only the fallback ranking value — see the loop below.
      let byBlock = blocksByType.get(legendIdx);
      if (!byBlock) blocksByType.set(legendIdx, (byBlock = new Map()));
      const tally = byBlock.get(key);
      if (!tally) {
        byBlock.set(key, { net, edgeIdx, edgeNet: net });
      } else {
        tally.net += net;
        // STRICTLY greater, so a tie keeps the lowest-index edge: the pin must
        // land in the same place on every device and every reload, and edge
        // order is the only ordering both agree on.
        if (net > tally.edgeNet) {
          tally.edgeNet = net;
          tally.edgeIdx = edgeIdx;
        }
      }
    }
  }

  const winners: VoteTypeWinner[] = [];
  for (const [legendIdx, byBlock] of blocksByType) {
    const label = legend[legendIdx];
    if (!label) continue;
    const blocks: { edgeIdx: number; net: number }[] = [];
    for (const [key, tally] of byBlock) {
      // How many PEOPLE back this block, not how many edges one of them
      // covered. The edge sum stands in only where no deduped row exists, and
      // is the sole place the over-counting semantics survive.
      const net = blockNetOf?.(key, legendIdx) ?? tally.net;
      // The floor is a BLOCK bar now: an edge in the red doesn't disqualify the
      // block, it just costs it that much support (a downvoted direction of a
      // two-way street is an argument about the block, not a separate place).
      if (net > floor) blocks.push({ edgeIdx: tally.edgeIdx, net });
    }
    blocks.sort((a, b) => b.net - a.net || a.edgeIdx - b.edgeIdx);
    for (const { edgeIdx, net } of blocks.slice(0, perTypeLimit)) {
      winners.push({ legendIdx, label, edgeIdx, count: net });
    }
  }
  return winners;
}

/** Drop candidates whose vote type is toggled off in the legend. */
export function filterWinnersByVisibility(
  candidates: VoteTypeWinner[],
  isVisible?: VoteTypeVisibility
): VoteTypeWinner[] {
  return isVisible ? candidates.filter((w) => isVisible(w.label)) : candidates;
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
 * Step 3 — at most ONE winner per street block, across ALL vote types
 * (tiebreak: higher net, then shuffle). Step 1 already tallies each type to one
 * candidate per block; what's left for this pass is the CROSS-type collision —
 * blocks are the unit users see and vote on, so two pins on one block, even of
 * different types, read as a stack. Winners on unmapped edges (key < 0) are
 * kept: no block layer, no constraint. Must be given the same `blockKeyOfEdge`
 * step 1 aggregated with, or the two disagree about what a block is.
 */
export function dedupeWinnersByBlock(
  winners: VoteTypeWinner[],
  salt: number,
  blockKeyOfEdge: EdgeBlockKey
): VoteTypeWinner[] {
  const bestByBlock = new Map<number, VoteTypeWinner>();
  const unmapped: VoteTypeWinner[] = [];
  for (const w of winners) {
    const key = blockKeyOfEdge(w.edgeIdx);
    if (key < 0) {
      unmapped.push(w);
      continue;
    }
    const cur = bestByBlock.get(key);
    if (!cur || compareWinners(w, cur, salt) < 0) {
      bestByBlock.set(key, w);
    }
  }
  return [...bestByBlock.values(), ...unmapped];
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
 * Step 4 — greedy per-type non-max suppression. Processes winners strongest
 * first (net, then shuffle) and drops any whose edge midpoint lies within
 * `minSpacingMeters` of an already-kept winner of the SAME vote type. Different
 * types never radius-suppress each other (a bike-lane pin and a tree pin can
 * sit on nearby corners — they're genuinely distinct proposals; only sharing a
 * BLOCK collapses them, see dedupeWinnersByBlock). Winners whose position can't
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

/** Step 5 — sort by net (tiebreak shuffle) and cap at `limit`. */
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

// Up to this many BLOCKS per vote type feed the spacing step, so a popular type
// can surface several distinct LOCATIONS (not just its single best block). The
// spacing pass then collapses any that land on the same corridor. Five rather
// than the six of the edge-grain era: a block already absorbs the near-duplicate
// candidates (both directions of a street, both halves of a split segment) that
// the sixth slot used to spend itself on.
export const TOP_PROPOSALS_PER_TYPE = 5;

// Two top proposals of the SAME vote type closer than this collapse to the
// stronger one — kills the "3 identical pins stacked on one avenue" look.
// Deliberately much wider than the cross-type grain (one pin per block, see
// dedupeWinnersByBlock): identical pins carry zero extra information nearby,
// while different-type pins are distinct proposals and only collapse when they
// share a block. A kilometre is ~12 NYC avenue blocks — wide enough that one
// popular type spreads across neighbourhoods instead of pooling in one.
export const TOP_PROPOSAL_MIN_SPACING_M = 1000;

/**
 * Full selection path: top-N-BLOCKS-per-POINT-type winners → one per edge
 * (tiebreak) → one per street block across all types → per-type spatial spacing
 * → top `limit` by net. Each surviving block appears once and consumes one slot.
 * `kindOf` (label → kind) keeps ROUTE-kind vote types out of the point family;
 * omit it (e.g. station networks, where every vote is a point) to admit all.
 * `minNet` is the top-proposal support floor (strictly-greater; default
 * TOP_PROPOSAL_MIN_NET) — pass 0 to admit any positive net. `isVisible` is the
 * legend toggle (map/voteTypeFilter.isVoteTypeVisible); omit it to admit all.
 */
export function selectTopProposals(
  data:
    | (Pick<GraphData,
        "vote_type_legend" | "edge_vote_types"
        | "block_vote_types" | "block_vote_type_legend"> & GraphTopology)
    | null,
  salt: number,
  limit: number,
  minSpacingMeters = TOP_PROPOSAL_MIN_SPACING_M,
  kindOf?: VoteTypeKindResolver,
  minNet = TOP_PROPOSAL_MIN_NET,
  isVisible?: VoteTypeVisibility
): VoteTypeWinner[] {
  if (!data) return [];
  return selectTopProposalsFrom(
    data,
    computeWinnerCandidates(
      data.vote_type_legend ?? [],
      data.edge_vote_types ?? [],
      TOP_PROPOSALS_PER_TYPE,
      kindOf,
      minNet,
      (edgeIdx) => blockKeyOf(data, edgeIdx),
      dedupedBlockNetResolver(data)
    ),
    salt, limit, minSpacingMeters, isVisible
  );
}

/**
 * Steps 2–5 over an already-scanned candidate set (`computeWinnerCandidates`).
 * Everything here operates on a few dozen survivors, so re-running it when the
 * legend's visibility toggles is effectively free — that's the whole point of
 * splitting it out from the edge scan.
 */
export function selectTopProposalsFrom(
  data: Pick<GraphData, "vote_type_legend" | "edge_vote_types"> & GraphTopology,
  candidates: VoteTypeWinner[],
  salt: number,
  limit: number,
  minSpacingMeters = TOP_PROPOSAL_MIN_SPACING_M,
  isVisible?: VoteTypeVisibility
): VoteTypeWinner[] {
  const perType = filterWinnersByVisibility(candidates, isVisible);
  const perEdge = dedupeWinnersByEdge(perType, salt);
  const perBlock = dedupeWinnersByBlock(
    perEdge, salt, (edgeIdx) => blockKeyOf(data, edgeIdx)
  );
  const spaced = spaceOutWinners(
    perBlock, salt, edgeMidpointResolver(data), minSpacingMeters
  );
  return applyTopProposalLimit(spaced, salt, limit);
}
