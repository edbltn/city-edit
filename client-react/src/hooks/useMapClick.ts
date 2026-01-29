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
  suppressNextClick?: () => boolean;
  onClearSuppress?: () => void;
}

export function useMapClick({
  state,
  onUpdateStart,
  onUpdateEnd,
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

      // Both exist: ignore clicks so panning works freely.
      // User must clear points to start a new route.
      return;
    },
    [state.start.coords, state.end.coords, onUpdateStart, onUpdateEnd, suppressNextClick, onClearSuppress]
  );

  return { handleMapClick };
}
