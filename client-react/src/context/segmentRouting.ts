// ==========================================================================
// Segment routing — turn an ordered point sequence into real routed geometry
// ==========================================================================
// A selection of N waypoints is N-1 independent segments. Each one either routes
// through the graph (OSRM, or a proposal corridor traced verbatim) or it FAILS.
//
// There is deliberately no third outcome. An earlier version fell back to a
// straight [a → b] connector whenever a segment couldn't route, on the theory
// that a partly-wrong route beats no route. It doesn't: a dropped waypoint that
// OSRM can't reach (Governors Island, a pier, a proposed lane that isn't in the
// routable network) rendered as a solid diagonal cutting across the city, looked
// exactly like a real path, and carried no edge ids — so it voted on nothing.
// Callers must be able to tell "routed" from "couldn't", which is what
// {@link SegmentCalcResult.failed} is for. Never fabricate geometry here.

import type { LatLng, RouteGeometry, SplitDesirePath } from "../types";

/** Split a routed polyline into its consecutive [lng,lat] pairs. */
export function segmentsFromGeometry(geometry: RouteGeometry): [number, number][][] {
  const coords = geometry.coordinates;
  const segs: [number, number][][] = [];
  for (let i = 0; i < coords.length - 1; i++) {
    segs.push([coords[i], coords[i + 1]]);
  }
  return segs;
}

export interface SegmentCalcResult {
  /** Routed segments, in order. Complete only when `failed` is empty. */
  paths: SplitDesirePath[];
  /** Indices of segments no router could produce a path for. */
  failed: number[];
  /** The batch was superseded by a newer one. Not a routing failure — the
   *  caller should discard the whole result rather than report it. */
  aborted: boolean;
}

/** Resolve segment `i` (a→b) from a forced proposal corridor, locally and
 *  verbatim, or null to route it normally. */
export type CorridorSegmentFor = (
  a: LatLng,
  b: LatLng,
  segmentIndex: number
) => SplitDesirePath | null;

export interface RouteSegmentsOptions {
  corridorFor: CorridorSegmentFor;
  signal: AbortSignal;
  apiUrl: string;
  mapSlug: string;
  /** Injectable for tests. */
  fetchImpl?: typeof fetch;
}

type SegmentOutcome =
  | { kind: "ok"; path: SplitDesirePath }
  | { kind: "failed" }
  | { kind: "aborted" };

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/**
 * Route every consecutive pair of `points`, concurrently and independently.
 *
 * A segment flagged as forced routes through its proposal's corridor verbatim,
 * with no round-trip. Everything else asks the server. A non-OK response, a
 * response with no geometry, or a network error marks that segment FAILED — it
 * does not become a straight line.
 */
export async function routeSegments(
  points: LatLng[],
  { corridorFor, signal, apiUrl, mapSlug, fetchImpl = fetch }: RouteSegmentsOptions
): Promise<SegmentCalcResult> {
  if (points.length < 2) return { paths: [], failed: [], aborted: false };

  const fetchSegment = async (i: number): Promise<SegmentOutcome> => {
    const a = points[i];
    const b = points[i + 1];

    const corridor = corridorFor(a, b, i);
    if (corridor) return { kind: "ok", path: corridor };

    try {
      const resp = await fetchImpl(`${apiUrl}/routes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: [a.lat, a.lng],
          end: [b.lat, b.lng],
          waypoints: [],
          map: mapSlug,
        }),
        signal,
      });
      if (!resp.ok) return { kind: "failed" };
      const data = await resp.json();
      const geometry: RouteGeometry | undefined = data.route?.geometry;
      // A geometry of fewer than two points isn't a path either.
      if (!geometry || (geometry.coordinates?.length ?? 0) < 2) return { kind: "failed" };
      return {
        kind: "ok",
        path: {
          id: `split-${i}`,
          segmentIndex: i,
          geometry,
          segments: data.desire_path_segments || [],
          edgeIds: data.edge_ids || [],
        },
      };
    } catch (err) {
      // An aborted batch is stale, not unroutable — the caller discards it whole.
      return isAbort(err) || signal.aborted ? { kind: "aborted" } : { kind: "failed" };
    }
  };

  const outcomes = await Promise.all(points.slice(0, -1).map((_, i) => fetchSegment(i)));

  const paths: SplitDesirePath[] = [];
  const failed: number[] = [];
  let aborted = false;
  outcomes.forEach((o, i) => {
    if (o.kind === "ok") paths.push(o.path);
    else if (o.kind === "aborted") aborted = true;
    else failed.push(i);
  });

  return { paths, failed, aborted };
}
