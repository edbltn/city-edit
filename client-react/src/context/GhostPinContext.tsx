import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import type { LatLng } from "../types";

interface ScreenPosition {
  x: number;
  y: number;
}

interface GhostDragState {
  isDragging: boolean;
  screenPosition: ScreenPosition | null;
  snappedLatLng: LatLng | null;
  /** Ghost kite color. Defaults (null) to the theme's selection color — used for a
   *  mid drag. A start/end proposal drag passes teal/red so the ghost matches the
   *  waypoint it's moving. */
  color: string | null;
}

interface GhostPinContextType {
  ghostState: GhostDragState;
  startDrag: (position: ScreenPosition, snapped?: LatLng | null, color?: string | null) => void;
  updateDrag: (position: ScreenPosition, snapped?: LatLng | null) => void;
  endDrag: () => void;
  cancelDrag: () => void;
}

const initialState: GhostDragState = {
  isDragging: false,
  screenPosition: null,
  snappedLatLng: null,
  color: null,
};

const GhostPinContext = createContext<GhostPinContextType | null>(null);

export function GhostPinProvider({ children }: { children: ReactNode }) {
  const [ghostState, setGhostState] = useState<GhostDragState>(initialState);

  const startDrag = useCallback((position: ScreenPosition, snapped: LatLng | null = null, color: string | null = null) => {
    setGhostState({
      isDragging: true,
      screenPosition: position,
      snappedLatLng: snapped,
      color,
    });
  }, []);

  const updateDrag = useCallback((position: ScreenPosition, snapped: LatLng | null = null) => {
    setGhostState((prev) => ({
      ...prev,
      screenPosition: position,
      snappedLatLng: snapped ?? prev.snappedLatLng,
    }));
  }, []);

  const endDrag = useCallback(() => {
    setGhostState(initialState);
  }, []);

  const cancelDrag = useCallback(() => {
    setGhostState(initialState);
  }, []);

  return (
    <GhostPinContext.Provider
      value={{ ghostState, startDrag, updateDrag, endDrag, cancelDrag }}
    >
      {children}
    </GhostPinContext.Provider>
  );
}

export function useGhostPin(): GhostPinContextType {
  const context = useContext(GhostPinContext);
  if (!context) {
    throw new Error("useGhostPin must be used within a GhostPinProvider");
  }
  return context;
}
