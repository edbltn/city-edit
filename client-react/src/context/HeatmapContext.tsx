import { createContext, useContext, useState, useCallback, useMemo, type ReactNode } from "react";

interface HeatmapContextValue {
  /** True until the heatmap (graph votes) has painted for the first time. */
  isHeatmapLoading: boolean;
  /** Called by GraphLayer once vote data first renders. Idempotent. */
  setHeatmapLoaded: () => void;
  /** True once the base map has first painted (or the raster fallback). */
  isMapReady: boolean;
  /** Called by the map background once it has loaded. Idempotent. */
  setMapReady: () => void;
  /** True until BOTH the base map and the vote heatmap have first painted.
   *  Drives the full-screen loader, so the map is only revealed once everything
   *  is there (on mobile the base map and votes each take a while to load). */
  isInitialLoading: boolean;
}

const HeatmapContext = createContext<HeatmapContextValue | null>(null);

/** Tracks the app's initial load: whether the base map and the vote heatmap
 *  have finished their first paint. The legend swaps the "Votes" swatch for a
 *  spinner while votes load, and the full-screen loader stays up until both are
 *  ready. */
export function HeatmapProvider({ children }: { children: ReactNode }) {
  const [heatmapLoaded, setHeatmapLoadedState] = useState(false);
  const [mapReady, setMapReadyState] = useState(false);

  const setHeatmapLoaded = useCallback(() => setHeatmapLoadedState(true), []);
  const setMapReady = useCallback(() => setMapReadyState(true), []);

  const value = useMemo(
    () => ({
      isHeatmapLoading: !heatmapLoaded,
      setHeatmapLoaded,
      isMapReady: mapReady,
      setMapReady,
      isInitialLoading: !heatmapLoaded || !mapReady,
    }),
    [heatmapLoaded, mapReady, setHeatmapLoaded, setMapReady],
  );

  return <HeatmapContext.Provider value={value}>{children}</HeatmapContext.Provider>;
}

export function useHeatmap(): HeatmapContextValue {
  const ctx = useContext(HeatmapContext);
  if (!ctx) throw new Error("useHeatmap must be used within a HeatmapProvider");
  return ctx;
}
