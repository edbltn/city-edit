import { useEffect } from "react";
import { THEME_ORDER, themeHref, iconSrc } from "../../themes";
import "./Landing.css";

export function Landing() {
  useEffect(() => {
    document.title = "City Edit — Pick a map";

    // Canonicalize legacy demo.cityedit.org → cityedit.org
    if (typeof window !== "undefined" && window.location.hostname.startsWith("demo.")) {
      const { hostname, protocol, port, pathname, search, hash } = window.location;
      const root = hostname.split(".").slice(1).join(".");
      const portSuffix = port ? `:${port}` : "";
      window.location.replace(`${protocol}//${root}${portSuffix}${pathname}${search}${hash}`);
    }
  }, []);

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

      <main className="landing-grid">
        {THEME_ORDER.map((theme) => (
          <a key={theme.id} className="landing-card" href={themeHref(theme)}>
            <img className="landing-card-symbol" src={iconSrc(theme.symbol)} alt={theme.name} />
            <div className="landing-card-name">{theme.name}</div>
            <div className="landing-card-tagline">{theme.tagline}</div>
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
    </div>
  );
}
