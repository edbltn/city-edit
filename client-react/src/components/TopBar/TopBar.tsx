import { memo, useState } from "react";
import { useRoute } from "../../context";
import { ModeSelector } from "../ModeSelector";
import { HowItWorksModal } from "../HowItWorksModal";
import { HexagonIcon } from "./HexagonIcon";
import { ROUTE_COLORS } from "../../colors";
import "./TopBar.css";

export const TopBar = memo(function TopBar() {
  const [showHowItWorks, setShowHowItWorks] = useState(false);

  const {
    start,
    end,
    mode,
    isCalculating,
    isCalculatingSplit,
    routeData,
    desirePathData,
    hasVoted,
    isVoting,
    clearPoints,
    castVote,
  } = useRoute();

  const isLoading = isCalculating || isCalculatingSplit;

  const formatCoords = (coords: { lat: number; lng: number } | null) => {
    if (!coords) return null;
    return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
  };

  // Show walk path legend when in walk mode with route data
  const showWalkLegend = mode === "walk" && routeData;
  // Show current/desired path legend when in bike/drive mode with both routes
  const showSplitLegend = mode !== "walk" && desirePathData && routeData;
  const modeLegendColor =
    mode === "bike" ? ROUTE_COLORS.bike.core : ROUTE_COLORS.drive.asphalt;

  // For walk mode, the route itself is voteable; for other modes, need desirePathData
  const canVote = mode === "walk" ? !!routeData : !!desirePathData;

  return (
    <header className="topbar">
      <div className="topbar-row topbar-row-main">
        <h1 onClick={() => window.location.reload()}>
          <span className="logo-wrapper">
            <img src="/path-icon.png" alt="" className="logo-icon" />
            <img
              src="/path-icon.png"
              alt=""
              className="logo-icon-shadow"
              aria-hidden="true"
            />
          </span>
          Desire Path Mapper
        </h1>

        <div className="header-divider"></div>

        {/* Route section: legend with coordinates on top, actions below */}
        <div className="route-section">
          <div className="route-legend">
            <div className="legend-item legend-item-coords">
              <span className="legend-icon-slot">
                {start.coords && <span className="legend-marker legend-marker-start"></span>}
              </span>
              <span className="legend-label">Start</span>
              <span className={`legend-coords ${!start.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}>
                {formatCoords(start.coords) || "Click map to set start"}
              </span>
            </div>
            <div className="legend-item legend-item-coords">
              <span className="legend-icon-slot">
                {end.coords && <span className="legend-marker legend-marker-end"></span>}
              </span>
              <span className="legend-label">End</span>
              <span className={`legend-coords ${!end.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}>
                {formatCoords(end.coords) || "Click map to set end"}
              </span>
            </div>
            <div className="legend-item">
              <span className="legend-icon-slot">
                <HexagonIcon />
              </span>
              <span>Most Desired</span>
            </div>
            {showWalkLegend && (
              <div className="legend-item">
                <span className="legend-icon-slot">
                  <span className="legend-dots legend-dots-walk"></span>
                </span>
                <span>Walk Path</span>
              </div>
            )}
            {showSplitLegend && (
              <>
                <div className="legend-item">
                  <span className="legend-icon-slot">
                    <span
                      className="legend-line legend-line-mode"
                      style={{ background: modeLegendColor }}
                    ></span>
                  </span>
                  <span>Current Route</span>
                </div>
                <div className="legend-item">
                  <span className="legend-icon-slot">
                    <span className="legend-line legend-line-desire"></span>
                  </span>
                  <span>Desired Path</span>
                </div>
              </>
            )}
          </div>

          <div className="route-actions">
            <button
              className="btn-header"
              onClick={() => setShowHowItWorks(true)}
            >
              How it Works
            </button>

            <ModeSelector />

            <button
              className="btn-header btn-clear"
              onClick={clearPoints}
              disabled={isLoading || isVoting}
            >
              Clear
            </button>

            {canVote && (
              <button
                className="btn-header btn-vote"
                onClick={castVote}
                disabled={hasVoted || isLoading || isVoting}
              >
                {isVoting ? "Voting..." : hasVoted ? "Vote Cast!" : "Cast Vote"}
              </button>
            )}

            {isLoading && (
              <div className="calculating-indicator active">
                <div className="spinner"></div>
                <span>Calculating...</span>
              </div>
            )}
          </div>
        </div>
      </div>

      <HowItWorksModal
        isOpen={showHowItWorks}
        onClose={() => setShowHowItWorks(false)}
      />
    </header>
  );
});
