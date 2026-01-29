/**
 * Voted Paths Layer - GeoJSON Polyline Renderer
 *
 * Renders voted desire path segments as polylines with vote-based
 * opacity and width styling. Replaces hex heatmap with cleaner lines.
 * Only visible at zoom level 15+ to match GIS point layers.
 */

import { useEffect, useRef, useCallback, useState } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { VOTED_PATHS_COLOR } from "../../colors";
import type { GeoJSONOverlay } from "../../types";

// Minimum zoom level to show voted paths (matches GIS layers)
const MIN_ZOOM_FOR_VOTED_PATHS = 15;

interface VotedPathsLayerProps {
  overlay: GeoJSONOverlay | undefined;
}

export function VotedPathsLayer({ overlay }: VotedPathsLayerProps) {
  const map = useMap();
  const layerRef = useRef<L.GeoJSON | null>(null);
  const [zoomLevel, setZoomLevel] = useState(map.getZoom());

  // Track zoom level changes
  useEffect(() => {
    const handleZoom = () => setZoomLevel(map.getZoom());
    map.on("zoomend", handleZoom);
    return () => {
      map.off("zoomend", handleZoom);
    };
  }, [map]);

  const isZoomedIn = zoomLevel >= MIN_ZOOM_FOR_VOTED_PATHS;

  // Style function based on vote count
  const getStyle = useCallback((feature: GeoJSON.Feature | undefined) => {
    if (!feature?.properties) {
      return {
        color: VOTED_PATHS_COLOR,
        weight: 2,
        opacity: 0.4,
      };
    }

    const votes = feature.properties.votes || 1;
    const maxVotes = overlay?.data?.features?.reduce(
      (max, f) => Math.max(max, f.properties?.votes || 0),
      1
    ) || 1;

    // Log-scaled normalization for better distribution
    const norm = Math.log(votes + 1) / Math.log(maxVotes + 1);

    // Weight: 2px at min, 6px at max
    const weight = 2 + norm * 4;

    // Opacity: 0.3 at min, 0.9 at max
    const opacity = 0.3 + norm * 0.6;

    return {
      color: VOTED_PATHS_COLOR,
      weight,
      opacity,
      lineCap: "round" as const,
      lineJoin: "round" as const,
    };
  }, [overlay]);

  useEffect(() => {
    // Remove layer if no data or not zoomed in enough
    if (!overlay?.data?.features?.length || !isZoomedIn) {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
      return;
    }

    // Ensure pane exists (create if MapPanes hasn't run yet)
    if (!map.getPane("votedPathsPane")) {
      map.createPane("votedPathsPane");
      const pane = map.getPane("votedPathsPane");
      if (pane) pane.style.zIndex = "400";
    }

    // Create or update layer
    if (layerRef.current) {
      map.removeLayer(layerRef.current);
    }

    const layer = L.geoJSON(overlay.data, {
      style: getStyle,
      pane: "votedPathsPane",
    });

    layer.addTo(map);
    layerRef.current = layer;

    return () => {
      if (layerRef.current) {
        map.removeLayer(layerRef.current);
        layerRef.current = null;
      }
    };
  }, [map, overlay, getStyle, isZoomedIn]);

  return null;
}
