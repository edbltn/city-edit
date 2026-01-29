import {
  createContext,
  useContext,
  useState,
  useCallback,
  useMemo,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useRouteCalculation } from "../hooks/useRouteCalculation";
import { CONFIG } from "../config";
import type {
  TransportMode,
  LatLng,
  RoutePoint,
  RouteData,
  DesirePathData,
  RouteGeometry,
  EditVertex,
  SplitDesirePath,
} from "../types";

// ── Vertex extraction ────────────────────────────────────────────────
// Extract sparse vertices from a route geometry for draggable handles.
// Uses angle-based detection to pick major turns, with a minimum spacing.

function angleBetween(a: [number, number], b: [number, number], c: [number, number]): number {
  const dx1 = b[0] - a[0];
  const dy1 = b[1] - a[1];
  const dx2 = c[0] - b[0];
  const dy2 = c[1] - b[1];
  const dot = dx1 * dx2 + dy1 * dy2;
  const cross = dx1 * dy2 - dy1 * dx2;
  return Math.abs(Math.atan2(cross, dot));
}

function extractVertices(geometry: RouteGeometry, targetCount = 5): EditVertex[] {
  const coords = geometry.coordinates;
  if (coords.length <= 2) {
    return coords.map((c, i) => ({
      position: { lat: c[1], lng: c[0] },
      coordIndex: i,
    }));
  }

  // Always include first and last
  const vertices: EditVertex[] = [
    { position: { lat: coords[0][1], lng: coords[0][0] }, coordIndex: 0 },
  ];

  // Calculate angles at each interior point
  const angles: { index: number; angle: number }[] = [];
  for (let i = 1; i < coords.length - 1; i++) {
    angles.push({ index: i, angle: angleBetween(coords[i - 1], coords[i], coords[i + 1]) });
  }

  // Sort by sharpest turns first
  angles.sort((a, b) => b.angle - a.angle);

  // Minimum spacing: don't place handles closer than this many coords apart
  const minSpacing = Math.max(3, Math.floor(coords.length / (targetCount * 2)));

  const selected = new Set<number>();
  selected.add(0);
  selected.add(coords.length - 1);

  for (const { index } of angles) {
    if (selected.size >= targetCount - 1) break; // -1 because we add last at end

    // Check minimum spacing from all already-selected
    let tooClose = false;
    for (const s of selected) {
      if (Math.abs(index - s) < minSpacing) {
        tooClose = true;
        break;
      }
    }
    if (!tooClose) {
      selected.add(index);
    }
  }

  // If we still have room, fill in evenly spaced vertices
  if (selected.size < targetCount - 1) {
    const step = Math.floor(coords.length / (targetCount - selected.size + 1));
    for (let i = step; i < coords.length - 1; i += step) {
      if (selected.size >= targetCount - 1) break;
      let tooClose = false;
      for (const s of selected) {
        if (Math.abs(i - s) < minSpacing) {
          tooClose = true;
          break;
        }
      }
      if (!tooClose) selected.add(i);
    }
  }

  // Build sorted vertex list (excluding first which is already added)
  const sortedIndices = Array.from(selected).sort((a, b) => a - b);
  for (const idx of sortedIndices) {
    if (idx === 0) continue;
    vertices.push({
      position: { lat: coords[idx][1], lng: coords[idx][0] },
      coordIndex: idx,
    });
  }

  return vertices;
}

// ── Context interface ────────────────────────────────────────────────

interface RouteContextValue {
  start: RoutePoint;
  end: RoutePoint;
  mode: TransportMode;
  waypoints: LatLng[];
  isCalculating: boolean;
  routeData: RouteData | null;
  desirePathData: DesirePathData | null;
  error: string | null;
  hasVoted: boolean;
  isVoting: boolean;
  // Vertex editing
  editVertices: EditVertex[];
  originalRouteGeometry: RouteGeometry | null;
  editedSegments: SplitDesirePath[];
  modifiedSegmentIndices: Set<number>;
  isCalculatingSplit: boolean;
  // Legacy (kept for compatibility)
  ghostWaypoints: LatLng[];
  splitDesirePaths: SplitDesirePath[];
  suppressNextClick: () => boolean;
  setStartPoint: (coords: LatLng, fromDrag?: boolean) => void;
  setEndPoint: (coords: LatLng, fromDrag?: boolean) => void;
  setMode: (mode: TransportMode) => void;
  clearPoints: () => void;
  clearError: () => void;
  addWaypoint: (coords: LatLng) => void;
  updateWaypoint: (index: number, coords: LatLng) => void;
  clearWaypoints: () => void;
  castVote: () => Promise<void>;
  dragVertex: (vertexIndex: number, position: LatLng) => Promise<void>;
  insertVertexOnLine: (afterVertexIndex: number, position: LatLng) => Promise<void>;
  clearSplitPaths: () => void;
  clearSuppressClick: () => void;
  setSuppressClick: () => void;
}

