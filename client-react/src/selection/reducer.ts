// ==========================================================================
// Selection reducer — pure waypoint-array transitions
// ==========================================================================
// Every mutation of the selection goes through one of these pure functions, so
// the gnarly "first=start, last=end, middle=mids" rebalancing collapses to plain
// array edits and is exhaustively unit-testable (see reducer.test.ts). The async
// route/split recalculation stays in RouteContext; this module only owns the
// ordered list of waypoints.
//
// Index conventions:
//   - A "full index" addresses the [start, ...mids, end] array directly.
//   - A "segment index" addresses the gap between consecutive waypoints; segment i
//     sits between full indices i and i+1. Inserting a mid into segment i lands it
//     at full index i+1 (so it falls AFTER the start, BEFORE the end).
//   - A "ghost index" addresses the mids only (mids[k] == full index k+1), matching
//     the legacy ghostWaypoints indexing the UI still hands us.

import type { LatLng } from "../types";
import type { Selection, SelWaypoint } from "./types";

export type IdGen = () => string;

export interface WaypointInit {
  coords: LatLng;
  address?: string | null;
  voteEdgeId?: number | null;
}

function makeWaypoint(init: WaypointInit, id: string): SelWaypoint {
  return {
    coords: init.coords,
    address: init.address ?? null,
    voteEdgeId: init.voteEdgeId ?? null,
    id,
  };
}

/** Place/replace the START (full index 0), preserving any mids + end. Appends when
 *  the selection is empty. A start drag reuses the slot's id (no remount). */
export function setStart(sel: Selection, init: WaypointInit, makeId: IdGen): Selection {
  const wps = sel.waypoints.slice();
  if (wps.length === 0) {
    wps.push(makeWaypoint(init, makeId()));
  } else {
    wps[0] = makeWaypoint(init, wps[0].id);
  }
  return { ...sel, waypoints: wps };
}

/** Set the END. Appends as the second point when only a start exists; replaces the
 *  last point when an end already exists. (With no start at all it becomes the sole
 *  point — the click flow prevents that case, but it degrades safely.) */
export function setEnd(sel: Selection, init: WaypointInit, makeId: IdGen): Selection {
  const wps = sel.waypoints.slice();
  if (wps.length <= 1) {
    wps.push(makeWaypoint(init, makeId()));
  } else {
    wps[wps.length - 1] = makeWaypoint(init, wps[wps.length - 1].id);
  }
  return { ...sel, waypoints: wps };
}

/** Insert a mid into `segmentIndex` (lands at full index segmentIndex+1, between the
 *  start and the end). No-op when there's no end to insert before. */
export function insertMid(
  sel: Selection,
  segmentIndex: number,
  init: WaypointInit,
  makeId: IdGen
): Selection {
  const at = segmentIndex + 1;
  // Must land strictly between the start (0) and the end (last): a mid can't be the
  // first or last element. Requires at least a start+end already.
  if (sel.waypoints.length < 2 || at < 1 || at > sel.waypoints.length - 1) return sel;
  const wps = sel.waypoints.slice();
  wps.splice(at, 0, makeWaypoint(init, makeId()));
  return { ...sel, waypoints: wps };
}

/** Move the waypoint at full index `index` to `coords`. Clears its voteEdgeId pin
 *  and address (a drag to a bare coordinate is no longer on the proposal edge and
 *  its address is stale — the caller refetches). Preserves the id (drag identity). */
export function updateAt(sel: Selection, index: number, coords: LatLng): Selection {
  if (index < 0 || index >= sel.waypoints.length) return sel;
  const wps = sel.waypoints.slice();
  wps[index] = { ...wps[index], coords, voteEdgeId: null, address: null };
  return { ...sel, waypoints: wps };
}

/** Remove the waypoint at full index `index`. Rebalancing is automatic: the new
 *  first becomes the start, the new last the end, the rest mids. */
export function removeAt(sel: Selection, index: number): Selection {
  if (index < 0 || index >= sel.waypoints.length) return sel;
  return { ...sel, waypoints: sel.waypoints.filter((_, i) => i !== index) };
}

/** Drop all waypoints (keep the requested vote type). */
export function clearWaypoints(sel: Selection): Selection {
  if (sel.waypoints.length === 0) return sel;
  return { ...sel, waypoints: [] };
}

export function setVoteType(sel: Selection, voteType: string): Selection {
  if (sel.voteType === voteType) return sel;
  return { ...sel, voteType };
}

/** Translate the UI's "start" | "end" | ghost-index addressing into a full index. */
export function fullIndexOf(sel: Selection, which: "start" | "end" | number): number {
  if (which === "start") return 0;
  if (which === "end") return sel.waypoints.length - 1;
  return which + 1; // ghost index → full index (skip the start)
}
