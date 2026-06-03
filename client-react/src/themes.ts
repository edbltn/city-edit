// ==========================================================================
// Theme Definitions
// Each theme maps to a subdomain (bikepaths.cityedit.org, etc.) and controls
// the input mode, vote suggestions, and display copy.
// ==========================================================================

import { getMapStyle, type MapStyle } from "./mapStyles";

export type InputMode = "route" | "point" | "both";

export interface VoteSuggestion {
  label: string;
  icon: string;
  pointType: "route" | "point";
}

/**
 * Resolve a theme's map style (basemap + accent + heat ramp). A theme's `id`
 * is the map's `style` key (see map/runtime.ts), so presets resolve to their
 * named style and user maps fall back to the neutral default.
 */
export function mapStyleForTheme(theme: Theme): MapStyle {
  return getMapStyle(theme.id);
}

export interface Theme {
  id: string;
  name: string;
  tagline: string;
  mode: string;          // Vote namespace sent to the API (e.g. "bikepaths", "trees")
  inputMode: InputMode;  // What input modality the user gets
  suggestions: VoteSuggestion[];
  locationLabel: string; // Label for start point (e.g. "Location", "Start")
  symbol: string;        // Icon filename (in /icons/) shown in mode switcher and landing card
  subdomain: string;     // Subdomain that hosts this theme (e.g. "bikepaths")
}

export const THEMES: Record<string, Theme> = {
  bikepaths: {
    id: "bikepaths",
    name: "Bikes",
    tagline: "Vote for better cycling infrastructure.",
    mode: "bikepaths",
    inputMode: "both",
    locationLabel: "Start",
    symbol: "bikes",
    subdomain: "bikepaths",
    suggestions: [
      { label: "Improve bike lane", icon: "bikes", pointType: "route" },
      { label: "Add bike lane", icon: "bikes", pointType: "route" },
      { label: "Add protected bike lane", icon: "safety", pointType: "route" },
      { label: "Widen bike lane", icon: "bikes", pointType: "route" },
      { label: "Add sharrow (shared lane markings)", icon: "bikes", pointType: "route" },
      { label: "Repave bike path", icon: "bikes", pointType: "route" },
      { label: "Add bike signal phase", icon: "traffic-reduction", pointType: "route" },
      { label: "Add bike bridge", icon: "parks", pointType: "route" },
      { label: "Add bike greenway", icon: "parks", pointType: "route" },
      { label: "Add bike parking", icon: "bikes", pointType: "point" },
      { label: "Add secure bike parking", icon: "safety", pointType: "point" },
      { label: "Add e-bike charging point", icon: "bikes", pointType: "point" },
      { label: "Add Citi Bike station", icon: "bikes", pointType: "point" },
      { label: "Add bike repair station", icon: "bikes", pointType: "point" },
      { label: "Add bike counter", icon: "mapping", pointType: "point" },
      { label: "Fix dangerous intersection", icon: "safety", pointType: "point" },
    ],
  },

  trees: {
    id: "trees",
    name: "Trees",
    tagline: "Vote to greenify your city.",
    mode: "trees",
    inputMode: "both",
    locationLabel: "Start",
    symbol: "trees",
    subdomain: "trees",
    suggestions: [
      { label: "Add tree-lined street", icon: "trees", pointType: "route" },
      { label: "Create green corridor", icon: "parks", pointType: "route" },
      { label: "Add greenway", icon: "parks", pointType: "route" },
      { label: "De-pave street section", icon: "public-space", pointType: "route" },
      { label: "Add bioswale corridor", icon: "waterfront", pointType: "route" },
      { label: "Add tree", icon: "trees", pointType: "point" },
      { label: "Plant native shrubs", icon: "parks", pointType: "point" },
      { label: "Create a tree pit", icon: "trees", pointType: "point" },
      { label: "Add planter boxes", icon: "parks", pointType: "point" },
      { label: "Create a community garden", icon: "community", pointType: "point" },
      { label: "Restore soil", icon: "public-space", pointType: "point" },
      { label: "Add a bioswale", icon: "waterfront", pointType: "point" },
      { label: "Protect existing tree", icon: "safety", pointType: "point" },
      { label: "Tree needs pruning", icon: "trees", pointType: "point" },
      { label: "Tree needs maintenance", icon: "trees", pointType: "point" },
    ],
  },

  walkways: {
    id: "walkways",
    name: "Walkways",
    tagline: "Vote for a more walkable city.",
    mode: "walkways",
    inputMode: "both",
    locationLabel: "Start",
    symbol: "walkways",
    subdomain: "walkways",
    suggestions: [
      { label: "Improve sidewalk", icon: "walkways", pointType: "route" },
      { label: "Add crosswalk", icon: "pedestrian-streets", pointType: "route" },
      { label: "Widen sidewalk", icon: "walkways", pointType: "route" },
      { label: "Fix broken sidewalk", icon: "walkways", pointType: "route" },
      { label: "Add pedestrian bridge", icon: "walkways", pointType: "route" },
      { label: "Add street lighting", icon: "public-space", pointType: "route" },
      { label: "Add traffic calming", icon: "traffic-reduction", pointType: "route" },
      { label: "Improve accessibility", icon: "accessibility", pointType: "route" },
      { label: "Add pedestrian signal", icon: "traffic-reduction", pointType: "point" },
      { label: "Fix curb cut", icon: "accessibility", pointType: "point" },
      { label: "Add bench", icon: "public-space", pointType: "point" },
      { label: "Add water fountain", icon: "waterfront", pointType: "point" },
      { label: "Add public restroom", icon: "public-space", pointType: "point" },
      { label: "Add bus shelter", icon: "transit", pointType: "point" },
    ],
  },
};

