// ==========================================================================
// Route / desire-path visuals → MapLibre GL
//
// Generic keyed-feature registry per GeoJSON source. React components own
// their features (add on mount/geometry change, remove on unmount); this
// module rebuilds the source's FeatureCollection and pushes it to the live
// map, re-priming when MapCanvas swaps the instance (style change).
//
// ==========================================================================

import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { getMapLibreMap, onMapLibreMap } from "./maplibreInstance";

export const ROUTE_SOURCE_ID = "route-path";
export const DESIRE_SOURCE_ID = "desire-path";
// Utility guide lines: marker/path drag trails + waypoint connectors (the
// dashed grey lines that were Leaflet polylines in the overlay pane).
export const UTIL_SOURCE_ID = "util-lines";

const registries = new Map<string, Map<string, GeoJSON.Feature>>();

function registry(sourceId: string): Map<string, GeoJSON.Feature> {
  let r = registries.get(sourceId);
  if (!r) {
    r = new Map();
    registries.set(sourceId, r);
  }
  return r;
}

function pushSource(map: maplibregl.Map | null, sourceId: string): void {
  if (!map) return;
  const apply = () => {
    const src = map.getSource(sourceId) as GeoJSONSource | undefined;
    src?.setData({
      type: "FeatureCollection",
      features: [...registry(sourceId).values()],
    });
  };
  if (map.getSource(sourceId)) {
    apply();
    return;
  }
  // The source exists once the style JSON parses — don't wait for "load",
  // which also waits for every initial tile fetch.
  const onStyleData = () => {
    if (!map.getSource(sourceId)) return;
    map.off("styledata", onStyleData);
    apply();
  };
  map.on("styledata", onStyleData);
}

// Re-prime all registered sources whenever a new map instance appears.
onMapLibreMap((map) => {
  if (!map) return;
  for (const sourceId of registries.keys()) pushSource(map, sourceId);
});

/** Add or replace one feature in a source. */
export function setOverlayFeature(
  sourceId: string,
  key: string,
  geometry: GeoJSON.Geometry,
): void {
  registry(sourceId).set(key, { type: "Feature", properties: { key }, geometry });
  pushSource(getMapLibreMap(), sourceId);
}

/** Remove one feature from a source (no-op if absent). */
export function removeOverlayFeature(sourceId: string, key: string): void {
  if (registry(sourceId).delete(key)) pushSource(getMapLibreMap(), sourceId);
}

// Desire-path dimming during a path/waypoint drag — replaces the old CSS rule
// that faded the Leaflet desirePathPane to 0.25 ("this section will change").
const DESIRE_DIM = 0.25;

export function setDesirePathDimmed(dimmed: boolean): void {
  const map = getMapLibreMap();
  if (!map || !map.isStyleLoaded()) return;
  const factor = dimmed ? DESIRE_DIM : 1;
  if (map.getLayer("desire-fill")) {
    map.setPaintProperty("desire-fill", "line-opacity", 0.12 * factor);
  }
  if (map.getLayer("desire-ring")) {
    map.setPaintProperty("desire-ring", "line-opacity", factor);
  }
}
