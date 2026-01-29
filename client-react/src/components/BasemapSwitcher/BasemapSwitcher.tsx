import { useState, useCallback } from "react";
import { CONFIG } from "../../config";
import type { BasemapId } from "../../types";
import "./BasemapSwitcher.css";

interface BasemapSwitcherProps {
  currentBasemap: BasemapId;
  onBasemapChange: (basemap: BasemapId) => void;
}

const basemapIds = Object.keys(CONFIG.basemaps) as BasemapId[];

export function BasemapSwitcher({ currentBasemap, onBasemapChange }: BasemapSwitcherProps) {
  const [isOpen, setIsOpen] = useState(false);

  const handleSelect = useCallback((id: BasemapId) => {
    onBasemapChange(id);
    setIsOpen(false);
  }, [onBasemapChange]);

  const currentName = CONFIG.basemaps[currentBasemap].name;

  return (
    <div className="basemap-switcher">
      <button
        className="basemap-toggle"
        onClick={() => setIsOpen(!isOpen)}
        title="Change basemap"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="7" height="7" />
          <rect x="14" y="3" width="7" height="7" />
          <rect x="3" y="14" width="7" height="7" />
          <rect x="14" y="14" width="7" height="7" />
        </svg>
        <span>{currentName}</span>
      </button>

      {isOpen && (
        <div className="basemap-dropdown">
          {basemapIds.map((id) => (
            <button
              key={id}
              className={`basemap-option ${id === currentBasemap ? "active" : ""}`}
              onClick={() => handleSelect(id)}
            >
              {CONFIG.basemaps[id].name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
