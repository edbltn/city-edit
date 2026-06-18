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

// Block-vote payload broadcast by GraphLayer (which owns the /api/graph-votes
// fetch). MapLibreBackground colors the block fills from it via feature-state.
export interface BlockVotesDetail {
  blockVotes: number[]; // net deduped votes per block_id
  max: number;          // normalization ceiling (floored so quiet maps don't saturate)
}
export const BLOCK_VOTES_EVENT = "city-edit:block-votes";

/** fill-color / fill-opacity expressions driven by feature-state "heat" ∈ [0,1].
 *  At heat 0 the block is fully transparent (no votes → invisible); it ramps up
 *  through the active style's heat colors. Blocks ARE the heat display, so there
 *  is no baseline edge layer (edges show only on Leaflet hover/selection). */
function blockFillPaint(heat: MapStyle["heat"]): maplibregl.FillLayerSpecification["paint"] {
  const h = ["coalesce", ["feature-state", "heat"], 0] as const;
  return {
    "fill-color": [
      "interpolate", ["linear"], h,
      0.0, heat.halo,
      0.4, heat.warm,
      0.7, heat.hot,
      1.0, heat.peak,
    ],
    "fill-opacity": [
      "interpolate", ["linear"], h,
      0.0, 0.0,
      0.001, 0.28,
      1.0, 0.72,
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
      blocks: {
        type: "vector",
        url: `pmtiles://${blockTilesUrl}`,
        promoteId: "block_id",
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
      // Block heat — the primary vote display, colored from feature-state.
      {
        id: "block-heat",
        type: "fill",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockFillPaint(mapStyle.heat),
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
   *  unavailable and the raster fallback takes over). Idempotent upstream. */
  onReady?: () => void;
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

      // Base map has rendered for the first time — let the loader dismiss.
      map.on("load", () => onReady?.());

      mapRef.current = map;

      return () => {
        map.remove();
        mapRef.current = null;
      };
    } catch (err) {
      console.warn("MapLibre GL JS unavailable (WebGL required):", err);
      // Leaflet TileLayer provides the fallback base map — it's already
      // mounting, so consider the base map ready.
      onReady?.();
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
  // map's 1–2 votes don't saturate. The latest payload is retained so it applies
  // even if it arrives before the block source has loaded.
  useEffect(() => {
    const latest = { current: null as BlockVotesDetail | null };

    const apply = () => {
      const ml = mapRef.current;
      const detail = latest.current;
      if (!ml || !detail || !ml.getSource("blocks")) return;
      const { blockVotes, max } = detail;
      const denom = Math.log(Math.max(1, max) + 1);
      // Clear prior states, then set only the blocks that have votes (sparse).
      ml.removeFeatureState({ source: "blocks", sourceLayer: "blocks" });
      for (let id = 0; id < blockVotes.length; id++) {
        const v = blockVotes[id];
        if (v > 0) {
          ml.setFeatureState(
            { source: "blocks", sourceLayer: "blocks", id },
            { heat: Math.log(v + 1) / denom },
          );
        }
      }
    };

    const onVotes = (e: Event) => {
      latest.current = (e as CustomEvent<BlockVotesDetail>).detail;
      apply();
    };
    window.addEventListener(BLOCK_VOTES_EVENT, onVotes);
    // Re-apply whenever the block source (re)loads tiles.
    const ml = mapRef.current;
    ml?.on("sourcedata", apply);
    return () => {
      window.removeEventListener(BLOCK_VOTES_EVENT, onVotes);
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