/**
 * Returns the path to an icon image in /icons/.
 */
export function iconSrc(icon: string): string {
  return `/icons/${icon}.svg`;
}

/**
 * Resolve the themed icon for a vote-type label — the single resolver shared by
 * the vote-type selector, map markers, and top-proposal cards so a label always
 * looks the same everywhere.
 *
 * The active map's own vote types win: this is what makes a custom vote-type
 * *set* (a user map's `voteTypes`, or a preset list) drive its own icons (e.g. a
 * bus map's "Add bus route" → the transit icon). Preset theme suggestions are
 * the fallback, then null — at which point callers render the colorized
 * suggestion glyph (see suggestionIcon.ts) for truly icon-less custom types.
 */
export function iconForLabel(
  label: string,
  voteTypes?: readonly { label: string; icon: string }[]
): string | null {
  const own = voteTypes?.find((s) => s.label === label)?.icon;
  if (own) return own;
  for (const theme of Object.values(THEMES)) {
    for (const s of theme.suggestions) {
      if (s.label === label) return s.icon;
    }
  }
  return null;
}

/**
 * Display order for the mode switcher and landing page cards.
 */
export const THEME_ORDER: Theme[] = [
  THEMES.bikepaths,
  THEMES.walkways,
  THEMES.trees,
];

/** True for hostnames that should be treated as the local dev server. */
function isLocalDevHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || !hostname.includes(".");
}

export interface ThemeNavState {
  zoom?: number;
  center?: { lat: number; lng: number };
  start?: { lat: number; lng: number } | null;
  end?: { lat: number; lng: number } | null;
  /** Pre-selected vote type — opens the proposal modal on the selected point. */
  vt?: string | null;
}


/** Serialize map view + selected points to a query string (shared by hrefs). */
function navStateToQuery(state?: ThemeNavState): string {
  if (!state) return "";
  const params = new URLSearchParams();
  if (state.zoom != null) params.set("z", String(state.zoom));
  if (state.center) {
    params.set("lat", state.center.lat.toFixed(5));
    params.set("lng", state.center.lng.toFixed(5));
  }
  if (state.start) {
    params.set("slat", state.start.lat.toFixed(5));
    params.set("slng", state.start.lng.toFixed(5));
  }
  if (state.end) {
    params.set("elat", state.end.lat.toFixed(5));
    params.set("elng", state.end.lng.toFixed(5));
  }
  if (state.vt) params.set("vt", state.vt);
  return params.toString();
}

