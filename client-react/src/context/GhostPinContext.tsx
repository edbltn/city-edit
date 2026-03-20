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
}

interface GhostPinContextType {
  ghostState: GhostDragState;
  startDrag: (position: ScreenPosition, snapped?: LatLng | null) => void;
  updateDrag: (position: ScreenPosition, snapped?: LatLng | null) => void;
  endDrag: () => void;
  cancelDrag: () => void;
}

const initialState: GhostDragState = {
  isDragging: false,
  screenPosition: null,
  snappedLatLng: null,
};

const GhostPinContext = createContext<GhostPinContextType | null>(null);

export function GhostPinProvider({ children }: { children: ReactNode }) {
  const [ghostState, setGhostState] = useState<GhostDragState>(initialState);

  const startDrag = useCallback((position: ScreenPosition, snapped: LatLng | null = null) => {
    setGhostState({
      isDragging: true,
      screenPosition: position,
      snappedLatLng: snapped,
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
