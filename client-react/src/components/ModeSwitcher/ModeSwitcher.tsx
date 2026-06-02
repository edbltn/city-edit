import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme, useRoute } from "../../context";
import { iconSrc, mapHref, symbolForMap, type ThemeNavState } from "../../themes";
import { getMapViewState } from "../../utils/mapViewState";
import { getMapSlug, getCurrentMap } from "../../map/runtime";
import { CONFIG } from "../../config";
import { CheckIcon } from "../CheckIcon";
import "./ModeSwitcher.css";

interface MapItem {
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

export const ModeSwitcher = memo(function ModeSwitcher() {
  const current = useTheme();
  const { start, end } = useRoute();
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [maps, setMaps] = useState<MapItem[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const currentSlug = getMapSlug();
  const currentCityId = getCurrentMap()?.cityId;

  // All maps, already ranked by the server (votes desc, then name asc).
  useEffect(() => {
    fetch(`${CONFIG.apiUrl}/maps`)
      .then((r) => r.json())
      .then((d) => setMaps(d.maps || []))
      .catch(() => {});
  }, []);

  // Close on outside click.
  useEffect(() => {
    if (!isOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
    else setQuery("");
  }, [isOpen]);

  const buildHref = useCallback((m: MapItem) => {
    // Switching to another city should land at that city's center — carrying
    // over the current view/points would be off-map. Only preserve within a city.
    if (m.cityId !== currentCityId) return mapHref(m.slug);

    const { zoom, center } = getMapViewState();
    const navState: ThemeNavState = {
      zoom,
      center,
      start: start.coords ? { lat: start.coords.lat, lng: start.coords.lng } : null,
      end: end.coords ? { lat: end.coords.lat, lng: end.coords.lng } : null,
    };
    return mapHref(m.slug, navState);
  }, [currentCityId, start.coords, end.coords]);

  const q = query.trim().toLowerCase();
  const visible = useMemo(() => {
    if (!q) return maps;
    return maps.filter((m) => {
      const labels = (m.voteTypes || []).map((v) => v.label).join(" ");
      const hay = `${m.name} ${m.subtitle || ""} ${m.city?.name || m.cityId} ${labels}`.toLowerCase();
      return hay.includes(q);
    });
  }, [maps, q]);

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
        <input
          ref={inputRef}
          className="mode-search"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search maps…"
          spellCheck={false}
          autoComplete="off"
        />
        <div className="mode-options">
          {visible.map((m) => {
            const isCurrent = m.slug === currentSlug;
            return (
              <a
                key={m.slug}
                className={`mode-option ${isCurrent ? "selected" : ""}`}
                href={buildHref(m)}
                role="option"
                aria-selected={isCurrent}
              >
                <img className="mode-icon-img" src={iconSrc(symbolForMap(m))} alt="" />
                <span className="mode-option-text">
                  <span className="mode-label">{m.name}</span>
                  {m.subtitle && <span className="mode-sub">{m.subtitle}</span>}
                </span>
                <span className="mode-votes">{m.voteCount ?? 0}</span>
                <span className="check-icon"><CheckIcon size={11} /></span>
              </a>
            );
          })}
          {visible.length === 0 && <div className="mode-empty">No maps found</div>}
        </div>
      </div>
    </div>
  );
});
