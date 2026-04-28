// ==========================================================================
// Theme Definitions
// Each theme maps to a subdomain (bikepaths.cityedit.org, etc.) and controls
// the input mode, vote suggestions, and display copy.
// ==========================================================================

export type InputMode = "route" | "point" | "both";

export interface VoteSuggestion {
  label: string;
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
  symbol: string;        // Emoji shown in mode switcher and landing card
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
    symbol: "🚴",
    subdomain: "bikepaths",
    suggestions: [
      // Route votes (2 points)
      { label: "🛠️ Improve bike lane", pointType: "route" },
      { label: "🚴 Add bike lane", pointType: "route" },
      { label: "🛡️ Add protected bike lane", pointType: "route" },
      { label: "↔️ Widen bike lane", pointType: "route" },
      { label: "🔵 Add sharrow (shared lane markings)", pointType: "route" },
      { label: "🛣️ Repave bike path", pointType: "route" },
      { label: "🚦 Add bike signal phase", pointType: "route" },
      { label: "🌉 Add bike bridge / greenway", pointType: "route" },
      // Point votes (1 point)
      { label: "🅿️ Add bike parking", pointType: "point" },
      { label: "🔒 Add secure bike parking", pointType: "point" },
      { label: "⚡ Add e-bike charging point", pointType: "point" },
      { label: "🚲 Add Citi Bike station", pointType: "point" },
      { label: "🔧 Add bike repair station", pointType: "point" },
      { label: "📊 Add bike counter", pointType: "point" },
      { label: "⚠️ Fix dangerous intersection", pointType: "point" },
    ],
  },

  trees: {
    id: "trees",
    name: "Trees",
    tagline: "Vote to greenify your city.",
    mode: "trees",
    inputMode: "point",
    locationLabel: "Location",
    symbol: "🌳",
    subdomain: "trees",
    suggestions: [
      { label: "🌳 Add tree", pointType: "point" },
      { label: "🌿 Plant native shrubs", pointType: "point" },
      { label: "🌱 Create a tree pit / planter", pointType: "point" },
      { label: "🪴 Add planter boxes", pointType: "point" },
      { label: "🌻 Create a community garden", pointType: "point" },
      { label: "🏡 De-pave / restore soil", pointType: "point" },
      { label: "💧 Add a bioswale", pointType: "point" },
      { label: "🛡️ Protect existing tree", pointType: "point" },
      { label: "✂️ Tree needs maintenance / pruning", pointType: "point" },
    ],
  },

  walkways: {
    id: "walkways",
    name: "Walkways",
    tagline: "Vote for a more walkable city.",
    mode: "walkways",
    inputMode: "both",
    locationLabel: "Start",
    symbol: "🚶",
    subdomain: "walkways",
    suggestions: [
      // Route votes (2 points)
      { label: "🛠️ Improve sidewalk", pointType: "route" },
      { label: "🚶 Add crosswalk", pointType: "route" },
      { label: "↔️ Widen sidewalk", pointType: "route" },
      { label: "🔨 Fix broken sidewalk", pointType: "route" },
      { label: "🌉 Add pedestrian bridge", pointType: "route" },
      { label: "💡 Add street lighting", pointType: "route" },
      { label: "🚧 Add traffic calming", pointType: "route" },
      { label: "♿ Improve accessibility", pointType: "route" },
      // Point votes (1 point)
      { label: "🚦 Add pedestrian signal", pointType: "point" },
      { label: "♿ Fix curb cut", pointType: "point" },
      { label: "🪑 Add bench", pointType: "point" },
      { label: "🚰 Add water fountain", pointType: "point" },
      { label: "🚻 Add public restroom", pointType: "point" },
      { label: "🚌 Add bus shelter", pointType: "point" },
    ],
  },
};

/**
 * Display order for the mode switcher and landing page cards.
 */
export const THEME_ORDER: Theme[] = [
  THEMES.bikepaths,
  THEMES.walkways,
  THEMES.trees,
];

/**
 * Build a URL that points at a given theme's subdomain.
 * Production: https://<subdomain>.<root>/   Local dev: /?theme=<id>
 */
export function themeHref(theme: Theme): string {
  if (typeof window === "undefined") return `https://${theme.subdomain}.cityedit.org/`;

  const { hostname, protocol, port } = window.location;
  const parts = hostname.split(".");

  if (parts.length >= 3) {
    const root = parts.slice(1).join(".");
    const portSuffix = port ? `:${port}` : "";
    return `${protocol}//${theme.subdomain}.${root}${portSuffix}/`;
  }

  return `/?theme=${theme.id}`;
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
  const parts = hostname.split(".");

  if (parts.length >= 3) {
    const root = parts.slice(1).join(".");
    const portSuffix = port ? `:${port}` : "";
    return `${protocol}//${root}${portSuffix}/`;
  }

  return "/?landing=1";
}
