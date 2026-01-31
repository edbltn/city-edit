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
import { getDefaultVoteType } from "../constants/voteTypes";
import type {
  TransportMode,
  LatLng,
  RoutePoint,
  RouteData,
  DesirePathData,
  SplitDesirePath,
} from "../types";

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
  ghostWaypoints: LatLng[];
  splitDesirePaths: SplitDesirePath[];
  isCalculatingSplit: boolean;
  voteType: string;
  pointType: "route" | "point";
  suppressNextClick: () => boolean;
  setStartPoint: (coords: LatLng) => void;
  setEndPoint: (coords: LatLng) => void;
  setMode: (mode: TransportMode) => void;
  clearPoints: () => void;
  clearStart: () => void;
  clearEnd: () => void;
  clearError: () => void;
  setError: (message: string) => void;
  addWaypoint: (coords: LatLng) => void;
  updateWaypoint: (index: number, coords: LatLng) => void;
  clearWaypoints: () => void;
  castVote: () => Promise<void>;
  insertWaypointAtSegment: (segmentIndex: number, position: LatLng) => Promise<void>;
  updateGhostWaypoint: (index: number, position: LatLng) => Promise<void>;
  removeGhostWaypoint: (index: number) => void;
  clearSplitPaths: () => void;
  clearSuppressClick: () => void;
  setSuppressClick: () => void;
  setVoteType: (voteType: string) => void;
}

const RouteContext = createContext<RouteContextValue | null>(null);

