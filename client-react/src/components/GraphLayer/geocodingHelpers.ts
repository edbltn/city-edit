import { reverseGeocode } from "../../utils/geocode";

// ---------------------------------------------------------------------------
// Reverse-geocode cache (module-level, survives re-renders)
// ---------------------------------------------------------------------------

export const geocodeCache = new Map<string, string | null>();
export const geocodeInFlight = new Set<string>();

function cacheKey(lat: number, lng: number): string {
  return `${lat.toFixed(5)},${lng.toFixed(5)}`;
}

function formatLatLng(lat: number, lng: number): string {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

/**
 * Return cached address, or a lat-lon placeholder while fetching.
 * Calls onResolved() when a fetch completes so the caller can re-render.
 * Does NOT cache failures so they can be retried.
 */
export function resolveAddress(
  lat: number,
  lng: number,
  onResolved: () => void,
): string {
  const key = cacheKey(lat, lng);
  const cached = geocodeCache.get(key);
  if (cached !== undefined) return cached || formatLatLng(lat, lng);

  // Not cached — show placeholder and fire async fetch (reverseGeocode retries
  // with backoff internally, so a transient failure resolves without a re-hover)
  if (!geocodeInFlight.has(key)) {
    geocodeInFlight.add(key);
    reverseGeocode(lat, lng)
      .then(({ ok, address }) => {
        // Cache any SERVER answer — including a null address (no named street,
        // every station-network point), which renders as the lat/lng fallback.
        // Only failure-after-retries stays uncached so the next hover retries;
        // caching by ok-ness (not null-ness) is what stops no-address targets
        // from refetching on every render forever.
        if (ok) {
          geocodeCache.set(key, address ?? "");
          onResolved();
        }
      })
      .finally(() => {
        geocodeInFlight.delete(key);
      });
  }
  return formatLatLng(lat, lng);
}
