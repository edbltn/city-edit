// ==========================================================================
// Vote heatmap → MapLibre GL
//
// Replaces GraphLayer's per-frame canvas strokes with a GeoJSON source of
// voted edges, styled by four line layers (halo/warm/hot/peak) declared in
// MapCanvas's style. Only edges with votes are pushed — the full
// network is already drawn by the PMTiles graph-edges layer — so setData
// payloads stay small regardless of graph size.
//
// Intensity is baked into feature properties (norm, tHot, tPeak) rather than
// feature-state: membership of the voted set changes on most vote events
// anyway (requiring setData), and properties can be used in layer filters
// where feature-state cannot.
// ==========================================================================

import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { getMapLibreMap, onMapLibreMap } from "../../map/maplibreInstance";
import type { GraphData } from "../../types";

export const HEAT_SOURCE_ID = "graph-live";

const EMPTY_FC = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;

// Last-built collection, so a recreated map (style/theme switch) can be
// re-primed without waiting for the next vote event.
let lastFC: GeoJSON.FeatureCollection = EMPTY_FC;

function pushToMap(map: maplibregl.Map | null): void {
  if (!map) return;
  const apply = () => {
    const src = map.getSource(HEAT_SOURCE_ID) as GeoJSONSource | undefined;
    src?.setData(lastFC);
  };
  if (map.isStyleLoaded()) apply();
  else map.once("load", apply);
}

// Re-prime whenever MapCanvas swaps in a new map instance.
onMapLibreMap((map) => pushToMap(map));

/** Loop-based max (matches GraphLayer.arrayMax) — avoids spread stack limits. */
function arrayMax(arr: number[]): number {
  let max = 0;
  for (let i = 0; i < arr.length; i++) if (arr[i] > max) max = arr[i];
  return max;
}

/**
 * Rebuild the voted-edge collection from graph data and push it to the live
 * MapLibre map. Call on any vote mutation (full fetch, delta, optimistic).
 */
export function syncHeatToMapLibre(data: Pick<GraphData, "nodes" | "edges" | "edge_votes">): void {
  const votes = data.edge_votes;
  if (!votes || !data.edges || !data.nodes) return;

  const maxVotes = Math.max(1, arrayMax(votes));
  const logMax = Math.log(maxVotes + 1);
  const features: GeoJSON.Feature[] = [];

  for (let i = 0; i < data.edges.length; i++) {
    const v = votes[i] ?? 0;
    if (v <= 0) continue;
    const [fromIdx, toIdx] = data.edges[i];
    const a = data.nodes[fromIdx];
    const b = data.nodes[toIdx];
    if (!a || !b) continue;
    const norm = Math.log(v + 1) / logMax;
    features.push({
      type: "Feature",
      id: i,
      properties: {
        norm,
        // Ramp-in factors for the hot core / bright peak passes, matching the
        // canvas renderer's (norm-0.2)/0.8 and (norm-0.7)/0.3 kick-ins.
        tHot: norm > 0.2 ? (norm - 0.2) / 0.8 : 0,
        tPeak: norm > 0.7 ? (norm - 0.7) / 0.3 : 0,
      },
      geometry: {
        type: "LineString",
        // GeoJSON is [lng, lat]; graph nodes are [lat, lng].
        coordinates: [[a[1], a[0]], [b[1], b[0]]],
      },
    });
  }

  lastFC = { type: "FeatureCollection", features };
  pushToMap(getMapLibreMap());
}
