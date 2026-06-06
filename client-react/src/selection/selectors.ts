// ==========================================================================
// Selection selectors — derive the legacy shape RouteContext exposes
// ==========================================================================
// RouteContext's public API (start / end / ghostWaypoints / ghostWaypointIds /
// pointType) is unchanged so every consumer (MapView, GraphLayer, TopBar…) keeps
// working. These pure helpers project the canonical Selection back into that shape.

import type { LatLng, RoutePoint } from "../types";
import type { Selection } from "./types";

const EMPTY_POINT: RoutePoint = {
  coords: null,
  timestamp: null,
  address: null,
  voteEdgeId: null,
};

function toPoint(sel: Selection, index: number): RoutePoint {
  const w = sel.waypoints[index];
  if (!w) return EMPTY_POINT;
  // timestamp is legacy write-only data (nothing reads it) — derive null.
  return {
    coords: w.coords,
    timestamp: null,
    address: w.address ?? null,
    voteEdgeId: w.voteEdgeId ?? null,
  };
}

/** The start point (waypoints[0]), or an empty RoutePoint. */
export function deriveStart(sel: Selection): RoutePoint {
  return toPoint(sel, 0);
}

/** The end point (the last waypoint when there are ≥2), or empty. Station networks
 *  never hold more than one waypoint, so this is naturally empty there. */
export function deriveEnd(sel: Selection): RoutePoint {
  if (sel.waypoints.length < 2) return EMPTY_POINT;
  return toPoint(sel, sel.waypoints.length - 1);
}

/** The mid waypoints (everything between start and end), as bare coords. */
export function deriveMids(sel: Selection): LatLng[] {
  if (sel.waypoints.length < 3) return [];
  return sel.waypoints.slice(1, -1).map((w) => w.coords);
}

/** Stable ids for the mid waypoints (parallel to deriveMids), for React keys. */
export function deriveMidIds(sel: Selection): string[] {
  if (sel.waypoints.length < 3) return [];
  return sel.waypoints.slice(1, -1).map((w) => w.id);
}
