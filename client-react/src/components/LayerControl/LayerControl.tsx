import { useState } from "react";
import { useGISLayers, type GISLayerType } from "../../context";
import "./LayerControl.css";

interface LayerOption {
  id: GISLayerType;
  label: string;
  icon: string;
}

const LAYER_OPTIONS: LayerOption[] = [
  { id: "crosswalks", label: "Crosswalks", icon: "zebra" },
  { id: "stopSigns", label: "Stop Signs", icon: "stop" },
  { id: "trafficSignals", label: "Traffic Signals", icon: "signal" },
  { id: "trees", label: "Trees", icon: "tree" },
];

export function LayerControl() {
  const [isOpen, setIsOpen] = useState(false);
  const { layers, toggleLayer } = useGISLayers();

  return (
    <div className="layer-control">
      <button
        className="layer-toggle"
        onClick={() => setIsOpen(!isOpen)}
        title="Toggle map layers"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
        <span>Layers</span>
      </button>

      {isOpen && (
        <div className="layer-dropdown">
          <div className="layer-dropdown-header">Map Layers</div>
          {LAYER_OPTIONS.map((option) => {
            const state = layers[option.id];
            return (
              <label key={option.id} className="layer-option">
                <input
                  type="checkbox"
                  checked={state.visible}
                  onChange={() => toggleLayer(option.id)}
                />
                <span className="layer-label">
                  <LayerIcon type={option.icon} />
                  {option.label}
                </span>
                {state.loading && <span className="layer-loading">...</span>}
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

function LayerIcon({ type }: { type: string }) {
  switch (type) {
    case "zebra":
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <rect x="2" y="8" width="4" height="8" />
          <rect x="10" y="8" width="4" height="8" />
          <rect x="18" y="8" width="4" height="8" />
        </svg>
      );
    case "stop":
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2" />
        </svg>
      );
    case "signal":
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="8" y="2" width="8" height="20" rx="2" />
          <circle cx="12" cy="7" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="12" cy="17" r="2" />
        </svg>
      );
    case "tree":
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 22v-7" />
          <path d="M12 15L5 8l7-6 7 6-7 7z" />
        </svg>
      );
    default:
      return null;
  }
}
