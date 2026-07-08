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
import type { ForcedCorridor, Selection, SelWaypoint } from "./types";

export type IdGen = () => string;

export interface WaypointInit {
  coords: LatLng;
  address?: string | null;
  voteEdgeId?: number | null;
  forcedCorridor?: ForcedCorridor | null;
}

function makeWaypoint(init: WaypointInit, id: string): SelWaypoint {
  return {
    coords: init.coords,
    address: init.address ?? null,
    voteEdgeId: init.voteEdgeId ?? null,
    forcedCorridor: init.forcedCorridor ?? null,
    id,
  };
}

function sameCoords(a: LatLng, b: LatLng): boolean {
  return a.lat === b.lat && a.lng === b.lng;
}

/** Drop the forced-corridor flag on `wps[index]` in place (array already copied).
 *  The flag annotates the segment LEAVING index, so any edit that invalidates that
 *  segment funnels through here. */
function clearForcedAt(wps: SelWaypoint[], index: number): void {
  if (index >= 0 && index < wps.length && wps[index].forcedCorridor) {
    wps[index] = { ...wps[index], forcedCorridor: null };
  }
}

/** Place/replace the START (full index 0), preserving any mids + end. Appends when
 *  the selection is empty. A start drag reuses the slot's id (no remount). MOVING
 *  the start breaks a forced corridor that departs from it; an in-place replace
 *  (same coords, e.g. re-pinning a vote edge) keeps the flag unless the caller
 *  supplies its own. */
export function setStart(sel: Selection, init: WaypointInit, makeId: IdGen): Selection {
  const wps = sel.waypoints.slice();
  if (wps.length === 0) {
    wps.push(makeWaypoint(init, makeId()));
  } else {
    const old = wps[0];
    const w = makeWaypoint(init, old.id);
    if (init.forcedCorridor === undefined && sameCoords(old.coords, w.coords)) {
      w.forcedCorridor = old.forcedCorridor ?? null;
    }
    wps[0] = w;
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
    const old = wps[wps.length - 1];
    const w = makeWaypoint(init, old.id);
    wps[wps.length - 1] = w;
    // Moving the end breaks a forced corridor ARRIVING at it (the flag lives on
    // the predecessor — the segment's leading point).
    if (!sameCoords(old.coords, w.coords)) clearForcedAt(wps, wps.length - 2);
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
  // Splitting a forcibly-routed segment un-forces it (introducing a waypoint
  // between a corridor's anchors reverts the pair to normal routing).
  clearForcedAt(wps, at - 1);
  wps.splice(at, 0, makeWaypoint(init, makeId()));
  return { ...sel, waypoints: wps };
}

/** Move the waypoint at full index `index` to `coords`. Clears its voteEdgeId pin
 *  and address (a drag to a bare coordinate is no longer on the proposal edge and
 *  its address is stale — the caller refetches). Preserves the id (drag identity).
 *  Breaks a forced corridor on EITHER side of the moved point: its own flag (the
 *  segment leaving it) and its predecessor's (the segment arriving at it). */
export function updateAt(sel: Selection, index: number, coords: LatLng): Selection {
  if (index < 0 || index >= sel.waypoints.length) return sel;
  const wps = sel.waypoints.slice();
  wps[index] = { ...wps[index], coords, voteEdgeId: null, address: null, forcedCorridor: null };
  clearForcedAt(wps, index - 1);
  return { ...sel, waypoints: wps };
}

/** Remove the waypoint at full index `index`. Rebalancing is automatic: the new
 *  first becomes the start, the new last the end, the rest mids. The predecessor's
 *  forced corridor (the segment that arrived at the removed point) breaks; the
 *  removed point's own flag leaves with it. */
export function removeAt(sel: Selection, index: number): Selection {
  if (index < 0 || index >= sel.waypoints.length) return sel;
  const wps = sel.waypoints.filter((_, i) => i !== index);
  clearForcedAt(wps, index - 1);
  return { ...sel, waypoints: wps };
}

/** Stamp (or clear, with null) the forced-corridor flag on the segment LEAVING
 *  full index `index`. No-op on the last waypoint — there is no segment after it. */
export function setForcedCorridorAt(
  sel: Selection,
  index: number,
  forcedCorridor: ForcedCorridor | null
): Selection {
  if (index < 0 || index >= sel.waypoints.length - 1) return sel;
  const wps = sel.waypoints.slice();
  wps[index] = { ...wps[index], forcedCorridor };
  return { ...sel, waypoints: wps };
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
