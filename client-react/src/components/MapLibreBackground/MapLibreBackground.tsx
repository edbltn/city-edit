/**
 * MapLibre GL JS background map — renders base map tiles and the OSM graph
 * from PMTiles. Sits behind the Leaflet overlay (which handles interactive
 * layers: routes, markers, drag-to-insert).
 *
 * Camera is synchronized from the Leaflet map via `move` events.
 */

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { CONFIG } from "../../config";
import { maplibreRasterTiles, type MapStyle } from "../../mapStyles";

// Register PMTiles protocol once at module level
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// Leaflet zoom N ≠ MapLibre zoom N: Leaflet's scale is defined against 256px
// world tiles, MapLibre's against 512px, so the same number renders MapLibre
// at exactly 2× Leaflet's scale. Subtract 1 when driving the MapLibre camera
// from Leaflet or the two renderers agree only at the screen center and every
// Leaflet-drawn layer (pins, routes, hover edges) drifts outward from there.
const LEAFLET_TO_MAPLIBRE_ZOOM = 1;

// Block-vote payload broadcast by GraphLayer (which owns the /api/graph-votes
// fetch). MapLibreBackground colors the block fills from it via feature-state.
export interface BlockVotesDetail {
  blockVotes: number[]; // net deduped votes per block_id
  max: number;          // normalization ceiling (floored so quiet maps don't saturate)
}
export const BLOCK_VOTES_EVENT = "city-edit:block-votes";

// Block-selection payload broadcast by GraphLayer whenever the selection/hover
// changes: the real block ids covering the selected/hovered edges. Rendered as
// feature-state { selected } on the block-select layers (docs §2.4 highlight).
export interface BlockSelectDetail {
  blockIds: number[];
}
export const BLOCK_SELECT_EVENT = "city-edit:block-select";

/** fill-color / fill-opacity expressions driven by feature-state "heat" ∈ [0,1].
 *  At heat 0 the block is fully transparent (no votes → invisible); it ramps up
 *  through the active style's heat colors. Blocks ARE the heat display, so there
 *  is no baseline edge layer (edges show only on Leaflet hover/selection). */
function blockFillPaint(heat: MapStyle["heat"]): maplibregl.FillLayerSpecification["paint"] {
  const h = ["coalesce", ["feature-state", "heat"], 0] as const;
  return {
    // Start the ramp at `warm` (not halo) so even a single vote reads clearly,
    // and keep the fill translucent — the outline layer below carries the
    // brightness, so a voted block doesn't render as a solid slab.
    "fill-color": [
      "interpolate", ["linear"], h,
      0.0, heat.warm,
      0.2, heat.hot,
      0.6, heat.peak,
      1.0, heat.peak,
    ],
    "fill-opacity": [
      "interpolate", ["linear"], h,
      0.0, 0.0,
      0.001, 0.42,
      1.0, 0.58,
    ],
  };
}

/** Thin bright outline tracing a voted block — the crisp edge that makes the
 *  heat pop without thickening the fill. Same ramp, heat-driven opacity. */
function blockLinePaint(heat: MapStyle["heat"]): maplibregl.LineLayerSpecification["paint"] {
  const h = ["coalesce", ["feature-state", "heat"], 0] as const;
  return {
    "line-color": [
      "interpolate", ["linear"], h,
      0.0, heat.warm,
      0.2, heat.hot,
      0.6, heat.peak,
      1.0, heat.peak,
    ],
    "line-width": 1.25,
    "line-opacity": [
      "interpolate", ["linear"], h,
      0.0, 0.0,
      0.001, 0.75,
      1.0, 0.95,
    ],
  };
}

