// ==========================================================================
// Hover / pinned highlight → MapLibre GL
//
// Renders the white selection rings as GL layers instead of the hover canvas.
// The source holds at most two features (pinned + hover); each is either the
// hovered edge (LineString) or node (Point) with an `alpha` property (1.0
// pinned, 0.6 hover — same as the canvas renderer). Layer styling in
// MapCanvas replicates the canvas ring geometry:
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
  if (map.getSource(HIGHLIGHT_SOURCE_ID)) {
    apply();
    return;
  }
  // The source exists as soon as the style JSON is parsed — don't wait for
  // the map "load" event, which also waits for every initial TILE fetch and
  // would hold early data (e.g. the /api/heat fast path) hostage to the
  // slowest tile.
  const onStyleData = () => {
    if (!map.getSource(HIGHLIGHT_SOURCE_ID)) return;
    map.off("styledata", onStyleData);
    apply();
  };
  map.on("styledata", onStyleData);
}

// Re-prime whenever MapCanvas swaps in a new map instance.
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
      properties: { alpha, kind: "ring" },
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
    properties: { alpha, kind: "ring" },
    geometry: { type: "Point", coordinates: [node[1], node[0]] },
  };
}

// The GIS-style yellow selection: every edge in the corridor as one
// MultiLineString, styled by the highlight-corridor layers in MapCanvas.
function corridorFeature(
  data: Pick<GraphData, "nodes" | "edges">,
  edgeIds: number[],
): GeoJSON.Feature | null {
  const lines: [number, number][][] = [];
  for (const i of edgeIds) {
    const e = data.edges[i];
    if (!e || e[0] === e[1]) continue;
    const a = data.nodes[e[0]];
    const b = data.nodes[e[1]];
    if (!a || !b) continue;
    lines.push([[a[1], a[0]], [b[1], b[0]]]);
  }
  if (lines.length === 0) return null;
  return {
    type: "Feature",
    properties: { kind: "corridor" },
    geometry: { type: "MultiLineString", coordinates: lines },
  };
}

/**
 * Set the current highlights. Pass null to clear any slot. When hover
 * resolves to the pinned target the caller suppresses it (same rule as the
 * canvas renderer), so this never double-draws. `corridor` is the connected
 * set of edges sharing the selected top proposal's vote type — rendered as
 * the QGIS-style yellow selection under the rings.
 */
export function syncHighlightsToMapLibre(
  data: Pick<GraphData, "nodes" | "edges"> | null,
  pinned: HighlightTarget | null,
  hover: HighlightTarget | null,
  corridor: number[] | null = null,
): void {
  const features: GeoJSON.Feature[] = [];
  if (data) {
    if (corridor?.length) {
      const f = corridorFeature(data, corridor);
      if (f) features.push(f);
    }
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
