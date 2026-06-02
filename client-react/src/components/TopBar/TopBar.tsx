import { memo, useState, useCallback } from "react";
import { useRoute, useTheme, useHeatmap } from "../../context";
import { landingHref } from "../../themes";
import { panTo } from "../../utils/mapViewState";
import { HowItWorksModal } from "../HowItWorksModal";
import { ModeSwitcher } from "../ModeSwitcher";
import { VoteTypeSelector } from "../VoteTypeSelector";
import { AddressSearch } from "../AddressSearch";
import type { LatLng } from "../../types";
import "./TopBar.css";

export const TopBar = memo(function TopBar() {
  const [showHowItWorks, setShowHowItWorks] = useState(false);
  const [typingField, setTypingField] = useState<"start" | "end" | null>(null);
  const theme = useTheme();
  const { isHeatmapLoading } = useHeatmap();

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
    activeTool,
    setActiveTool,
    setStartPoint,
    setEndPoint,
    clearPoints,
    castVote,
  } = useRoute();

  const isPointOnly = theme.inputMode === "point";

  const isLoading = isCalculating || isCalculatingSplit;

  const formatLocation = (point: { coords: { lat: number; lng: number } | null; address?: string | null } | null) => {
    if (!point?.coords) return null;
    if (typeof point.address === 'string') return point.address;
    return `${point.coords.lat.toFixed(5)}, ${point.coords.lng.toFixed(5)}`;
  };

  const handleStartClick = useCallback(() => {
    if (isPointOnly) return;
    setActiveTool("start");
    setTypingField("start");
  }, [isPointOnly, setActiveTool]);

  const handleEndClick = useCallback(() => {
    setActiveTool("end");
    setTypingField("end");
  }, [setActiveTool]);

  const handleAddressSelect = useCallback(
    (field: "start" | "end", coords: LatLng, address: string) => {
      if (field === "start") {
        setStartPoint(coords, address);
      } else {
        setEndPoint(coords, address);
        setActiveTool("start");
      }
      setTypingField(null);
      panTo(coords);
    },
    [setStartPoint, setEndPoint, setActiveTool]
  );

  const handleTypingClose = useCallback(() => {
    setTypingField(null);
  }, []);

  // Can vote once we have a route (2 points), split paths (waypoints), OR a single point (for point votes)
  const canVote = !!routeData || splitDesirePaths.length > 0 || (pointType === "point" && !!start.coords);

  const startPlaceholder = isPointOnly ? "click map to set location" : "click map to set start";
  const endPlaceholder = !start.coords
    ? "place a start first"
    : activeTool === "end"
      ? "click map to set endpoint"
      : "click here to set endpoint";

  const startToolClass = ["legend-item", "legend-item-coords", "legend-tool"];
  if (!isPointOnly && activeTool === "start") startToolClass.push("active");

  const endToolClass = ["legend-item", "legend-item-coords", "legend-tool"];
  if (activeTool === "end") endToolClass.push("active");

  const startCoordsClass = ["legend-coords"];
  if (!start.coords) startCoordsClass.push("empty");
  if (isLoading) startCoordsClass.push("loading");

  const endCoordsClass = ["legend-coords"];
  if (!end.coords) endCoordsClass.push("empty");
  if (isLoading) endCoordsClass.push("loading");

  const goToLanding = () => {
    window.location.href = landingHref();
  };

  return (
    <header className="topbar">
      <h1 onClick={goToLanding} className="logo-container">
        <img src="/logo.svg" alt="City Edit" className="logo-img" />
      </h1>
      <div className="logo-mobile-banner" onClick={goToLanding}>
        <svg viewBox="-1 -1 233 24" xmlns="http://www.w3.org/2000/svg" className="logo-mobile-svg">
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
        <div className="topbar-legend">
          {typingField === "start" ? (
            <div className={`${startToolClass.join(" ")} typing`}>
              <span className="legend-icon-slot">
                <span className="legend-char-start">◆</span>
              </span>
              <span className="legend-label">{theme.locationLabel}</span>
              <AddressSearch
                onSelect={(coords, address) => handleAddressSelect("start", coords, address)}
                onClose={handleTypingClose}
                placeholder="Search address..."
                accentColor="var(--color-start)"
              />
            </div>
          ) : (
            <button
              type="button"
              className={startToolClass.join(" ")}
              onClick={handleStartClick}
              disabled={isPointOnly}
              aria-pressed={!isPointOnly && activeTool === "start"}
            >
              <span className="legend-icon-slot">
                <span className="legend-char-start">◆</span>
              </span>
              <span className="legend-label">{theme.locationLabel}</span>
              <span className={startCoordsClass.join(" ")}>
                {formatLocation(start) || startPlaceholder}
              </span>
            </button>
          )}

          <div className="legend-item">
            <span className="legend-icon-slot">
              <span className="legend-char-selection">◻</span>
            </span>
            <span>Selection</span>
          </div>

          {typingField === "end" ? (
            <div className={`${endToolClass.join(" ")} typing`}>
              <span className="legend-icon-slot">
                <span className="legend-char-end">◆</span>
              </span>
              <span className="legend-label">End</span>
              <AddressSearch
                onSelect={(coords, address) => handleAddressSelect("end", coords, address)}
                onClose={handleTypingClose}
                placeholder="Search address..."
                accentColor="var(--color-end)"
              />
            </div>
          ) : (
            <button
              type="button"
              className={[...endToolClass, isPointOnly ? "hidden-reserve" : ""].join(" ")}
              onClick={handleEndClick}
              aria-pressed={activeTool === "end"}
              tabIndex={isPointOnly ? -1 : undefined}
            >
              <span className="legend-icon-slot">
                <span className="legend-char-end">◆</span>
              </span>
              <span className="legend-label">End</span>
              <span className={endCoordsClass.join(" ")}>
                {formatLocation(end) || endPlaceholder}
              </span>
            </button>
          )}

          <div className="legend-item legend-item-heatmap">
            <span className="legend-icon-slot">
              {isHeatmapLoading ? (
                <span className="spinner" aria-label="Loading votes" />
              ) : (
                <span className="legend-heat-swatch" />
              )}
            </span>
            <span>{isHeatmapLoading ? "Loading…" : "Votes"}</span>
          </div>
        </div>

        <div className={`topbar-actions${start.coords ? " has-selection" : ""}`}>
          <div className="mode-switcher-group">
            <span className="mode-prefix-label">Mode:</span>
            <ModeSwitcher />
          </div>

          <button
            className="btn-header"
            onClick={() => setShowHowItWorks(true)}
          >
            How it Works
          </button>

          <div className={`vote-switcher-group ${(canVote || (start.coords && end.coords)) ? "" : "hidden-reserve"}`}>
            <span className="mode-prefix-label">Vote:</span>
            <VoteTypeSelector />
          </div>

          <div className="actions-group">
            <div className={`calculating-indicator ${isLoading && start.coords && end.coords ? "active" : ""}`}>
              <div className="spinner"></div>
              <span>Calculating...</span>
            </div>

            <button
              className={`btn-header btn-clear ${start.coords && !isLoading ? "" : "hidden-reserve"}`}
              onClick={clearPoints}
              disabled={isVoting}
              tabIndex={start.coords && !isLoading ? undefined : -1}
            >
              Clear
            </button>

            <button
              className={`btn-header btn-vote ${canVote && !isLoading ? "" : "hidden-reserve"}`}
              onClick={castVote}
              disabled={hasVoted || isVoting}
              tabIndex={canVote && !isLoading ? undefined : -1}
            >
              <span>{isVoting ? "Voting..." : hasVoted ? "Vote Cast!" : "Cast Vote"}</span>
            </button>
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
