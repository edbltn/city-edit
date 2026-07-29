// First-map-load beacon.
//
// One measurement per page load: elapsed time from navigation start
// (performance.now()'s origin) to the moment the full-screen loader dismisses
// (base map ready AND heatmap painted) — the user-perceived "map is up"
// latency. Sent via sendBeacon to /api/client-timing, which logs a [MAPLOAD]
// line that the log-based `cityedit_map_load_ms` distribution metric
// (terraform/monitoring.tf) turns into the System Health dashboard's
// first-load P50/P95/P99. Fire-and-forget: telemetry must never break or slow
// the app, so every failure path is swallowed.

import { CONFIG } from "../config";
import { getMapSlug, detectMapSlugFromUrl } from "../map/runtime";
import { getSourceTag } from "./sourceTag";

let topoSource: "cache" | "network" | null = null;
let sent = false;

/** Called by GraphLayer once it knows whether the topology came from the
 *  IndexedDB cache (repeat visit) or the network (true first load) — the
 *  label that separates cold-load P99 from warm-load P99 on the dashboard. */
export function reportTopologySource(cached: boolean) {
  if (topoSource === null) topoSource = cached ? "cache" : "network";
}

/** Called when the initial full-screen loader dismisses. Idempotent. */
export function reportMapLoaded() {
  if (sent) return;
  sent = true;
  try {
    const ms = Math.round(performance.now());
    const nav = (performance.getEntriesByType("navigation")[0] as
      PerformanceNavigationTiming | undefined)?.type ?? "unknown";
    const body = JSON.stringify({
      map: getMapSlug() || detectMapSlugFromUrl() || "unknown",
      ms,
      cachedTopo: topoSource === "cache",
      nav,
      // Campaign attribution (?src=…, captured+stripped at boot); absent for
      // direct visits — the server labels those "direct".
      src: getSourceTag() ?? undefined,
    });
    const url = `${CONFIG.apiUrl}/client-timing`;
    // Send as a plain string (text/plain — CORS-safelisted). A Blob typed
    // application/json would require a preflight sendBeacon can't perform, so
    // Chrome silently drops it when apiUrl is cross-origin (dev). The server
    // parses the raw bytes and never looks at Content-Type.
    const ok = navigator.sendBeacon?.(url, body);
    if (!ok) {
      fetch(url, { method: "POST", body, keepalive: true }).catch(() => {});
    }
  } catch {
    // Never let telemetry surface as an app error.
  }
}
