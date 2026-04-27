import { useCallback } from "react";
import { isWithinMappedBounds } from "../utils/bounds";
import type { InputMode } from "../themes";
import type { LatLng, RoutePoint } from "../types";

interface MapClickState {
  start: RoutePoint;
  end: RoutePoint;
}

interface UseMapClickOptions {
  state: MapClickState;
  inputMode: InputMode;
  onUpdateStart: (coords: LatLng) => void;
  onUpdateEnd: (coords: LatLng) => void;
  onClearPoints: () => void;
  onClearGhostWaypoints: () => void;
  onSetError: (message: string) => void;
  suppressNextClick?: () => boolean;
  onClearSuppress?: () => void;
}

export function useMapClick({
  state,
  inputMode,
  onUpdateStart,
  onUpdateEnd,
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

      // Validate click is within mapped bounds
      if (!isWithinMappedBounds(latlng)) {
        onSetError("Not mapped yet — please limit to Manhattan");
        return;
      }

      // Point-only mode (e.g. trees): every click sets a new single location
      if (inputMode === "point") {
        onClearPoints();
        onUpdateStart(latlng);
        return;
      }

      // Route / both mode: standard two-point flow
      onClearGhostWaypoints();

      if (!state.start.coords) {
        onUpdateStart(latlng);
        return;
      }

      if (!state.end.coords) {
        onUpdateEnd(latlng);
        return;
      }

      // Both exist: previous end becomes start, new click becomes end
      const prevEnd = state.end.coords;
      onUpdateStart(prevEnd);
      onUpdateEnd(latlng);
    },
    [
      state.start.coords,
      state.end.coords,
      inputMode,
      onUpdateStart,
      onUpdateEnd,
      onClearPoints,
      onClearGhostWaypoints,
      onSetError,
      suppressNextClick,
      onClearSuppress,
    ]
  );

  return { handleMapClick };
}