export function RouteProvider({ children }: { children: ReactNode }) {
  // Core state
  const [start, setStart] = useState<RoutePoint>({ coords: null, timestamp: null });
  const [end, setEnd] = useState<RoutePoint>({ coords: null, timestamp: null });
  const [mode, setModeState] = useState<TransportMode>("bike");
  const [waypoints, setWaypoints] = useState<LatLng[]>([]);
  const [ghostWaypoints, setGhostWaypoints] = useState<LatLng[]>([]);
  const [splitDesirePaths, setSplitDesirePaths] = useState<SplitDesirePath[]>([]);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);
  const [hasVoted, setHasVoted] = useState(false);
  const [isVoting, setIsVoting] = useState(false);
  const [voteType, setVoteTypeState] = useState<string>(() => getDefaultVoteType("bike", "route"));

  // Ref for click suppression (needs immediate effect, not async like state)
  const suppressNextClickRef = useRef(false);

  // Ref to track ghost waypoints for reading in effects without triggering re-runs
  const ghostWaypointsRef = useRef<LatLng[]>([]);

  // Ref to track if we're handling point removal (skip main effect)
  const handlingRemovalRef = useRef(false);

  // Route version counter - increments when path changes, used to detect stale votes
  const routeVersionRef = useRef(0);

  // Keep ghostWaypointsRef in sync
  useEffect(() => {
    ghostWaypointsRef.current = ghostWaypoints;
  }, [ghostWaypoints]);

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
    setError,
  } = useRouteCalculation();

  // Compute point type based on whether both points are set
  const pointType: "route" | "point" = start.coords && end.coords ? "route" : "point";

  // ============================================
  // Helper: Calculate all route segments for split paths
  // ============================================
  const calculateAllSegments = useCallback(async (points: LatLng[]): Promise<SplitDesirePath[]> => {
    if (points.length < 2) return [];

    const fetchPromises = [];
    for (let i = 0; i < points.length - 1; i++) {
      fetchPromises.push(
        fetch(`${CONFIG.apiUrl}/routes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: [points[i].lat, points[i].lng],
            end: [points[i + 1].lat, points[i + 1].lng],
            mode,
            waypoints: []
          })
        })
      );
    }

    const responses = await Promise.all(fetchPromises);
    for (const response of responses) {
      if (!response.ok) throw new Error("Failed to calculate split paths");
    }

    const allData = await Promise.all(responses.map(r => r.json()));
    const splitPaths: SplitDesirePath[] = [];

    for (let i = 0; i < allData.length; i++) {
      const data = allData[i];
      const geometry = mode === "walk" ? data.route?.geometry : data.desire_path?.geometry;
      if (geometry) {
        splitPaths.push({
          id: `split-${i}`,
          segmentIndex: i,
          geometry,
          segments: data.desire_path_segments || []
        });
      }
    }

    return splitPaths;
  }, [mode]);

  // ============================================
  // Simple setters
  // ============================================
  const setStartPoint = useCallback((coords: LatLng) => {
    setStart({ coords, timestamp: Date.now() });
  }, []);

  const setEndPoint = useCallback((coords: LatLng) => {
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
    setGhostWaypoints([]);
    setSplitDesirePaths([]);
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

  const setVoteType = useCallback((newVoteType: string) => {
    setVoteTypeState(newVoteType);
    // Allow re-voting with a different suggestion
    setHasVoted(false);
  }, []);

  // ============================================
  // Clear all points
  // ============================================
  const clearPoints = useCallback(() => {
    setStart({ coords: null, timestamp: null });
    setEnd({ coords: null, timestamp: null });
    setWaypoints([]);
    setGhostWaypoints([]);
    setSplitDesirePaths([]);
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);
    clearRoute();
  }, [clearRoute]);

  // ============================================
  // Insert ghost waypoint at segment (drag on path)
  // ============================================
  const insertWaypointAtSegment = useCallback(async (segmentIndex: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;

    suppressNextClickRef.current = true;

    // Insert waypoint immediately so it appears right away
    const newWaypoints = [...ghostWaypoints];
    newWaypoints.splice(segmentIndex, 0, position);
    setGhostWaypoints(newWaypoints);

    // Path changed - reset vote state immediately
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);

    // Clear paths immediately - they'll reappear when calculation completes
    setSplitDesirePaths([]);
    setIsCalculatingSplit(true);

    try {
      const allPoints = [start.coords, ...newWaypoints, end.coords];
      const splitPaths = await calculateAllSegments(allPoints);

      if (splitPaths.length === newWaypoints.length + 1) {
        setSplitDesirePaths(splitPaths);
      }
    } catch (err) {
      console.error("Failed to calculate split paths:", err);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, ghostWaypoints, calculateAllSegments]);

  // ============================================
  // Update ghost waypoint position (drag existing waypoint)
  // ============================================
  const updateGhostWaypoint = useCallback(async (index: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;
    if (index < 0 || index >= ghostWaypoints.length) return;

    suppressNextClickRef.current = true;

    // Update ghost waypoint position immediately so marker doesn't snap back
    const newWaypoints = [...ghostWaypoints];
    newWaypoints[index] = position;
    setGhostWaypoints(newWaypoints);

    // Path changed - reset vote state immediately
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);

    // Clear paths immediately - they'll reappear when calculation completes
    setSplitDesirePaths([]);
    setIsCalculatingSplit(true);

    try {
      const allPoints = [start.coords, ...newWaypoints, end.coords];
      const splitPaths = await calculateAllSegments(allPoints);

      if (splitPaths.length === newWaypoints.length + 1) {
        setSplitDesirePaths(splitPaths);
      }
    } catch (err) {
      console.error("Failed to recalculate split paths:", err);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, ghostWaypoints, calculateAllSegments]);

  // ============================================
  // Remove any point (unified logic)
  // All points conceptually: [start, ...ghostWaypoints, end]
  // After removal: reassign based on count
  // ============================================
  const removePoint = useCallback((which: "start" | "end" | number) => {
    // Build ordered point list
    const allPoints: LatLng[] = [];
    if (start.coords) allPoints.push(start.coords);
    allPoints.push(...ghostWaypoints);
    if (end.coords) allPoints.push(end.coords);

    // Determine index to remove
    let removeIndex: number;
    if (which === "start") {
      removeIndex = 0;
    } else if (which === "end") {
      removeIndex = allPoints.length - 1;
    } else {
      // Ghost waypoint index: offset by 1 if start exists
      removeIndex = start.coords ? which + 1 : which;
    }

    const remaining = allPoints.filter((_, i) => i !== removeIndex);

    // Mark that we're handling removal (skip main effect)
    handlingRemovalRef.current = true;
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);

    // Clear paths immediately - they'll reappear when calculation completes
    clearRoute();
    setSplitDesirePaths([]);

    if (remaining.length === 0) {
      // No points left
      setStart({ coords: null, timestamp: null });
      setEnd({ coords: null, timestamp: null });
      setGhostWaypoints([]);
    } else if (remaining.length === 1) {
      // One point left → just start, no route
      setStart({ coords: remaining[0], timestamp: Date.now() });
      setEnd({ coords: null, timestamp: null });
      setGhostWaypoints([]);
    } else {
      // Two or more: first=start, last=end, middle=ghostWaypoints
      const newStart = remaining[0];
      const newEnd = remaining[remaining.length - 1];
      const newGhostWaypoints = remaining.slice(1, -1);

      // Only update start/end if coordinates actually changed
      // This prevents triggering the main effect unnecessarily when removing a ghost waypoint
      const startChanged = !start.coords ||
        start.coords.lat !== newStart.lat ||
        start.coords.lng !== newStart.lng;
      const endChanged = !end.coords ||
        end.coords.lat !== newEnd.lat ||
        end.coords.lng !== newEnd.lng;

      if (startChanged) {
        setStart({ coords: newStart, timestamp: Date.now() });
      }
      if (endChanged) {
        setEnd({ coords: newEnd, timestamp: Date.now() });
      }
      setGhostWaypoints(newGhostWaypoints);

      // Recalculate based on new structure
      if (newGhostWaypoints.length > 0) {
        // Has ghost waypoints: calculate split paths
        setIsCalculatingSplit(true);
        calculateAllSegments(remaining)
          .then(splitPaths => {
            setSplitDesirePaths(splitPaths);
          })
          .catch(console.error)
          .finally(() => setIsCalculatingSplit(false));
      } else {
        // Just start and end: calculate main route
        calculateRoute({ start: newStart, end: newEnd, mode, waypoints: [] });
      }
    }
  }, [start.coords, end.coords, ghostWaypoints, mode, calculateAllSegments, calculateRoute, clearRoute]);

  // Convenience wrappers
  const clearStart = useCallback(() => removePoint("start"), [removePoint]);
  const clearEnd = useCallback(() => removePoint("end"), [removePoint]);
  const removeGhostWaypoint = useCallback((index: number) => removePoint(index), [removePoint]);

  // ============================================
  // Cast vote
  // ============================================
  const castVote = useCallback(async () => {
    const segmentsToVote = splitDesirePaths.length > 0
      ? splitDesirePaths.flatMap(sp => sp.segments)
      : desirePathSegments;

    const isPointVote = start.coords && !end.coords;

    // For point votes, use mode directly (no route calculation needed)
    // For split paths, use mode directly; otherwise need voteMode from route calculation
    const effectiveVoteMode = isPointVote
      ? mode
      : (splitDesirePaths.length > 0 ? mode : voteMode);

    if (!isPointVote && (!segmentsToVote || segmentsToVote.length === 0 || !effectiveVoteMode)) {
      console.warn("castVote: No segments to vote on", { segmentsToVote, effectiveVoteMode });
      return;
    }
    if (isPointVote && !start.coords) {
      return;
    }

    // Capture route version at vote start
    const voteRouteVersion = routeVersionRef.current;

    setIsVoting(true);
    try {
      const body: Record<string, unknown> = {
        mode: effectiveVoteMode,
        vote_type: voteType,
      };

      if (isPointVote && start.coords) {
        body.point = [start.coords.lat, start.coords.lng];
      } else {
        body.segments = segmentsToVote;
      }

      const response = await fetch(`${CONFIG.apiUrl}/vote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!response.ok) {
        throw new Error(`Vote failed: ${response.statusText}`);
      }

      // Only mark as voted if route hasn't changed during the vote
      if (routeVersionRef.current === voteRouteVersion) {
        setHasVoted(true);
      }
    } catch (err) {
      console.error("Failed to cast vote:", err);
    } finally {
      setIsVoting(false);
    }
  }, [splitDesirePaths, desirePathSegments, voteMode, voteType, start.coords, end.coords, mode]);

  // ============================================
  // Main calculation effect
  // Runs when start, end, or mode changes
  // Uses current ghost waypoints to determine what to calculate
  // ============================================
  useEffect(() => {
    // Skip if removal is being handled (it does its own calculation)
    if (handlingRemovalRef.current) {
      handlingRemovalRef.current = false;
      return;
    }

    // Reset vote status on any change
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);
    setWaypoints([]);

    // Need both start and end to calculate
    if (!start.coords || !end.coords) {
      // Clear everything - can't have a route or ghost waypoints without both endpoints
      clearRoute();
      setGhostWaypoints([]);
      setSplitDesirePaths([]);
      return;
    }

    // Clear existing paths immediately - they'll reappear when calculation completes
    clearRoute();
    setSplitDesirePaths([]);

    const currentGhostWaypoints = ghostWaypointsRef.current;

    // Always calculate the main route
    calculateRoute({ start: start.coords, end: end.coords, mode, waypoints: [] });

    // If there are ghost waypoints, also calculate split paths
    if (currentGhostWaypoints.length > 0) {
      setIsCalculatingSplit(true);
      const allPoints = [start.coords, ...currentGhostWaypoints, end.coords];
      calculateAllSegments(allPoints)
        .then(splitPaths => {
          if (splitPaths.length === currentGhostWaypoints.length + 1) {
            setSplitDesirePaths(splitPaths);
          }
        })
        .catch(console.error)
        .finally(() => setIsCalculatingSplit(false));
    }
  }, [start, end, mode, calculateRoute, clearRoute, calculateAllSegments]);

  // ============================================
  // Recalculate when waypoints change
  // ============================================
  useEffect(() => {
    if (start.coords && end.coords && waypoints.length > 0) {
      routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);
      calculateRoute({ start: start.coords, end: end.coords, mode, waypoints });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waypoints]);

  // ============================================
  // Auto-update vote type when mode or pointType changes
  // ============================================
  useEffect(() => {
    setVoteTypeState(getDefaultVoteType(mode, pointType));
  }, [mode, pointType]);

  // ============================================
  // Context value
  // ============================================
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
      ghostWaypoints,
      splitDesirePaths,
      isCalculatingSplit,
      voteType,
      pointType,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setMode,
      clearPoints,
      clearStart,
      clearEnd,
      clearError,
      setError,
      addWaypoint,
      updateWaypoint,
      clearWaypoints,
      castVote,
      insertWaypointAtSegment,
      updateGhostWaypoint,
      removeGhostWaypoint,
      clearSplitPaths,
      clearSuppressClick,
      setSuppressClick,
      setVoteType,
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
      ghostWaypoints,
      splitDesirePaths,
      isCalculatingSplit,
      voteType,
      pointType,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setMode,
      clearPoints,
      clearStart,
      clearEnd,
      clearError,
      setError,
      addWaypoint,
      updateWaypoint,
      clearWaypoints,
      castVote,
      insertWaypointAtSegment,
      updateGhostWaypoint,
      removeGhostWaypoint,
      clearSplitPaths,
      clearSuppressClick,
      setSuppressClick,
      setVoteType,
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
