import { useEffect, useMemo, useState } from "react";
import { CONFIG } from "../../config";
import { iconSrc } from "../../themes";
import { SELECTABLE_MAP_STYLES, getMapStyle } from "../../mapStyles";
import { noAutofillProps } from "../../utils/noAutofill";
import "./ProposeMapModal.css";

// The app's iconset (public/icons/*.svg) — pickable per custom vote type.
const ICONS = [
  "walkways", "bikes", "trees", "parks", "transit", "safety", "accessibility",
  "pedestrian-streets", "traffic-reduction", "public-space", "cafes",
  "waterfront", "mapping",
];
const DEFAULT_ICON = "mapping";

interface City {
  id: string;
  name: string;
}

interface VoteTypeListSummary {
  id: number;
  key: string;
  name: string;
  voteTypes: { label: string; icon: string; pointType: "route" | "point" }[];
}

interface CustomRow {
  label: string;
  pointType: "route" | "point";
  icon: string;
}

function slugify(s: string): string {
  return s.toLowerCase().trim().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}

export function ProposeMapModal({ onClose }: { onClose: () => void }) {
  const [cities, setCities] = useState<City[]>([]);
  const [lists, setLists] = useState<VoteTypeListSummary[]>([]);

  const [name, setName] = useState("");
  // The map's main display icon (shown on its landing card). Defaults to bikes,
  // matching the SF Bike Lanes example the form is themed around.
  const [symbol, setSymbol] = useState("bikes");
  const [symbolPickerOpen, setSymbolPickerOpen] = useState(false);
  // Visual theme (basemap + accent + heat ramp); keyed into MAP_STYLES.
  const [style, setStyle] = useState("default");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [cityId, setCityId] = useState("");
  const [listChoice, setListChoice] = useState<string>(""); // preset id, or "custom"
  const [customRows, setCustomRows] = useState<CustomRow[]>([{ label: "", pointType: "route", icon: DEFAULT_ICON }]);
  const [iconPickerRow, setIconPickerRow] = useState<number | null>(null);
  const [allowSuggestions, setAllowSuggestions] = useState(true);
  const [passcode, setPasscode] = useState("");
  const [subtitle, setSubtitle] = useState("");

  const [slugAvailable, setSlugAvailable] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load cities + preset lists.
  useEffect(() => {
    fetch(`${CONFIG.apiUrl}/cities`)
      .then((r) => r.json())
      .then((d) => {
        setCities(d.cities || []);
        // Default to SF so the form's example (title/slug/description) is consistent.
        const cities = d.cities || [];
        const preferred = cities.find((c: { id: string }) => c.id === "sf") || cities[0];
        if (preferred) setCityId(preferred.id);
      })
      .catch(() => setError("Failed to load cities"));
    fetch(`${CONFIG.apiUrl}/vote-type-lists`)
      .then((r) => r.json())
      .then((d) => {
        setLists(d.lists || []);
        if (d.lists?.[0]) setListChoice(String(d.lists[0].id));
      })
      .catch(() => {});
  }, []);

  // Derive slug from name unless the user edited it directly.
  const effectiveSlug = useMemo(() => (slugTouched ? slug : slugify(name)), [slug, slugTouched, name]);

  // Debounced slug availability check.
  useEffect(() => {
    if (!effectiveSlug) {
      setSlugAvailable(null);
      return;
    }
    const t = setTimeout(() => {
      fetch(`${CONFIG.apiUrl}/maps/check-slug?slug=${encodeURIComponent(effectiveSlug)}`)
        .then((r) => r.json())
        .then((d) => setSlugAvailable(!!d.available))
        .catch(() => setSlugAvailable(null));
    }, 300);
    return () => clearTimeout(t);
  }, [effectiveSlug]);

  const isCustom = listChoice === "custom";

  // Shown as the greyed placeholder for the (optional) subtitle: "<city> · <list
  // name>". The custom-list name defaults to its first vote type's label. Left
  // blank, the server fills in this same default.
  const cityName = cities.find((c) => c.id === cityId)?.name || cityId;
  const listName = isCustom
    ? (customRows.find((r) => r.label.trim())?.label.trim() || "")
    : (lists.find((l) => String(l.id) === listChoice)?.name || "");
  const defaultSubtitle = [cityName, listName].filter(Boolean).join(" · ");

  const updateRow = (i: number, patch: Partial<CustomRow>) =>
    setCustomRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addRow = () => setCustomRows((rows) => [...rows, { label: "", pointType: "route", icon: DEFAULT_ICON }]);
  const removeRow = (i: number) => setCustomRows((rows) => rows.filter((_, idx) => idx !== i));

  const canSubmit =
    !!name.trim() && !!cityId && !!effectiveSlug && slugAvailable !== false && !submitting &&
    (isCustom ? customRows.some((r) => r.label.trim()) : !!listChoice);

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        name: name.trim(),
        subtitle: subtitle.trim() || undefined,
        slug: effectiveSlug,
        city_id: cityId,
        symbol,
        style,
        allow_suggestions: allowSuggestions,
        passcode: passcode.trim() || undefined,
      };
      if (isCustom) {
        body.custom_vote_types = customRows
          .filter((r) => r.label.trim())
          .map((r) => ({ label: r.label.trim(), icon: r.icon || DEFAULT_ICON, pointType: r.pointType }));
      } else {
        body.vote_type_list_id = Number(listChoice);
      }

      const res = await fetch(`${CONFIG.apiUrl}/maps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Failed to create map");
        setSubmitting(false);
        return;
      }
      // Navigate to the new map.
      window.location.href = `/m/${data.slug}`;
    } catch {
      setError("Network error");
      setSubmitting(false);
    }
  };

  return (
    <div className="propose-overlay" onClick={onClose}>
      <div className="propose-modal" onClick={(e) => e.stopPropagation()}>
        <header className="propose-header">
          <h2>Propose a Map</h2>
          <button className="propose-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        <div className="propose-body">
          <label className="propose-field">
            <span>Map name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. SF Bike Lanes" {...noAutofillProps} />
          </label>

          <div className="propose-field">
            <span>Display icon</span>
            <button
              type="button"
              className="propose-icon-btn"
              onClick={() => setSymbolPickerOpen((open) => !open)}
              aria-label="Choose display icon"
              title="Choose display icon"
            >
              <img src={iconSrc(symbol)} alt="" />
            </button>
            {symbolPickerOpen && (
              <div className="propose-icon-grid">
                {ICONS.map((name) => (
                  <button
                    type="button"
                    key={name}
                    className={`propose-icon-option ${symbol === name ? "selected" : ""}`}
                    onClick={() => { setSymbol(name); setSymbolPickerOpen(false); }}
                    title={name}
                  >
                    <img src={iconSrc(name)} alt={name} />
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="propose-field">
            <span>Theme</span>
            <div className="propose-theme-row">
              {SELECTABLE_MAP_STYLES.map((t) => {
                const ms = getMapStyle(t.id);
                return (
                  <button
                    type="button"
                    key={t.id}
                    className={`propose-theme-swatch ${style === t.id ? "selected" : ""}`}
                    onClick={() => setStyle(t.id)}
                    title={t.label}
                    aria-label={t.label}
                    style={{ background: ms.base }}
                  >
                    <span style={{ background: ms.accent }} />
                  </button>
                );
              })}
            </div>
          </div>

          <label className="propose-field">
            <span>URL slug</span>
            <input
              value={effectiveSlug}
              onChange={(e) => { setSlugTouched(true); setSlug(slugify(e.target.value)); }}
              placeholder="sf-bike-lanes"
              {...noAutofillProps}
            />
            {effectiveSlug && (
              <small className={slugAvailable === false ? "propose-bad" : "propose-ok"}>
                {slugAvailable === false ? "Taken" : slugAvailable ? "Available" : "…"} · /m/{effectiveSlug}
              </small>
            )}
          </label>

          <label className="propose-field">
            <span>City</span>
            <select value={cityId} onChange={(e) => setCityId(e.target.value)}>
              {cities.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>

          <label className="propose-field">
            <span>Vote-type list</span>
            <select value={listChoice} onChange={(e) => setListChoice(e.target.value)}>
              {lists.map((l) => <option key={l.id} value={String(l.id)}>{l.name}</option>)}
              <option value="custom">Custom…</option>
            </select>
          </label>

          {isCustom && (
            <div className="propose-custom">
              {customRows.map((row, i) => (
                <div className="propose-custom-item" key={i}>
                  <div className="propose-custom-row">
                    <button
                      type="button"
                      className="propose-icon-btn"
                      onClick={() => setIconPickerRow(iconPickerRow === i ? null : i)}
                      aria-label="Choose icon"
                      title="Choose icon"
                    >
                      <img src={iconSrc(row.icon)} alt="" />
                    </button>
                    <input
                      value={row.label}
                      onChange={(e) => updateRow(i, { label: e.target.value })}
                      placeholder="Vote type label"
                      {...noAutofillProps}
                    />
                    <select value={row.pointType} onChange={(e) => updateRow(i, { pointType: e.target.value as "route" | "point" })}>
                      <option value="route">Route</option>
                      <option value="point">Point</option>
                    </select>
                    <button onClick={() => removeRow(i)} aria-label="Remove">×</button>
                  </div>
                  {iconPickerRow === i && (
                    <div className="propose-icon-grid">
                      {ICONS.map((name) => (
                        <button
                          type="button"
                          key={name}
                          className={`propose-icon-option ${row.icon === name ? "selected" : ""}`}
                          onClick={() => { updateRow(i, { icon: name }); setIconPickerRow(null); }}
                          title={name}
                        >
                          <img src={iconSrc(name)} alt={name} />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              <button className="propose-add-row" onClick={addRow}>+ Add vote type</button>
            </div>
          )}

          <label className="propose-check">
            <input type="checkbox" checked={allowSuggestions} onChange={(e) => setAllowSuggestions(e.target.checked)} />
            <span>Allow users to suggest new vote types</span>
          </label>

          <label className="propose-field">
            <span>Subtitle <em>(optional)</em></span>
            <input
              value={subtitle}
              onChange={(e) => setSubtitle(e.target.value)}
              placeholder={defaultSubtitle}
              {...noAutofillProps}
            />
          </label>

          <label className="propose-field">
            <span>Passcode <em>(optional — required to open the map)</em></span>
            <input type="text" name="map-passcode" value={passcode} onChange={(e) => setPasscode(e.target.value)} placeholder="Leave blank for an open map" {...noAutofillProps} />
          </label>

          {error && <div className="propose-error">{error}</div>}
        </div>

        <footer className="propose-footer">
          <button className="propose-cancel" onClick={onClose}>Cancel</button>
          <button className="propose-submit" disabled={!canSubmit} onClick={submit}>
            {submitting ? "Creating…" : "Create map"}
          </button>
        </footer>
      </div>
    </div>
  );
}
