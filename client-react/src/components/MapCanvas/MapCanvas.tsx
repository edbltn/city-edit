/**
 * MapCanvas — the interactive MapLibre GL map (formerly MapLibreBackground,
 * which rendered behind a transparent Leaflet map that owned the camera).
 * Phase 4 of the migration flipped ownership: this map IS the camera, and all
 * interaction plumbing reaches it through the MapFacade (Leaflet-style zooms,
 * see map/facade.ts).
 *
 * Owns the style: all sources + layers (basemap raster, PMTiles graph,
 * boundary scrim, heat halo/warm/hot/peak, highlight rings, desire rings,
 * route dashes, utility guide lines). The map is recreated on style change;
 * source-pushing modules re-prime via the maplibreInstance registry.
 */

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { CONFIG } from "../../config";
import { DESIRE_PATH, ROUTE_COLORS, SELECTION_YELLOW } from "../../colors";
import { maplibreRasterTiles, type HeatRamp, type MapStyle } from "../../mapStyles";
import { setMapLibreStatus } from "../../map/maplibreStatus";
import { setMapLibreMap } from "../../map/maplibreInstance";
import { MapFacade, ML_ZOOM_OFFSET } from "../../map/facade";
import { HEAT_SOURCE_ID } from "../GraphLayer/maplibreHeat";
import { HIGHLIGHT_SOURCE_ID } from "../GraphLayer/maplibreHighlight";
import {
  ROUTE_SOURCE_ID,
  DESIRE_SOURCE_ID,
  UTIL_SOURCE_ID,
} from "../../map/maplibreOverlays";
import { getStartupView, setMapViewState } from "../../utils/mapViewState";
import "maplibre-gl/dist/maplibre-gl.css";

// Register PMTiles protocol once at module level
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// The canvas heatmap scaled strokes by 2^((leafletZoom-14)/2). The MapLibre
// camera runs one level below Leaflet-style zoom (ML_ZOOM_OFFSET), so the
// equivalent curve here is sqrt(2)^(zoom-13) — expressed as an exponential
// interpolation whose endpoints are that curve evaluated at zooms 5 and 21
// (factor 1/16 and 16). `base` is a per-feature width expression (a function
// of vote norm).
function zoomScaled(base: unknown): maplibregl.ExpressionSpecification {
  return [
    "interpolate", ["exponential", Math.SQRT2], ["zoom"],
    5, ["*", 0.0625, base],
    21, ["*", 16, base],
  ] as unknown as maplibregl.ExpressionSpecification;
}

// Vote heatmap layers over the `graph-live` GeoJSON source (voted edges only,
// pushed by GraphLayer/maplibreHeat.ts). Four passes replicate the old canvas
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

// Votable-region scrim geometry from CONFIG.mappedBounds: a world-sized outer
// ring with the city bbox as a hole (dims outside), plus the bbox ring itself
// for the dashed boundary hairline.
function boundaryFC(): GeoJSON.FeatureCollection {
  const { sw, ne } = CONFIG.mappedBounds;
  const hole: [number, number][] = [
    [sw.lon, sw.lat],
    [sw.lon, ne.lat],
    [ne.lon, ne.lat],
    [ne.lon, sw.lat],
    [sw.lon, sw.lat],
  ];
  const world: [number, number][] = [
    [-180, 85], [180, 85], [180, -85], [-180, -85], [-180, 85],
  ];
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { part: "scrim" },
        geometry: { type: "Polygon", coordinates: [world, hole] },
      },
      {
        type: "Feature",
        properties: { part: "edge" },
        geometry: { type: "LineString", coordinates: hole },
      },
    ],
  };
}

// Desire-path ring layers — same hollow-ring construction as the selection
// highlight (the old SVG erode filter): 1.5px borders around a 4px gap plus a
// faint interior, in the desire color.
function desireLayers(): maplibregl.LayerSpecification[] {
  const round = { "line-cap": "round" as const, "line-join": "round" as const };
  return [
    {
      id: "desire-fill",
      type: "line",
      source: DESIRE_SOURCE_ID,
      layout: round,
      paint: {
        "line-color": ROUTE_COLORS.desire.middle,
        "line-width": 4,
        "line-opacity": 0.12,
      },
    },
    {
      id: "desire-ring",
      type: "line",
      source: DESIRE_SOURCE_ID,
      layout: round,
      paint: {
        "line-color": ROUTE_COLORS.desire.middle,
        "line-width": 1.5,
        "line-gap-width": 4,
      },
    },
  ] as maplibregl.LayerSpecification[];
}

