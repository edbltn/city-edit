/**
 * Hex Heatmap Layer - H3 Hexagonal Canvas Renderer
 *
 * Custom canvas layer for rendering filled hexagons with log-scaled
 * orange coloring based on vote counts.
 */

import { useEffect, useRef, useCallback } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { cellToBoundary } from "h3-js";
import { HEX_HEATMAP } from "../../colors";
import type { HexOverlay } from "../../types";

interface HexHeatmapLayerProps {
  hexOverlay: HexOverlay | null | undefined;
}

export function HexHeatmapLayer({ hexOverlay }: HexHeatmapLayerProps) {
  const map = useMap();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  const redrawTimeoutRef = useRef<number | null>(null);

  // Initialize canvas once
  useEffect(() => {
    const canvas = document.createElement("canvas");
    canvas.className = "hex-heatmap";
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";

    const ctx = canvas.getContext("2d");
    canvasRef.current = canvas;
    ctxRef.current = ctx;

    // Add to desirePathPane (below routes)
    const pane = map.getPane("desirePathPane");
    if (pane) {
      pane.appendChild(canvas);
    }

    return () => {
      if (canvas.parentNode) {
        canvas.parentNode.removeChild(canvas);
      }
    };
  }, [map]);

  // Color and opacity calculation with log scale
  const getColorAndOpacity = useCallback(
    (votes: number, maxVotes: number) => {
      const norm = Math.log(votes + 1) / Math.log(maxVotes + 1);
      const r = Math.round(
        HEX_HEATMAP.light.r + norm * (HEX_HEATMAP.dark.r - HEX_HEATMAP.light.r)
      );
      const g = Math.round(
        HEX_HEATMAP.light.g + norm * (HEX_HEATMAP.dark.g - HEX_HEATMAP.light.g)
      );
      const b = Math.round(
        HEX_HEATMAP.light.b + norm * (HEX_HEATMAP.dark.b - HEX_HEATMAP.light.b)
      );

      // Opacity: 0.3 at min votes, 0.9 at max votes
      const opacity = 0.3 + norm * 0.6;

      return { color: `rgb(${r}, ${g}, ${b})`, opacity };
    },
    []
  );

  // Redraw function
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;
    if (!canvas || !ctx) return;

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;

    // Position canvas at map origin
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!hexOverlay?.hexes || Object.keys(hexOverlay.hexes).length === 0) {
      return;
    }

    const maxVotes = hexOverlay.max_votes || 1;

    // Get viewport bounds for culling
    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();

    for (const [hexId, votes] of Object.entries(hexOverlay.hexes)) {
      // Get hex boundary (returns [[lat, lon], ...])
      const boundary = cellToBoundary(hexId);

      // Quick viewport check using first vertex
      const firstLat = boundary[0][0];
      const firstLon = boundary[0][1];
      if (
        firstLat < sw.lat - 0.01 ||
        firstLat > ne.lat + 0.01 ||
        firstLon < sw.lng - 0.01 ||
        firstLon > ne.lng + 0.01
      ) {
        continue;
      }

      // Convert to screen coords
      const screenCoords = boundary.map(([lat, lon]) => {
        const point = map.latLngToContainerPoint([lat, lon]);
        return [point.x, point.y];
      });

      // Draw filled hex with log-scaled opacity
      const { color, opacity } = getColorAndOpacity(votes as number, maxVotes);
      ctx.globalAlpha = opacity;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(screenCoords[0][0], screenCoords[0][1]);
      for (let i = 1; i < screenCoords.length; i++) {
        ctx.lineTo(screenCoords[i][0], screenCoords[i][1]);
      }
      ctx.closePath();
      ctx.fill();
    }

    // Reset globalAlpha after drawing all hexes
    ctx.globalAlpha = 1.0;
  }, [map, hexOverlay, getColorAndOpacity]);

  // Schedule redraw with requestAnimationFrame
  const scheduleRedraw = useCallback(() => {
    if (redrawTimeoutRef.current) {
      cancelAnimationFrame(redrawTimeoutRef.current);
    }
    redrawTimeoutRef.current = requestAnimationFrame(redraw);
  }, [redraw]);

  // Redraw on map movement
  useEffect(() => {
    map.on("moveend", scheduleRedraw);
    map.on("zoomend", scheduleRedraw);
    map.on("resize", scheduleRedraw);

    return () => {
      map.off("moveend", scheduleRedraw);
      map.off("zoomend", scheduleRedraw);
      map.off("resize", scheduleRedraw);
    };
  }, [map, scheduleRedraw]);

  // Redraw when data changes
  useEffect(() => {
    scheduleRedraw();
  }, [hexOverlay, scheduleRedraw]);

  return null;
}
