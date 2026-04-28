/**
 * Tree basemap layer. Loads the pre-baked NYC Street Tree Census from
 * /trees.bin (Float32 triples of lat, lng, dbh) once and renders every tree
 * as a canvas dot, radius scaled by DBH. Not interactive.
 *
 * Runs at every zoom level; at low zoom, draws every Nth tree to stay fast.
 */

import { useEffect, useRef, useState } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";

const TREES_URL = "/trees.bin";

function drawTree(ctx: CanvasRenderingContext2D, x: number, y: number, diameter: number) {
  const radius = Math.max(2, Math.min(7, 2 + (diameter / 40) * 5));
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(34, 139, 34, 0.7)";
  ctx.fill();
}

function strideForZoom(zoom: number): number {
  if (zoom >= 15) return 1;
  if (zoom >= 14) return 2;
  if (zoom >= 13) return 4;
  if (zoom >= 12) return 8;
  if (zoom >= 11) return 16;
  return 32;
}

export function GISLayers() {
  const map = useMap();
  const [, setViewportKey] = useState(0);
  const treesRef = useRef<Float32Array | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(TREES_URL)
      .then((r) => r.arrayBuffer())
      .then((buf) => {
        if (cancelled) return;
        treesRef.current = new Float32Array(buf);
        setViewportKey((k) => k + 1);
      })
      .catch((err) => console.error("Failed to load trees.bin:", err));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handle = () => setViewportKey((k) => k + 1);
    map.on("zoomend", handle);
    map.on("moveend", handle);
    return () => {
      map.off("zoomend", handle);
      map.off("moveend", handle);
    };
  }, [map]);

  useEffect(() => {
    if (!canvasRef.current) {
      const canvas = document.createElement("canvas");
      canvas.className = "tree-basemap-layer";
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "400";
      canvasRef.current = canvas;
    }
    const canvas = canvasRef.current;
    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const trees = treesRef.current;
    if (!canvas) return;

    const pane = map.getPane("overlayPane");
    if (pane && !canvas.parentNode) pane.appendChild(canvas);

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!trees) return;

    const bounds = map.getBounds();
    const south = bounds.getSouth();
    const north = bounds.getNorth();
    const west = bounds.getWest();
    const east = bounds.getEast();
    const stride = strideForZoom(map.getZoom());

    for (let i = 0; i < trees.length; i += 3 * stride) {
      const lat = trees[i];
      const lng = trees[i + 1];
      if (lat < south || lat > north || lng < west || lng > east) continue;
      const p = map.latLngToContainerPoint([lat, lng]);
      drawTree(ctx, p.x, p.y, trees[i + 2]);
    }
  });

  return null;
}