// Walk-route strokes (glow/edge/core). Leaflet dashArray "5, 7" was in px;
// MapLibre line-dasharray is in multiples of line-width, hence the divisions.
function routeLayers(): maplibregl.LayerSpecification[] {
  const c = ROUTE_COLORS.walk;
  const stroke = (id: string, color: string, width: number, opacity: number) => ({
    id,
    type: "line" as const,
    source: ROUTE_SOURCE_ID,
    layout: { "line-cap": "butt" as const, "line-join": "round" as const },
    paint: {
      "line-color": color,
      "line-width": width,
      "line-opacity": opacity,
      "line-dasharray": [5 / width, 7 / width],
    },
  });
  return [
    stroke("route-glow", c.glow, 12, 0.4),
    stroke("route-edge", c.edge, 8, 0.9),
    stroke("route-core", c.core, 5, 1),
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
      // CartoDB raster tiles as base map
      "carto-base": {
        type: "raster",
        tiles,
        tileSize: 256,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      },
      // Graph overlay — z/x/y tile URLs when the server provides them
      // (browser/CDN cacheable); pmtiles range requests as fallback.
      graph: CONFIG.graphTiles
        ? {
            type: "vector",
            tiles: [CONFIG.graphTiles.template],
            minzoom: CONFIG.graphTiles.minzoom,
            maxzoom: CONFIG.graphTiles.maxzoom,
          }
        : {
            type: "vector",
            url: `pmtiles://${graphTilesUrl}`,
          },
      // Voted edges — small GeoJSON pushed by GraphLayer on vote changes.
      [HEAT_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
      // Hover/pinned selection rings — at most two features (see maplibreHighlight.ts).
      [HIGHLIGHT_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
      // Route + desire-path visuals, pushed by their React components
      // (see map/maplibreOverlays.ts).
      [ROUTE_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
      [DESIRE_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
      // Drag trails + waypoint connectors (dashed grey guide lines).
      [UTIL_SOURCE_ID]: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
      // Votable-region scrim: world ring with the city bbox punched out
      // (GeoJSON polygon hole), plus the bbox ring for the dashed hairline.
      boundary: {
        type: "geojson",
        data: boundaryFC(),
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
      // Utility guide lines (drag trails, waypoint connectors). Lowest overlay,
      // matching the old Leaflet overlayPane (400) under graph/desire/route.
      // Leaflet dashArray "1, 4" px at width 2 → dasharray in width multiples.
      {
        id: "util-lines",
        type: "line",
        source: UTIL_SOURCE_ID,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#999999",
          "line-width": 2,
          "line-opacity": 0.6,
          "line-dasharray": [0.5, 2],
        },
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
      // Votable-region scrim + dashed boundary hairline. Sits above the graph
      // baseline but below heat/highlights/routes, matching the old Leaflet
      // pane order (boundary 405 < graph 430 < desire 440 < route 450).
      {
        id: "boundary-scrim",
        type: "fill",
        source: "boundary",
        filter: ["==", ["get", "part"], "scrim"],
        paint: { "fill-color": "#000", "fill-opacity": 0.12 },
      },
      {
        id: "boundary-edge",
        type: "line",
        source: "boundary",
        filter: ["==", ["get", "part"], "edge"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "rgba(255, 255, 255, 0.35)",
          "line-width": 1.5,
          "line-dasharray": [4, 4],
        },
      },
      // Vote heatmap passes (halo → warm → hot → peak) above the baseline network
      ...heatLayers(heat),
      // GIS-style yellow selection: the connected corridor of edges sharing a
      // selected top proposal's vote type (QGIS/ArcGIS selection look). Sits
      // under the white rings so the exact clicked segment still reads.
      {
        id: "highlight-corridor-glow",
        type: "line",
        source: HIGHLIGHT_SOURCE_ID,
        filter: ["==", ["get", "kind"], "corridor"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTION_YELLOW,
          "line-width": 10,
          "line-opacity": 0.25,
        },
      },
      {
        id: "highlight-corridor",
        type: "line",
        source: HIGHLIGHT_SOURCE_ID,
        filter: ["==", ["get", "kind"], "corridor"],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": SELECTION_YELLOW,
          "line-width": 3.5,
          "line-opacity": 0.9,
        },
      },
      // Selection rings, above the heat. Ring geometry mirrors the old canvas
      // renderer: edges get 1.5px white borders around a 4px gap plus a faint
      // interior; nodes get a 3.5px circle with a 1.5px stroke (outer edge 5px).
      // geometry-type normalizes MultiLineString → "LineString", so the ring
      // layers must exclude the corridor feature explicitly.
      {
        id: "highlight-edge-fill",
        type: "line",
        source: HIGHLIGHT_SOURCE_ID,
        filter: ["all",
          ["==", ["geometry-type"], "LineString"],
          ["!=", ["get", "kind"], "corridor"],
        ],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#ffffff",
          "line-width": 4,
          "line-opacity": ["*", 0.12, ["get", "alpha"]] as unknown as number,
        },
      },
      {
        id: "highlight-edge-ring",
        type: "line",
        source: HIGHLIGHT_SOURCE_ID,
        filter: ["all",
          ["==", ["geometry-type"], "LineString"],
          ["!=", ["get", "kind"], "corridor"],
        ],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": "#ffffff",
          "line-width": 1.5,
          "line-gap-width": 4,
          "line-opacity": ["get", "alpha"] as unknown as number,
        },
      },
      {
        id: "highlight-node",
        type: "circle",
        source: HIGHLIGHT_SOURCE_ID,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 3.5,
          "circle-color": "#ffffff",
          "circle-opacity": ["*", 0.12, ["get", "alpha"]] as unknown as number,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-opacity": ["get", "alpha"] as unknown as number,
        },
      },
      // Desire path (selection boundary ring), then the walk route on top —
      // same order as the old Leaflet desirePathPane/routePane stack.
      ...desireLayers(),
      ...routeLayers(),
    ],
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  };
}

