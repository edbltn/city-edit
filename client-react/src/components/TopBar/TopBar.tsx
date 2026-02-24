import { memo, useState } from "react";
import { useRoute } from "../../context";
import { ModeSelector } from "../ModeSelector";
import { HowItWorksModal } from "../HowItWorksModal";
import { VoteTypeSelector } from "../VoteTypeSelector";
import { HeatLineIcon } from "./HeatLineIcon";
import "./TopBar.css";

export const TopBar = memo(function TopBar() {
  const [showHowItWorks, setShowHowItWorks] = useState(false);

  const {
    start,
    end,
    startLabel,
    endLabel,
    mode,
    isCalculating,
    isCalculatingSplit,
    routeData,
    splitDesirePaths,
    hasVoted,
    isVoting,
    pointType,
    clearPoints,
    castVote,
  } = useRoute();

  const isLoading = isCalculating || isCalculatingSplit;

  const formatLocation = (
    coords: { lat: number; lng: number } | null,
    label: string | null
  ) => {
    if (!coords) return null;
    if (label) return label;
    return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
  };

  // Can vote once we have a route (2 points), split paths (waypoints), OR a single point (for point votes)
  const canVote = !!routeData || splitDesirePaths.length > 0 || (pointType === "point" && !!start.coords);

  // Get legend icon class based on mode
  const getLegendIcon = () => {
    if (mode === "walk") {
      return <span className="legend-dots legend-dots-walk"></span>;
    }
    if (mode === "bike") {
      return <span className="legend-line legend-line-bike"></span>;
    }
    // Drive mode
    return <span className="legend-line legend-line-drive"></span>;
  };

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
                {formatLocation(start.coords, startLabel) || "Click map to set start"}
              </span>
            </div>
            <div className="legend-item legend-item-coords">
              <span className="legend-icon-slot">
                {end.coords && <span className="legend-marker legend-marker-end"></span>}
              </span>
              <span className="legend-label">End</span>
              <span className={`legend-coords ${!end.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}>
                {formatLocation(end.coords, endLabel) || "Click map to set end"}
              </span>
            </div>
            <div className="legend-item">
              <span className="legend-icon-slot">
                <HeatLineIcon />
              </span>
              <span>Most Requested</span>
            </div>
            {(routeData || splitDesirePaths.length > 0) && !isLoading && (
              <div className="legend-item">
                <span className="legend-icon-slot">
                  {getLegendIcon()}
                </span>
                <span>Proposed Path</span>
              </div>
            )}
          </div>

          <div className="route-actions">
            <button
              className="btn-header"
              onClick={() => setShowHowItWorks(true)}
            >
              How it Works
            </button>

            <div className="btn-group">
              <ModeSelector />
              {(canVote || (start.coords && end.coords)) && <VoteTypeSelector />}
            </div>

            <div className="btn-group">
              <button
                className="btn-header btn-clear"
                onClick={clearPoints}
                disabled={isLoading || isVoting}
              >
                Clear
              </button>

              {/* Show calculating when loading with both points set */}
              {isLoading && start.coords && end.coords && (
                <div className="calculating-indicator active">
                  <div className="spinner"></div>
                  <span>Calculating...</span>
                </div>
              )}

              {/* Show Cast Vote when route ready and not loading */}
              {canVote && !isLoading && (
                <button
                  className="btn-header btn-vote"
                  onClick={castVote}
                  disabled={hasVoted || isVoting}
                >
                  {isVoting ? "Voting..." : hasVoted ? "Vote Cast!" : "Cast Vote"}
                </button>
              )}
            </div>
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
