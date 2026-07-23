// ==========================================================================
// MapLibre map status
//
// The MapLibre GL map is the app's only map. "failed" means WebGL is
// unavailable — MapView shows a notice instead of a map, and the interactive
// subtree (which requires the facade) never mounts.
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

/** React hook: true while the GL map is live. */
export function useMapLibreLive(): boolean {
  return useSyncExternalStore(
    (cb) => onMapLibreStatus(cb),
    () => status === "ready",
  );
}
