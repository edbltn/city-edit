// ==========================================================================
// MapLibre map instance registry
//
// The MapLibre GL map is created by MapCanvas but consumed by other
// modules (vote heat sync, and eventually hit-testing) that live outside its
// React subtree. This module holds the live instance the same way
// maplibreStatus holds the load state. The instance is replaced when the map
// style changes (MapCanvas recreates the map), so consumers should
// subscribe rather than cache the map they got.
// ==========================================================================

import type maplibregl from "maplibre-gl";

let instance: maplibregl.Map | null = null;
const listeners = new Set<(m: maplibregl.Map | null) => void>();

export function setMapLibreMap(m: maplibregl.Map | null): void {
  if (m === instance) return;
  instance = m;
  if (import.meta.env.DEV) {
    // Debug/perf-harness handle to the live map.
    (window as unknown as { __mlMap?: maplibregl.Map | null }).__mlMap = m;
  }
  listeners.forEach((l) => l(m));
}

export function getMapLibreMap(): maplibregl.Map | null {
  return instance;
}

/** Subscribe to instance changes; returns an unsubscribe function. */
export function onMapLibreMap(l: (m: maplibregl.Map | null) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
