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
import { maplibreRasterTiles, type MapStyle } from "../../mapStyles";

// Register PMTiles protocol once at module level
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

function buildStyle(
  graphTilesUrl: string,
  tiles: string[],
  background: string,
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
        ),
        center: [CONFIG.initialView.lon, CONFIG.initialView.lat],
        zoom: CONFIG.initialView.zoom,
        interactive: false,
        attributionControl: false,
      });

      map.on("error", (e) => {
        console.warn("MapLibre error (non-fatal):", e.error?.message ?? e);
      });

      mapRef.current = map;

      return () => {
        map.remove();
        mapRef.current = null;
      };
    } catch (err) {
      console.warn("MapLibre GL JS unavailable (WebGL required):", err);
      // Leaflet TileLayer provides the fallback base map
    }
    // mapStyle is resolved once at bootstrap and stable for the session.
  }, [mapStyle]);

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
