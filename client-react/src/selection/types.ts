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

/** The segment leaving a waypoint is FORCIBLY routed through a route-based top
 *  proposal's corridor (the user threaded it there by dropping onto the diamond)
 *  instead of being computed as the min-cost path. `edgeIds` is a snapshot of the
 *  corridor's ordered path edges taken at threading time, so the forced geometry
 *  stays stable even if live vote deltas reshape or retire the proposal;
 *  `proposalId` is the deterministic proposal id (also what the URL carries — a
 *  restored link re-resolves the live proposal and falls back gracefully when it
 *  no longer exists). Cleared by the reducer whenever the user edits either end
 *  of the pair or inserts a waypoint between them. */
export interface ForcedCorridor {
  proposalId: string;
  edgeIds?: number[];
}

/** One selected waypoint. `coords` (plus a forced-corridor marker) is what the URL
 *  carries; the rest is runtime sugar: `address` for the banner, `voteEdgeId` to pin
 *  a vote to the exact edge of a chosen top-proposal (vs. re-snapping a bare
 *  coordinate), and `id` for stable React keys / drag identity (mirrors the old
 *  ghostWaypointIds). `forcedCorridor` annotates the segment from THIS waypoint to
 *  the NEXT one. */
export interface SelWaypoint {
  coords: LatLng;
  address?: string | null;
  voteEdgeId?: number | null;
  forcedCorridor?: ForcedCorridor | null;
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
