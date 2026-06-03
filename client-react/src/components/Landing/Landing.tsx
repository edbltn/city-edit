import { useEffect, useMemo, useState } from "react";
import {
  THEME_ORDER, iconSrc, isApexHost, subdomainHref, presetForMap, symbolForMap, type Theme,
} from "../../themes";
import { CONFIG } from "../../config";
import { ProposeMapModal } from "../ProposeMap/ProposeMapModal";
import "./Landing.css";

interface ApiMap {
  slug: string;
  name: string;
  subtitle?: string;
  cityId: string;
  subdomain?: string | null;
  voteCount?: number;
  voteTypes?: { label: string; icon: string }[];
  symbol?: string;
  city?: { name: string };
}

interface DisplayCard {
  key: string;
  href: string;
  name: string;
  tagline: string;
  symbol: string;
  bgImage?: string;
  haystack: string; // lowercased searchable text
}

function toCard(m: ApiMap): DisplayCard {
  const cityName = m.city?.name || m.cityId;
  const tagline = m.subtitle || presetForMap(m)?.tagline || cityName;
  const labels = (m.voteTypes || []).map((v) => v.label).join(" ");
  return {
    key: m.slug,
    // Preset maps live on a subdomain (bikepaths.cityedit.org); link straight
    // there from the apex. Everything else opens by slug.
    href: m.subdomain && isApexHost() ? subdomainHref(m.subdomain) : `/m/${m.slug}`,
    name: m.name,
    tagline,
    symbol: symbolForMap(m),
    bgImage: `/previews/${m.slug}.png`,
    haystack: `${m.name} ${tagline} ${cityName} ${labels}`.toLowerCase(),
  };
}

// Slug for a preset theme (bikepaths → nyc-bikes, trees/walkways → nyc-<id>).
const presetSlug = (t: Theme) => `nyc-${t.id === "bikepaths" ? "bikes" : t.id}`;

// Fallback cards (no API/DB) so the presets always render.
function fallbackCards(): DisplayCard[] {
  return THEME_ORDER.map((t) => ({
    key: t.id,
    href: isApexHost() ? subdomainHref(t.subdomain) : `/m/${presetSlug(t)}`,
    name: t.name,
    tagline: t.tagline,
    symbol: t.symbol,
    bgImage: `/previews/${presetSlug(t)}.png`,
    haystack: `${t.name} ${t.tagline}`.toLowerCase(),
  }));
}

// Placeholder tiles shown while /api/maps is in flight, so a slow or failed
// fetch never flashes a misleading partial list of presets.
const SKELETON_COUNT = 5;

export function Landing() {
  const [cards, setCards] = useState<DisplayCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [proposeOpen, setProposeOpen] = useState(false);

  useEffect(() => {
    document.title = "City Edit — Pick a map";

    if (typeof window !== "undefined" && window.location.hostname.startsWith("demo.")) {
      const { hostname, protocol, port, pathname, search, hash } = window.location;
      const root = hostname.split(".").slice(1).join(".");
      const portSuffix = port ? `:${port}` : "";
      window.location.replace(`${protocol}//${root}${portSuffix}${pathname}${search}${hash}`);
    }
  }, []);

  // All maps, already ranked by the server (votes desc, then name asc). Fall
  // back to the hardcoded presets only when the fetch fails or returns nothing,
  // so a healthy response always wins and we never show a stale partial list.
  useEffect(() => {
    fetch(`${CONFIG.apiUrl}/maps`)
      .then((r) => r.json())
      .then((d) => {
        const maps: ApiMap[] = d.maps || [];
        setCards(maps.length ? maps.map(toCard) : fallbackCards());
      })
      .catch(() => setCards(fallbackCards()))
      .finally(() => setLoading(false));
  }, []);

  const q = query.trim().toLowerCase();
  const visible = useMemo(
    () => (q ? cards.filter((c) => c.haystack.includes(q)) : cards),
    [cards, q]
  );

  const year = new Date().getFullYear();
  const homeHref = typeof window !== "undefined" ? window.location.pathname + window.location.search : "/";

  return (
    <div className="landing">
      <header className="landing-header">
        <a href={homeHref} className="landing-logo-link" aria-label="City Edit home">
          <img src="/logo.svg" alt="City Edit" className="landing-logo" />
        </a>
        <p className="landing-tagline">Redraw your city.</p>
      </header>

      <div className="landing-toolbar">
        <div className="landing-search-wrap">
          <svg className="landing-search-loupe" viewBox="0 0 16 16" aria-hidden focusable="false">
            <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <line x1="11" y1="11" x2="15" y2="15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input
            className="landing-search"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search maps by city or vote type…"
            aria-label="Search maps"
            spellCheck={false}
            autoComplete="off"
          />
          {query && (
            <button
              className="landing-search-clear"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              type="button"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <main className="landing-grid">
        {/* Propose-a-map tile — always first (top-left). */}
        <button className="landing-card landing-card-add" onClick={() => setProposeOpen(true)}>
          <div className="landing-card-content">
            <div className="landing-card-plus" aria-hidden>+</div>
            <div className="landing-card-name">Propose a Map</div>
            <div className="landing-card-tagline">Pick a city and vote types.</div>
          </div>
        </button>

        {loading
          ? Array.from({ length: SKELETON_COUNT }, (_, i) => (
              <div key={`skeleton-${i}`} className="landing-card landing-card-skeleton" aria-hidden />
            ))
          : visible.map((c) => (
              <a
                key={c.key}
                className="landing-card"
                href={c.href}
                style={c.bgImage ? { backgroundImage: `url(${c.bgImage})` } : undefined}
              >
                <div className="landing-card-content">
                  <img className="landing-card-symbol" src={iconSrc(c.symbol)} alt={c.name} />
                  <div className="landing-card-name">{c.name}</div>
                  <div className="landing-card-tagline">{c.tagline}</div>
                </div>
              </a>
            ))}
      </main>

      <footer className="landing-footer">
        <span>© {year} City Edit. All rights reserved.</span>
        <span className="landing-footer-sep">·</span>
        <a href="https://sphericalharmonics.org/" className="landing-footer-link">
          sphericalharmonics.org
        </a>
      </footer>

      {proposeOpen && <ProposeMapModal onClose={() => setProposeOpen(false)} />}
    </div>
  );
}
