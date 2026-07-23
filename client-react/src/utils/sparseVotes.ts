// Sparse /api/graph-votes decoding (format=sparse).
//
// The dense vote body is >99.5% zeros/empty entries on a city graph, and
// JSON.parse-ing its ~26MB materialized ~9M boxed JS values — hundreds of MB
// on mobile Safari, the "bikepaths" jetsam OOM (the topology got typed arrays
// in graphTopology.ts; the vote arrays never did). The sparse body carries
// only the nonzero indices + values; this decoder rebuilds the dense,
// index-addressable shape consumers already expect:
//
//  - vote totals as Int32Array — indexable, mutable, `.length`-bearing, ~8x
//    smaller than a boxed number[] of the same length
//  - vote-type breakdowns as HOLEY arrays — entries exist only at voted
//    indices. Every consumer guards with `?? []` / `if (!pairs) continue`,
//    and the mutation paths (`edgeVoteTypes[eid] || []`) materialize a FRESH
//    array before writing, so holes are safe where a shared empty-array
//    sentinel would not be (a shared [] would be mutated in place by
//    voteApply's push).

import type { GraphData } from "../types";

type VtPairs = [number, number, number][];

/** Wire shape of the format=sparse /api/graph-votes body. */
export interface SparseVotesPayload {
  sparse: 1;
  rev?: number;
  vote_types?: Record<string, string>;
  vote_type_legend?: string[];
  n_edges: number;
  n_nodes: number;
  topology_version?: string;
  ev_idx?: number[];
  ev_val?: number[];
  nv_idx?: number[];
  nv_val?: number[];
  evt?: Record<string, VtPairs>;
  nvt?: Record<string, VtPairs>;
  // Present only when the map has a block layer.
  n_blocks?: number;
  blocks_version?: string | null;
  block_vote_type_legend?: string[];
  bv_idx?: number[];
  bv_val?: number[];
  bvt?: Record<string, VtPairs>;
}

export function isSparseVotes(x: unknown): x is SparseVotesPayload {
  return !!x && typeof x === "object" && (x as { sparse?: unknown }).sparse === 1;
}

function denseInt32(n: number, idx?: number[], val?: number[]): Int32Array {
  const a = new Int32Array(n);
  if (idx && val) {
    for (let i = 0; i < idx.length; i++) a[idx[i]] = val[i];
  }
  return a;
}

function holeyVt(n: number, m?: Record<string, VtPairs>): VtPairs[] {
  const a: VtPairs[] = new Array(n);
  if (m) {
    for (const k in m) a[+k] = m[k];
  }
  return a;
}

/** Rebuild the dense vote fields (`edge_votes`, `edge_vote_types`, …) that the
 *  rest of the app indexes into. Pure; scales with VOTED entries, not graph
 *  size (aside from the two zero-filled typed allocations). */
export function decodeSparseVotes(sp: SparseVotesPayload): Partial<GraphData> {
  const out: Partial<GraphData> = {
    rev: sp.rev,
    vote_types: sp.vote_types,
    vote_type_legend: sp.vote_type_legend ?? [],
    n_edges: sp.n_edges,
    n_nodes: sp.n_nodes,
    topology_version: sp.topology_version,
    edge_votes: denseInt32(sp.n_edges, sp.ev_idx, sp.ev_val),
    node_votes: denseInt32(sp.n_nodes, sp.nv_idx, sp.nv_val),
    edge_vote_types: holeyVt(sp.n_edges, sp.evt),
    node_vote_types: holeyVt(sp.n_nodes, sp.nvt),
  };
  if (sp.n_blocks != null) {
    out.n_blocks = sp.n_blocks;
    out.blocks_version = sp.blocks_version ?? undefined;
    out.block_vote_type_legend = sp.block_vote_type_legend ?? [];
    out.block_votes = denseInt32(sp.n_blocks, sp.bv_idx, sp.bv_val);
    out.block_vote_types = holeyVt(sp.n_blocks, sp.bvt);
  }
  return out;
}
