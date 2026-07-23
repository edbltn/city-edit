import type { MapFacade } from "../map/facade";
import { CONFIG } from "../config";

interface MapViewState {
  zoom: number;
  center: { lat: number; lng: number };
}

let current: MapViewState = {
  zoom: CONFIG.initialView.zoom,
  center: { lat: CONFIG.initialView.lat, lng: CONFIG.initialView.lon },
};

let mapInstance: MapFacade | null = null;

export function setMapInstance(map: MapFacade | null) {
  mapInstance = map;
}

export function panTo(coords: { lat: number; lng: number }) {
  mapInstance?.panTo(coords);
}

// The camera view the map should (re)create with. First call resolves the
// URL/city-config initial view (computed lazily so applyCityConfig has run);
// after that it's the last view synced by MapCanvas — so a style-change map
// recreation resumes exactly where the user was.
let seeded = false;

export function getStartupView(): { lat: number; lng: number; zoom: number } {
  if (!seeded) {
    seeded = true;
    const v = getInitialMapView();
    current = { zoom: v.zoom, center: { lat: v.lat, lng: v.lng } };
  }
  return { lat: current.center.lat, lng: current.center.lng, zoom: current.zoom };
}

export function setMapViewState(zoom: number, center: { lat: number; lng: number }) {
  current = { zoom, center };
}

export function getMapViewState(): MapViewState {
  return { ...current };
}

const NAV_PARAMS = ["z", "lat", "lng", "slat", "slng", "elat", "elng", "vt"];

export function getInitialMapView(): { lat: number; lng: number; zoom: number } {
  if (typeof window === "undefined") {
    return { lat: CONFIG.initialView.lat, lng: CONFIG.initialView.lon, zoom: CONFIG.initialView.zoom };
  }
  const params = new URLSearchParams(window.location.search);
  const z = params.get("z");
  const lat = params.get("lat");
  const lng = params.get("lng");

  return {
    lat: lat ? parseFloat(lat) : CONFIG.initialView.lat,
    lng: lng ? parseFloat(lng) : CONFIG.initialView.lon,
    zoom: z ? parseInt(z, 10) : CONFIG.initialView.zoom,
  };
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
