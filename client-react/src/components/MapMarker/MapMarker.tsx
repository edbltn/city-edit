/**
 * MapMarker — React wrapper around maplibregl.Marker with an HTML element,
 * replacing react-leaflet's <Marker icon={divIcon}>. The same DivIcon HTML
 * (kite pins, vote-type icons) renders unchanged; `size` + `anchor` replicate
 * Leaflet's iconSize/iconAnchor.
 *
 * Click/touch handlers are DOM listeners on the element. They deliberately do
 * NOT stop propagation — marker drags rely on events reaching the map — so
 * hosts that also listen for map clicks must ignore clicks originating from
 * `.maplibregl-marker` elements (see MapView's click handler).
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import maplibregl from "maplibre-gl";
import { useMapFacade } from "../../map/MapFacadeContext";
import type { LatLng } from "../../types";

export interface MapMarkerHandle {
  getLatLng(): LatLng;
  setLatLng(pos: LatLng): void;
  getElement(): HTMLElement | null;
}

interface MapMarkerProps {
  position: LatLng;
  /** Inner HTML of the marker element (the old DivIcon `html`). */
  html: string;
  /** Class for the marker element (the old DivIcon `className`). */
  className?: string;
  /** Explicit element size in px (the old DivIcon `iconSize`). */
  size?: [number, number];
  /** 'bottom' = pin tip at position; 'center' = centered on position. */
  anchor?: "bottom" | "center";
  draggable?: boolean;
  /** false = pointer-events: none (ghost/preview markers). */
  interactive?: boolean;
  zIndex?: number;
  hidden?: boolean;
  onClick?: () => void;
  onMouseOver?: () => void;
  onMouseOut?: () => void;
  onDragStart?: () => void;
  onDrag?: () => void;
  onDragEnd?: () => void;
  onTouchStart?: () => void;
  onTouchEnd?: () => void;
}

export const MapMarker = forwardRef<MapMarkerHandle, MapMarkerProps>(function MapMarker(
  {
    position,
    html,
    className,
    size,
    anchor = "bottom",
    draggable = false,
    interactive = true,
    zIndex,
    hidden = false,
    onClick,
    onMouseOver,
    onMouseOut,
    onDragStart,
    onDrag,
    onDragEnd,
    onTouchStart,
    onTouchEnd,
  }: MapMarkerProps,
  ref,
) {
  const facade = useMapFacade();
  const markerRef = useRef<maplibregl.Marker | null>(null);
  const elementRef = useRef<HTMLElement | null>(null);
  const isDraggingRef = useRef(false);

  // Latest handlers, so the mount effect doesn't re-run per render.
  const handlersRef = useRef({
    onClick, onMouseOver, onMouseOut,
    onDragStart, onDrag, onDragEnd,
    onTouchStart, onTouchEnd,
  });
  handlersRef.current = {
    onClick, onMouseOver, onMouseOut,
    onDragStart, onDrag, onDragEnd,
    onTouchStart, onTouchEnd,
  };

  useImperativeHandle(ref, () => ({
    getLatLng() {
      const ll = markerRef.current?.getLngLat();
      return ll ? { lat: ll.lat, lng: ll.lng } : { lat: 0, lng: 0 };
    },
    setLatLng(pos: LatLng) {
      markerRef.current?.setLngLat([pos.lng, pos.lat]);
    },
    getElement() {
      return elementRef.current;
    },
  }), []);

  // Create the marker once per mount (position/flags applied by the
  // follow-up effects below).
  useEffect(() => {
    const el = document.createElement("div");
    if (className) el.className = className;
    el.innerHTML = html;

    const marker = new maplibregl.Marker({ element: el, anchor })
      .setLngLat([position.lng, position.lat])
      .addTo(facade.ml);

    const h = handlersRef.current;
    el.addEventListener("click", () => h.onClick?.());
    el.addEventListener("mouseover", () => h.onMouseOver?.());
    el.addEventListener("mouseout", () => h.onMouseOut?.());
    el.addEventListener("touchstart", () => h.onTouchStart?.(), { passive: true });
    el.addEventListener("touchend", () => h.onTouchEnd?.());

    marker.on("dragstart", () => {
      isDraggingRef.current = true;
      handlersRef.current.onDragStart?.();
    });
    marker.on("drag", () => handlersRef.current.onDrag?.());
    marker.on("dragend", () => {
      isDraggingRef.current = false;
      handlersRef.current.onDragEnd?.();
    });

    markerRef.current = marker;
    elementRef.current = el;

    return () => {
      marker.remove();
      markerRef.current = null;
      elementRef.current = null;
    };
    // html/className/anchor are static per marker in practice; position and
    // the toggles below are handled without remounting.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facade, html, className, anchor]);

  // Position follows the prop — except mid-drag, when the marker itself owns
  // its position (prop echoes during drag would fight the pointer).
  useEffect(() => {
    if (isDraggingRef.current) return;
    markerRef.current?.setLngLat([position.lng, position.lat]);
  }, [position.lat, position.lng]);

  useEffect(() => {
    markerRef.current?.setDraggable(draggable);
  }, [draggable]);

  useEffect(() => {
    const el = elementRef.current;
    if (!el) return;
    if (size) {
      el.style.width = `${size[0]}px`;
      el.style.height = `${size[1]}px`;
    }
    el.style.zIndex = zIndex != null ? String(zIndex) : "";
    el.style.opacity = hidden ? "0" : "";
    el.style.pointerEvents = hidden || !interactive ? "none" : "";
  }, [size, zIndex, hidden, interactive]);

  return null;
});
