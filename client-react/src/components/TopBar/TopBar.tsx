import { memo, useState, useCallback } from "react";
import { useRoute, useTheme, useHeatmap } from "../../context";
import { landingHref } from "../../themes";
import { getCurrentMap } from "../../map/runtime";
import { fitPoints } from "../../utils/mapViewState";
import { HowItWorksModal } from "../HowItWorksModal";
import { ModeSwitcher } from "../ModeSwitcher";
import { VoteTypeSelector } from "../VoteTypeSelector";
import { AddressSearch } from "../AddressSearch";
import { NavRail } from "../NavRail";
import { Logo } from "../Logo";
import { chromeMode } from "../../utils/chromeMode";
import type { LatLng } from "../../types";
import "./TopBar.css";
import "./TopBarFloat.css";

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
    pointType,
    activeTool,
    isDirectionCast,
    setActiveTool,
    armStartReplace,
    setStartPoint,
    setEndPoint,
    clearPoints,
    castVote,
  } = useRoute();

  // Station networks (e.g. ebikes) vote on a single point, so there's no end
  // tool — treat them as point-only regardless of the theme's input mode.
  // Point-only maps (point input mode, or a station network like ebikes) vote on
  // a single location with no route, so the whole start/end legend is hidden.
  const isStationNetwork = (getCurrentMap()?.network ?? "streets") !== "streets";
  const isPointOnly = theme.inputMode === "point" || isStationNetwork;

  const isLoading = isCalculating || isCalculatingSplit;

  const formatLocation = (point: { coords: { lat: number; lng: number } | null; address?: string | null } | null) => {
    if (!point?.coords) return null;
    if (typeof point.address === 'string') return point.address;
    return `${point.coords.lat.toFixed(5)}, ${point.coords.lng.toFixed(5)}`;
  };

  const handleStartClick = useCallback(() => {
    if (isPointOnly) return;
    // Arm an in-place move: the next map click relocates the start and keeps the
    // existing end + waypoints (instead of the default click-to-restart wipe).
    armStartReplace();
    setTypingField("start");
  }, [isPointOnly, armStartReplace]);

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
      // Frame the whole route: with both ends placed, zooming to the point just
      // set would push the other one off screen.
      fitPoints(
        field === "start" ? [coords, end.coords] : [start.coords, coords]
      );
    },
    [setStartPoint, setEndPoint, setActiveTool, start.coords, end.coords]
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
    // data-chrome selects the whole top-chrome treatment: "float" lays the
    // controls on the map as plates, "banner" is the original opaque strip.
    // Every floating rule is scoped to this attribute (TopBarFloat.css).
    <header className="topbar" data-chrome={chromeMode()}>
      <h1 onClick={goToLanding} className="logo-container">
        <Logo className="logo-img" />
      </h1>
      <div className="logo-mobile-banner" onClick={goToLanding}>
        <svg viewBox="-1 -1 233 24" xmlns="http://www.w3.org/2000/svg" className="logo-mobile-svg">
          {["C","I","T","Y","","E","D","I","T"].map((ch, i) => {
            const x = i * 26;
            if (!ch) return <rect key={i} x={x} y="0" width="22" height="22" fill="none" stroke="#d4d4d4" strokeWidth="1.5" opacity="0.15"/>;
            return <g key={i}>
              <rect x={x} y="0" width="22" height="22" fill="none" stroke="#d4d4d4" strokeWidth="1.5"/>
              <text x={x + 11} y="11" fontFamily="var(--font-main)" fontSize="14" fontWeight="600" fill="#d4d4d4" textAnchor="middle" dominantBaseline="central">{ch}</text>
            </g>;
          })}
        </svg>
      </div>

      {/* `data-coach` marks the three controls the first-run coach hangs its
          callout off (onboarding/coachAnchor.ts). They are hooks for a reader,
          not for layout: nothing here styles them, and the coach queries them
          rather than holding a ref, so this file keeps no state for it. Moving
          a control is fine — the callout is measured from wherever it lands.
          Deleting an attribute is not: that step silently falls back to the
          bottom strip. */}
      <div className="topbar-content">
        {/* Point-only maps vote on a single clicked location — no start/end
            route legend at all (covers desktop and mobile). */}
        {!isPointOnly && (
          <div className="topbar-legend">
            {/* Each stacked pair is a real element, so the floating chrome has a
                container to hang ONE shadow on. Two cells of a single surface
                cannot shade each other, and without a wrapper the only thing left
                to carry the shadow is the cells themselves — which is exactly how
                the plus came to cast one onto the minus. In banner mode these
                collapse to `display: contents`, so that layout is untouched. */}
            <div className="legend-pair legend-pair-tools">
              {typingField === "start" ? (
                <div className={`${startToolClass.join(" ")} typing`} data-coach="start">
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
                  data-coach="start"
                  onClick={handleStartClick}
                  aria-pressed={activeTool === "start"}
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

              {typingField === "end" ? (
                <div className={`${endToolClass.join(" ")} typing`} data-coach="end">
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
                  className={endToolClass.join(" ")}
                  data-coach="end"
                  onClick={handleEndClick}
                  aria-pressed={activeTool === "end"}
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
            </div>

            <div className="legend-pair legend-pair-key">
              <div className="legend-item">
                <span className="legend-icon-slot">
                  <span className="legend-char-selection">◻</span>
                </span>
                <span>Selection</span>
              </div>

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
          </div>
        )}

        <div className={`topbar-actions${start.coords ? " has-selection" : ""}`}>
          {/* One grid cell: .topbar-actions is a strict 2×2 grid (4-across in
              landscape), so all the meta links share the one rail. How-it-Works
              lives here too — it's something to read, not an action on the
              current selection, which is what the second row is for.

              FIRST CHILD ON PURPOSE. It is the only one of these four that
              does NOT belong to the band's bottom row — it sits up on the
              wordmark's line — so it has to stand outside .picker-row below,
              and a wrapper can only enclose adjacent siblings. Its cell is
              stated explicitly in TopBar.css rather than left to
              auto-placement, which would now hand it (1,1). */}
          <NavRail onHowItWorks={() => setShowHowItWorks(true)} />

          {/* THE BAND'S BOTTOM ROW, as one element. Map, Vote and the -/+ over
              Clear block are one row's worth of controls, and at the width
              where they sit side by side that row needs a layout context of
              its own: sharing the bar's column tracks with the Start/End block
              and the legend above meant the block inherited a track sized for
              the LEGEND (251px for 117px of content) and the pills could not
              reach it.

              The wrapper is BOXLESS (`display: contents`) at every other
              width, so the banner's 2×2 grid and the floating desktop grid go
              on placing these three controls exactly as they did before it
              existed. Only the 580–1080 band gives it a box. */}
          <div className="picker-row">
            <div className="mode-switcher-group">
              <span className="mode-prefix-label">Map:</span>
              <ModeSwitcher />
            </div>

            {/* Always visible: this control is now the map's LEGEND as well as
                the cast-target picker (see VoteTypeSelector) — it says which
                proposal types are drawn and lets you toggle them — so it can no
                longer be gated on having a selection to vote on. */}
            <div className="vote-switcher-group" data-coach="votetype">
              <span className="mode-prefix-label">Vote:</span>
              <VoteTypeSelector />
            </div>

            {/* Row 2, right cell: acts on the current selection only, so it is
                empty until there is one. How-it-Works used to sit here purely to
                keep the row from collapsing — the fixed grid-template-rows does
                that on its own. */}
            <div className="actions-group">
              <div className={`calculating-indicator ${isLoading && start.coords && end.coords ? "active" : ""}`}>
                <div className="spinner"></div>
                <span>Calculating...</span>
              </div>

              <div
                className={`cast-group ${canVote && !isLoading ? "" : "hidden-reserve"}`}
                data-coach="cast"
                role="group"
                aria-label="Cast a vote for or against"
              >
                <span className="cast-prefix-label">Cast:</span>
                <button
                  type="button"
                  className={`btn-cast btn-cast-down${isDirectionCast(-1) ? " is-cast" : ""}`}
                  onClick={() => castVote(-1)}
                  aria-pressed={isDirectionCast(-1)}
                  title={isDirectionCast(-1) ? "Remove your vote against" : "Vote against"}
                  tabIndex={canVote && !isLoading ? undefined : -1}
                >
                  −
                </button>
                <button
                  type="button"
                  className={`btn-cast btn-cast-up${isDirectionCast(1) ? " is-cast" : ""}`}
                  onClick={() => castVote(1)}
                  aria-pressed={isDirectionCast(1)}
                  title={isDirectionCast(1) ? "Remove your vote for" : "Vote for"}
                  tabIndex={canVote && !isLoading ? undefined : -1}
                >
                  +
                </button>
              </div>

              <button
                className={`btn-header btn-clear ${start.coords && !isLoading ? "" : "hidden-reserve"}`}
                onClick={clearPoints}
                tabIndex={start.coords && !isLoading ? undefined : -1}
              >
                Clear
              </button>
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
