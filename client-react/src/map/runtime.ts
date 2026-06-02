// ==========================================================================
// Runtime map resolution
//
// Resolves which *map* the app is showing from the URL — a path /m/<slug>, a
// ?map=<slug> query param, or a preset subdomain (bikepaths./trees./walkways.).
// Fetches its config from the API and rebinds CONFIG to the map's city. Falls
// back to legacy NYC/theme behavior when no map can be resolved.
// ==========================================================================

import { CONFIG, applyCityConfig, type CityConfig } from "../config";
import { detectSubdomain } from "../themes";

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
  style: string;  // visual style key (see mapStyles.ts) — drives basemap/accent/heat
  symbol: string; // proposer-chosen display icon (in /icons/), may be ""
  allowSuggestions: boolean;
  requiresPasscode: boolean;
  /** Server withheld this map's config behind its passcode (see PasscodeGate). */
  locked?: boolean;
  voteTypes: MapVoteType[];
  /** Custom vote types already voted here — shown by the selector only when
   *  searched, never in the default suggestion list. Labels only (no pointType). */
  searchVoteTypes?: string[];
  subdomain?: string | null;
  voteCount?: number;
  city?: CityConfig;
}

// The map shown when the URL names no slug and the host has no mapped subdomain.
const DEFAULT_MAP_SLUG = "nyc-walkways";

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

export async function fetchMapConfig(slug: string): Promise<MapConfig | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/${encodeURIComponent(slug)}`, {
      headers: passcodeHeaders(slug),
    });
    if (!res.ok) return null;
    return (await res.json()) as MapConfig;
  } catch {
    return null;
  }
}

/** Look up the map whose DB `subdomain` matches the current host, else null. */
export async function fetchMapConfigBySubdomain(subdomain: string): Promise<MapConfig | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/by-subdomain/${encodeURIComponent(subdomain)}`);
    if (!res.ok) return null;
    return (await res.json()) as MapConfig;
  } catch {
    return null;
  }
}

/**
 * The active map's config, resolved from the URL with no hardcoded theme table:
 *   1. an explicit slug (/m/<slug> or ?map=<slug>),
 *   2. else the host's subdomain mapped via the DB (bikepaths → nyc-bikes, and
 *      any admin-assigned vanity subdomain),
 *   3. else the default map.
 * Returning the config (not just a slug) lets a single fetch drive both the
 * load and the canonical-subdomain redirect in App.
 */
export async function resolveMapConfig(): Promise<MapConfig | null> {
  const slug = detectMapSlugFromUrl();
  if (slug) return fetchMapConfig(slug);

  const subdomain = detectSubdomain();
  if (subdomain) {
    const bySubdomain = await fetchMapConfigBySubdomain(subdomain);
    if (bySubdomain) return bySubdomain;
  }

  return fetchMapConfig(DEFAULT_MAP_SLUG);
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

/**
 * Auth header carrying the map's passcode token, when we hold one. Spread onto
 * the content fetches (topology/votes) so a gated map's data loads post-unlock.
 */
export function passcodeHeaders(slug?: string): Record<string, string> {
  const s = slug || getMapSlug();
  const token = s ? getPasscodeToken(s) : null;
  return token ? { "X-Map-Passcode": token } : {};
}
