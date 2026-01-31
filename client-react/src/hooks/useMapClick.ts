import { useCallback } from "react";
import type { LatLng, RoutePoint } from "../types";

interface MapClickState {
  start: RoutePoint;
  end: RoutePoint;
}

interface UseMapClickOptions {
  state: MapClickState;
  onUpdateStart: (coords: LatLng) => void;
  onUpdateEnd: (coords: LatLng) => void;
  onClearGhostWaypoints: () => void;
  suppressNextClick?: () => boolean;
  onClearSuppress?: () => void;
}

export function useMapClick({
  state,
  onUpdateStart,
  onUpdateEnd,
  onClearGhostWaypoints,
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

      // New map click always clears ghost waypoints (starting fresh route segment)
      onClearGhostWaypoints();

      // If no start, set start
      if (!state.start.coords) {
        onUpdateStart(latlng);
        return;
      }

      // If start exists but no end, set end
      if (!state.end.coords) {
        onUpdateEnd(latlng);
        return;
      }

      // Both exist: previous end becomes start, new click becomes end
      const prevEnd = state.end.coords;
      onUpdateStart(prevEnd);
      onUpdateEnd(latlng);
    },
    [state.start.coords, state.end.coords, onUpdateStart, onUpdateEnd, onClearGhostWaypoints, suppressNextClick, onClearSuppress]
  );

  return { handleMapClick };
}
