// ==========================================================================
// Theme Definitions
// Each theme maps to a subdomain (bikepaths.cityedit.org, etc.) and controls
// the input mode, vote suggestions, and display copy.
// ==========================================================================

export type InputMode = "route" | "point" | "both";

export interface VoteSuggestion {
  label: string;
  icon: string;
  pointType: "route" | "point";
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
 * Look up the icon for a vote-type label by searching all theme suggestions.
 * Falls back to null if no match is found (e.g. custom user-typed vote types).
 */
export function iconForLabel(label: string): string | null {
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
}

/**
 * Build a URL that points at a given theme's subdomain.
 * Production: https://<subdomain>.<root>/   Local dev: /?theme=<id>
 *
 * Handles three host shapes:
 *   - localhost / 127.0.0.1 → query-param fallback for dev
 *   - apex (cityedit.org, 2 parts) → prepend subdomain to whole hostname
 *   - subdomain (foo.cityedit.org, 3+ parts) → swap first label for theme subdomain
 *
 * Optional `state` encodes map view + selected points as query params
 * so they persist across theme switches.
 */
export function themeHref(theme: Theme, state?: ThemeNavState): string {
  if (typeof window === "undefined") return `https://${theme.subdomain}.cityedit.org/`;

  const { hostname, protocol, port } = window.location;

  let base: string;
  if (isLocalDevHost(hostname)) {
    base = `/?theme=${theme.id}`;
  } else {
    const parts = hostname.split(".");
    const root = parts.length >= 3 ? parts.slice(1).join(".") : hostname;
    const portSuffix = port ? `:${port}` : "";
    base = `${protocol}//${theme.subdomain}.${root}${portSuffix}/`;
  }

  if (!state) return base;

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
  const qs = params.toString();
  if (!qs) return base;
  return base + (base.includes("?") ? "&" : "?") + qs;
}

/**
 * Detect which theme to use based on the current hostname.
 * Falls back to URL param `?theme=X` for local development.
 */
export function detectTheme(): Theme {
  if (typeof window === "undefined") return THEMES.walkways;

  const hostname = window.location.hostname;

  if (hostname.startsWith("bikepaths.")) return THEMES.bikepaths;
  if (hostname.startsWith("trees.")) return THEMES.trees;
  if (hostname.startsWith("walkways.")) return THEMES.walkways;

  // Local dev: ?theme=bikepaths, ?theme=trees, ?theme=walkways
  const params = new URLSearchParams(window.location.search);
  const param = params.get("theme");
  if (param && THEMES[param]) return THEMES[param];

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