function buildStyle(
  graphTilesUrl: string,
  blockTilesUrl: string,
  tiles: string[],
  mapStyle: MapStyle,
): maplibregl.StyleSpecification {
  return {
    version: 8,
    name: "desire-path",
    sources: {
      // CartoDB raster tiles as base map (same as the Leaflet TileLayer)
      "carto-base": {
        type: "raster",
        tiles,
        tileSize: 256,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      },
      // Block polygons from PMTiles (one per street segment / merged foot path).
      // block_id is the NATIVE feature id (tippecanoe --use-attribute-for-id
      // moves the attribute onto the id and out of properties) — do NOT set
      // promoteId: it would look up the now-absent property and override every
      // id with undefined, silently detaching all feature-state (heat,
      // selection).
      blocks: {
        type: "vector",
        url: `pmtiles://${blockTilesUrl}`,
      },
    },
    layers: [
      // Background fill (shows through before tiles load) — matches the map style.
      {
        id: "background",
        type: "background",
        paint: { "background-color": mapStyle.base },
      },
      // Base map raster tiles
      {
        id: "carto-tiles",
        type: "raster",
        source: "carto-base",
        minzoom: 0,
        maxzoom: 19,
      },
      // Block heat — the primary vote display, colored from feature-state:
      // a translucent fill plus a thin bright outline (brightness without bulk).
      {
        id: "block-heat",
        type: "fill",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockFillPaint(mapStyle.heat),
      },
      {
        id: "block-heat-line",
        type: "line",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockLinePaint(mapStyle.heat),
      },
      // Block selection — feature-state { selected } lights the block polygons
      // covering the current selection/hover: a subtle translucent fill plus a
      // 2px casing in the style's selection token (white on dark, near-black on
      // light), so it reads as selection over the heat in both themes.
      {
        id: "block-select",
        type: "fill",
        source: "blocks",
        "source-layer": "blocks",
        paint: {
          "fill-color": mapStyle.selection,
          "fill-opacity": [
            "case", ["boolean", ["feature-state", "selected"], false], 0.14, 0,
          ],
        },
      },
      {
        id: "block-select-casing",
        type: "line",
        source: "blocks",
        "source-layer": "blocks",
        paint: {
          "line-color": mapStyle.selection,
          "line-width": 2,
          "line-opacity": [
            "case", ["boolean", ["feature-state", "selected"], false], 0.85, 0,
          ],
        },
      },
    ],
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  };
}

interface MapLibreBackgroundProps {
  /** Leaflet map instance to sync camera from */
  leafletMap: L.Map | null;
  /** Active map style — drives basemap tiles + background color */
  mapStyle: MapStyle;
  /** Called once the base map has first loaded (or immediately if WebGL is
   *  unavailable and the raster fallback takes over). `active` is true only in
   *  the WebGL case — the caller uses it to drop the opaque Leaflet raster
   *  fallback, WITHOUT which every MapLibre layer (block heat, block selection)
   *  is painted over and invisible. Idempotent upstream. */
  onReady?: (active: boolean) => void;
}

