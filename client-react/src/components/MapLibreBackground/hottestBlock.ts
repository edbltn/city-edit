/**
 * Pick the block that wins a point hit among overlapping block polygons.
 *
 * queryRenderedFeatures returns features topmost-render-order first, but
 * within one fill layer that order is tile/feature order — NOT heat. Where
 * block polygons overlap (stale bakes, or tile-simplification slivers at
 * low zooms), taking the first feature makes the hotter block underneath
 * impossible to hover or select. The rule here: the block with the highest
 * |feature-state heat| owns the point; ties (including the common
 * all-zero-heat case) keep render order, i.e. the first feature.
 */

export interface BlockHitFeature {
  id?: unknown;
  state?: { heat?: unknown };
}

export function hottestBlockId(feats: readonly BlockHitFeature[]): number | null {
  let best: number | null = null;
  let bestHeat = -Infinity;
  for (const f of feats) {
    if (typeof f.id !== "number") continue;
    const raw = f.state?.heat;
    const heat = typeof raw === "number" ? Math.abs(raw) : 0;
    if (heat > bestHeat) {
      bestHeat = heat;
      best = f.id;
    }
  }
  return best;
}
