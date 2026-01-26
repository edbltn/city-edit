import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

interface ScreenPosition {
  x: number;
  y: number;
}

interface GhostDragState {
  isDragging: boolean;
  screenPosition: ScreenPosition | null;
}

interface GhostPinContextType {
  ghostState: GhostDragState;
  startDrag: (position: ScreenPosition) => void;
  updateDrag: (position: ScreenPosition) => void;
  endDrag: () => void;
  cancelDrag: () => void;
}

const initialState: GhostDragState = {
  isDragging: false,
  screenPosition: null,
};

const GhostPinContext = createContext<GhostPinContextType | null>(null);

export function GhostPinProvider({ children }: { children: ReactNode }) {
  const [ghostState, setGhostState] = useState<GhostDragState>(initialState);

  const startDrag = useCallback((position: ScreenPosition) => {
    setGhostState({
      isDragging: true,
      screenPosition: position,
    });
  }, []);

  const updateDrag = useCallback((position: ScreenPosition) => {
    setGhostState((prev) => ({
      ...prev,
      screenPosition: position,
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
