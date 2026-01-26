import { useState, useCallback, useEffect, useRef, memo } from "react";
import { useRoute } from "../../context";
import { useWebSocketContext } from "../../context";
import { MODES, type TransportMode } from "../../types";
import "./ModeSelector.css";

export const ModeSelector = memo(function ModeSelector() {
  const [isOpen, setIsOpen] = useState(false);
  const { mode, setMode } = useRoute();
  const { setMode: sendModeToServer } = useWebSocketContext();
  const containerRef = useRef<HTMLDivElement>(null);

  const currentMode = MODES.find((m) => m.mode === mode) || MODES[0];

  const handleToggle = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsOpen((prev) => !prev);
  }, []);

  const handleSelectMode = useCallback(
    (newMode: TransportMode) => {
      setMode(newMode);
      sendModeToServer(newMode);
      setIsOpen(false);
    },
    [setMode, sendModeToServer]
  );

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = () => setIsOpen(false);
    document.addEventListener("click", handleClickOutside);
    return () => document.removeEventListener("click", handleClickOutside);
  }, []);

  return (
    <div
      ref={containerRef}
      className={`mode-selector ${isOpen ? "active" : ""}`}
    >
      <button className="mode-btn" onClick={handleToggle}>
        <span className="mode-icon">{currentMode.icon}</span>
        <span className="mode-label">{currentMode.label}</span>
      </button>
      <div className="mode-dropdown">
        {MODES.map((option) => (
          <div
            key={option.mode}
            className={`mode-option ${mode === option.mode ? "selected" : ""}`}
            onClick={() => handleSelectMode(option.mode)}
          >
            <span className="mode-icon">{option.icon}</span>
            <span className="mode-label">{option.label}</span>
            <span className="check-icon">{"\u2713"}</span>
          </div>
        ))}
      </div>
    </div>
  );
});
