// ==========================================================================
// Selection ⇄ URL serialization
// ==========================================================================
// The shareable/persisted form of a selection is intentionally minimal: the
// ordered waypoint coordinates and the requested vote type. NOTHING else — no
// per-waypoint "is this a top proposal", no direction, no display state. Those are
// always re-derived from live data, so a link stays correct as votes change.
//
//   ?w=lat,lng[,f<proposalId>];lat,lng;…&vt=<label>
//
// The one exception to "nothing else": a waypoint token may carry a forced-
// corridor marker `f<proposalId>` — the user threaded the segment leaving that
// waypoint through a route proposal's corridor, which IS navigable state (it
// changes the route), not display state. Only the deterministic proposal id is
// carried; the restored client re-resolves the live proposal and degrades to
// normal routing when it no longer exists.
//
// Coordinates use 6 decimal places (~0.11 m) — enough that re-snapping in dense
// node clusters resolves to the same feature the user picked.
//
// Legacy links (?slat/slng/elat/elng[,vt]) from before the `w` param are still
// read, so old shares keep working. They are READ-ONLY: nothing in the app emits
// them any more, and the URL mirror rewrites them to `w=` on the first render, so
// a printed QR poster from the old format normalises itself the moment it lands.
// Those links exist on paper and cannot be reissued — never stop parsing them.
//
// See docs/url-routing.md#query-param-inventory for the full param taxonomy
// (which params are state and get mirrored, which are load-time input and get
// consumed).

import type { LatLng } from "../types";
import type { Selection, SelWaypoint } from "./types";

const PRECISION = 6;

/** Forced-corridor URL marker: `f` + the proposal's deterministic hex id. */
const FORCED_TOKEN = /^f[0-9a-f]{1,16}$/i;

function fmt(n: number): string {
  return n.toFixed(PRECISION);
}

export interface ParsedWaypoint {
  coords: LatLng;
  /** Proposal id of a forced corridor leaving this waypoint, or null. */
  forcedProposalId: string | null;
}

function parseWaypointToken(s: string): ParsedWaypoint | null {
  const parts = s.split(",");
  if (parts.length !== 2 && parts.length !== 3) return null;
  const lat = Number(parts[0]);
  const lng = Number(parts[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  const forced = parts.length === 3 && FORCED_TOKEN.test(parts[2]) ? parts[2].slice(1) : null;
  if (parts.length === 3 && !forced) return null; // malformed third field
  return { coords: { lat, lng }, forcedProposalId: forced };
}

function legacyPair(params: URLSearchParams, latKey: string, lngKey: string): ParsedWaypoint | null {
  const lat = params.get(latKey);
  const lng = params.get(lngKey);
  if (lat == null || lng == null) return null;
  return parseWaypointToken(`${lat},${lng}`);
}

/**
 * The minimum a caller must supply to serialize a selection. Structurally
 * satisfied by a real {@link Selection}, so the live selection and a synthesized
 * one (a share link, a sticker target) go through the SAME writer — there is
 * exactly one place that knows how to spell a selection as a URL.
 */
export interface SerializableSelection {
  /** Only `coords` is required; the runtime sugar a real SelWaypoint carries
   *  (id / address / voteEdgeId) is accepted and ignored, so callers can pass a
   *  live waypoint or a bare coordinate without ceremony. */
  readonly waypoints: readonly (Partial<SelWaypoint> & { coords: LatLng })[];
  readonly voteType?: string | null;
}

/** Serialize a selection to URL params (`w` + optional `vt`). Empty selection →
 *  no `w`. The caller merges these with the camera params. */
export function selectionToParams(sel: SerializableSelection): URLSearchParams {
  const params = new URLSearchParams();
  if (sel.waypoints.length > 0) {
    params.set(
      "w",
      sel.waypoints
        .map((wp) => {
          const base = `${fmt(wp.coords.lat)},${fmt(wp.coords.lng)}`;
          return wp.forcedCorridor ? `${base},f${wp.forcedCorridor.proposalId}` : base;
        })
        .join(";")
    );
  }
  if (sel.voteType) params.set("vt", sel.voteType);
  return params;
}

/** The canonical selection params — the only ones this module ever WRITES. */
export const SELECTION_PARAM_KEYS = ["w", "vt"] as const;

/** Pre-`w` selection params. Read forever (printed QR posters carry them), never
 *  written; stripped once the mirror has rewritten them as `w`. */
export const LEGACY_SELECTION_PARAM_KEYS = ["slat", "slng", "elat", "elng"] as const;

/** Every param the selection consumes — what the URL mirror clears before
 *  writing the canonical form, so a legacy link normalises instead of doubling
 *  up. Camera params (z/lat/lng) are NOT here: they belong to mapViewState. */
export const CONSUMED_SELECTION_PARAM_KEYS = [
  ...SELECTION_PARAM_KEYS,
  ...LEGACY_SELECTION_PARAM_KEYS,
];

export interface ParsedSelection {
  /** Ordered waypoints (start … end), each with an optional forced-corridor id. */
  waypoints: ParsedWaypoint[];
  /** Requested vote type, or null when absent. */
  voteType: string | null;
}

/**
 * Parse a selection out of URL params, or null when there's nothing to restore.
 * On a station network the points collapse to just the first (a single start) —
 * those maps never hold a route.
 */
export function selectionFromParams(
  params: URLSearchParams,
  opts?: { stationNetwork?: boolean }
): ParsedSelection | null {
  let waypoints: ParsedWaypoint[];
  const w = params.get("w");
  if (w) {
    waypoints = w
      .split(";")
      .map(parseWaypointToken)
      .filter((x): x is ParsedWaypoint => x !== null);
  } else {
    // Legacy back-compat: ?slat/slng[&elat/elng].
    waypoints = [];
    const start = legacyPair(params, "slat", "slng");
    const end = legacyPair(params, "elat", "elng");
    if (start) waypoints.push(start);
    if (end) waypoints.push(end);
  }

  const voteType = params.get("vt");
  if (waypoints.length === 0 && !voteType) return null;

  if (opts?.stationNetwork && waypoints.length > 1) {
    waypoints = waypoints.slice(0, 1);
  }

  return { waypoints, voteType };
}
