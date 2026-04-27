import { useEffect } from "react";
import { THEMES, type Theme } from "../../themes";
import "./Landing.css";

const CARD_ORDER: Array<{ id: keyof typeof THEMES; symbol: string; subdomain: string }> = [
  { id: "bikepaths", symbol: "🚴", subdomain: "bikepaths" },
  { id: "walkways", symbol: "🚶", subdomain: "walkways" },
  { id: "trees", symbol: "🌳", subdomain: "trees" },
];

function buildHref(subdomain: string): string {
  if (typeof window === "undefined") return `https://${subdomain}.cityedit.org/`;

  const { hostname, protocol, port } = window.location;

  // Production / staging: hop to the sibling subdomain.
  // demo.cityedit.org -> bikepaths.cityedit.org, etc.
  const parts = hostname.split(".");
  if (parts.length >= 3) {
    const root = parts.slice(1).join(".");
    const portSuffix = port ? `:${port}` : "";
    return `${protocol}//${subdomain}.${root}${portSuffix}/`;
  }

  // Local dev (localhost / 127.0.0.1): use the ?theme= query param.
  return `/?theme=${subdomain}`;
}

export function Landing() {
  useEffect(() => {
    document.title = "City Edit — Pick a map";
  }, []);

  return (
    <div className="landing">
      <header className="landing-header">
        <img src="/logo.svg" alt="City Edit" className="landing-logo" />
        <p className="landing-tagline">Help redraw your city.</p>
      </header>

      <main className="landing-grid">
        {CARD_ORDER.map(({ id, symbol, subdomain }) => {
          const theme: Theme = THEMES[id];
          return (
            <a key={id} className="landing-card" href={buildHref(subdomain)}>
              <div className="landing-card-symbol">{symbol}</div>
              <div className="landing-card-name">{theme.name}</div>
              <div className="landing-card-tagline">{theme.tagline}</div>
              <div className="landing-card-cta">Open map →</div>
            </a>
          );
        })}
      </main>

      <footer className="landing-footer">
        <span>cityedit.org</span>
      </footer>
    </div>
  );
}
