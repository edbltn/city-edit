/**
 * Find the nearest graph node to a lat/lng point.
 * O(n) scan — fast enough for viewport-sized node sets (~5–15k nodes).
 */
export function findNearestNode(
  lat: number,
  lng: number,
  nodes: [number, number][]
): [number, number] | null {
  if (nodes.length === 0) return null;

  let best = nodes[0];
  let bestDist = Infinity;

  for (const node of nodes) {
    const d = (node[0] - lat) ** 2 + (node[1] - lng) ** 2;
    if (d < bestDist) {
      bestDist = d;
      best = node;
    }
  }

  return best;
}
