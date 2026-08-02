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
import { MAP_STYLES } from "../mapStyles";

export interface MapVoteType {
  label: string;
  icon: string;
  pointType: "route" | "point";
}

/** One location link for a vote type: a real proposal of that type somewhere on
 *  the map, pointing at the source document (e.g. the project's page on
 *  nycdotprojects.info). Rendered as a lettered [a] [b] [c] anchor beside the
 *  vote-type label in proposal cards. `lat`/`lng` are where the proposal is, so
 *  a card shows the ones NEAREST it rather than an arbitrary first few. */
export interface VoteTypeLink {
  url: string;
  /** Tooltip text — e.g. the official project's title. */
  title?: string;
  lat?: number;
  lng?: number;
}

export interface MapConfig {
  slug: string;
  name: string;
  subtitle?: string;
  cityId: string;
  mode: string;   // packed-key vote namespace ("bikepaths"/"trees"/"walkways"/"walk")
  style: string;  // visual style key (see mapStyles.ts) — drives basemap/accent/heat
  /** What the map votes on: "streets" (routable) or a station network like
   *  "ebikes" (NYC-only fixed points, no routing). Absent ⇒ "streets". */
  network?: string;
  /** Per-map override of the top-proposal support floor (both PBTP winners
   *  and RBTP corridors). Absent ⇒ TOP_PROPOSAL_MIN_NET. 0 admits any
   *  net-positive proposal — curated/imported maps whose entries carry one
   *  vote each would otherwise show nothing. */
  topProposalMinNet?: number;
  symbol: string; // proposer-chosen display icon (in /icons/), may be ""
  allowSuggestions: boolean;
  requiresPasscode: boolean;
  /** Server withheld this map's config behind its passcode (see PasscodeGate). */
  locked?: boolean;
  voteTypes: MapVoteType[];
  /** Custom vote types already voted here — shown by the selector only when
   *  searched, never in the default suggestion list. pointType is the kind the
   *  creating cast recorded; null on legacy rows flagged before kinds existed. */
  searchVoteTypes?: { label: string; pointType: "route" | "point" | null }[];
  /** Per-vote-type location links keyed by label (server: maps.vote_type_links).
   *  Present only on maps that curate them (e.g. nyc-proposals' official DOT
   *  proposal locations). */
  voteTypeLinks?: Record<string, VoteTypeLink[]>;
  subdomain?: string | null;
  voteCount?: number;
  city?: CityConfig;
  /** Server runs with APP_ENV=staging: skip the canonical-subdomain redirect
   *  (it would bounce testers to prod) and show the STAGING ribbon. */
  staging?: boolean;
  /** The slug was retired by a rename (DB map_redirects row): no map config —
   *  navigate to `toSlug`, keeping the current query and merging `appendQuery`
   *  (e.g. "src=qr-poster", how pre-printed QR links get campaign-tagged). */
  redirect?: { toSlug: string; appendQuery?: string | null };
}

// The map shown when the URL names no slug and the host has no mapped subdomain.
const DEFAULT_MAP_SLUG = "nyc-walkways";

let current: MapConfig | null = null;

export function getCurrentMap(): MapConfig | null {
  return current;
}

/**
 * A map's vote types applicable to a given point type. Station networks
 * (e.g. ebikes) only ever vote on fixed points, yet their vote types may be
 * authored as either "route" or "point", so they ignore the pointType filter —
 * every vote type applies. Street maps filter to the matching pointType.
 */
