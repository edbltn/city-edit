/**
 * GIS Layers - Fetches and renders external GIS data
 *
 * All layers use canvas rendering for high performance.
 *
 * Data sources:
 * - Crosswalks, Stop Signs, Traffic Signals: OpenStreetMap via Overpass API
 * - Trees: NYC Open Data Tree Census
 */

import { useEffect, useRef, useCallback, useState } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { useGISLayers, type GISLayerType } from "../../context";

// Minimum zoom level to show GIS layers (15 = street-level details)
const MIN_ZOOM_FOR_GIS_LAYERS = 15;

// Build Overpass query for current viewport
const buildOverpassQuery = (layerType: string, bounds: L.LatLngBounds): string => {
  const bbox = `${bounds.getSouth()},${bounds.getWest()},${bounds.getNorth()},${bounds.getEast()}`;

  switch (layerType) {
    case "crosswalks":
      return `[out:json][timeout:15];(node["highway"="crossing"](${bbox});way["highway"="crossing"](${bbox}););out center;`;
    case "stopSigns":
      return `[out:json][timeout:15];node["highway"="stop"](${bbox});out;`;
    case "trafficSignals":
      return `[out:json][timeout:15];node["highway"="traffic_signals"](${bbox});out;`;
    default:
      return "";
  }
};

// NYC Open Data tree census API
const TREE_API_URL = "https://data.cityofnewyork.us/resource/uvpi-gqnh.json";

