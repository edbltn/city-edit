// ==========================================================================
// Hover / pinned highlight → MapLibre GL
//
// Renders the white selection rings as GL layers instead of the hover canvas.
// The source holds at most two features (pinned + hover); each is either the
// hovered edge (LineString) or node (Point) with an `alpha` property (1.0
// pinned, 0.6 hover — same as the canvas renderer). Layer styling in
// MapLibreBackground replicates the canvas ring geometry:
//   edge: 1.5px white borders around a 4px gap (line-gap-width), plus a faint
//         4px interior stroke at 0.12 alpha
//   node: 3.5px-radius circle, 1.5px white stroke, 0.12-alpha interior
// ==========================================================================

import type maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { getMapLibreMap, onMapLibreMap } from "../../map/maplibreInstance";
import type { GraphData } from "../../types";

export const HIGHLIGHT_SOURCE_ID = "graph-highlight";

interface HighlightTarget {
  kind: "edge" | "node";
  index: number;
}

const EMPTY_FC = { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection;

let lastFC: GeoJSON.FeatureCollection = EMPTY_FC;

function pushToMap(map: maplibregl.Map | null): void {
  if (!map) return;
  const apply = () => {
    const src = map.getSource(HIGHLIGHT_SOURCE_ID) as GeoJSONSource | undefined;
    src?.setData(lastFC);
  };
  if (map.isStyleLoaded()) apply();
  else map.once("load", apply);
}

// Re-prime whenever MapLibreBackground swaps in a new map instance.
onMapLibreMap((map) => pushToMap(map));

function targetFeature(
  data: Pick<GraphData, "nodes" | "edges">,
  t: HighlightTarget,
  alpha: number,
): GeoJSON.Feature | null {
  if (t.kind === "edge") {
    const edge = data.edges[t.index];
    if (!edge) return null;
    const a = data.nodes[edge[0]];
    const b = data.nodes[edge[1]];
    if (!a || !b) return null;
    return {
      type: "Feature",
      properties: { alpha },
      geometry: {
        type: "LineString",
        coordinates: [[a[1], a[0]], [b[1], b[0]]],
      },
    };
  }
  const node = data.nodes[t.index];
  if (!node) return null;
  return {
    type: "Feature",
    properties: { alpha },
    geometry: { type: "Point", coordinates: [node[1], node[0]] },
  };
}

/**
 * Set the current highlights. Pass null to clear either slot. When hover
 * resolves to the pinned target the caller suppresses it (same rule as the
 * canvas renderer), so this never double-draws.
 */
export function syncHighlightsToMapLibre(
  data: Pick<GraphData, "nodes" | "edges"> | null,
  pinned: HighlightTarget | null,
  hover: HighlightTarget | null,
): void {
  const features: GeoJSON.Feature[] = [];
  if (data) {
    if (pinned) {
      const f = targetFeature(data, pinned, 1.0);
      if (f) features.push(f);
    }
    if (hover) {
      const f = targetFeature(data, hover, 0.6);
      if (f) features.push(f);
    }
  }
  lastFC = features.length ? { type: "FeatureCollection", features } : EMPTY_FC;
  pushToMap(getMapLibreMap());
}