interface MapCanvasProps {
  /** Active map style — drives basemap tiles + background color */
  mapStyle: MapStyle;
  /** Receives the live facade (null while unavailable / after teardown) */
  onFacade: (facade: MapFacade | null) => void;
}

export function MapCanvas({ mapStyle, onFacade }: MapCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  const onFacadeRef = useRef(onFacade);
  useEffect(() => { onFacadeRef.current = onFacade; }, [onFacade]);

  useEffect(() => {
    if (!containerRef.current) return;

    try {
      const view = getStartupView();
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: buildStyle(
          CONFIG.graphTilesUrl,
          maplibreRasterTiles(mapStyle),
          mapStyle.base,
          mapStyle.heat,
        ),
        center: [view.lng, view.lat],
        zoom: view.zoom + ML_ZOOM_OFFSET,
        minZoom: CONFIG.minZoom + ML_ZOOM_OFFSET,
        maxZoom: CONFIG.maxZoom + ML_ZOOM_OFFSET,
        maxBounds: [
          [CONFIG.nycBounds.sw.lon, CONFIG.nycBounds.sw.lat],
          [CONFIG.nycBounds.ne.lon, CONFIG.nycBounds.ne.lat],
        ],
        // 2D map: no rotation/pitch (parity with the old Leaflet camera).
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        attributionControl: false,
      });
      map.touchZoomRotate.disableRotation();
      map.keyboard.disableRotation();

      map.addControl(new maplibregl.AttributionControl({ compact: false }), "bottom-right");

      map.on("error", (e) => {
        console.warn("MapLibre error (non-fatal):", e.error?.message ?? e);
      });
      // 'load' never fires if a source is unreachable (e.g. a city without
      // graph PMTiles), so also accept 'idle' — it fires once loading settles,
      // including when a source errored out. Whichever comes first wins.
      map.on("load", () => setMapLibreStatus("ready"));
      map.once("idle", () => setMapLibreStatus("ready"));

      // Keep the module-level view state current so share links, theme-switch
      // URLs, and map recreation all resume from the live camera.
      const facade = new MapFacade(map);
      const syncView = () => setMapViewState(facade.getZoom(), facade.getCenter());
      map.on("moveend", syncView);
      map.on("zoomend", syncView);
      syncView();

      setMapLibreMap(map);
      onFacadeRef.current(facade);

      return () => {
        onFacadeRef.current(null);
        setMapLibreMap(null);
        map.remove();
        setMapLibreStatus("pending");
      };
    } catch (err) {
      console.warn("MapLibre GL JS unavailable (WebGL required):", err);
      setMapLibreStatus("failed");
    }
    // mapStyle is resolved once at bootstrap and stable for the session.
  }, [mapStyle]);

  return (
    <div
      ref={containerRef}
      className="map-canvas"
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
