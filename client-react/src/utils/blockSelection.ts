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
