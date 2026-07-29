// Campaign source tag (?src=…).
//
// Attribution for a page visit — e.g. QR posters carry ?src=qr-poster (either
// printed into the QR URL, or merged in by a retired slug's redirect). Captured
// once at map boot, reported in the map-load beacon (loadTelemetry → [MAPLOAD]
// src=… → the `src` label on cityedit_map_load_ms), then stripped from the
// address bar so a re-shared link doesn't inherit the campaign's attribution.
// Untagged visits report as "direct" server-side.

const SRC_PATTERN = /^[a-zA-Z0-9_-]{1,32}$/;

let captured: string | null = null;

/**
 * Read + strip the `?src=` param. Idempotent: the first call wins (React
 * StrictMode re-runs effects; the second call finds a stripped URL). Must run
 * before anything rewrites the URL (RouteContext's selection sync drops
 * unknown params) — App calls it at the top of the map-resolution effect.
 */
export function captureSourceTag(): void {
  if (typeof window === "undefined" || captured !== null) return;
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("src");
  if (!raw) return;
  if (SRC_PATTERN.test(raw)) captured = raw;
  params.delete("src");
  const qs = params.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  try {
    window.history.replaceState(null, "", url);
  } catch {
    /* ignore */
  }
}

/** The captured source tag, or null for a direct visit. */
export function getSourceTag(): string | null {
  return captured;
}

/**
 * Re-attach the captured tag to a redirect target. The canonical-subdomain
 * redirect is a full page load on another host — without this the tag
 * (already stripped from this page's URL) would die on the hop.
 */
export function withSourceTag(url: string): string {
  if (!captured) return url;
  return url + (url.includes("?") ? "&" : "?") + "src=" + encodeURIComponent(captured);
}
