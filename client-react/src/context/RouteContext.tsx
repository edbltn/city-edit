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
  insertWaypointAtSegment: (segmentIndex: number, position: LatLng) => Promise<void>;
  updateGhostWaypoint: (index: number, position: LatLng) => Promise<void>;
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
  const [ghostWaypoints, setGhostWaypoints] = useState<LatLng[]>([]);
  const [splitDesirePaths, setSplitDesirePaths] = useState<SplitDesirePath[]>([]);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);

  // Use a ref for click suppression so it takes effect immediately (not async like state)
  const suppressNextClickRef = useRef(false);

  // Refs to track previous values for detecting new points vs drags
  const prevStartCoordsRef = useRef<LatLng | null>(null);
  const prevEndCoordsRef = useRef<LatLng | null>(null);
  const ghostWaypointsRef = useRef<LatLng[]>([]);
  const fromDragRef = useRef(false);

  // Keep ghostWaypointsRef in sync with state
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
  } = useRouteCalculation();

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
    setGhostWaypoints([]);
    setSplitDesirePaths([]);
  }, []);

  const clearSuppressClick = useCallback(() => {
    suppressNextClickRef.current = false;
  }, []);

  const setSuppressClick = useCallback(() => {
    suppressNextClickRef.current = true;
  }, []);

  // Getter for suppressNextClick - reads from ref for immediate effect
  const suppressNextClick = useCallback(() => {
    return suppressNextClickRef.current;
  }, []);

  // Helper to calculate all route segments given a list of points
  const calculateAllSegments = useCallback(async (points: LatLng[]): Promise<SplitDesirePath[]> => {
    if (points.length < 2) return [];

    // Calculate N-1 routes in parallel for N points
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

    // Check all responses succeeded
    for (const response of responses) {
      if (!response.ok) {
        throw new Error("Failed to calculate split paths");
      }
    }

    const dataPromises = responses.map(r => r.json());
    const allData = await Promise.all(dataPromises);

    // Build split paths from response data
    const splitPaths: SplitDesirePath[] = [];
    for (let i = 0; i < allData.length; i++) {
      const data = allData[i];
      // For walk mode, use route geometry; for other modes, use desire_path geometry
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

  // Insert a new waypoint at the given segment index
  const insertWaypointAtSegment = useCallback(async (segmentIndex: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;

    // Suppress the click event that follows mouseup on ghost pin drop
    suppressNextClickRef.current = true;

    setIsCalculatingSplit(true);
    try {
      // Insert the new waypoint at the appropriate position
      const newWaypoints = [...ghostWaypoints];
      newWaypoints.splice(segmentIndex, 0, position);

      // Build the full list of points: start → waypoints → end
      const allPoints = [start.coords, ...newWaypoints, end.coords];

      const splitPaths = await calculateAllSegments(allPoints);

      if (splitPaths.length === newWaypoints.length + 1) {
        setGhostWaypoints(newWaypoints);
        setSplitDesirePaths(splitPaths);
        setHasVoted(false);
      }
    } catch (error) {
      console.error("Failed to calculate split paths:", error);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, ghostWaypoints, calculateAllSegments]);

  // Update an existing ghost waypoint position
  const updateGhostWaypoint = useCallback(async (index: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;
    if (index < 0 || index >= ghostWaypoints.length) return;

    // Suppress the click event that follows mouseup
    suppressNextClickRef.current = true;

    setIsCalculatingSplit(true);
    try {
      // Update the waypoint at the given index
      const newWaypoints = [...ghostWaypoints];
      newWaypoints[index] = position;

      // Build the full list of points: start → waypoints → end
      const allPoints = [start.coords, ...newWaypoints, end.coords];

      const splitPaths = await calculateAllSegments(allPoints);

      if (splitPaths.length === newWaypoints.length + 1) {
        setGhostWaypoints(newWaypoints);
        setSplitDesirePaths(splitPaths);
        setHasVoted(false);
      }
    } catch (error) {
      console.error("Failed to recalculate split paths:", error);
    } finally {
      setIsCalculatingSplit(false);
    }
  }, [start.coords, end.coords, ghostWaypoints, calculateAllSegments]);

  const clearPoints = useCallback(() => {
    setStart({ coords: null, timestamp: null });
    setEnd({ coords: null, timestamp: null });
    setWaypoints([]);
    setHasVoted(false);
    setGhostWaypoints([]);
    setSplitDesirePaths([]);
    clearRoute();
  }, [clearRoute]);

  const castVote = useCallback(async () => {
    // Use split path segments if available, otherwise use desire path segments
    const segmentsToVote = splitDesirePaths.length > 0
      ? splitDesirePaths.flatMap(sp => sp.segments)
      : desirePathSegments;

    if (!segmentsToVote || segmentsToVote.length === 0 || !voteMode) {
      return;
    }

    setIsVoting(true);
    try {
      const response = await fetch(`${CONFIG.apiUrl}/vote`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          segments: segmentsToVote,
          mode: voteMode,
        }),
      });

      if (!response.ok) {
        throw new Error(`Vote failed: ${response.statusText}`);
      }

      setHasVoted(true);
    } catch (error) {
      console.error("Failed to cast vote:", error);
    } finally {
      setIsVoting(false);
    }
  }, [splitDesirePaths, desirePathSegments, voteMode]);

  // Auto-calculate route when both points are set or mode changes
  // Preserve ghost waypoints on mode change or drag, clear only on click
  useEffect(() => {
    // Check if this update came from a drag (vs a click or mode change)
    const isDrag = fromDragRef.current;
    fromDragRef.current = false; // Reset for next update

    // Detect if start or end coords changed
    const prevStart = prevStartCoordsRef.current;
    const prevEnd = prevEndCoordsRef.current;
    const startChanged = start.coords !== prevStart &&
      (start.coords === null || prevStart === null ||
       start.coords.lat !== prevStart.lat || start.coords.lng !== prevStart.lng);
    const endChanged = end.coords !== prevEnd &&
      (end.coords === null || prevEnd === null ||
       end.coords.lat !== prevEnd.lat || end.coords.lng !== prevEnd.lng);

    // Update refs for next run
    prevStartCoordsRef.current = start.coords;
    prevEndCoordsRef.current = end.coords;

    // Preserve ghost waypoints only on: mode change or drag
    // Clear ghost waypoints on: click (new point)
    const shouldPreserveGhostWaypoints = isDrag || (!startChanged && !endChanged);

    // Always clear the main route first
    clearRoute();
    // Always clear regular waypoints
    setWaypoints([]);
    // Always reset vote status
    setHasVoted(false);

    // Clear ghost waypoints on click (not drag, not mode change)
    if (!shouldPreserveGhostWaypoints) {
      setGhostWaypoints([]);
      setSplitDesirePaths([]);
    }

    if (start.coords && end.coords) {
      // Read from ref to avoid adding ghostWaypoints as a dependency
      const currentGhostWaypoints = ghostWaypointsRef.current;

      // Always calculate the main route
      calculateRoute({ start: start.coords, end: end.coords, mode, waypoints: [] });

      if (currentGhostWaypoints.length > 0 && shouldPreserveGhostWaypoints) {
        // Clear old split paths before recalculating to avoid stale styling
        setSplitDesirePaths([]);
        setIsCalculatingSplit(true);
        // Recalculate all segments with preserved ghost waypoints
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
    }
  }, [start.coords, end.coords, mode, calculateRoute, clearRoute, calculateAllSegments]);

  // Global mouse event tracking
  useEffect(() => {
    const logEvent = (e: MouseEvent) => {
      console.log(`[MOUSE-${e.type}]`, {
        target: e.target instanceof Element ? e.target.className : 'unknown',
        button: e.button,
        clientX: e.clientX,
        clientY: e.clientY,
        timestamp: Date.now()
      });
    };

    document.addEventListener('click', logEvent, true);
    document.addEventListener('mousedown', logEvent, true);
    document.addEventListener('mouseup', logEvent, true);

    return () => {
      document.removeEventListener('click', logEvent, true);
      document.removeEventListener('mousedown', logEvent, true);
      document.removeEventListener('mouseup', logEvent, true);
    };
  }, []);

  // Recalculate route when waypoints change (don't clear waypoints)
  // Also reset vote status when route changes due to waypoints
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
      ghostWaypoints,
      splitDesirePaths,
      isCalculatingSplit,
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
      insertWaypointAtSegment,
      updateGhostWaypoint,
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
      ghostWaypoints,
      splitDesirePaths,
      isCalculatingSplit,
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
      insertWaypointAtSegment,
      updateGhostWaypoint,
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