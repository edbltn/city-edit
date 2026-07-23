// ==========================================================================
// MapLibre basemap status
//
// The MapLibre GL background is the primary basemap when WebGL is available;
// the Leaflet raster TileLayer is only mounted as a fallback when it isn't.
// GraphLayer also reads this to skip its zero-vote baseline pass (the PMTiles
// graph-edges layer already draws the network on the GPU).
// ==========================================================================

import { useSyncExternalStore } from "react";

export type MapLibreStatus = "pending" | "ready" | "failed";

let status: MapLibreStatus = "pending";
const listeners = new Set<(s: MapLibreStatus) => void>();

export function setMapLibreStatus(s: MapLibreStatus): void {
  if (s === status) return;
  status = s;
  listeners.forEach((l) => l(s));
}

export function getMapLibreStatus(): MapLibreStatus {
  return status;
}

export function isMapLibreReady(): boolean {
  return status === "ready";
}

/** Subscribe to status changes; returns an unsubscribe function. */
export function onMapLibreStatus(l: (s: MapLibreStatus) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/**
 * React hook: true while the GL basemap is live. Components use this to
 * switch between rendering their visuals as MapLibre layers vs. the legacy
 * Leaflet path (the no-WebGL fallback).
 */
export function useMapLibreLive(): boolean {
  return useSyncExternalStore(
    (cb) => onMapLibreStatus(cb),
    () => status === "ready",
  );
}
