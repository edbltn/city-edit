import { memo, useEffect, useRef, useState } from "react";
import { useTheme } from "../../context";
import { THEME_ORDER, themeHref } from "../../themes";

export const ModeSwitcher = memo(function ModeSwitcher() {
  const current = useTheme();
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

  return (
    <div ref={ref} className={`mode-selector ${isOpen ? "active" : ""}`}>
      <button
        type="button"
        className="mode-btn"
        onClick={() => setIsOpen(!isOpen)}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
      >
        <span className="mode-icon">{current.symbol}</span>
        <span className="mode-label">{current.name}</span>
        <span className="mode-caret" aria-hidden>▾</span>
      </button>

      <div className="mode-dropdown" role="listbox">
        {THEME_ORDER.map((theme) => (
          <a
            key={theme.id}
            className={`mode-option ${theme.id === current.id ? "selected" : ""}`}
            href={themeHref(theme)}
            role="option"
            aria-selected={theme.id === current.id}
          >
            <span className="mode-icon">{theme.symbol}</span>
            <span className="mode-label">{theme.name}</span>
            <span className="check-icon">✓</span>
          </a>
        ))}
      </div>
    </div>
  );
});
