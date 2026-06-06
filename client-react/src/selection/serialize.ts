// ==========================================================================
// Selection ⇄ URL serialization
// ==========================================================================
// The shareable/persisted form of a selection is intentionally minimal: the
// ordered waypoint coordinates and the requested vote type. NOTHING else — no
// per-waypoint "is this a top proposal", no direction, no display state. Those are
// always re-derived from live data, so a link stays correct as votes change.
//
//   ?w=lat,lng;lat,lng;…&vt=<label>
//
// Coordinates use 6 decimal places (~0.11 m) — enough that re-snapping in dense
// node clusters resolves to the same feature the user picked.
//
// Legacy links (?slat/slng/elat/elng[,vt]) from before the `w` param are still
// read, so old shares keep working.

import type { LatLng } from "../types";
import type { Selection } from "./types";

const PRECISION = 6;

function fmt(n: number): string {
  return n.toFixed(PRECISION);
}

function parsePair(s: string): LatLng | null {
  const parts = s.split(",");
  if (parts.length !== 2) return null;
  const lat = Number(parts[0]);
  const lng = Number(parts[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return { lat, lng };
}

function legacyPair(params: URLSearchParams, latKey: string, lngKey: string): LatLng | null {
  const lat = params.get(latKey);
  const lng = params.get(lngKey);
  if (lat == null || lng == null) return null;
  return parsePair(`${lat},${lng}`);
}

/** Serialize a selection to URL params (`w` + optional `vt`). Empty selection →
 *  no `w`. The caller merges these with the camera params. */
export function selectionToParams(sel: Selection): URLSearchParams {
  const params = new URLSearchParams();
  if (sel.waypoints.length > 0) {
    params.set(
      "w",
      sel.waypoints.map((wp) => `${fmt(wp.coords.lat)},${fmt(wp.coords.lng)}`).join(";")
    );
  }
  if (sel.voteType) params.set("vt", sel.voteType);
  return params;
}

/** The param names this module owns — for stripping/replacing without touching the
 *  camera params (z/lat/lng). Includes the legacy point params. */
export const SELECTION_PARAM_KEYS = ["w", "vt", "slat", "slng", "elat", "elng"];

export interface ParsedSelection {
  /** Ordered waypoint coordinates (start … end). */
  waypoints: LatLng[];
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
  let waypoints: LatLng[];
  const w = params.get("w");
  if (w) {
    waypoints = w
      .split(";")
      .map(parsePair)
      .filter((x): x is LatLng => x !== null);
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
