import { memo } from "react";
import { useRoute } from "../../context";
import { ModeSelector } from "../ModeSelector";
import { HexagonIcon } from "./HexagonIcon";
import { ROUTE_COLORS } from "../../colors";
import "./TopBar.css";

export const TopBar = memo(function TopBar() {
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

  const showLegend = mode !== "walk" && desirePathData && routeData;
  const modeLegendColor =
    mode === "bike" ? ROUTE_COLORS.bike.core : ROUTE_COLORS.drive.asphalt;
  const modeLabel = mode === "bike" ? "Bike path" : "Drive path";

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

        {/* Route section: coordinates + legend on top, actions below */}
        <div className="route-section">
          <div className="route-fields-row">
            <div className="route-fields">
              <div className="route-field">
                <span className="route-field-label">Start</span>
                <div
                  className={`route-field-value ${!start.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}
                >
                  {formatCoords(start.coords) || "Click map to set start"}
                </div>
              </div>
              <div className="route-field">
                <span className="route-field-label">End</span>
                <div
                  className={`route-field-value ${!end.coords ? "empty" : ""} ${isLoading ? "loading" : ""}`}
                >
                  {formatCoords(end.coords) || "Click map to set end"}
                </div>
              </div>
            </div>

            <div className={`route-legend ${showLegend ? "active" : ""}`}>
              <div className="legend-item">
                <HexagonIcon />
                <span>Most Requested</span>
              </div>
              <div className="legend-path-items">
                <div className="legend-item">
                  <span
                    className="legend-line legend-line-mode"
                    style={{ background: modeLegendColor }}
                  ></span>
                  <span className="legend-mode-label">{modeLabel}</span>
                </div>
                <div className="legend-item">
                  <span className="legend-line legend-line-desire"></span>
                  <span className="legend-desire-label">Desire Path</span>
                </div>
              </div>
            </div>

            {isLoading && (
              <div className="calculating-indicator active">
                <div className="spinner"></div>
                <span>Calculating...</span>
              </div>
            )}
          </div>

          <div className="route-actions">
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
          </div>
        </div>
      </div>
    </header>
  );
});
