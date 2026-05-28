import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useTheme, useRoute } from "../../context";
import { THEME_ORDER, themeHref, iconSrc } from "../../themes";
import { getMapViewState } from "../../utils/mapViewState";
import type { ThemeNavState, Theme } from "../../themes";

export const ModeSwitcher = memo(function ModeSwitcher() {
  const current = useTheme();
  const { start, end } = useRoute();
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!isOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [isOpen]);

  const buildHref = useCallback((theme: Theme) => {
    const { zoom, center } = getMapViewState();
    const navState: ThemeNavState = {
      zoom,
      center,
      start: start.coords ? { lat: start.coords.lat, lng: start.coords.lng } : null,
      end: end.coords ? { lat: end.coords.lat, lng: end.coords.lng } : null,
    };
    return themeHref(theme, navState);
  }, [start.coords, end.coords]);

  return (
    <div ref={ref} className={`mode-selector ${isOpen ? "active" : ""}`}>
      <button
        type="button"
        className="mode-btn"
        onClick={() => setIsOpen(!isOpen)}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <img className="mode-icon-img" src={iconSrc(current.symbol)} alt={current.name} />
        <span className="mode-label">{current.name}</span>
        <span className="mode-caret" aria-hidden><span className="caret-down" /></span>
      </button>

      <div className="mode-dropdown" role="listbox">
        {THEME_ORDER.map((theme) => (
          <a
            key={theme.id}
            className={`mode-option ${theme.id === current.id ? "selected" : ""}`}
            href={buildHref(theme)}
            role="option"
            aria-selected={theme.id === current.id}
          >
            <img className="mode-icon-img" src={iconSrc(theme.symbol)} alt={theme.name} />
            <span className="mode-label">{theme.name}</span>
            <span className="check-icon">✓</span>
          </a>
        ))}
      </div>
    </div>
  );
});
