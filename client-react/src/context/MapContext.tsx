import { createContext, useContext, type ReactNode } from "react";
import { getCurrentMap, type MapConfig } from "../map/runtime";

const MapContext = createContext<MapConfig | null>(null);

/** Provides the resolved map config (or null in legacy single-map mode). */
export function MapProvider({ children }: { children: ReactNode }) {
  return <MapContext.Provider value={getCurrentMap()}>{children}</MapContext.Provider>;
}

/** Current map config, or null when running without a resolved map. */
export function useMap(): MapConfig | null {
  return useContext(MapContext);
}