/**
 * Origin of the apex/root domain for the current host (e.g. "https://cityedit.org"),
 * or "" when already on the apex or on localhost. A relative /m/<slug> link would
 * inherit whatever vanity subdomain you're on (bikepaths.cityedit.org/m/sf-… —
 * the wrong host); rooting at the apex lets the app's load-time redirect settle
 * each map onto its own subdomain (if any) instead.
 */
function apexOrigin(): string {
  if (typeof window === "undefined") return "";
  const { protocol, hostname, port } = window.location;
  if (isLocalDevHost(hostname)) return "";
  const parts = hostname.split(".");
  if (parts.length < 3) return ""; // already on the apex
  const root = parts.slice(1).join(".");
  return `${protocol}//${root}${port ? `:${port}` : ""}`;
}

/**
 * Link to a map by slug — the canonical apex address (cityedit.org/m/<slug>).
 * Absolute when on a vanity subdomain so cross-map navigation leaves that host;
 * relative on the apex and in local dev.
 */
export function mapHref(slug: string, state?: ThemeNavState): string {
  const base = `${apexOrigin()}/m/${slug}`;
  const qs = navStateToQuery(state);
  return qs ? `${base}?${qs}` : base;
}

interface MapLike {
  slug: string;
  name: string;
  subtitle?: string;
  mode?: string;
  style?: string;
  symbol?: string;
  subdomain?: string | null;
  voteTypes?: VoteSuggestion[];
}

const DEFAULT_STYLE = {
  inputMode: "both" as InputMode,
  locationLabel: "Start",
  symbol: "mapping",
  tagline: "",
};

/**
 * The preset theme a map belongs to, or null for user maps. Presets are
 * identified by their `subdomain` (only seeded preset maps have one) — NOT by
 * `style`, which a proposer can freely pick (a user "trees"-styled map is not
 * the trees preset and must keep its own copy/icon/input mode).
 */
export function presetForMap(map: { subdomain?: string | null }): Theme | null {
  return (map.subdomain && THEMES[map.subdomain]) || null;
}

/**
 * A map's display icon, derived once so the mode switcher, landing cards, and
 * active-map button always agree: the proposer's chosen symbol wins, then the
 * preset's symbol (seeded maps store none), then the first vote type's icon.
 */
export function symbolForMap(
  map: { symbol?: string; subdomain?: string | null; voteTypes?: { icon: string }[] },
): string {
  return map.symbol || presetForMap(map)?.symbol || map.voteTypes?.[0]?.icon || DEFAULT_STYLE.symbol;
}

/**
 * Build a Theme (styling + copy) from a resolved map. The visual style is keyed
 * by the map's `style` (see mapStyles.ts); the copy/input mode come from the
 * preset it belongs to (by subdomain) or, for user maps, from the map's own
 * fields. This is how a map — not the subdomain — drives the active theme.
 */
export function themeFromMap(map: MapLike): Theme {
  const preset = presetForMap(map);
  return {
    id: map.style || map.slug,
    name: map.name,
    tagline: map.subtitle || preset?.tagline || DEFAULT_STYLE.tagline,
    mode: map.mode || "walk",
    inputMode: preset?.inputMode ?? DEFAULT_STYLE.inputMode,
    suggestions: map.voteTypes ?? [],
    locationLabel: preset?.locationLabel ?? DEFAULT_STYLE.locationLabel,
    symbol: symbolForMap(map),
    subdomain: map.subdomain || "",
  };
}

/**
 * Fallback theme/style from the hostname's preset subdomain, used only when no
 * map could be resolved (the active theme normally comes from the map via
 * themeFromMap). Maps are addressed by slug, so there's no theme= param.
 */
