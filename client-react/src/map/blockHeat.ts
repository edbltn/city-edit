// Algorithm doc: docs/algorithms/05-heat-coloring.md
// ==========================================================================
// Block heat normalization — signed differential → ramp position
// ==========================================================================
// The last step of the heat pipeline, and the only arithmetic in it. Upstream,
// `voteApply.topProposalDiffs` reduces each block to ONE signed number: the
// differential (up − down) of its top-ranked proposal. Downstream, MapLibre
// samples the style's ramp. This module is the map between them.
//
// Two properties matter, and they pull against each other:
//
//   - a quiet map must not saturate. Two votes on a new city's first block
//     should read as "someone was here", not as "maximum demand" — hence the
//     LOG curve and the floored ceilings.
//   - a busy map must not flatten. Vote totals are heavy-tailed; a linear
//     scale on the NYC bike map paints everything below the top corridor the
//     same near-black.
//
// The arms are normalized SEPARATELY. Opposition is rarer and much smaller in
// magnitude than support, so sharing a denominator would leave every
// net-against block a barely-visible smudge at the warm end of cold. Its own
// (much tighter) ceiling lets it actually reach the deep-cold end of the ramp.

/** Position on the ramp for one block: −1 (deep cold) … 0 (invisible) … 1 (peak). */
export type SignedHeat = number;

export interface HeatNormalization {
  /** Signed differential → ramp position. */
  heatOf(diff: number): SignedHeat;
  /** Identity of this normalization. When it changes, EVERY lit block's heat
   *  changes, so the paint layer must rewrite in full rather than diff. */
  denomKey: string;
}

/**
 * Build the normalization for one broadcast.
 *
 * `max` / `maxNeg` are the observed per-arm ceilings, already floored by the
 * caller against HEAT_FULL_SCALE / NEG_HEAT_FULL_SCALE so a map with three
 * votes on it doesn't paint them as citywide consensus.
 *
 * `log(v + 1)` (and its mirror `log(1 − v)`) rather than `log(v)` so that a
 * differential of exactly 1 maps to a small positive number instead of zero —
 * one vote must be visible, or the first vote on a new map appears to do
 * nothing. Zero maps to zero, and zero is invisible by design: a block whose
 * support and opposition cancel carries no signal, and painting it lukewarm
 * would claim consensus that isn't there.
 */
export function makeHeatNormalization(max: number, maxNeg: number): HeatNormalization {
  const denomPos = Math.log(Math.max(1, max) + 1);
  const denomNeg = Math.log(Math.max(1, maxNeg) + 1);
  return {
    heatOf: (diff: number) =>
      diff > 0 ? Math.log(diff + 1) / denomPos : -Math.log(1 - diff) / denomNeg,
    denomKey: `${denomPos}|${denomNeg}`,
  };
}
