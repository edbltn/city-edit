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
import { reverseGeocode } from "../utils/reverseGeocode";
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
// Smart vertex placement at significant turns, plus line-dragging for new vertices.

// Haversine distance in meters
function haversineDistance(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000; // Earth radius in meters
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLng / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Calculate angle (in degrees) between two segments at point B
// Returns 0-180, where 0 = straight line, 180 = U-turn
function angleBetween(
  a: [number, number],
  b: [number, number],
  c: [number, number]
): number {
  const v1 = [b[0] - a[0], b[1] - a[1]];
  const v2 = [c[0] - b[0], c[1] - b[1]];
  const dot = v1[0] * v2[0] + v1[1] * v2[1];
  const mag1 = Math.sqrt(v1[0] ** 2 + v1[1] ** 2);
  const mag2 = Math.sqrt(v2[0] ** 2 + v2[1] ** 2);
  if (mag1 === 0 || mag2 === 0) return 0;
  const cos = Math.max(-1, Math.min(1, dot / (mag1 * mag2)));
  return Math.acos(cos) * (180 / Math.PI);
}

function extractVertices(geometry: RouteGeometry): EditVertex[] {
  const coords = geometry.coordinates;
  if (coords.length === 0) return [];
  if (coords.length === 1) {
    return [{ position: { lat: coords[0][1], lng: coords[0][0] }, coordIndex: 0 }];
  }

  const MIN_SPACING = 100;   // Minimum meters between vertices
  const TURN_THRESHOLD = 40; // Degrees - consider this a significant turn
  
  const vertices: EditVertex[] = [];
  
  // Always include start
  vertices.push({ 
    position: { lat: coords[0][1], lng: coords[0][0] }, 
    coordIndex: 0 
  });
  
  let distanceSinceLastVertex = 0;
  
  for (let i = 1; i < coords.length - 1; i++) {
    const prevCoord = coords[i - 1];
    const currCoord = coords[i];
    const nextCoord = coords[i + 1];
    
    // Calculate distance from last vertex
    const segmentDist = haversineDistance(
      coords[i - 1][1], coords[i - 1][0],
      currCoord[1], currCoord[0]
    );
    distanceSinceLastVertex += segmentDist;
    
    // Calculate turn angle at this point (0 = straight, 180 = U-turn)
    const turnAngle = angleBetween(prevCoord, currCoord, nextCoord);
    
    const isSignificantTurn = turnAngle >= TURN_THRESHOLD;
    const meetsMinSpacing = distanceSinceLastVertex >= MIN_SPACING;
    
    // Add vertex only at significant turns (with min spacing)
    if (isSignificantTurn && meetsMinSpacing) {
      vertices.push({
        position: { lat: currCoord[1], lng: currCoord[0] },
        coordIndex: i,
      });
      distanceSinceLastVertex = 0;
    }
  }
  
  // Always include end
  vertices.push({ 
    position: { lat: coords[coords.length - 1][1], lng: coords[coords.length - 1][0] }, 
    coordIndex: coords.length - 1 
  });
  
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
    address: null,
  });
  const [end, setEnd] = useState<RoutePoint>({ coords: null, timestamp: null, address: null });
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
  const hasEditsRef = useRef(false);

  useEffect(() => {
    editVerticesRef.current = editVertices;
  }, [editVertices]);

  // Track whether edits are in progress (for preventing vertex reset)
  useEffect(() => {
    hasEditsRef.current = editedSegments.length > 0 || modifiedSegmentIndices.size > 0;
  }, [editedSegments, modifiedSegmentIndices]);

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

  // Extract vertices when route data changes - but only if no edits are in progress
  useEffect(() => {
    // Don't reset vertices if there are edits in progress (would cause discontinuities)
    if (routeData?.geometry && !hasEditsRef.current) {
      // Use the desire path geometry for bike/drive, route geometry for walk
      const geom = mode === "walk" ? routeData.geometry : (desirePathData?.geometry || routeData.geometry);
      const vertices = extractVertices(geom);
      setEditVertices(vertices);
    } else if (!routeData?.geometry) {
      setEditVertices([]);
    }
  }, [routeData, desirePathData, mode]);

  const setStartPoint = useCallback((coords: LatLng, fromDrag = false) => {
    fromDragRef.current = fromDrag;
    setStart({ coords, timestamp: Date.now(), address: null });
    // Fetch address in background
    reverseGeocode(coords).then(address => {
      setStart(prev => prev.coords?.lat === coords.lat && prev.coords?.lng === coords.lng
        ? { ...prev, address }
        : prev
      );
    });
  }, []);

  const setEndPoint = useCallback((coords: LatLng, fromDrag = false) => {
    fromDragRef.current = fromDrag;
    setEnd({ coords, timestamp: Date.now(), address: null });
    // Fetch address in background
    reverseGeocode(coords).then(address => {
      setEnd(prev => prev.coords?.lat === coords.lat && prev.coords?.lng === coords.lng
        ? { ...prev, address }
        : prev
      );
    });
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
          if (!result) {
            console.warn(`[dragVertex] Segment ${affectedIndices[i]} returned null!`);
            continue;
          }
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
    setStart({ coords: null, timestamp: null, address: null });
    setEnd({ coords: null, timestamp: null, address: null });
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
