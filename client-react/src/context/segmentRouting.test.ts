import { describe, it, expect, vi } from "vitest";
import { routeSegments, segmentsFromGeometry, type CorridorSegmentFor } from "./segmentRouting";
import type { LatLng, SplitDesirePath } from "../types";

// ==========================================================================
// Regression: a dragged ghost waypoint must never become a straight diagonal
// ==========================================================================
// The bug: dropping a waypoint somewhere OSRM can't reach (Governors Island,
// a pier, a proposed lane outside the routable network) returned a fabricated
// [a → b] connector. It rendered as a solid line cutting across the city,
// looked like a real route, and carried no edge ids — so it voted on nothing.
//
// The contract these tests pin down: a segment either produces routed geometry
// or reports as FAILED. There is no third outcome and no synthesized geometry.

const BATTERY: LatLng = { lat: 40.7035, lng: -74.017 };
const GOVERNORS: LatLng = { lat: 40.6895, lng: -74.0165 };
const CITY_HALL: LatLng = { lat: 40.7128, lng: -74.006 };

/** A real OSRM-shaped answer: a polyline that follows the graph, plus edge ids. */
function routedResponse(coords: [number, number][], edgeIds: number[]) {
  return {
    ok: true,
    json: async () => ({
      route: { type: "Feature", geometry: { type: "LineString", coordinates: coords } },
      desire_path_segments: [],
      edge_ids: edgeIds,
    }),
  } as unknown as Response;
}

/** What the server actually returns for an unreachable point (verified against
 *  the dev server: POST /api/routes → 404 "OSRM request failed"). */
function unreachableResponse() {
  return {
    ok: false,
    status: 404,
    json: async () => ({ error: "OSRM request failed: 400 Client Error" }),
  } as unknown as Response;
}

const NO_CORRIDORS: CorridorSegmentFor = () => null;

function opts(fetchImpl: typeof fetch, corridorFor: CorridorSegmentFor = NO_CORRIDORS) {
  return {
    corridorFor,
    signal: new AbortController().signal,
    apiUrl: "/api",
    mapSlug: "nyc-walkways",
    fetchImpl,
  };
}