const RouteContext = createContext<RouteContextValue | null>(null);

export function RouteProvider({ children }: { children: ReactNode }) {
  const [start, setStart] = useState<RoutePoint>({
    coords: null,
    timestamp: null,
  });
  const [end, setEnd] = useState<RoutePoint>({ coords: null, timestamp: null });
  const [mode, setModeState] = useState<TransportMode>("bike");
  const [waypoints, setWaypoints] = useState<LatLng[]>([]);
  const [hasVoted, setHasVoted] = useState(false);
  const [isVoting, setIsVoting] = useState(false);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);

  // Vertex editing state
  const [editVertices, setEditVertices] = useState<EditVertex[]>([]);
  const [originalRouteGeometry, setOriginalRouteGeometry] = useState<RouteGeometry | null>(null);
  const [editedSegments, setEditedSegments] = useState<SplitDesirePath[]>([]);
  const [modifiedSegmentIndices, setModifiedSegmentIndices] = useState<Set<number>>(new Set());

  // Legacy state kept for compatibility
  const [ghostWaypoints] = useState<LatLng[]>([]);
  const [splitDesirePaths] = useState<SplitDesirePath[]>([]);

  // Click suppression ref
  const suppressNextClickRef = useRef(false);

  // Refs for tracking
  const prevStartCoordsRef = useRef<LatLng | null>(null);
  const prevEndCoordsRef = useRef<LatLng | null>(null);
  const fromDragRef = useRef(false);
  const editVerticesRef = useRef<EditVertex[]>([]);

  useEffect(() => {
    editVerticesRef.current = editVertices;
  }, [editVertices]);

  const {
    isCalculating,
    error,
    routeData,
    desirePathData,
    desirePathSegments,
    voteMode,
    calculateRoute,
    clearRoute,
    clearError,
  } = useRouteCalculation();

  // Extract vertices when route data changes
  useEffect(() => {
    if (routeData?.geometry) {
      // Use the desire path geometry for bike/drive, route geometry for walk
      const geom = mode === "walk" ? routeData.geometry : (desirePathData?.geometry || routeData.geometry);
      const vertices = extractVertices(geom);
      setEditVertices(vertices);
    } else {
      setEditVertices([]);
    }
  }, [routeData, desirePathData, mode]);

  const setStartPoint = useCallback((coords: LatLng, fromDrag = false) => {
    fromDragRef.current = fromDrag;
    setStart({ coords, timestamp: Date.now() });
  }, []);

  const setEndPoint = useCallback((coords: LatLng, fromDrag = false) => {
    fromDragRef.current = fromDrag;
    setEnd({ coords, timestamp: Date.now() });
  }, []);

  const setMode = useCallback((newMode: TransportMode) => {
    setModeState(newMode);
  }, []);

  const addWaypoint = useCallback((coords: LatLng) => {
    setWaypoints((prev) => [...prev, coords]);
  }, []);

  const updateWaypoint = useCallback((index: number, coords: LatLng) => {
    setWaypoints((prev) => prev.map((wp, i) => (i === index ? coords : wp)));
  }, []);

  const clearWaypoints = useCallback(() => {
    setWaypoints([]);
  }, []);

  const clearSplitPaths = useCallback(() => {
    setEditedSegments([]);
    setModifiedSegmentIndices(new Set());
    setOriginalRouteGeometry(null);
  }, []);

  const clearSuppressClick = useCallback(() => {
    suppressNextClickRef.current = false;
  }, []);

  const setSuppressClick = useCallback(() => {
    suppressNextClickRef.current = true;
  }, []);

  const suppressNextClick = useCallback(() => {
    return suppressNextClickRef.current;
  }, []);

  // Calculate a single segment between two points via ORS
  const calculateSegment = useCallback(async (
    from: LatLng,
    to: LatLng,
    segmentIndex: number,
  ): Promise<SplitDesirePath | null> => {
    const response = await fetch(`${CONFIG.apiUrl}/routes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: [from.lat, from.lng],
        end: [to.lat, to.lng],
        mode,
        waypoints: [],
      }),
    });

    if (!response.ok) throw new Error("Failed to calculate segment");

    const data = await response.json();
    const geometry = mode === "walk" ? data.route?.geometry : data.desire_path?.geometry;
    if (!geometry) return null;

    return {
      id: `edited-${segmentIndex}`,
      segmentIndex,
      geometry,
      segments: data.desire_path_segments || [],
      isModified: true,
    };
  }, [mode]);

  // Drag an existing vertex to a new position
  const dragVertex = useCallback(async (vertexIndex: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;

    suppressNextClickRef.current = true;
    setIsCalculatingSplit(true);

    try {
      const currentVertices = editVerticesRef.current;
      if (vertexIndex < 0 || vertexIndex >= currentVertices.length) return;

      // Save original route on first edit
      const geom = mode === "walk" ? routeData?.geometry : (desirePathData?.geometry || routeData?.geometry);
      if (!originalRouteGeometry && geom) {
        setOriginalRouteGeometry(geom);
      }

      // Update the vertex position immediately (optimistic update)
      const newVertices = [...currentVertices];
      newVertices[vertexIndex] = { ...newVertices[vertexIndex], position };
      setEditVertices(newVertices);

      // Mark affected segments as modified immediately
      const affectedIndices: number[] = [];
      if (vertexIndex > 0) affectedIndices.push(vertexIndex - 1);
      if (vertexIndex < newVertices.length - 1) affectedIndices.push(vertexIndex);

      setModifiedSegmentIndices(prev => {
        const next = new Set(prev);
        for (const idx of affectedIndices) next.add(idx);
        return next;
      });

      // Recalculate the affected segments in background
      const segmentPromises: Promise<SplitDesirePath | null>[] = [];

      if (vertexIndex > 0) {
        segmentPromises.push(
          calculateSegment(newVertices[vertexIndex - 1].position, position, vertexIndex - 1)
        );
      }

      if (vertexIndex < newVertices.length - 1) {
        segmentPromises.push(
          calculateSegment(position, newVertices[vertexIndex + 1].position, vertexIndex)
        );
      }

      const results = await Promise.all(segmentPromises);

      // Update edited segments with API results
      setEditedSegments(prev => {
        const updated = [...prev];
        for (let i = 0; i < results.length; i++) {
          const result = results[i];
          if (!result) continue;
          const existingIdx = updated.findIndex(s => s.segmentIndex === affectedIndices[i]);
          if (existingIdx >= 0) {
            updated[existingIdx] = result;
          } else {
            updated.push(result);
          }
        }
        return updated;
      });

      setHasVoted(false);
    } catch (err) {
      console.error("Failed to drag vertex:", err);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, mode, routeData, desirePathData, originalRouteGeometry, calculateSegment]);

  // Insert a new vertex by dragging from the middle of a segment
  const insertVertexOnLine = useCallback(async (afterVertexIndex: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;

    suppressNextClickRef.current = true;
    setIsCalculatingSplit(true);

    try {
      const currentVertices = editVerticesRef.current;
      if (afterVertexIndex < 0 || afterVertexIndex >= currentVertices.length - 1) return;

      // Save original route on first edit
      const geom = mode === "walk" ? routeData?.geometry : (desirePathData?.geometry || routeData?.geometry);
      if (!originalRouteGeometry && geom) {
        setOriginalRouteGeometry(geom);
      }

      // Insert new vertex between afterVertexIndex and afterVertexIndex+1
      const newVertex: EditVertex = {
        position,
        coordIndex: -1, // Not from original geometry
      };

      const newVertices = [...currentVertices];
      newVertices.splice(afterVertexIndex + 1, 0, newVertex);

      // Calculate both new segments
      const segBefore = await calculateSegment(
        currentVertices[afterVertexIndex].position,
        position,
        afterVertexIndex,
      );
      const segAfter = await calculateSegment(
        position,
        currentVertices[afterVertexIndex + 1].position,
        afterVertexIndex + 1,
      );

      // Rebuild edited segments with updated indices
      setEditedSegments(prev => {
        // Shift existing segment indices that are >= afterVertexIndex + 1
        const updated = prev.map(s => {
          if (s.segmentIndex > afterVertexIndex) {
            return { ...s, segmentIndex: s.segmentIndex + 1, id: `edited-${s.segmentIndex + 1}` };
          }
          return s;
        });

        // Replace/add the two affected segments
        const beforeIdx = updated.findIndex(s => s.segmentIndex === afterVertexIndex);
        if (segBefore) {
          if (beforeIdx >= 0) updated[beforeIdx] = segBefore;
          else updated.push(segBefore);
        }
        if (segAfter) updated.push(segAfter);

        return updated;
      });

      // Track modified segments
      setModifiedSegmentIndices(prev => {
        const next = new Set<number>();
        for (const idx of prev) {
          next.add(idx > afterVertexIndex ? idx + 1 : idx);
        }
        next.add(afterVertexIndex);
        next.add(afterVertexIndex + 1);
        return next;
      });

      setEditVertices(newVertices);
      setHasVoted(false);
    } catch (err) {
      console.error("Failed to insert vertex:", err);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, mode, routeData, desirePathData, originalRouteGeometry, calculateSegment]);

  const clearPoints = useCallback(() => {
    setStart({ coords: null, timestamp: null });
    setEnd({ coords: null, timestamp: null });
    setWaypoints([]);
    setHasVoted(false);
    setEditVertices([]);
    setEditedSegments([]);
    setModifiedSegmentIndices(new Set());
    setOriginalRouteGeometry(null);
    clearRoute();
  }, [clearRoute]);

  const castVote = useCallback(async () => {
    // Use edited segments if available, otherwise use desire path segments
    const segmentsToVote = editedSegments.length > 0
      ? editedSegments.flatMap(sp => sp.segments)
      : desirePathSegments;

    if (!segmentsToVote || segmentsToVote.length === 0 || !voteMode) {
      return;
    }

    setIsVoting(true);
    try {
      const response = await fetch(`${CONFIG.apiUrl}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          segments: segmentsToVote,
          mode: voteMode,
        }),
      });

      if (!response.ok) {
        throw new Error(`Vote failed: ${response.statusText}`);
      }

      setHasVoted(true);
    } catch (err) {
      console.error("Failed to cast vote:", err);
    } finally {
      setIsVoting(false);
    }
  }, [editedSegments, desirePathSegments, voteMode]);

  // Auto-calculate route when both points are set or mode changes
  useEffect(() => {
    const isDrag = fromDragRef.current;
    fromDragRef.current = false;

    const prevStart = prevStartCoordsRef.current;
    const prevEnd = prevEndCoordsRef.current;
    const startChanged = start.coords !== prevStart &&
      (start.coords === null || prevStart === null ||
       start.coords.lat !== prevStart.lat || start.coords.lng !== prevStart.lng);
    const endChanged = end.coords !== prevEnd &&
      (end.coords === null || prevEnd === null ||
       end.coords.lat !== prevEnd.lat || end.coords.lng !== prevEnd.lng);

    prevStartCoordsRef.current = start.coords;
    prevEndCoordsRef.current = end.coords;

    const shouldPreserveEdits = isDrag || (!startChanged && !endChanged);

    clearRoute();
    setWaypoints([]);
    setHasVoted(false);

    if (!shouldPreserveEdits) {
      setEditedSegments([]);
      setModifiedSegmentIndices(new Set());
      setOriginalRouteGeometry(null);
    }

    if (start.coords && end.coords) {
      calculateRoute({ start: start.coords, end: end.coords, mode, waypoints: [] });
    }
  }, [start.coords, end.coords, mode, calculateRoute, clearRoute]);

  // Recalculate route when waypoints change
  useEffect(() => {
    if (start.coords && end.coords && waypoints.length > 0) {
      setHasVoted(false);
      calculateRoute({ start: start.coords, end: end.coords, mode, waypoints });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waypoints]);

  const value = useMemo(
    () => ({
      start,
      end,
      mode,
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      editVertices,
      originalRouteGeometry,
      editedSegments,
      modifiedSegmentIndices,
      isCalculatingSplit,
      ghostWaypoints,
      splitDesirePaths,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setMode,
      clearPoints,
      clearError,
      addWaypoint,
      updateWaypoint,
      clearWaypoints,
      castVote,
      dragVertex,
      insertVertexOnLine,
      clearSplitPaths,
      clearSuppressClick,
      setSuppressClick,
    }),
    [
      start,
      end,
      mode,
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      editVertices,
      originalRouteGeometry,
      editedSegments,
      modifiedSegmentIndices,
      isCalculatingSplit,
      ghostWaypoints,
      splitDesirePaths,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setMode,
      clearPoints,
      clearError,
      addWaypoint,
      updateWaypoint,
      clearWaypoints,
      castVote,
      dragVertex,
      insertVertexOnLine,
      clearSplitPaths,
      clearSuppressClick,
      setSuppressClick,
    ]
  );

  return (
    <RouteContext.Provider value={value}>{children}</RouteContext.Provider>
  );
}

export function useRoute(): RouteContextValue {
  const context = useContext(RouteContext);
  if (!context) {
    throw new Error("useRoute must be used within a RouteProvider");
  }
  return context;
}
