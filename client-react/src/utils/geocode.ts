import { CONFIG } from "../config";
import { withMap } from "../map/runtime";

/**
 * Reverse-geocode with retries. During a cold load the request can queue
 * behind tile/topology downloads on a slow connection (mobile especially) and
 * time out — one silent failure used to leave the pin's address unresolved
 * forever. Three attempts with backoff; high priority hint so the browser
 * schedules it ahead of tile fetches.
 */
export async function reverseGeocode(lat: number, lng: number): Promise<string | null> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(
        withMap(`${CONFIG.apiUrl}/reverse-geocode?lat=${lat}&lng=${lng}`),
        { priority: "high" } as RequestInit,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      return data.address ?? null;
    } catch {
      if (attempt < 2) {
        await new Promise((res) => setTimeout(res, 800 * (attempt + 1)));
      }
    }
  }
  return null;
}