// Canvas drawing functions for each layer type
function drawStopSign(ctx: CanvasRenderingContext2D, x: number, y: number) {
  const size = 8;
  ctx.beginPath();
  // Draw octagon
  for (let i = 0; i < 8; i++) {
    const angle = (Math.PI / 8) + (i * Math.PI / 4);
    const px = x + size * Math.cos(angle);
    const py = y + size * Math.sin(angle);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fillStyle = "#CC0000";
  ctx.fill();
  ctx.strokeStyle = "#800000";
  ctx.lineWidth = 1;
  ctx.stroke();
}

function drawCrosswalk(ctx: CanvasRenderingContext2D, x: number, y: number) {
  ctx.fillStyle = "#FFD700";
  // Draw zebra stripes
  for (let i = -2; i <= 2; i++) {
    ctx.fillRect(x + i * 4 - 1, y - 4, 2, 8);
  }
}

function drawTrafficSignal(ctx: CanvasRenderingContext2D, x: number, y: number) {
  // Draw housing
  ctx.fillStyle = "#333";
  ctx.fillRect(x - 4, y - 10, 8, 20);
  // Draw lights
  ctx.beginPath();
  ctx.arc(x, y - 6, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#FF0000";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#FFCC00";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y + 6, 2.5, 0, Math.PI * 2);
  ctx.fillStyle = "#00CC00";
  ctx.fill();
}

function drawTree(ctx: CanvasRenderingContext2D, x: number, y: number, diameter: number) {
  // Scale size based on diameter (2-7px radius)
  const radius = Math.max(2, Math.min(7, 2 + (diameter / 40) * 5));
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(34, 139, 34, 0.7)";
  ctx.fill();
}

export function GISLayers() {
  const map = useMap();
  const { layers, mergeLayerData, setLayerLoading } = useGISLayers();
  const [zoomLevel, setZoomLevel] = useState(map.getZoom());
  const [viewportKey, setViewportKey] = useState(0);

  // Single canvas for all GIS layers
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Track fetched bounds to avoid refetching same area
  const fetchedBoundsRef = useRef<Record<GISLayerType, L.LatLngBounds[]>>({
    crosswalks: [],
    stopSigns: [],
    trafficSignals: [],
    trees: [],
  });

  // Track zoom level and viewport changes
  useEffect(() => {
    const handleZoom = () => setZoomLevel(map.getZoom());
    const handleMoveEnd = () => setViewportKey((k) => k + 1);
    map.on("zoomend", handleZoom);
    map.on("moveend", handleMoveEnd);
    return () => {
      map.off("zoomend", handleZoom);
      map.off("moveend", handleMoveEnd);
    };
  }, [map]);

  const isZoomedIn = zoomLevel >= MIN_ZOOM_FOR_GIS_LAYERS;

  // Initialize canvas once
  useEffect(() => {
    if (!canvasRef.current) {
      const canvas = document.createElement("canvas");
      canvas.className = "gis-canvas-layer";
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "400";
      canvasRef.current = canvas;
    }
    return () => {
      if (canvasRef.current?.parentNode) {
        canvasRef.current.parentNode.removeChild(canvasRef.current);
      }
    };
  }, []);

  // Render all visible layers on canvas
  const renderAllLayersOnCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Add canvas to map if not already
    const pane = map.getPane("overlayPane");
    if (pane && !canvas.parentNode) {
      pane.appendChild(canvas);
    }

    // Resize and position canvas
    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const bounds = map.getBounds();

    // Draw each visible layer
    (Object.keys(layers) as GISLayerType[]).forEach((layerType) => {
      const state = layers[layerType];
      if (!state.visible || !state.data || !isZoomedIn) return;

      state.data.features.forEach((feature) => {
        if (feature.geometry.type !== "Point") return;

        const [lng, lat] = feature.geometry.coordinates;
        if (!bounds.contains([lat, lng])) return;

        const point = map.latLngToContainerPoint([lat, lng]);
        const x = point.x;
        const y = point.y;

        switch (layerType) {
          case "stopSigns":
            drawStopSign(ctx, x, y);
            break;
          case "crosswalks":
            drawCrosswalk(ctx, x, y);
            break;
          case "trafficSignals":
            drawTrafficSignal(ctx, x, y);
            break;
          case "trees":
            drawTree(ctx, x, y, feature.properties?.diameter || 10);
            break;
        }
      });
    });
  }, [map, layers, isZoomedIn]);

  // Clear canvas
  const clearCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
  }, []);

  // Check if current bounds overlap with any previously fetched bounds
  const hasOverlapWithFetched = useCallback(
    (layerType: GISLayerType, bounds: L.LatLngBounds): boolean => {
      return fetchedBoundsRef.current[layerType].some((fb) => fb.contains(bounds));
    },
    []
  );

  // Fetch Overpass data for current viewport
  const fetchOverpassData = useCallback(
    async (layerType: GISLayerType) => {
      const bounds = map.getBounds();
      const query = buildOverpassQuery(layerType, bounds);
      if (!query) return;

      setLayerLoading(layerType, true);

      try {
        const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;
        const response = await fetch(url);
        const data = await response.json();

        const features: GeoJSON.Feature[] = data.elements
          .filter((el: { lat?: number; lon?: number; center?: { lat: number; lon: number } }) =>
            el.lat !== undefined || el.center?.lat !== undefined
          )
          .map((el: { id: number; lat?: number; lon?: number; center?: { lat: number; lon: number }; tags?: Record<string, string> }) => ({
            type: "Feature" as const,
            properties: { id: el.id, ...el.tags },
            geometry: {
              type: "Point" as const,
              coordinates: [
                el.lon ?? el.center?.lon ?? 0,
                el.lat ?? el.center?.lat ?? 0,
              ],
            },
          }));

        const geojson: GeoJSON.FeatureCollection = {
          type: "FeatureCollection",
          features,
        };

        mergeLayerData(layerType, geojson);
        fetchedBoundsRef.current[layerType].push(bounds);
      } catch (error) {
        console.error(`Failed to fetch ${layerType}:`, error);
        setLayerLoading(layerType, false);
      }
    },
    [map, mergeLayerData, setLayerLoading]
  );

  // Fetch NYC tree data
  const fetchTreeData = useCallback(async () => {
    setLayerLoading("trees", true);

    try {
      const bounds = map.getBounds();
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();

      const query = `$where=latitude between ${sw.lat} and ${ne.lat} and longitude between ${sw.lng} and ${ne.lng}&$limit=5000&$select=tree_id,latitude,longitude,tree_dbh`;
      const url = `${TREE_API_URL}?${query}`;

      const response = await fetch(url);
      const data = await response.json();

      const features: GeoJSON.Feature[] = data
        .filter((tree: { latitude?: string; longitude?: string }) => tree.latitude && tree.longitude)
        .map((tree: { tree_id: string; latitude: string; longitude: string; tree_dbh?: string }) => ({
          type: "Feature" as const,
          properties: {
            id: tree.tree_id,
            diameter: parseFloat(tree.tree_dbh || "10"),
          },
          geometry: {
            type: "Point" as const,
            coordinates: [parseFloat(tree.longitude), parseFloat(tree.latitude)],
          },
        }));

      const geojson: GeoJSON.FeatureCollection = {
        type: "FeatureCollection",
        features,
      };

      mergeLayerData("trees", geojson);
      fetchedBoundsRef.current.trees.push(bounds);
    } catch (error) {
      console.error("Failed to fetch trees:", error);
      setLayerLoading("trees", false);
    }
  }, [map, mergeLayerData, setLayerLoading]);

  // Handle layer visibility and fetching
  useEffect(() => {
    if (!isZoomedIn) {
      clearCanvas();
      return;
    }

    const currentBounds = map.getBounds();

    (Object.keys(layers) as GISLayerType[]).forEach((layerType) => {
      const state = layers[layerType];

      if (state.visible) {
        const needsFetch = !state.loading && !hasOverlapWithFetched(layerType, currentBounds);

        if (needsFetch) {
          if (layerType === "trees") {
            fetchTreeData();
          } else {
            fetchOverpassData(layerType);
          }
        }
      }
    });

    // Render all layers on canvas
    renderAllLayersOnCanvas();
  }, [layers, isZoomedIn, viewportKey, map, hasOverlapWithFetched, fetchOverpassData, fetchTreeData, renderAllLayersOnCanvas, clearCanvas]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (canvasRef.current?.parentNode) {
        canvasRef.current.parentNode.removeChild(canvasRef.current);
      }
    };
  }, []);

  return null;
}
