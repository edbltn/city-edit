import { createContext, useContext, useState, useCallback, useMemo, type ReactNode } from "react";

interface HeatmapContextValue {
  /** True until the heatmap (graph votes) has painted for the first time. */
  isHeatmapLoading: boolean;
  /** Called by GraphLayer once vote data first renders. Idempotent. */
  setHeatmapLoaded: () => void;
}

const HeatmapContext = createContext<HeatmapContextValue | null>(null);

/** Tracks whether the vote heatmap has finished its initial load, so the
 *  legend can show a loading indicator in place of the "Votes" swatch. */
export function HeatmapProvider({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState(false);

  const setHeatmapLoaded = useCallback(() => setLoaded(true), []);

  const value = useMemo(
    () => ({ isHeatmapLoading: !loaded, setHeatmapLoaded }),
    [loaded, setHeatmapLoaded],
  );

  return <HeatmapContext.Provider value={value}>{children}</HeatmapContext.Provider>;
}

export function useHeatmap(): HeatmapContextValue {
  const ctx = useContext(HeatmapContext);
  if (!ctx) throw new Error("useHeatmap must be used within a HeatmapProvider");
  return ctx;
}
