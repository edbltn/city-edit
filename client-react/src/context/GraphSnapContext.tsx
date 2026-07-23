import { createContext, useContext, useRef, useCallback, type ReactNode, type MutableRefObject } from "react";
import type { LatLng } from "../types";

type SnapFn = (lat: number, lng: number) => LatLng | null;

interface GraphSnapContextValue {
  /** Register a snap function (called by GraphLayer) */
  setSnapFn: (fn: SnapFn) => void;
  /** Snap a lat/lng to the nearest graph node/edge (on-demand, for dragend) */
  snapToGraph: (lat: number, lng: number) => LatLng | null;
  /** Current snap position — updated by GraphLayer on every mousemove.
   *  Read this during drag for zero-overhead snapping. */
  currentSnapRef: MutableRefObject<LatLng | null>;
  /** Update the current snap position (called by GraphLayer) */
  setCurrentSnap: (pos: LatLng | null) => void;
  /** Whether a marker or path drag is in progress — GraphLayer skips hover when true */
  isDraggingRef: MutableRefObject<boolean>;
  setDragging: (v: boolean) => void;
}

const GraphSnapContext = createContext<GraphSnapContextValue | null>(null);

export function GraphSnapProvider({ children }: { children: ReactNode }) {
  const snapFnRef = useRef<SnapFn | null>(null);
  const currentSnapRef = useRef<LatLng | null>(null);
  const isDraggingRef = useRef(false);

  const setSnapFn = useCallback((fn: SnapFn) => {
    snapFnRef.current = fn;
  }, []);

  const snapToGraph = useCallback((lat: number, lng: number): LatLng | null => {
    return snapFnRef.current?.(lat, lng) ?? null;
  }, []);

  const setCurrentSnap = useCallback((pos: LatLng | null) => {
    currentSnapRef.current = pos;
  }, []);

  const setDragging = useCallback((v: boolean) => {
    isDraggingRef.current = v;
  }, []);

  return (
    <GraphSnapContext.Provider value={{ setSnapFn, snapToGraph, currentSnapRef, setCurrentSnap, isDraggingRef, setDragging }}>
      {children}
    </GraphSnapContext.Provider>
  );
}

export function useGraphSnap(): GraphSnapContextValue {
  const ctx = useContext(GraphSnapContext);
  if (!ctx) throw new Error("useGraphSnap must be used within GraphSnapProvider");
  return ctx;
}
