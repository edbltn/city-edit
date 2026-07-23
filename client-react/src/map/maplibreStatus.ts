// ==========================================================================
// MapLibre basemap status
//
// The MapLibre GL background is the primary basemap when WebGL is available;
// the Leaflet raster TileLayer is only mounted as a fallback when it isn't.
// GraphLayer also reads this to skip its zero-vote baseline pass (the PMTiles
// graph-edges layer already draws the network on the GPU).
// ==========================================================================

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
