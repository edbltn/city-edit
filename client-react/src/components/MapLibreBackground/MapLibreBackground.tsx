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
import { DESIRE_PATH } from "../../colors";
import { maplibreRasterTiles, type HeatRamp, type MapStyle } from "../../mapStyles";
import { setMapLibreStatus } from "../../map/maplibreStatus";
import { setMapLibreMap } from "../../map/maplibreInstance";
import { HEAT_SOURCE_ID } from "../GraphLayer/maplibreHeat";

// Matches Leaflet's zoom animation duration (0.25s CSS transition) so the
// MapLibre camera glides in step with Leaflet's animated zoom.
const LEAFLET_ZOOM_ANIM_MS = 250;

// Leaflet zoom N = 256·2^N px world; MapLibre zoom N = 512·2^N px world.
// MapLibre must run one level below Leaflet or the basemap renders at 2×
// scale relative to the Leaflet-drawn overlays.
const ML_ZOOM_OFFSET = -1;

// Register PMTiles protocol once at module level
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// The canvas heatmap scaled strokes by 2^((leafletZoom-14)/2). The MapLibre
// camera runs one level below Leaflet (ML_ZOOM_OFFSET), so the equivalent
// curve here is sqrt(2)^(zoom-13) — expressed as an exponential interpolation
// whose endpoints are that curve evaluated at zooms 5 and 21 (factor 1/16 and
// 16). `base` is a per-feature width expression (a function of vote norm).
function zoomScaled(base: unknown): maplibregl.ExpressionSpecification {
  return [
    "interpolate", ["exponential", Math.SQRT2], ["zoom"],
    5, ["*", 0.0625, base],
    21, ["*", 16, base],
  ] as unknown as maplibregl.ExpressionSpecification;
}

// Vote heatmap layers over the `graph-live` GeoJSON source (voted edges only,
// pushed by GraphLayer/maplibreHeat.ts). Four passes replicate the canvas
// renderer's cross-stroke gradient: wide faint halo → dominant warm stroke →
// hot core (kicks in past norm 0.2) → bright peak (past 0.7). MapLibre has no
// additive/multiply blending against the basemap, so stacked translucent
// strokes approximate the old screen/multiply canvas composite.
function heatLayers(heat: HeatRamp): maplibregl.LayerSpecification[] {
  const round = { "line-cap": "round" as const, "line-join": "round" as const };
  const norm = ["get", "norm"];
  const tHot = ["get", "tHot"];
  const tPeak = ["get", "tPeak"];
  return [
    {
      id: "heat-halo",
      type: "line",
      source: HEAT_SOURCE_ID,
      layout: round,
      paint: {
        "line-color": heat.halo,
        "line-width": zoomScaled(["+", 2, ["*", 8, norm]]),
        "line-opacity": ["+", 0.025, ["*", 0.06, norm]] as unknown as number,
      },
    },
    {
      id: "heat-warm",
      type: "line",
      source: HEAT_SOURCE_ID,
      layout: round,
      paint: {
        "line-color": heat.warm,
        "line-width": zoomScaled(["+", 1, ["*", 2, norm]]),
        "line-opacity": ["+", 0.08, ["*", 0.2, norm]] as unknown as number,
      },
    },
    {
      id: "heat-hot",
      type: "line",
      source: HEAT_SOURCE_ID,
      filter: [">", ["get", "norm"], 0.2],
      layout: round,
      paint: {
        "line-color": heat.hot,
        "line-width": zoomScaled(["+", 0.6, tHot]),
        "line-opacity": ["+", 0.1, ["*", 0.3, tHot]] as unknown as number,
      },
    },
    {
      id: "heat-peak",
      type: "line",
      source: HEAT_SOURCE_ID,
      filter: [">", ["get", "norm"], 0.7],
      layout: round,
      paint: {
        "line-color": heat.peak,
        // Canvas used max(0.3, 0.4·zoomScale); the low stop approximates the clamp.
        "line-width": [
          "interpolate", ["exponential", Math.SQRT2], ["zoom"],
          5, 0.3,
          13, 0.4,
          21, 6.4,
        ] as unknown as number,
        "line-opacity": ["*", 0.3, tPeak] as unknown as number,
      },
    },
  ] as maplibregl.LayerSpecification[];
}

