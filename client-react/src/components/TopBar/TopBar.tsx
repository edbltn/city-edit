import { memo, useState } from "react";
import { useRoute, useTheme } from "../../context";
import { landingHref } from "../../themes";
import { HowItWorksModal } from "../HowItWorksModal";
import { VoteTypeSelector } from "../VoteTypeSelector";
import "./TopBar.css";

export const TopBar = memo(function TopBar() {
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const theme = useTheme();

  const {
    start,
    end,
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

  const isPointOnly = theme.inputMode === "point";

  const isLoading = isCalculating || isCalculatingSplit;

  const formatLocation = (point: { coords: { lat: number; lng: number } | null; address?: string | null } | null) => {
    if (!point?.coords) return null;
    // If address is a string, show it
    if (typeof point.address === 'string') return point.address;
    // Show coordinates as placeholder while loading or if no address
    return `${point.coords.lat.toFixed(5)}, ${point.coords.lng.toFixed(5)}`;
  };

  // Can vote once we have a route (2 points), split paths (waypoints), OR a single point (for point votes)
  const canVote = !!routeData || splitDesirePaths.length > 0 || (pointType === "point" && !!start.coords);

  const goToLanding = () => {
    window.location.href = landingHref();
  };

  return (
    <header className="topbar">
      <h1 onClick={goToLanding} className="logo-container">
        <img src="/logo.svg" alt="City Edit" className="logo-img" />
      </h1>
      <div className="logo-mobile-banner" onClick={goToLanding}>
        <svg viewBox="-1 -1 233 25" xmlns="http://www.w3.org/2000/svg" className="logo-mobile-svg">
          {["C","I","T","Y","","E","D","I","T"].map((ch, i) => {
            const x = i * 26;
            if (!ch) return <rect key={i} x={x} y="0" width="22" height="22" fill="none" stroke="#d4d4d4" strokeWidth="1.5" opacity="0.15"/>;
            return <g key={i}>
              <rect x={x} y="0" width="22" height="22" fill="none" stroke="#d4d4d4" strokeWidth="1.5"/>
              <text x={x + 11} y="11" fontFamily="monospace" fontSize="14" fontWeight="600" fill="#d4d4d4" textAnchor="middle" dominantBaseline="central">{ch}</text>
            </g>;
          })}
        </svg>
      </div>

      <div className="topbar-content">
        <div className="topbar-row topbar-row-main">
          {/* Route section: legend with coordinates on top, actions below */}
          <div className="route-section">
          <div className="route-legend">
            <div className="legend-item legend-item-coords">
              <span className="legend-icon-slot">
                <span className="legend-char-start">●</span>
              </span>
              <span className="legend-label">{theme.locationLabel}</span>
              <span className={`legend-coords ${!start.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}>
                {formatLocation(start) || (isPointOnly ? "click map to set location" : "click map to set start")}
              </span>
            </div>
            {!isPointOnly && (
              <div className="legend-item legend-item-coords">
                <span className="legend-icon-slot">
                  <span className="legend-char-end">●</span>
                </span>
                <span className="legend-label">End</span>
                <span className={`legend-coords ${!end.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}>
                  {formatLocation(end) || "click map to set end"}
                </span>
              </div>
            )}
            {(routeData || splitDesirePaths.length > 0) && !isLoading && (
              <div className="legend-item">
                <span className="legend-icon-slot">
                  <span className="legend-char-selection">◻</span>
                </span>
                <span>Selection</span>
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
              {(canVote || (start.coords && end.coords)) && <VoteTypeSelector />}
            </div>

            <div className="btn-group">
              {start.coords && (
                <button
                  className="btn-header btn-clear"
                  onClick={clearPoints}
                  disabled={isLoading || isVoting}
                >
                  Clear
                </button>
              )}

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
      </div>

      <HowItWorksModal
        isOpen={showHowItWorks}
        onClose={() => setShowHowItWorks(false)}
      />
    </header>
  );
});
