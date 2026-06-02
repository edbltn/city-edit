// ==========================================================================
// Runtime map resolution
//
// Resolves which *map* the app is showing from the URL — a path /m/<slug>, a
// ?map=<slug> query param, or a preset subdomain (bikepaths./trees./walkways.).
// Fetches its config from the API and rebinds CONFIG to the map's city. Falls
// back to legacy NYC/theme behavior when no map can be resolved.
// ==========================================================================

import { CONFIG, applyCityConfig, type CityConfig } from "../config";
import { detectTheme } from "../themes";

export interface MapVoteType {
  label: string;
  icon: string;
  pointType: "route" | "point";
}

export interface MapConfig {
  slug: string;
  name: string;
  subtitle?: string;
  cityId: string;
  mode: string;   // packed-key vote namespace ("bikepaths"/"trees"/"walkways"/"walk")
  style: string;  // client style key (preset subdomain, or "default")
  allowSuggestions: boolean;
  requiresPasscode: boolean;
  voteTypes: MapVoteType[];
  subdomain?: string | null;
  voteCount?: number;
  city?: CityConfig;
}

// Preset theme id → seeded map slug (presets are reachable by subdomain).
const THEME_TO_SLUG: Record<string, string> = {
  bikepaths: "nyc-bikes",
  trees: "nyc-trees",
  walkways: "nyc-walkways",
};

let current: MapConfig | null = null;

export function getCurrentMap(): MapConfig | null {
  return current;
}

export function getMapSlug(): string {
  return current?.slug || CONFIG.mapSlug || "";
}

/** Slug from the URL path (/m/<slug>) or ?map= query param, else null. */
export function detectMapSlugFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/m\/([a-z0-9-]+)/i);
  if (m) return m[1];
  const params = new URLSearchParams(window.location.search);
  return params.get("map");
}

/** The slug to load: explicit URL slug, else the preset for the detected theme. */
export function resolveMapSlug(): string {
  return detectMapSlugFromUrl() || THEME_TO_SLUG[detectTheme().id] || "nyc-walkways";
}

export async function fetchMapConfig(slug: string): Promise<MapConfig | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/${encodeURIComponent(slug)}`);
    if (!res.ok) return null;
    return (await res.json()) as MapConfig;
  } catch {
    return null;
  }
}

/** Store the resolved map and rebind CONFIG to its city. */
export function applyMap(cfg: MapConfig): void {
  current = cfg;
  CONFIG.mapSlug = cfg.slug;
  if (cfg.city) applyCityConfig(cfg.city);
  if (typeof document !== "undefined" && cfg.name) {
    document.title = `${cfg.name} — City Edit`;
  }
}

/** Append the active map slug as a query param (no-op when none is set). */
export function withMap(url: string): string {
  const slug = getMapSlug();
  if (!slug) return url;
  return url + (url.includes("?") ? "&" : "?") + "map=" + encodeURIComponent(slug);
}

// ── Passcode token (for maps that gate voting) ──────────────────────────────

function tokenKey(slug: string): string {
  return `mapPasscode:${slug}`;
}

export function getPasscodeToken(slug: string): string | null {
  try {
    return localStorage.getItem(tokenKey(slug));
  } catch {
    return null;
  }
}

export function setPasscodeToken(slug: string, token: string): void {
  try {
    localStorage.setItem(tokenKey(slug), token);
  } catch {
    /* ignore */
  }
}