export function detectTheme(): Theme {
  if (typeof window === "undefined") return THEMES.walkways;

  const hostname = window.location.hostname;

  if (hostname.startsWith("bikepaths.")) return THEMES.bikepaths;
  if (hostname.startsWith("trees.")) return THEMES.trees;
  if (hostname.startsWith("walkways.")) return THEMES.walkways;

  return THEMES.walkways;
}

/**
 * True when the current host should render the landing page (theme picker)
 * instead of a map. Triggered by the apex (cityedit.org), the legacy demo.*
 * subdomain, or `?landing=1` for local dev.
 */
export function isLandingHost(): boolean {
  if (typeof window === "undefined") return false;

  const { hostname } = window.location;

  // Apex domain (no subdomain): cityedit.org, sphericalharmonics.org, etc.
  if (hostname.split(".").length === 2) return true;

  // Legacy: demo.cityedit.org continues to serve landing
  if (hostname.startsWith("demo.")) return true;

  const params = new URLSearchParams(window.location.search);
  return params.get("landing") === "1";
}

/**
 * True only on the bare apex domain in production (cityedit.org) — not on a
 * theme subdomain (bikepaths.cityedit.org) and not on localhost. Used to decide
 * whether a preset map opened via /m/<slug> should redirect to its subdomain.
 */
export function isApexHost(): boolean {
  if (typeof window === "undefined") return false;
  const { hostname } = window.location;
  if (isLocalDevHost(hostname)) return false;
  return hostname.split(".").length === 2;
}

/**
 * Absolute URL of a preset map's subdomain on the current root domain, e.g.
 * "bikepaths" → https://bikepaths.cityedit.org/.
 */
export function subdomainHref(subdomain: string): string {
  if (typeof window === "undefined") return `https://${subdomain}.cityedit.org/`;
  const { protocol, hostname, port } = window.location;
  const parts = hostname.split(".");
  const root = parts.length >= 3 ? parts.slice(1).join(".") : hostname;
  const portSuffix = port ? `:${port}` : "";
  return `${protocol}//${subdomain}.${root}${portSuffix}/`;
}

/**
 * The current host's subdomain label (e.g. "bikepaths" for
 * bikepaths.cityedit.org), or null on the apex, localhost, or the reserved
 * www/demo hosts. This is the key to data-driven subdomain routing: the app
 * resolves it against the DB `subdomain` column, so an admin can point any
 * subdomain at any map without a code change.
 */
export function detectSubdomain(): string | null {
  if (typeof window === "undefined") return null;
  const { hostname } = window.location;
  if (isLocalDevHost(hostname)) return null;
  const parts = hostname.split(".");
  if (parts.length < 3) return null; // apex (cityedit.org) has no subdomain
  const sub = parts[0];
  if (sub === "www" || sub === "demo") return null; // reserved / landing hosts
  return sub;
}

/**
 * Where to send a visitor whose resolved map has a canonical `subdomain` — the
 * apex (cityedit.org/m/<slug>) or a different subdomain — so a preset/vanity map
 * always settles on its own host. Returns null when already there or on
 * localhost. Preserves the query string so shared deep links (?slat,?vt,…)
 * survive the redirect.
 */
export function subdomainRedirectUrl(subdomain: string): string | null {
  if (typeof window === "undefined" || !subdomain) return null;
  if (isLocalDevHost(window.location.hostname)) return null;
  if (detectSubdomain() === subdomain) return null; // already canonical
  return subdomainHref(subdomain) + window.location.search;
}

/**
 * URL of the landing page from any subdomain. Production: the apex domain;
 * local dev: /?landing=1.
 */
export function landingHref(): string {
  if (typeof window === "undefined") return "https://cityedit.org/";

  const { hostname, protocol, port } = window.location;
  if (isLocalDevHost(hostname)) {
    return "/?landing=1";
  }

  const parts = hostname.split(".");
  const root = parts.length >= 3 ? parts.slice(1).join(".") : hostname;
  const portSuffix = port ? `:${port}` : "";
  return `${protocol}//${root}${portSuffix}/`;
}
