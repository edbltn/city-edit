import type L from "leaflet";
import { CONFIG } from "../config";
import { selectionFromParams } from "../selection/serialize";

interface MapViewState {
  zoom: number;
  center: { lat: number; lng: number };
}

let current: MapViewState = {
  zoom: CONFIG.initialView.zoom,
  center: { lat: CONFIG.initialView.lat, lng: CONFIG.initialView.lon },
};

let mapInstance: L.Map | null = null;

export function setMapInstance(map: L.Map | null) {
  mapInstance = map;
}

export function panTo(coords: { lat: number; lng: number }) {
  mapInstance?.panTo([coords.lat, coords.lng]);
}

/** Zoom cap when framing a route, so a short hop doesn't slam to max zoom. */
const FIT_MAX_ZOOM = 17;

/** The topbar overlays the map, so the top edge needs more breathing room. */
const FIT_PADDING_TOP: [number, number] = [64, 110];
const FIT_PADDING_BOTTOM: [number, number] = [64, 80];

/** Frame every given point. One point pans (keeping the current zoom); several
 *  fit their bounds, so adding an endpoint keeps the start on screen instead of
 *  centering on the new point alone. */
export function fitPoints(points: ({ lat: number; lng: number } | null | undefined)[]) {
  if (!mapInstance) return;

  const pts = points.filter((p): p is { lat: number; lng: number } => !!p);
  if (pts.length === 0) return;
  if (pts.length === 1) {
    mapInstance.panTo([pts[0].lat, pts[0].lng]);
    return;
  }

  mapInstance.fitBounds(
    pts.map((p) => [p.lat, p.lng] as [number, number]),
    {
      paddingTopLeft: FIT_PADDING_TOP,
      paddingBottomRight: FIT_PADDING_BOTTOM,
      maxZoom: FIT_MAX_ZOOM,
    }
  );
}

export function setMapViewState(zoom: number, center: { lat: number; lng: number }) {
  current = { zoom, center };
}

export function getMapViewState(): MapViewState {
  return { ...current };
}

const NAV_PARAMS = ["z", "lat", "lng", "slat", "slng", "elat", "elng", "vt"];

/** Zoom for a single deep-linked point (an intersection fills the screen). */
const POINT_DEEP_LINK_ZOOM = 17;

/** Approximate zoom that fits a lat/lng span (degrees) with breathing room. */
function zoomForSpan(span: number): number {
  if (span < 0.004) return 16;
  if (span < 0.012) return 15;
  if (span < 0.025) return 14;
  if (span < 0.06) return 13;
  if (span < 0.12) return 12;
  return 11;
}

/** Derive a view from the URL's selection waypoints (`w=` or legacy
 *  slat/slng), so a shared link without explicit camera params opens centered
 *  on what it selects instead of the citywide default. */
function viewFromSelectionParams(
  params: URLSearchParams
): { lat: number; lng: number; zoom: number } | null {
  const parsed = selectionFromParams(params);
  const pts = parsed?.waypoints.map((wp) => wp.coords) ?? [];
  if (pts.length === 0) return null;
  if (pts.length === 1) {
    return { lat: pts[0].lat, lng: pts[0].lng, zoom: POINT_DEEP_LINK_ZOOM };
  }
  const lats = pts.map((p) => p.lat);
  const lngs = pts.map((p) => p.lng);
  const latMin = Math.min(...lats), latMax = Math.max(...lats);
  const lngMin = Math.min(...lngs), lngMax = Math.max(...lngs);
  // Longitude degrees are ~cos(lat) the size of latitude degrees.
  const span = Math.max(latMax - latMin, (lngMax - lngMin) * Math.cos((latMin * Math.PI) / 180));
  return {
    lat: (latMin + latMax) / 2,
    lng: (lngMin + lngMax) / 2,
    zoom: zoomForSpan(span),
  };
}

/** Pure core of getInitialMapView (exported for tests). */
export function computeInitialMapView(
  params: URLSearchParams
): { lat: number; lng: number; zoom: number } {
  const z = params.get("z");
  const lat = params.get("lat");
  const lng = params.get("lng");

  // Explicit camera params always win; otherwise center on the deep-linked
  // selection if the URL carries one.
  if (!z && !lat && !lng) {
    const fromSelection = viewFromSelectionParams(params);
    if (fromSelection) return fromSelection;
  }

  return {
    lat: lat ? parseFloat(lat) : CONFIG.initialView.lat,
    lng: lng ? parseFloat(lng) : CONFIG.initialView.lon,
    zoom: z ? parseInt(z, 10) : CONFIG.initialView.zoom,
  };
}

export function getInitialMapView(): { lat: number; lng: number; zoom: number } {
  if (typeof window === "undefined") {
    return { lat: CONFIG.initialView.lat, lng: CONFIG.initialView.lon, zoom: CONFIG.initialView.zoom };
  }
  return computeInitialMapView(new URLSearchParams(window.location.search));
}

export function getInitialPoints(): {
  start: { lat: number; lng: number } | null;
  end: { lat: number; lng: number } | null;
  vt: string | null;
} {
  if (typeof window === "undefined") return { start: null, end: null, vt: null };
  const params = new URLSearchParams(window.location.search);
  const slat = params.get("slat");
  const slng = params.get("slng");
  const elat = params.get("elat");
  const elng = params.get("elng");
  const vt = params.get("vt");

  return {
    start: slat && slng ? { lat: parseFloat(slat), lng: parseFloat(slng) } : null,
    end: elat && elng ? { lat: parseFloat(elat), lng: parseFloat(elng) } : null,
    vt: vt || null,
  };
}

export function cleanNavParams() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  let changed = false;
  for (const key of NAV_PARAMS) {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      changed = true;
    }
  }
  if (changed) {
    window.history.replaceState({}, "", url.toString());
  }
}
