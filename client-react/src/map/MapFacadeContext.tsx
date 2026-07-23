import { createContext, useContext, type ReactNode } from "react";
import type { MapFacade } from "./facade";

const MapFacadeContext = createContext<MapFacade | null>(null);

/** Provides the live MapFacade to the interactive map subtree. MapView only
 *  renders that subtree once the facade exists, so consumers can rely on it. */
export function MapFacadeProvider({ facade, children }: { facade: MapFacade; children: ReactNode }) {
  return <MapFacadeContext.Provider value={facade}>{children}</MapFacadeContext.Provider>;
}

export function useMapFacade(): MapFacade {
  const facade = useContext(MapFacadeContext);
  if (!facade) throw new Error("useMapFacade must be used within MapFacadeProvider");
  return facade;
}
