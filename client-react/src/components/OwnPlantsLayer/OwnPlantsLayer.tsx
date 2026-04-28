/**
 * Renders the current user's planted trees as dots for immediate visual
 * feedback. Reads from localStorage["ownPlantedPoints"], updated by
 * RouteContext.castVote on point-vote success and broadcast via the
 * "ownPlants:changed" custom event.
 */

import { useEffect, useRef, useState } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import type { LatLng } from "../../types";

const STORAGE_KEY = "ownPlantedPoints";
const CHANGE_EVENT = "ownPlants:changed";

function readPoints(): LatLng[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function drawOwnPlant(ctx: CanvasRenderingContext2D, x: number, y: number) {
  ctx.beginPath();
  ctx.arc(x, y, 6, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(30, 64, 175, 0.9)";
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
  ctx.stroke();
}

export function OwnPlantsLayer() {
  const map = useMap();
  const [points, setPoints] = useState<LatLng[]>(readPoints);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const refresh = () => setPoints(readPoints());
    window.addEventListener(CHANGE_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(CHANGE_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  useEffect(() => {
    if (!canvasRef.current) {
      const canvas = document.createElement("canvas");
      canvas.className = "own-plants-layer";
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "410";
      canvasRef.current = canvas;
    }
    const canvas = canvasRef.current;
    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pane = map.getPane("overlayPane");
    if (pane && !canvas.parentNode) pane.appendChild(canvas);

    const render = () => {
      const size = map.getSize();
      canvas.width = size.x;
      canvas.height = size.y;
      const topLeft = map.containerPointToLayerPoint([0, 0]);
      L.DomUtil.setPosition(canvas, topLeft);

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      for (const pt of points) {
        const p = map.latLngToContainerPoint([pt.lat, pt.lng]);
        drawOwnPlant(ctx, p.x, p.y);
      }
    };

    render();
    map.on("zoomend", render);
    map.on("moveend", render);
    return () => {
      map.off("zoomend", render);
      map.off("moveend", render);
    };
  }, [map, points]);

  return null;
}
