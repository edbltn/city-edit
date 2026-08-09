// Referenced by: docs/algorithms/06-ranking.md (proposal-row order) and
//   docs/algorithms/07-counts.md (the block-vs-edge regime split, and why
//   summing block counts over-counts people).
// ==========================================================================
// Block-scoped selection helpers (docs/three-layer-model.md §2.4, §4)
// ==========================================================================
// Blocks are the aggregation/interaction grain. These helpers materialize a
// selection's touched blocks into per-block edge lists (the `blocks` argument
// castVotes/blockCoverage expect) and sum the deduped per-block counts for the
// modal rows. Pure (no React/DOM) so they're unit-testable.

import {
  touchedBlockKeys,
  edgesOfBlockKey,
  type BlockIndex,
  type GraphTopology,
} from "../components/GraphLayer/graphTopology";

export interface SelectionVoteRow {
  label: string;
  up: number;
  down: number;
}

// Session cache of resolved /api/route-votes rows (distinct-voter counts per
// selection). Keyed by an order-insensitive signature of the capped edge-id
// set, so the route card can render server truth immediately on reopen instead
// of flashing the inflated per-block stand-in rows. Small: entries are a
// handful of rows each; the cap only bounds a long browse session.
export const ROUTE_VOTES_CACHE_MAX = 64;

/**
 * Order-insensitive signature of a selection's (capped) block-edge union, used
 * to key cached /api/route-votes rows. FNV-1a over the sorted ids — tiny keys
 * regardless of union size (a merged block can union thousands of edges), and
 * the length + slug prefix keeps accidental collisions vanishingly unlikely.
 */
export function routeVotesKey(slug: string, edgeIds: readonly number[]): string {
  const sorted = [...edgeIds].sort((a, b) => a - b);
  let h = 0x811c9dc5;
  for (const e of sorted) {
    h ^= e & 0xffff;
    h = Math.imul(h, 0x01000193);
    h ^= e >>> 16;
    h = Math.imul(h, 0x01000193);
  }
  return `${slug}:${sorted.length}:${h >>> 0}`;
}

/**
 * Materialize a selection's touched blocks into per-block edge lists. Real
 * blocks resolve through the CSR index (subarray views, no copies — the mobile
 * typed-array memory rule); unmapped edges and maps without block artifacts
 * fall back to singleton [edge] blocks, so every rule downstream still holds.
 */
export function materializeBlocks(
  topo: GraphTopology,
  blockIndex: BlockIndex | null,
  edgeIds: ArrayLike<number>,
): ArrayLike<number>[] {
  const blocks: ArrayLike<number>[] = [];
  for (const key of touchedBlockKeys(topo, edgeIds)) {
    const members = edgesOfBlockKey(topo, blockIndex, key);
    if (members.length > 0) blocks.push(members);
  }
  return blocks;
}

/**
 * Modal rows for a selection at BLOCK grain: the deduped per-block counts
 * (`block_vote_types`, each [legendIdx, up, down] indexing
 * `block_vote_type_legend`) summed across the selection's touched blocks — a
 * device present in N blocks counts once per block (docs §2.4). Edges without
 * a block (singleton keys) contribute their own per-edge breakdown, so mixed
 * selections still total. Returns null when the map has no block layer
 * (topology without edgeBlockId, or no block_vote_types payload) so callers
 * fall back to today's per-edge rows.
 */
export function selectionVoteRows(
  data: GraphTopology & {
    block_vote_types?: [number, number, number][][];
    block_vote_type_legend?: string[];
    edge_vote_types?: [number, number, number][][];
    vote_type_legend?: string[];
  },
  edgeIds: ArrayLike<number>,
): SelectionVoteRow[] | null {
  if (!data.edgeBlockId || !data.block_vote_types) return null;
  const sums = new Map<string, { up: number; down: number }>();
  const add = (label: string | undefined, up: number, down: number) => {
    if (!label) return;
    const cur = sums.get(label);
    if (cur) {
      cur.up += up;
      cur.down += down;
    } else {
      sums.set(label, { up, down });
    }
  };
  for (const key of touchedBlockKeys(data, edgeIds)) {
    if (key >= 0) {
      for (const [li, up, down] of data.block_vote_types[key] ?? []) {
        add(data.block_vote_type_legend?.[li], up ?? 0, down ?? 0);
      }
    } else {
      const edgeId = -key - 2;
      for (const [li, up, down] of data.edge_vote_types?.[edgeId] ?? []) {
        add(data.vote_type_legend?.[li], up ?? 0, down ?? 0);
      }
    }
  }
  return [...sums.entries()]
    .map(([label, { up, down }]) => ({ label, up, down }))
    .sort((a, b) => (b.up - b.down) - (a.up - a.down));
}

/**
 * How much of a selection each vote type actually covers.
 *
 * The vote tallies on a route card answer "how many PEOPLE", which says
 * nothing about REACH: 36 voters concentrated on one corner of a 12-block
 * corridor and 36 spread along all of it render identically. This is the
 * other axis — for each vote type, how many of the selection's touched blocks
 * hold at least one vote of that type (either direction).
 *
 * Counted at the same grain the card's meta line reports ("Covers 12 blocks"),
 * so `total` is that number and every `byLabel` value reads against it.
 * Unlike selectionVoteRows this never returns null: maps without a block layer
 * fall back to singleton per-edge blocks, where the count is simply "how many
 * of the selected segments carry this type". Purely local — no round trip.
 */
export interface SelectionCoverage {
  /** Blocks (or, without a block layer, segments) the selection touches. */
  total: number;
  /** Label → how many of those hold ≥1 vote of that type. */
  byLabel: Map<string, number>;
}

export function selectionCoverage(
  data: GraphTopology & {
    block_vote_types?: [number, number, number][][];
    block_vote_type_legend?: string[];
    edge_vote_types?: [number, number, number][][];
    vote_type_legend?: string[];
  },
  edgeIds: ArrayLike<number>,
): SelectionCoverage {
  const byLabel = new Map<string, number>();
  // One unit (block or segment) counts ONCE per label, however many of its
  // edges or how many votes carry that type.
  const seenHere = new Set<string>();
  const countUnit = (
    pairs: [number, number, number][] | undefined,
    legend: string[] | undefined,
  ) => {
    seenHere.clear();
    for (const [li, up, down] of pairs ?? []) {
      const label = legend?.[li];
      // A [li, 0, 0] triple is a type whose up/down cancelled out — present in
      // the breakdown, but nobody's vote stands here.
      if (!label || (up ?? 0) + (down ?? 0) === 0) continue;
      if (seenHere.has(label)) continue;
      seenHere.add(label);
      byLabel.set(label, (byLabel.get(label) ?? 0) + 1);
    }
  };

  // Same regime split as selectionVoteRows, so coverage and counts always
  // describe the same units.
  if (data.edgeBlockId && data.block_vote_types) {
    const keys = touchedBlockKeys(data, edgeIds);
    for (const key of keys) {
      if (key >= 0) countUnit(data.block_vote_types[key], data.block_vote_type_legend);
      else countUnit(data.edge_vote_types?.[-key - 2], data.vote_type_legend);
    }
    return { total: keys.length, byLabel };
  }

  const edges = new Set<number>();
  for (let i = 0; i < edgeIds.length; i++) edges.add(edgeIds[i]);
  for (const edgeId of edges) {
    countUnit(data.edge_vote_types?.[edgeId], data.vote_type_legend);
  }
  return { total: edges.size, byLabel };
}
