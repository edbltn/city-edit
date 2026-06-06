import { useCallback } from "react";
import { isWithinMappedBounds } from "../utils/bounds";
import type { InputMode } from "../themes";
import type { LatLng, RoutePoint } from "../types";
import type { ActiveTool } from "../context/RouteContext";

interface MapClickState {
  start: RoutePoint;
  end: RoutePoint;
}

interface UseMapClickOptions {
  state: MapClickState;
  inputMode: InputMode;
  activeTool: ActiveTool;
  /** When true, a start-tool click MOVES the start in place (keeps the end +
   *  waypoints) instead of wiping the path. Armed from the legend; one-shot. */
  startReplaceArmed: boolean;
  onUpdateStart: (coords: LatLng) => void;
  onUpdateEnd: (coords: LatLng) => void;
  onSetActiveTool: (tool: ActiveTool) => void;
  onClearPoints: () => void;
  onClearGhostWaypoints: () => void;
  onSetError: (message: string) => void;
  suppressNextClick?: () => boolean;
  onClearSuppress?: () => void;
}

/**
 * Click flow:
 *
 *   - Point-only themes (e.g. trees): every click resets to a single point.
 *
 *   - Route/both themes: tool-based.
 *     - Start tool (default, sticky):
 *       - empty:           place start.
 *       - start exists:    wipe everything, place new start.
 *       - both exist:      wipe everything, place new start.
 *     - End tool (legend-armed):
 *       - no start:        ignore (need a start first).
 *       - no end:          place end. Tool reverts to Start.
 *       - end exists:      replace end. Tool reverts to Start.
 *
 *   - Drag from path: midwaypoint (handled elsewhere).
 *
 * Sticky Start enables tap-to-inspect-segment on mobile: each tap selects a
 * different segment without committing to a route until the user explicitly
 * arms the End tool from the legend.
 */
export function useMapClick({
  state,
  inputMode,
  activeTool,
  startReplaceArmed,
  onUpdateStart,
  onUpdateEnd,
  onSetActiveTool,
  onClearPoints,
  onClearGhostWaypoints,
  onSetError,
  suppressNextClick,
  onClearSuppress,
}: UseMapClickOptions) {
  const handleMapClick = useCallback(
    (latlng: LatLng) => {
      // Skip this click if suppressed (after ghost pin drop)
      if (suppressNextClick?.()) {
        onClearSuppress?.();
        return;
      }

      if (!isWithinMappedBounds(latlng)) {
        onSetError("That's outside this map — drop your pins inside the highlighted area.");
        return;
      }

      // Point-only mode: each click resets to a single point
      if (inputMode === "point") {
        onClearPoints();
        onUpdateStart(latlng);
        return;
      }

      if (activeTool === "start") {
        // Start armed for an in-place move (from the legend): drop the start on
        // the new spot but keep the end + waypoints, so the route just re-routes
        // from here. setStartPoint consumes the one-shot arm; the main calc
        // effect recomputes the path (and any split segments through the mids).
        if (startReplaceArmed) {
          onUpdateStart(latlng);
          return;
        }
        // Default sticky start: replace start, wiping any existing route.
        onClearGhostWaypoints();
        if (state.start.coords || state.end.coords) {
          onClearPoints();
        }
        onUpdateStart(latlng);
        return;
      }

      onClearGhostWaypoints();

      // activeTool === "end"
      if (!state.start.coords) {
        // Can't end without a start — silently ignore.
        // Legend should communicate this state ("place a start first").
        return;
      }

      onUpdateEnd(latlng);
      onSetActiveTool("start");
    },
    [
      state.start.coords,
      state.end.coords,
      inputMode,
      activeTool,
      startReplaceArmed,
      onUpdateStart,
      onUpdateEnd,
      onSetActiveTool,
      onClearPoints,
      onClearGhostWaypoints,
      onSetError,
      suppressNextClick,
      onClearSuppress,
    ]
  );

  return { handleMapClick };
}
