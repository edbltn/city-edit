// ==========================================================================
// MapFacade — the app's interface to the interactive MapLibre map.
//
// Presents the handful of Leaflet-style methods the codebase was written
// against so components port mechanically. The one convention to know:
// zooms at this boundary are LEAFLET-STYLE (256px tile world), one level
// above MapLibre's native zoom (512px tile world). CONFIG zooms, persisted
// view state, share-link `z` params, and thresholds like INDICATOR_MIN_ZOOM
// all stay in Leaflet terms; the facade converts at the edge.
// ==========================================================================

import type maplibregl from "maplibre-gl";
import type { LatLng } from "../types";

// Leaflet zoom N = 256·2^N px world; MapLibre zoom N = 512·2^N px world.
export const ML_ZOOM_OFFSET = -1;

export interface ContainerPoint {
  x: number;
  y: number;
}

export interface MapMouseEvent {
  latlng: LatLng;
  containerPoint: ContainerPoint;
  originalEvent: MouseEvent;
  /** Prevents the map's default handling (e.g. drag-pan on mousedown). */
  preventDefault(): void;
}

export type FacadeEvent =
  | "move"
  | "moveend"
  | "zoom"
  | "zoomstart"
  | "zoomend"
  | "dragstart"
  | "dragend"
  | "resize";

export type FacadeMouseEvent = "click" | "mousemove" | "mouseout" | "mousedown";

export class MapFacade {
  readonly ml: maplibregl.Map;

  constructor(ml: maplibregl.Map) {
    this.ml = ml;
  }

  /** Leaflet-style zoom (MapLibre native + 1). */
  getZoom(): number {
    return this.ml.getZoom() - ML_ZOOM_OFFSET;
  }

  getCenter(): LatLng {
    const c = this.ml.getCenter();
    return { lat: c.lat, lng: c.lng };
  }

  getContainer(): HTMLElement {
    return this.ml.getContainer();
  }

  getCanvas(): HTMLCanvasElement {
    return this.ml.getCanvas();
  }

  getSize(): ContainerPoint {
    const el = this.ml.getContainer();
    return { x: el.clientWidth, y: el.clientHeight };
  }

  latLngToContainerPoint(pos: LatLng | [number, number]): ContainerPoint {
    const lat = Array.isArray(pos) ? pos[0] : pos.lat;
    const lng = Array.isArray(pos) ? pos[1] : pos.lng;
    const p = this.ml.project([lng, lat]);
    return { x: p.x, y: p.y };
  }

  containerPointToLatLng(pt: ContainerPoint | [number, number]): LatLng {
    const x = Array.isArray(pt) ? pt[0] : pt.x;
    const y = Array.isArray(pt) ? pt[1] : pt.y;
    const ll = this.ml.unproject([x, y]);
    return { lat: ll.lat, lng: ll.lng };
  }

  panTo(pos: LatLng | [number, number]): void {
    const lat = Array.isArray(pos) ? pos[0] : pos.lat;
    const lng = Array.isArray(pos) ? pos[1] : pos.lng;
    this.ml.panTo([lng, lat]);
  }

  zoomIn(): void {
    this.ml.zoomIn();
  }

  zoomOut(): void {
    this.ml.zoomOut();
  }

  /** Toggle map drag-panning (Leaflet's map.dragging.enable/disable). */
  setDragPanEnabled(enabled: boolean): void {
    if (enabled) this.ml.dragPan.enable();
    else this.ml.dragPan.disable();
  }

  on(event: FacadeEvent, fn: () => void): void;
  on(event: FacadeMouseEvent, fn: (e: MapMouseEvent) => void): void;
  on(event: FacadeEvent | FacadeMouseEvent, fn: ((e: MapMouseEvent) => void) | (() => void)): void {
    if (isMouseEvent(event)) {
      const wrapped = this.wrapMouseHandler(fn as (e: MapMouseEvent) => void);
      this.ml.on(event, wrapped);
    } else {
      this.ml.on(event, fn as () => void);
    }
  }

  off(event: FacadeEvent, fn: () => void): void;
  off(event: FacadeMouseEvent, fn: (e: MapMouseEvent) => void): void;
  off(event: FacadeEvent | FacadeMouseEvent, fn: ((e: MapMouseEvent) => void) | (() => void)): void {
    if (isMouseEvent(event)) {
      const wrapped = this.wrappedHandlers.get(fn as (e: MapMouseEvent) => void);
      if (wrapped) {
        this.ml.off(event, wrapped);
        this.wrappedHandlers.delete(fn as (e: MapMouseEvent) => void);
      }
    } else {
      this.ml.off(event, fn as () => void);
    }
  }

  // MapLibre mouse events carry lngLat + point; normalize to the Leaflet
  // shape (latlng + containerPoint + originalEvent) handlers expect. Keep the
  // wrapper per-handler so off() can unregister the same function.
  private wrappedHandlers = new Map<
    (e: MapMouseEvent) => void,
    (e: maplibregl.MapMouseEvent) => void
  >();

  private wrapMouseHandler(fn: (e: MapMouseEvent) => void): (e: maplibregl.MapMouseEvent) => void {
    let wrapped = this.wrappedHandlers.get(fn);
    if (!wrapped) {
      wrapped = (e: maplibregl.MapMouseEvent) => {
        fn({
          latlng: { lat: e.lngLat.lat, lng: e.lngLat.lng },
          containerPoint: { x: e.point.x, y: e.point.y },
          originalEvent: e.originalEvent,
          preventDefault: () => e.preventDefault(),
        });
      };
      this.wrappedHandlers.set(fn, wrapped);
    }
    return wrapped;
  }
}

function isMouseEvent(event: string): event is FacadeMouseEvent {
  return event === "click" || event === "mousemove" || event === "mouseout" || event === "mousedown";
}
