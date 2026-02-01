import { CONFIG } from "../config";
import type { LatLng } from "../types";

export function isWithinMappedBounds(latlng: LatLng): boolean {
  const { sw, ne } = CONFIG.mappedBounds;
  return (
    latlng.lat >= sw.lat &&
    latlng.lat <= ne.lat &&
    latlng.lng >= sw.lon &&
    latlng.lng <= ne.lon
  );
}
