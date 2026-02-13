/**
 * Hex Heatmap Layer - H3 Hexagonal Canvas Renderer
 *
 * Custom canvas layer for rendering filled hexagons with log-scaled
 * orange coloring based on vote counts. Hotswaps resolution on zoom.
 * Shows tooltip with top suggestions on hex hover.
 * Uses a separate canvas for hover outline (cheap per-frame redraw).
 */

import { useEffect, useRef, useCallback, useState } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { cellToBoundary, latLngToCell } from "h3-js";
import { HEX_HEATMAP } from "../../colors";
import { useWebSocketContext } from "../../context/WebSocketContext";
import { HexTooltip } from "./HexTooltip";

export function HexHeatmapLayer() {
  const map = useMap();
  const { currentResolution, setZoom, getHexOverlayForResolution } = useWebSocketContext();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  const hoverCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const hoverCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  const redrawTimeoutRef = useRef<number | null>(null);
  const isZoomingRef = useRef(false);

  // Hover state
  const [hoveredHexId, setHoveredHexId] = useState<string | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoveredHexIdRef = useRef<string | null>(null);

  // Initialize canvases once
  useEffect(() => {
    const canvas = document.createElement("canvas");
    canvas.className = "hex-heatmap";
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";

    const hoverCanvas = document.createElement("canvas");
    hoverCanvas.className = "hex-heatmap-hover";
    hoverCanvas.style.position = "absolute";
    hoverCanvas.style.top = "0";
    hoverCanvas.style.left = "0";
    hoverCanvas.style.pointerEvents = "none";

    const ctx = canvas.getContext("2d");
    const hoverCtx = hoverCanvas.getContext("2d");
    canvasRef.current = canvas;
    ctxRef.current = ctx;
    hoverCanvasRef.current = hoverCanvas;
    hoverCtxRef.current = hoverCtx;

    // Add to desirePathPane (before other children so routes render on top)
    const pane = map.getPane("desirePathPane");
    if (pane) {
      const firstChild = pane.firstChild;
      if (firstChild) {
        pane.insertBefore(canvas, firstChild);
        pane.insertBefore(hoverCanvas, canvas.nextSibling);
      } else {
        pane.appendChild(canvas);
        pane.appendChild(hoverCanvas);
      }
    }

    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      if (hoverCanvas.parentNode) hoverCanvas.parentNode.removeChild(hoverCanvas);
    };
  }, [map]);

  // Send initial zoom on mount
  useEffect(() => {
    setZoom(Math.round(map.getZoom()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // Draw a hex polygon path on a context
  const drawHexPath = useCallback((ctx: CanvasRenderingContext2D, hexId: string) => {
    const boundary = cellToBoundary(hexId);
    const screenCoords = boundary.map(([lat, lon]) => {
      const point = map.latLngToContainerPoint([lat, lon]);
      return [point.x, point.y];
    });
    ctx.beginPath();
    ctx.moveTo(screenCoords[0][0], screenCoords[0][1]);
    for (let i = 1; i < screenCoords.length; i++) {
      ctx.lineTo(screenCoords[i][0], screenCoords[i][1]);
    }
    ctx.closePath();
  }, [map]);

  // Draw hover outline on separate canvas (cheap - single polygon)
  const redrawHoverOutline = useCallback(() => {
    const hoverCanvas = hoverCanvasRef.current;
    const hoverCtx = hoverCtxRef.current;
    if (!hoverCanvas || !hoverCtx) return;

    const size = map.getSize();
    hoverCanvas.width = size.x;
    hoverCanvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(hoverCanvas, topLeft);

    hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);

    const hexId = hoveredHexIdRef.current;
    if (!hexId) return;

    drawHexPath(hoverCtx, hexId);
    hoverCtx.globalAlpha = 0.4;
    hoverCtx.strokeStyle = "#343148";
    hoverCtx.lineWidth = 1.5;
    hoverCtx.stroke();
  }, [map, drawHexPath]);

  // Redraw function - uses currentResolution to get the right hex overlay
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

    // Get hex overlay for current resolution
    const hexOverlay = getHexOverlayForResolution(currentResolution);
    const hexCount = hexOverlay?.hexes ? Object.keys(hexOverlay.hexes).length : 0;
    console.log(`[HEXLAYER] Redrawing res=${currentResolution}, hexCount=${hexCount}`);
    if (!hexOverlay?.hexes || hexCount === 0) {
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

    // Also update hover outline position
    redrawHoverOutline();
  }, [map, currentResolution, getHexOverlayForResolution, getColorAndOpacity, redrawHoverOutline]);

  // Schedule redraw with requestAnimationFrame
  const scheduleRedraw = useCallback(() => {
    if (redrawTimeoutRef.current) {
      cancelAnimationFrame(redrawTimeoutRef.current);
    }
    redrawTimeoutRef.current = requestAnimationFrame(redraw);
  }, [redraw]);

  // Redraw on map movement
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;

    const handleZoomStart = () => {
      isZoomingRef.current = true;
      console.log(`[HEXLAYER] ZOOM START - clearing canvas`);
      if (canvas && ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
      // Clear hover outline and tooltip during zoom
      const hoverCtx = hoverCtxRef.current;
      const hoverCanvas = hoverCanvasRef.current;
      if (hoverCanvas && hoverCtx) {
        hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);
      }
      setHoveredHexId(null);
      hoveredHexIdRef.current = null;
    };

    const handleZoomEnd = () => {
      const newZoom = Math.round(map.getZoom());
      console.log(`[HEXLAYER] ZOOM END - zoom=${newZoom}, scheduling redraw`);
      setZoom(newZoom);
      scheduleRedraw();
      queueMicrotask(() => {
        isZoomingRef.current = false;
      });
    };

    const handleMoveEnd = () => {
      if (isZoomingRef.current) return;
      scheduleRedraw();
    };

    map.on("moveend", handleMoveEnd);
    map.on("zoomstart", handleZoomStart);
    map.on("zoomend", handleZoomEnd);
    map.on("resize", scheduleRedraw);

    return () => {
      map.off("moveend", handleMoveEnd);
      map.off("zoomstart", handleZoomStart);
      map.off("zoomend", handleZoomEnd);
      map.off("resize", scheduleRedraw);
    };
  }, [map, scheduleRedraw, setZoom]);

  // Hex hover detection via map mousemove
  useEffect(() => {
    const handleMouseMove = (e: L.LeafletMouseEvent) => {
      const hexOverlay = getHexOverlayForResolution(currentResolution);
      if (!hexOverlay?.hexes) {
        if (hoveredHexIdRef.current) {
          hoveredHexIdRef.current = null;
          setHoveredHexId(null);
          redrawHoverOutline();
        }
        return;
      }

      // Convert mouse position to H3 hex at current resolution
      const { lat, lng } = e.latlng;
      const hexId = latLngToCell(lat, lng, currentResolution);

      // Check if this hex exists in our overlay
      if (hexId in hexOverlay.hexes) {
        if (hoveredHexIdRef.current !== hexId) {
          hoveredHexIdRef.current = hexId;
          setHoveredHexId(hexId);
          redrawHoverOutline();
        }
        const containerPoint = e.containerPoint;
        setTooltipPos({ x: containerPoint.x, y: containerPoint.y });
      } else {
        if (hoveredHexIdRef.current) {
          hoveredHexIdRef.current = null;
          setHoveredHexId(null);
          redrawHoverOutline();
        }
      }
    };

    const handleMouseOut = () => {
      if (hoveredHexIdRef.current) {
        hoveredHexIdRef.current = null;
        setHoveredHexId(null);
        redrawHoverOutline();
      }
    };

    map.on("mousemove", handleMouseMove);
    map.on("mouseout", handleMouseOut);

    return () => {
      map.off("mousemove", handleMouseMove);
      map.off("mouseout", handleMouseOut);
    };
  }, [map, currentResolution, getHexOverlayForResolution, redrawHoverOutline]);

  // Redraw when resolution changes (hotswap)
  useEffect(() => {
    console.log(`[HEXLAYER] Resolution changed to ${currentResolution}, redrawing`);
    scheduleRedraw();
  }, [currentResolution, scheduleRedraw]);

  // Redraw when hex data changes
  useEffect(() => {
    scheduleRedraw();
  }, [getHexOverlayForResolution, scheduleRedraw]);

  // Resolve tooltip content from legend + indices, with vote count fallback
  const tooltipSuggestions: string[] = [];
  let hoveredVotes = 0;
  if (hoveredHexId) {
    const hexOverlay = getHexOverlayForResolution(currentResolution);
    if (hexOverlay) {
      hoveredVotes = (hexOverlay.hexes[hoveredHexId] as number) || 0;
      if (hexOverlay.suggestionLegend && hexOverlay.suggestions?.[hoveredHexId]) {
        const indices = hexOverlay.suggestions[hoveredHexId];
        for (const idx of indices) {
          if (idx < hexOverlay.suggestionLegend.length) {
            tooltipSuggestions.push(hexOverlay.suggestionLegend[idx]);
          }
        }
      }
    }
  }

  const mapContainer = map.getContainer();

  return hoveredHexId && hoveredVotes > 0 ? (
    <HexTooltip
      suggestions={tooltipSuggestions}
      votes={hoveredVotes}
      x={tooltipPos.x}
      y={tooltipPos.y}
      container={mapContainer}
    />
  ) : null;
}
