/**
 * Reverse geocoding utility using OpenStreetMap Nominatim API
 */

import type { LatLng } from "../types";

const NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse";

// Cache to avoid repeated lookups for the same location
const addressCache = new Map<string, string>();

function cacheKey(coords: LatLng): string {
  // Round to 5 decimal places (~1 meter precision) for cache
  return `${coords.lat.toFixed(5)},${coords.lng.toFixed(5)}`;
}

/**
 * Format a Nominatim response into a short, readable address
 */
function formatAddress(data: {
  address?: {
    house_number?: string;
    road?: string;
    neighbourhood?: string;
    suburb?: string;
    city?: string;
    town?: string;
    village?: string;
  };
  display_name?: string;
}): string {
  const addr = data.address;
  if (!addr) return data.display_name?.split(",")[0] || "Unknown location";

  // Try to build a concise address: "123 Main St" or "Main St & Oak Ave"
  const parts: string[] = [];

  if (addr.house_number && addr.road) {
    parts.push(`${addr.house_number} ${addr.road}`);
  } else if (addr.road) {
    parts.push(addr.road);
  }

  // Add neighborhood or suburb for context
  const area = addr.neighbourhood || addr.suburb;
  if (area && parts.length > 0) {
    parts.push(area);
  }

  // If we got nothing useful, try the display name
  if (parts.length === 0) {
    const city = addr.city || addr.town || addr.village;
    if (city) parts.push(city);
    else return data.display_name?.split(",").slice(0, 2).join(", ") || "Unknown location";
  }

  return parts.join(", ");
}

/**
 * Reverse geocode coordinates to a human-readable address
 */
export async function reverseGeocode(coords: LatLng): Promise<string> {
  const key = cacheKey(coords);

  // Check cache first
  if (addressCache.has(key)) {
    return addressCache.get(key)!;
  }

  try {
    const params = new URLSearchParams({
      lat: coords.lat.toString(),
      lon: coords.lng.toString(),
      format: "json",
      addressdetails: "1",
      zoom: "18", // Street-level detail
    });

    const response = await fetch(`${NOMINATIM_URL}?${params}`, {
      headers: {
        // Nominatim requires a User-Agent for their usage policy
        "User-Agent": "DesirePathMapper/1.0",
      },
    });

    if (!response.ok) {
      throw new Error(`Nominatim error: ${response.status}`);
    }

    const data = await response.json();
    const address = formatAddress(data);

    // Cache the result
    addressCache.set(key, address);

    return address;
  } catch (error) {
    console.error("Reverse geocoding failed:", error);
    // Return coordinates as fallback
    return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
  }
}
