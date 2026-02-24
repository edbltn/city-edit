/**
 * Weighted Segments Layer - Canvas renderer for vote-weighted line segments.
 *
 * Reads GeoJSON segment data from WebSocket state (overlays.desire_paths)
 * and renders glowing heat lines on the dark map.
 *
 * Glow is rendered to an offscreen canvas first, then composited as one layer
 * to avoid seam artifacts where segments meet.
 *
 * Color ramp: blackbody heat — dark red → orange → yellow → white-hot.
 */

import { useEffect, useRef, useCallback } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { useWebSocketContext } from "../../context/WebSocketContext";

// Blackbody radiation ramp: dark ember → red → orange → yellow → white-hot
const COLOR_RAMP = [
  { r: 60, g: 10, b: 5 },      // barely visible dark ember
  { r: 180, g: 30, b: 10 },    // deep red
  { r: 230, g: 90, b: 10 },    // orange
  { r: 255, g: 200, b: 40 },   // yellow
  { r: 255, g: 255, b: 220 },  // white-hot
];

// Core line width range (pixels)
const MIN_WIDTH = 1;
const MAX_WIDTH = 5;

// Glow width multiplier
const GLOW_MULTIPLIER = 4;

function colorAtT(t: number): { r: number; g: number; b: number } {
  const clamped = Math.max(0, Math.min(1, t));
  const seg = clamped * (COLOR_RAMP.length - 1);
  const i = Math.floor(seg);
  const frac = seg - i;

  if (i >= COLOR_RAMP.length - 1) return COLOR_RAMP[COLOR_RAMP.length - 1];

  const a = COLOR_RAMP[i];
  const b = COLOR_RAMP[i + 1];
  return {
    r: Math.round(a.r + frac * (b.r - a.r)),
    g: Math.round(a.g + frac * (b.g - a.g)),
    b: Math.round(a.b + frac * (b.b - a.b)),
  };
}

interface SegmentFeature {
  votes: number;
  coords: [number, number][]; // [[lon, lat], [lon, lat]]
}

function parseSegments(
  overlays: Record<string, unknown> | undefined
): SegmentFeature[] {
  if (!overlays) return [];

  const desirePaths = overlays["desire_paths"] as
    | { data?: { features?: Array<{ properties?: { votes?: number }; geometry?: { coordinates?: [number, number][] } }> } }
    | undefined;

  if (!desirePaths?.data?.features) return [];

  const segments: SegmentFeature[] = [];
  for (const feature of desirePaths.data.features) {
    const votes = feature.properties?.votes;
    const coords = feature.geometry?.coordinates;
    if (typeof votes === "number" && coords && coords.length >= 2) {
      segments.push({ votes, coords });
    }
  }
  return segments;
}

