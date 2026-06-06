// ==========================================================================
// Canonical selection model
// ==========================================================================
// One ordered list of waypoints is the SINGLE source of truth for what the user
// has selected. start = waypoints[0], end = waypoints[n-1], mids = the rest.
// Everything else is derived:
//   - pointType / the phase the UI is in (selectionPhase)
//   - whether a waypoint sits on a "top proposal" (recomputed from live votes)
//   - the *effective* vote type (resolveEffectiveVoteType)
//
// The URL serializes only the waypoint coordinates + the requested vote type
// (see serialize.ts). Nothing else is persisted — display state is always
// derived from current data.

import type { LatLng } from "../types";

/** One selected waypoint. `coords` is the only field the URL carries; the rest is
 *  runtime sugar: `address` for the banner, `voteEdgeId` to pin a vote to the exact
 *  edge of a chosen top-proposal (vs. re-snapping a bare coordinate), and `id` for
 *  stable React keys / drag identity (mirrors the old ghostWaypointIds). */
export interface SelWaypoint {
  coords: LatLng;
  address?: string | null;
  voteEdgeId?: number | null;
  id: string;
}

/** The canonical selection: ordered waypoints + the requested vote-type label. */
export interface Selection {
  waypoints: SelWaypoint[];
  /** The vote-type label the user requested (from a click or a deep link). It may
   *  not be valid for the current map; the *effective* label the Cast control uses
   *  is resolved separately (see resolveEffectiveVoteType). */
  voteType: string;
}

export const EMPTY_SELECTION: Selection = { waypoints: [], voteType: "" };

/** The four UI states, as a pure projection of the selection. */
export type SelectionPhase = "empty" | "point" | "route" | "route-mids";

export function selectionPhase(
  sel: Selection,
  isStationNetwork: boolean
): SelectionPhase {
  const n = sel.waypoints.length;
  if (n === 0) return "empty";
  // Station networks (ebikes) only ever vote on a single fixed point — never a
  // route — so any selection is a "point" regardless of how many coords arrived.
  if (n === 1 || isStationNetwork) return "point";
  if (n === 2) return "route";
  return "route-mids";
}