describe("routeSegments — success path", () => {
  it("returns one routed path per consecutive pair, with the server's geometry and edge ids", async () => {
    const fetchImpl = vi.fn(async () =>
      routedResponse(
        [
          [-74.017, 40.7035],
          [-74.0155, 40.7071],
          [-74.006, 40.7128],
        ],
        [11, 12]
      )
    ) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, CITY_HALL], opts(fetchImpl));

    expect(result.failed).toEqual([]);
    expect(result.aborted).toBe(false);
    expect(result.paths).toHaveLength(1);
    expect(result.paths[0].segmentIndex).toBe(0);
    expect(result.paths[0].edgeIds).toEqual([11, 12]);
    // Real routed geometry has interior vertices — it is NOT just the endpoints.
    expect(result.paths[0].geometry.coordinates).toHaveLength(3);
  });

  it("routes N-1 segments for N points, in order", async () => {
    const fetchImpl = vi.fn(async () =>
      routedResponse(
        [
          [-74.017, 40.7035],
          [-74.01, 40.708],
          [-74.006, 40.7128],
        ],
        [7]
      )
    ) as unknown as typeof fetch;

    const mid: LatLng = { lat: 40.708, lng: -74.01 };
    const result = await routeSegments([BATTERY, mid, CITY_HALL], opts(fetchImpl));

    expect(result.failed).toEqual([]);
    expect(result.paths.map((p) => p.segmentIndex)).toEqual([0, 1]);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("traces a forced corridor verbatim, without asking the router", async () => {
    const fetchImpl = vi.fn(async () => routedResponse([[0, 0], [1, 1]], [])) as unknown as typeof fetch;
    const corridor: SplitDesirePath = {
      id: "split-0",
      segmentIndex: 0,
      geometry: { type: "LineString", coordinates: [[-74.017, 40.7035], [-74.006, 40.7128]] },
      segments: [],
      edgeIds: [900, 901],
    };

    const result = await routeSegments([BATTERY, CITY_HALL], opts(fetchImpl, () => corridor));

    expect(result.failed).toEqual([]);
    expect(result.paths[0].edgeIds).toEqual([900, 901]);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("routeSegments — router-failure path", () => {
  it("reports an unreachable point as FAILED and invents no geometry", async () => {
    const fetchImpl = vi.fn(async () => unreachableResponse()) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, GOVERNORS], opts(fetchImpl));

    expect(result.failed).toEqual([0]);
    expect(result.paths).toEqual([]);
    expect(result.aborted).toBe(false);
  });

  it("never emits the straight [a → b] diagonal that caused the bug", async () => {
    const fetchImpl = vi.fn(async () => unreachableResponse()) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, GOVERNORS, CITY_HALL], opts(fetchImpl));

    expect(result.failed).toEqual([0, 1]);
    // The old fallback would have produced exactly these two two-point lines.
    const diagonals = [
      [[BATTERY.lng, BATTERY.lat], [GOVERNORS.lng, GOVERNORS.lat]],
      [[GOVERNORS.lng, GOVERNORS.lat], [CITY_HALL.lng, CITY_HALL.lat]],
    ];
    for (const path of result.paths) {
      expect(diagonals).not.toContainEqual(path.geometry.coordinates);
    }
    expect(result.paths).toEqual([]);
  });

  it("fails the unroutable segment only, keeping the routable ones", async () => {
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      const endsOnIsland = body.end[0] === GOVERNORS.lat;
      const startsOnIsland = body.start[0] === GOVERNORS.lat;
      return endsOnIsland || startsOnIsland
        ? unreachableResponse()
        : routedResponse([[-74.017, 40.7035], [-74.012, 40.708], [-74.006, 40.7128]], [3]);
    }) as unknown as typeof fetch;

    const mid: LatLng = { lat: 40.708, lng: -74.012 };
    // Battery → mid routes; mid → Governors does not.
    const result = await routeSegments([BATTERY, mid, GOVERNORS], opts(fetchImpl));

    expect(result.paths).toHaveLength(1);
    expect(result.paths[0].segmentIndex).toBe(0);
    expect(result.failed).toEqual([1]);
  });

  it("treats a 200 with no geometry as a failure", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => ({ route: null, edge_ids: [] }),
    })) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, GOVERNORS], opts(fetchImpl));
    expect(result.failed).toEqual([0]);
    expect(result.paths).toEqual([]);
  });

  it("treats a degenerate one-point geometry as a failure", async () => {
    const fetchImpl = vi.fn(async () =>
      routedResponse([[-74.017, 40.7035]], [])
    ) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, GOVERNORS], opts(fetchImpl));
    expect(result.failed).toEqual([0]);
  });

  it("reports a network error as a failure, not as geometry", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, GOVERNORS], opts(fetchImpl));
    expect(result.failed).toEqual([0]);
    expect(result.paths).toEqual([]);
  });

  it("distinguishes an ABORTED batch from an unroutable one", async () => {
    // A superseded batch is stale, not a routing verdict — reporting "no route
    // found" for it would flash a banner every time the user drags twice.
    const controller = new AbortController();
    const fetchImpl = vi.fn(async () => {
      controller.abort();
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch;

    const result = await routeSegments([BATTERY, CITY_HALL], {
      corridorFor: NO_CORRIDORS,
      signal: controller.signal,
      apiUrl: "/api",
      mapSlug: "nyc-walkways",
      fetchImpl,
    });

    expect(result.aborted).toBe(true);
    expect(result.failed).toEqual([]);
    expect(result.paths).toEqual([]);
  });
});

describe("routeSegments — degenerate input", () => {
  it("routes nothing for a single point", async () => {
    const fetchImpl = vi.fn() as unknown as typeof fetch;
    const result = await routeSegments([BATTERY], opts(fetchImpl));
    expect(result).toEqual({ paths: [], failed: [], aborted: false });
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("segmentsFromGeometry", () => {
  it("splits a polyline into consecutive pairs", () => {
    expect(
      segmentsFromGeometry({
        type: "LineString",
        coordinates: [[0, 0], [1, 1], [2, 2]],
      })
    ).toEqual([[[0, 0], [1, 1]], [[1, 1], [2, 2]]]);
  });

  it("yields nothing for a single-point geometry", () => {
    expect(segmentsFromGeometry({ type: "LineString", coordinates: [[0, 0]] })).toEqual([]);
  });
});