export function WeightedSegmentsLayer() {
  const map = useMap();
  const { mapState } = useWebSocketContext();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  // Offscreen canvas for glow compositing
  const glowCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const glowCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  const rafRef = useRef<number | null>(null);
  const isZoomingRef = useRef(false);

  useEffect(() => {
    const canvas = document.createElement("canvas");
    canvas.className = "weighted-segments";
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";

    const ctx = canvas.getContext("2d");
    canvasRef.current = canvas;
    ctxRef.current = ctx;

    // Offscreen canvas for glow (never added to DOM)
    const glowCanvas = document.createElement("canvas");
    const glowCtx = glowCanvas.getContext("2d");
    glowCanvasRef.current = glowCanvas;
    glowCtxRef.current = glowCtx;

    const pane = map.getPane("desirePathPane");
    if (pane) {
      const firstChild = pane.firstChild;
      if (firstChild) {
        pane.insertBefore(canvas, firstChild);
      } else {
        pane.appendChild(canvas);
      }
    }

    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, [map]);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;
    const glowCanvas = glowCanvasRef.current;
    const glowCtx = glowCtxRef.current;
    if (!canvas || !ctx || !glowCanvas || !glowCtx) return;

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;
    glowCanvas.width = size.x;
    glowCanvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    glowCtx.clearRect(0, 0, glowCanvas.width, glowCanvas.height);

    const segments = parseSegments(
      mapState?.overlays as Record<string, unknown> | undefined
    );
    if (segments.length === 0) return;

    let maxVotes = 1;
    for (const seg of segments) {
      if (seg.votes > maxVotes) maxVotes = seg.votes;
    }

    const bounds = map.getBounds();
    const sw = bounds.getSouthWest();
    const ne = bounds.getNorthEast();
    const pad = 0.005;

    // Precompute visible segments
    const visible: Array<{
      pts: Array<{ x: number; y: number }>;
      t: number;
      width: number;
      color: { r: number; g: number; b: number };
    }> = [];

    for (const seg of segments) {
      const coords = seg.coords;
      const lon = coords[0][0];
      const lat = coords[0][1];
      if (
        lat < sw.lat - pad ||
        lat > ne.lat + pad ||
        lon < sw.lng - pad ||
        lon > ne.lng + pad
      ) {
        continue;
      }

      const t = Math.log(seg.votes + 1) / Math.log(maxVotes + 1);
      const width = MIN_WIDTH + t * (MAX_WIDTH - MIN_WIDTH);
      const color = colorAtT(t);

      const pts = coords.map((c) => {
        const pt = map.latLngToContainerPoint([c[1], c[0]]);
        return { x: pt.x, y: pt.y };
      });

      visible.push({ pts, t, width, color });
    }

    // === Pass 1: Glow on offscreen canvas ===
    // Use "lighter" (additive) blending so overlapping glows merge naturally
    // and intersections get brighter. All drawn to offscreen canvas first,
    // then stamped onto main canvas as one layer — no seam artifacts.
    glowCtx.lineCap = "round";
    glowCtx.lineJoin = "round";
    glowCtx.globalCompositeOperation = "lighter";

    for (const seg of visible) {
      glowCtx.beginPath();
      glowCtx.moveTo(seg.pts[0].x, seg.pts[0].y);
      for (let i = 1; i < seg.pts.length; i++) {
        glowCtx.lineTo(seg.pts[i].x, seg.pts[i].y);
      }

      const glowOpacity = 0.06 + seg.t * 0.15;
      glowCtx.globalAlpha = glowOpacity;
      glowCtx.strokeStyle = `rgb(${seg.color.r}, ${seg.color.g}, ${seg.color.b})`;
      glowCtx.lineWidth = seg.width * GLOW_MULTIPLIER;
      glowCtx.stroke();
    }

    // Stamp glow onto main canvas
    ctx.drawImage(glowCanvas, 0, 0);

    // === Pass 2: Core lines on main canvas ===
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    // Collect intersection nodes: screen-space key → { x, y, totalT, count }
    const nodeMap = new Map<string, { x: number; y: number; totalT: number; count: number }>();

    for (const seg of visible) {
      ctx.beginPath();
      ctx.moveTo(seg.pts[0].x, seg.pts[0].y);
      for (let i = 1; i < seg.pts.length; i++) {
        ctx.lineTo(seg.pts[i].x, seg.pts[i].y);
      }

      const coreOpacity = 0.3 + seg.t * 0.7;
      ctx.globalAlpha = coreOpacity;
      ctx.strokeStyle = `rgb(${seg.color.r}, ${seg.color.g}, ${seg.color.b})`;
      ctx.lineWidth = seg.width;
      ctx.stroke();

      // Track endpoints for intersection detection
      // Round to nearest pixel so nearby endpoints merge
      for (const pt of [seg.pts[0], seg.pts[seg.pts.length - 1]]) {
        const key = `${Math.round(pt.x)},${Math.round(pt.y)}`;
        const existing = nodeMap.get(key);
        if (existing) {
          existing.count++;
          existing.totalT = Math.max(existing.totalT, seg.t);
        } else {
          nodeMap.set(key, { x: pt.x, y: pt.y, totalT: seg.t, count: 1 });
        }
      }
    }

    // === Pass 3: Intersection dots ===
    // Dot radius scales with zoom: tiny when zoomed out, larger when zoomed in
    const zoom = map.getZoom();
    const baseRadius = 1 + (zoom - 10) * 0.4; // zoom 10→1px, zoom 14→2.6px, zoom 18→4.2px

    for (const node of nodeMap.values()) {
      // Only draw at intersections (2+ segments meeting)
      if (node.count < 2) continue;

      // Radius scales with connectivity and zoom
      const connectivityScale = Math.min(node.count / 4, 1.5); // cap at 1.5x
      const radius = baseRadius * (0.6 + connectivityScale * 0.6);

      const color = colorAtT(node.totalT);
      const dotOpacity = 0.4 + node.totalT * 0.6;

      // Glow ring
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius * 2, 0, Math.PI * 2);
      ctx.globalAlpha = dotOpacity * 0.2;
      ctx.fillStyle = `rgb(${color.r}, ${color.g}, ${color.b})`;
      ctx.fill();

      // Core dot
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      ctx.globalAlpha = dotOpacity;
      ctx.fillStyle = `rgb(${color.r}, ${color.g}, ${color.b})`;
      ctx.fill();
    }

    ctx.globalAlpha = 1.0;
  }, [map, mapState]);

  const scheduleRedraw = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
    }
    rafRef.current = requestAnimationFrame(redraw);
  }, [redraw]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;

    const handleZoomStart = () => {
      isZoomingRef.current = true;
      if (canvas && ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    };

    const handleZoomEnd = () => {
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
  }, [map, scheduleRedraw]);

  useEffect(() => {
    scheduleRedraw();
  }, [mapState, scheduleRedraw]);

  return null;
}
