import { CONFIG } from "../config";
import { withMap } from "../map/runtime";

export interface ReverseGeocodeResult {
  /** True when the server answered — even with a null address (a point with
   *  no named street, every station-network point). Callers must cache
   *  success-null; treating it as failure refires the request on every
   *  render, forever. False only after all retries failed. */
  ok: boolean;
  address: string | null;
}

/**
 * Reverse-geocode with retries. During a cold load the request can queue
 * behind tile/topology downloads on a slow connection (mobile especially) and
 * time out — one silent failure used to leave the pin's address unresolved
 * forever. Three attempts with backoff; high priority hint so the browser
 * schedules it ahead of tile fetches.
 */
export async function reverseGeocode(lat: number, lng: number): Promise<ReverseGeocodeResult> {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(
        withMap(`${CONFIG.apiUrl}/reverse-geocode?lat=${lat}&lng=${lng}`),
        { priority: "high" } as RequestInit,
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      return { ok: true, address: data.address ?? null };
    } catch {
      if (attempt < 2) {
        await new Promise((res) => setTimeout(res, 800 * (attempt + 1)));
      }
    }
  }
  return { ok: false, address: null };
}