export function mapVoteTypesForPointType(
  cfg: MapConfig | null,
  pointType: "route" | "point"
): MapVoteType[] {
  if (!cfg || !cfg.voteTypes?.length) return [];
  const isStationNetwork = (cfg.network ?? "streets") !== "streets";
  if (isStationNetwork) return cfg.voteTypes;
  return cfg.voteTypes.filter((s) => s.pointType === pointType);
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

/**
 * Target URL for a retired slug's redirect (MapConfig.redirect). Same-origin
 * path only: keeps every current query param (QR deep links carry ?w/?vt or
 * ?z/?lat/?lng), drops ?map= (it names the retired slug), and merges the
 * redirect's appendQuery WITHOUT overriding params already present.
 */
export function slugRedirectUrl(
  redirect: { toSlug: string; appendQuery?: string | null },
  search: string = window.location.search,
  hash: string = window.location.hash
): string {
  const params = new URLSearchParams(search);
  params.delete("map");
  if (redirect.appendQuery) {
    new URLSearchParams(redirect.appendQuery).forEach((value, key) => {
      if (!params.has(key)) params.set(key, value);
    });
  }
  const qs = params.toString();
  return `/m/${redirect.toSlug}` + (qs ? `?${qs}` : "") + hash;
}

/**
 * Preview override: `?style=<id>` swaps ONLY the visual style (heat ramp,
 * basemap, marker outline) and leaves `mode` untouched, so a map's existing
 * votes stay on screen while you compare themes. Unknown ids are ignored.
 */
function applyStyleOverride(cfg: MapConfig): MapConfig {
  if (typeof window === "undefined") return cfg;
  const override = new URLSearchParams(window.location.search).get("style");
  if (override && Object.prototype.hasOwnProperty.call(MAP_STYLES, override)) {
    return { ...cfg, style: override };
  }
  return cfg;
}

export async function fetchMapConfig(slug: string): Promise<MapConfig | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/${encodeURIComponent(slug)}`, {
      headers: passcodeHeaders(slug),
    });
    if (!res.ok) return null;
    return applyStyleOverride((await res.json()) as MapConfig);
  } catch {
    return null;
  }
}

/** Look up the map whose DB `subdomain` matches the current host, else null. */
export async function fetchMapConfigBySubdomain(subdomain: string): Promise<MapConfig | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/by-subdomain/${encodeURIComponent(subdomain)}`);
    if (!res.ok) return null;
    return applyStyleOverride((await res.json()) as MapConfig);
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
  // Station networks (ebikes) are Manhattan-clustered, so tighten the votable
  // region — and with it the boundary scrim and pan limits — from all-NYC down
  // to Manhattan. applyCityConfig must run first; this overrides its bounds.
  if (cfg.network && cfg.network !== "streets") {
    const { sw, ne } = CONFIG.stationNetworkBounds;
    CONFIG.mappedBounds = { sw: { ...sw }, ne: { ...ne } };
    CONFIG.nycBounds = {
      sw: { lat: sw.lat - 0.05, lon: sw.lon - 0.08 },
      ne: { lat: ne.lat + 0.05, lon: ne.lon + 0.08 },
    };
  }
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

/**
 * Read and strip a `?passcode=…` query param. Returns the raw passcode (or null).
 * Lets a shareable link auto-unlock a gated map — e.g.
 * https://ebikes.cityedit.org/?passcode=go-electric. We strip it from the address
 * bar immediately (via replaceState) so it isn't bookmarked, re-shared, or left
 * to re-trigger; the value is then exchanged for a token via authWithPasscode.
 */
export function takePasscodeParam(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const pc = params.get("passcode");
  if (!pc) return null;
  params.delete("passcode");
  const qs = params.toString();
  const url = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  try {
    window.history.replaceState(null, "", url);
  } catch {
    /* ignore */
  }
  return pc;
}

/**
 * Exchange a raw passcode for a session token and store it (same endpoint the
 * PasscodeGate uses). Returns true on success so the caller can re-fetch the
 * now-unlocked config. Never throws — a bad passcode just leaves the map locked.
 */
export async function authWithPasscode(slug: string, passcode: string): Promise<boolean> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/maps/${encodeURIComponent(slug)}/auth`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: passcode.trim() }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (!data?.token) return false;
    setPasscodeToken(slug, data.token);
    return true;
  } catch {
    return false;
  }
}
