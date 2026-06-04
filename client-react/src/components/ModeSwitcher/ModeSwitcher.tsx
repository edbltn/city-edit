import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  // Explicit width for the (left-anchored) dropdown: wide enough to show the
  // longest map row in full, capped at the viewport's right edge (then rows
  // ellipsize). Measured in JS because the dropdown's `.mode-options` is a
  // vertical scroll container, which silently swallows CSS `width: max-content`.
  const [dropdownWidth, setDropdownWidth] = useState<number>();
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

  // Size the dropdown to fit the widest map row, left-anchored, capped at the
  // viewport's right edge. A row's required width = its fixed parts (icon, votes,
  // check, padding) + the natural text width — `scrollWidth` reports that even
  // when the label/subtitle is currently ellipsized, so this is independent of
  // the dropdown's present width and stays correct across resizes.
  //
  // Computed even while closed (the dropdown is visibility:hidden, so it's still
  // laid out and measurable). This is deliberate: with no CSS width, an absolutely
  // positioned dropdown shrink-to-fits to ~max-content, which flickers between the
  // button width and the content width and made it flash/pop on open. Pinning an
  // explicit width up front keeps it stable so opening is just the fade-in.
  // Depends on `visible` so it recomputes when maps load or the search filters.
  useLayoutEffect(() => {
    const update = () => {
      const el = ref.current;
      if (!el) return;
      const dropdown = el.querySelector(".mode-dropdown");
      if (!dropdown) return;
      const cap = Math.max(240, Math.round(window.innerWidth - el.getBoundingClientRect().left - 8));

      let needed = 0;
      el.querySelectorAll<HTMLElement>(".mode-option").forEach((opt) => {
        const textBlock = opt.querySelector<HTMLElement>(".mode-option-text");
        if (!textBlock) return;
        const fixedParts = opt.clientWidth - textBlock.clientWidth; // icon + votes + check + gaps + padding
        const label = opt.querySelector<HTMLElement>(".mode-label");
        const sub = opt.querySelector<HTMLElement>(".mode-sub");
        const naturalText = Math.max(label?.scrollWidth ?? 0, sub?.scrollWidth ?? 0);
        needed = Math.max(needed, fixedParts + naturalText);
      });
      if (needed === 0) return; // list not rendered yet — recomputed when it arrives
      const border = dropdown.clientWidth ? (dropdown as HTMLElement).offsetWidth - dropdown.clientWidth : 2;
      setDropdownWidth(Math.min(Math.ceil(needed + border) + 1, cap));
    };
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [visible]);

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

      <div
        className="mode-dropdown"
        role="listbox"
        style={dropdownWidth ? { width: dropdownWidth } : undefined}
      >
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