export function MapLibreBackground({ leafletMap, mapStyle, onReady }: MapLibreBackgroundProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  // Initialize MapLibre map (gracefully handles WebGL unavailability)
  useEffect(() => {
    if (!containerRef.current) return;

    try {
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: buildStyle(
          CONFIG.graphTilesUrl,
          CONFIG.blockTilesUrl,
          maplibreRasterTiles(mapStyle),
          mapStyle,
        ),
        center: [CONFIG.initialView.lon, CONFIG.initialView.lat],
        zoom: CONFIG.initialView.zoom,
        interactive: false,
        attributionControl: false,
      });

      map.on("error", (e) => {
        console.warn("MapLibre error (non-fatal):", e.error?.message ?? e);
      });

      // Base map has rendered for the first time — let the loader dismiss and
      // the Leaflet raster fallback unmount (MapLibre is the base map now).
      map.on("load", () => onReady?.(true));

      mapRef.current = map;
      if (import.meta.env.DEV) {
        (window as unknown as Record<string, unknown>).__ml = map;
      }

      return () => {
        map.remove();
        mapRef.current = null;
      };
    } catch (err) {
      console.warn("MapLibre GL JS unavailable (WebGL required):", err);
      // Leaflet TileLayer provides the fallback base map — it's already
      // mounting, so consider the base map ready (and keep the raster).
      onReady?.(false);
    }
    // mapStyle is resolved once at bootstrap and stable for the session.
    // onReady is a stable callback (useCallback) so it won't re-init the map.
  }, [mapStyle, onReady]);

  // Sync camera from Leaflet
  useEffect(() => {
    if (!leafletMap) return;

    const syncCamera = () => {
      const ml = mapRef.current;
      if (!ml) return;
      const center = leafletMap.getCenter();
      const zoom = leafletMap.getZoom();
      ml.jumpTo({
        center: [center.lng, center.lat],
        zoom,
        bearing: 0,
        pitch: 0,
      });
    };

    // Sync on every move frame for smooth panning
    leafletMap.on("move", syncCamera);
    // Initial sync
    syncCamera();

    return () => {
      leafletMap.off("move", syncCamera);
    };
  }, [leafletMap]);

  // Color the block fills from GraphLayer's block-vote broadcasts via
  // feature-state. Heat is log-normalized (like the old edge heatmap) so a quiet
  // map's 1–2 votes don't saturate. The latest payloads (votes + selection) are
  // retained so they apply even if they arrive before the block source loads.
  useEffect(() => {
    const latest = { current: null as BlockVotesDetail | null };
    // Block ids currently holding feature-state { selected: true }.
    const selected = { current: [] as number[] };

    const featureOf = (id: number) => ({ source: "blocks", sourceLayer: "blocks", id });

    const applySelected = () => {
      const ml = mapRef.current;
      if (!ml || !ml.getSource("blocks")) return;
      for (const id of selected.current) {
        ml.setFeatureState(featureOf(id), { selected: true });
      }
    };

    const apply = () => {
      const ml = mapRef.current;
      const detail = latest.current;
      if (!ml || !ml.getSource("blocks")) return;
      if (!detail) {
        applySelected();
        return;
      }
      const { blockVotes, max } = detail;
      const denom = Math.log(Math.max(1, max) + 1);
      // Clear prior states, then set only the blocks that have votes (sparse).
      // The wholesale clear drops the `selected` key too — re-apply it after.
      ml.removeFeatureState({ source: "blocks", sourceLayer: "blocks" });
      for (let id = 0; id < blockVotes.length; id++) {
        const v = blockVotes[id];
        if (v > 0) {
          ml.setFeatureState(featureOf(id), { heat: Math.log(v + 1) / denom });
        }
      }
      applySelected();
    };

    const onVotes = (e: Event) => {
      latest.current = (e as CustomEvent<BlockVotesDetail>).detail;
      apply();
    };

    // Selection updates are incremental per-key writes ({ selected: false } on
    // the blocks leaving, true on the ones entering) — never removeFeatureState,
    // which would also drop the heat key set above.
    const onSelect = (e: Event) => {
      const next = (e as CustomEvent<BlockSelectDetail>).detail.blockIds;
      const ml = mapRef.current;
      if (ml && ml.getSource("blocks")) {
        const nextSet = new Set(next);
        for (const id of selected.current) {
          if (!nextSet.has(id)) ml.setFeatureState(featureOf(id), { selected: false });
        }
        for (const id of next) {
          ml.setFeatureState(featureOf(id), { selected: true });
        }
      }
      selected.current = next;
    };

    window.addEventListener(BLOCK_VOTES_EVENT, onVotes);
    window.addEventListener(BLOCK_SELECT_EVENT, onSelect);
    // Re-apply whenever the block source (re)loads tiles.
    const ml = mapRef.current;
    ml?.on("sourcedata", apply);
    return () => {
      window.removeEventListener(BLOCK_VOTES_EVENT, onVotes);
      window.removeEventListener(BLOCK_SELECT_EVENT, onSelect);
      ml?.off("sourcedata", apply);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        height: "100%",
        zIndex: 0,
      }}
    />
  );
}
