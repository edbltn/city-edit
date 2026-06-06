import { createContext, useContext, useRef, useCallback, type ReactNode, type MutableRefObject } from "react";
import type { LatLng } from "../types";

type SnapFn = (map: L.Map, lat: number, lng: number) => LatLng | null;
type ResolveEdgeFn = (lat: number, lng: number) => number | null;
type ResolveTopLabelFn = (edgeIds: number[]) => string | null;

interface GraphSnapContextValue {
  /** Register a snap function (called by GraphLayer) */
  setSnapFn: (fn: SnapFn) => void;
  /** Snap a lat/lng to the nearest graph node/edge (on-demand, for dragend) */
  snapToGraph: (map: L.Map, lat: number, lng: number) => LatLng | null;
  /** Register the point→vote-edge resolver (called by GraphLayer) */
  setResolveVoteEdgeId: (fn: ResolveEdgeFn) => void;
  /** Resolve a lat/lng to the edge id a vote would land on (same path as hover/click). */
  resolveVoteEdgeId: (lat: number, lng: number) => number | null;
  /** Register the path→top-vote-type resolver (called by GraphLayer). */
  setResolveTopLabelForPath: (fn: ResolveTopLabelFn) => void;
  /** The highest net-voted vote-type label across a set of path edges, or null —
   *  used to pick a sensible default vote type when a deep link's requested type
   *  isn't valid for the map. Reads live vote data held by GraphLayer. */
  resolveTopLabelForPath: (edgeIds: number[]) => string | null;
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
  const resolveEdgeFnRef = useRef<ResolveEdgeFn | null>(null);
  const resolveTopLabelFnRef = useRef<ResolveTopLabelFn | null>(null);
  const currentSnapRef = useRef<LatLng | null>(null);
  const isDraggingRef = useRef(false);

  const setSnapFn = useCallback((fn: SnapFn) => {
    snapFnRef.current = fn;
  }, []);

  const snapToGraph = useCallback((map: L.Map, lat: number, lng: number): LatLng | null => {
    return snapFnRef.current?.(map, lat, lng) ?? null;
  }, []);

  const setResolveVoteEdgeId = useCallback((fn: ResolveEdgeFn) => {
    resolveEdgeFnRef.current = fn;
  }, []);

  const resolveVoteEdgeId = useCallback((lat: number, lng: number): number | null => {
    return resolveEdgeFnRef.current?.(lat, lng) ?? null;
  }, []);

  const setResolveTopLabelForPath = useCallback((fn: ResolveTopLabelFn) => {
    resolveTopLabelFnRef.current = fn;
  }, []);

  const resolveTopLabelForPath = useCallback((edgeIds: number[]): string | null => {
    return resolveTopLabelFnRef.current?.(edgeIds) ?? null;
  }, []);

  const setCurrentSnap = useCallback((pos: LatLng | null) => {
    currentSnapRef.current = pos;
  }, []);

  const setDragging = useCallback((v: boolean) => {
    isDraggingRef.current = v;
  }, []);

  return (
    <GraphSnapContext.Provider value={{ setSnapFn, snapToGraph, setResolveVoteEdgeId, resolveVoteEdgeId, setResolveTopLabelForPath, resolveTopLabelForPath, currentSnapRef, setCurrentSnap, isDraggingRef, setDragging }}>
      {children}
    </GraphSnapContext.Provider>
  );
}

export function useGraphSnap(): GraphSnapContextValue {
  const ctx = useContext(GraphSnapContext);
  if (!ctx) throw new Error("useGraphSnap must be used within GraphSnapProvider");
  return ctx;
}
