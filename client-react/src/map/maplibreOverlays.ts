// ==========================================================================
// Route / desire-path visuals → MapLibre GL
//
// Generic keyed-feature registry per GeoJSON source. React components own
// their features (add on mount/geometry change, remove on unmount); this
// module rebuilds the source's FeatureCollection and pushes it to the live
// map, re-priming when MapLibreBackground swaps the instance (style change).
//
// Only the *visuals* live here. The transparent interactive hit-lines, drag
// handlers, and markers stay on the Leaflet overlay until camera ownership
// flips to MapLibre.
// ==========================================================================

import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { getMapLibreMap, onMapLibreMap } from "./maplibreInstance";

export const ROUTE_SOURCE_ID = "route-path";
export const DESIRE_SOURCE_ID = "desire-path";

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
  if (map.isStyleLoaded()) apply();
  else map.once("load", apply);
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
