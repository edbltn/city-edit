// Referenced by: docs/algorithms/05-heat-coloring.md
//   (HEAT_FULL_SCALE / NEG_HEAT_FULL_SCALE — the per-arm ceiling floors).
// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

/** Distance from point (px,py) to line segment (ax,ay)-(bx,by), in pixels. */
export function pointToSegmentDist(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) {
    const ex = px - ax;
    const ey = py - ay;
    return Math.sqrt(ex * ex + ey * ey);
  }
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx - px;
  const cy = ay + t * dy - py;
  return Math.sqrt(cx * cx + cy * cy);
}

/** Great-circle distance between two lat/lng points, in meters. */
export function haversineMeters(
  lat1: number, lng1: number, lat2: number, lng2: number
): number {
  const R = 6371000;
  const toRad = Math.PI / 180;
  const dLat = (lat2 - lat1) * toRad;
  const dLng = (lng2 - lng1) * toRad;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * toRad) * Math.cos(lat2 * toRad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Loop-based max to avoid stack overflow with large arrays. */
export function arrayMax(arr: ArrayLike<number>): number {
  let max = 0;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > max) max = arr[i];
  }
  return max;
}

// Floor for the heat normalization denominator. Brightness is
// log(votes+1)/log(scale+1); using a map's own max as `scale` makes a quiet map
// (small max) blow out — log() pushes its 1–2-vote edges to near-full heat, so
// e.g. SF (max ~18) lit up almost every edge while NYC (max ~400) stayed mostly
// cool. Flooring the denominator at this many votes means a low-traffic map is
// measured against a fixed scale and renders proportionally cooler, while a
// busy map past the floor still uses its own (larger) max for full dynamic range.
export const HEAT_FULL_SCALE = 50;

// The negative arm's own denominator floor. Net-against differentials are far
// rarer and smaller than net-for ones (organic downvotes, not bulk imports), so
// normalizing them against the positive ceiling would render every net-against
// block a barely-visible tint. A tighter floor gives the negative range its own
// dynamic range: ~10 net-against is already "overwhelmingly negative" and earns
// the full cold color.
export const NEG_HEAT_FULL_SCALE = 10;

// ---------------------------------------------------------------------------
// Heatmap color stops — flame cross-section
// ---------------------------------------------------------------------------
// Each pass uses a different color and width so the gradient runs ACROSS the
// stroke (halo on the outside, hot core on the inside) rather than ALONG it.
// The ramp + blend mode come from the active map style (mapStyles.ts): dark
// styles blend additively (`lighter`/`screen`) for Strava-style intersection
// brightening; the light style blends via `multiply` so heat darkens the map.

// Slightly hold the heatmap back so the top-proposal pins read as the brightest
// thing on the map. Applied as canvas element opacity (atop the blend mode), so
// it dims the whole heat field — and its faint zero-vote network skeleton —
// uniformly without touching per-edge intensity math.
export const HEATMAP_OPACITY = "0.55";