function buildStyle(
  graphTilesUrl: string,
  tiles: string[],
  background: string,
  heat: HeatRamp,
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
      // Graph overlay from PMTiles
      graph: {
        type: "vector",
        url: `pmtiles://${graphTilesUrl}`,
      },
      // Voted edges — small GeoJSON pushed by GraphLayer on vote changes.
      [HEAT_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
    },
    layers: [
      // Background fill (shows through before tiles load) — matches the map style.
      {
        id: "background",
        type: "background",
        paint: { "background-color": background },
      },
      // Base map raster tiles
      {
        id: "carto-tiles",
        type: "raster",
        source: "carto-base",
        minzoom: 0,
        maxzoom: 19,
      },
      // Graph edges — baseline (0 votes)
      {
        id: "graph-edges",
        type: "line",
        source: "graph",
        "source-layer": "edges",
        paint: {
          "line-color": DESIRE_PATH.stroke,
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            12, 0.3,
            14, 0.5,
            16, 0.8,
          ],
          "line-opacity": 0.12,
        },
        layout: {
          "line-cap": "round",
          "line-join": "round",
        },
      },
      // Vote heatmap passes (halo → warm → hot → peak) above the baseline network
      ...heatLayers(heat),
    ],
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  };
}

interface MapLibreBackgroundProps {
  /** Leaflet map instance to sync camera from */
  leafletMap: L.Map | null;
  /** Active map style — drives basemap tiles + background color */
  mapStyle: MapStyle;
}

export function MapLibreBackground({ leafletMap, mapStyle }: MapLibreBackgroundProps) {
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
          maplibreRasterTiles(mapStyle),
          mapStyle.base,
          mapStyle.heat,
        ),
        center: [CONFIG.initialView.lon, CONFIG.initialView.lat],
        zoom: CONFIG.initialView.zoom + ML_ZOOM_OFFSET,
        interactive: false,
        attributionControl: false,
      });

      map.on("error", (e) => {
        console.warn("MapLibre error (non-fatal):", e.error?.message ?? e);
      });
      // 'load' never fires if a source is unreachable (e.g. a city without
      // graph PMTiles), so also accept 'idle' — it fires once loading settles,
      // including when a source errored out. Whichever comes first wins.
      map.on("load", () => setMapLibreStatus("ready"));
      map.once("idle", () => setMapLibreStatus("ready"));

      mapRef.current = map;
      setMapLibreMap(map);

      return () => {
        setMapLibreMap(null);
        map.remove();
        mapRef.current = null;
        setMapLibreStatus("pending");
      };
    } catch (err) {
      console.warn("MapLibre GL JS unavailable (WebGL required):", err);
      setMapLibreStatus("failed");
      // Leaflet TileLayer provides the fallback base map
    }
    // mapStyle is resolved once at bootstrap and stable for the session.
  }, [mapStyle]);

  // Sync camera from Leaflet
  useEffect(() => {
    if (!leafletMap) return;

    // True while Leaflet runs its CSS zoom animation. Leaflet doesn't emit
    // per-frame moves during it, so we drive MapLibre with a matching easeTo
    // and suppress the jumpTo sync until zoomend.
    let zoomAnimating = false;

    const syncCamera = () => {
      const ml = mapRef.current;
      if (!ml || zoomAnimating) return;
      const center = leafletMap.getCenter();
      const zoom = leafletMap.getZoom();
      ml.jumpTo({
        center: [center.lng, center.lat],
        zoom: zoom + ML_ZOOM_OFFSET,
        bearing: 0,
        pitch: 0,
      });
    };

    const onZoomAnim = (e: L.ZoomAnimEvent) => {
      const ml = mapRef.current;
      if (!ml) return;
      zoomAnimating = true;
      ml.easeTo({
        center: [e.center.lng, e.center.lat],
        zoom: e.zoom + ML_ZOOM_OFFSET,
        duration: LEAFLET_ZOOM_ANIM_MS,
        bearing: 0,
        pitch: 0,
      });
    };

    const onZoomEnd = () => {
      zoomAnimating = false;
      syncCamera();
    };

    // Sync on every move frame for smooth panning
    leafletMap.on("move", syncCamera);
    leafletMap.on("zoomanim", onZoomAnim);
    leafletMap.on("zoomend", onZoomEnd);
    // Initial sync
    syncCamera();

    return () => {
      leafletMap.off("move", syncCamera);
      leafletMap.off("zoomanim", onZoomAnim);
      leafletMap.off("zoomend", onZoomEnd);
    };
  }, [leafletMap]);

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
