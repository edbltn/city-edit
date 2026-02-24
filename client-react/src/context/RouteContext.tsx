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
  RouteGeometry,
} from "../types";

// ============================================
// Geometry splitting utilities
// ============================================

/** Haversine distance in meters between two [lon, lat] coords. */
function haversineMeters(a: [number, number], b: [number, number]): number {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const [lon1, lat1] = a;
  const [lon2, lat2] = b;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const sin2 = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 6_371_000 * 2 * Math.atan2(Math.sqrt(sin2), Math.sqrt(1 - sin2));
}

/** Project point P onto segment A-B, returning the closest point and parameter t in [0,1]. */
function projectOntoSegment(
  p: [number, number],
  a: [number, number],
  b: [number, number]
): { point: [number, number]; t: number } {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return { point: a, t: 0 };
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lenSq));
  return {
    point: [a[0] + t * dx, a[1] + t * dy],
    t,
  };
}

/**
 * Split a geometry at the closest point to `latlng`.
 * Returns two geometry halves and the distance (meters) from the point to the geometry.
 * Returns null if the geometry has fewer than 2 coordinates.
 */
function splitGeometryAtPoint(
  geometry: RouteGeometry,
  latlng: LatLng
): { first: RouteGeometry; second: RouteGeometry; distanceMeters: number } | null {
  const coords = geometry.coordinates;
  if (coords.length < 2) return null;

  const p: [number, number] = [latlng.lng, latlng.lat]; // GeoJSON: [lon, lat]
  let bestDist = Infinity;
  let bestIdx = 0;
  let bestProj: [number, number] = coords[0];

  for (let i = 0; i < coords.length - 1; i++) {
    const { point: proj } = projectOntoSegment(p, coords[i], coords[i + 1]);
    const dist = haversineMeters(p, proj);
    if (dist < bestDist) {
      bestDist = dist;
      bestIdx = i;
      bestProj = proj;
    }
  }

  // First half: coords[0..bestIdx] + projected point
  // Second half: projected point + coords[bestIdx+1..end]
  const firstCoords = coords.slice(0, bestIdx + 1).concat([bestProj]);
  const secondCoords = [bestProj].concat(coords.slice(bestIdx + 1));

  return {
    first: { type: "LineString", coordinates: firstCoords },
    second: { type: "LineString", coordinates: secondCoords },
    distanceMeters: bestDist,
  };
}

/** Derive vote segments (consecutive coordinate pairs) from a geometry. */
function segmentsFromGeometry(geometry: RouteGeometry): [number, number][][] {
  const coords = geometry.coordinates;
  const segs: [number, number][][] = [];
  for (let i = 0; i < coords.length - 1; i++) {
    segs.push([coords[i], coords[i + 1]]);
  }
  return segs;
}

// Max distance (meters) from insertion point to geometry to qualify for local splitting.
// Beyond this, fall back to server requests (the waypoint was dragged off-route).
const LOCAL_SPLIT_THRESHOLD_METERS = 100;

/**
 * Reverse geocode a lat/lng to a short address via Nominatim.
 * Returns street + house number, or just the street, or coords as fallback.
 */
async function reverseGeocode(coords: LatLng): Promise<string> {
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/reverse?lat=${coords.lat}&lon=${coords.lng}&format=json&zoom=18&addressdetails=1`,
      { headers: { "Accept-Language": "en" } }
    );
    if (!res.ok) return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
    const data = await res.json();
    const addr = data.address;
    if (!addr) return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;

    // Build a short label: "123 Broadway" or "Broadway & Wall St" or just the road
    const parts: string[] = [];
    if (addr.house_number) parts.push(addr.house_number);
    if (addr.road) parts.push(addr.road);
    else if (addr.pedestrian) parts.push(addr.pedestrian);
    if (parts.length === 0 && addr.neighbourhood) parts.push(addr.neighbourhood);
    return parts.join(" ") || `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
  } catch {
    return `${coords.lat.toFixed(5)}, ${coords.lng.toFixed(5)}`;
  }
}

interface RouteContextValue {
  start: RoutePoint;
  end: RoutePoint;
  startLabel: string | null;
  endLabel: string | null;
  mode: TransportMode;
  waypoints: LatLng[];
  isCalculating: boolean;
  routeData: RouteData | null;
  desirePathData: DesirePathData | null;
  error: string | null;
  hasVoted: boolean;
  isVoting: boolean;
  ghostWaypoints: LatLng[];
  ghostWaypointIds: string[];
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
  const [ghostWaypointIds, setGhostWaypointIds] = useState<string[]>([]);
  const nextGhostIdRef = useRef(0);
  const [splitDesirePaths, setSplitDesirePaths] = useState<SplitDesirePath[]>([]);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);
  const [hasVoted, setHasVoted] = useState(false);
  const [isVoting, setIsVoting] = useState(false);
  const [voteType, setVoteTypeState] = useState<string>(() => getDefaultVoteType("bike", "route"));

  // Reverse geocoded labels
  const [startLabel, setStartLabel] = useState<string | null>(null);
  const [endLabel, setEndLabel] = useState<string | null>(null);

  // Ref for click suppression (needs immediate effect, not async like state)
  const suppressNextClickRef = useRef(false);

  // Ref to track ghost waypoints for reading in effects without triggering re-runs
  const ghostWaypointsRef = useRef<LatLng[]>([]);

  // Ref to track if we're handling point removal (skip main effect)
  const handlingRemovalRef = useRef(false);

  // Route version counter - increments when path changes, used to detect stale votes
  const routeVersionRef = useRef(0);

  // Split calculation version - increments on each calculateAllSegments call, used to discard stale responses
  const splitCalcVersionRef = useRef(0);

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
    setStartLabel(null);
    reverseGeocode(coords).then(setStartLabel);
  }, []);

  const setEndPoint = useCallback((coords: LatLng) => {
    setEnd({ coords, timestamp: Date.now() });
    setEndLabel(null);
    reverseGeocode(coords).then(setEndLabel);
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
    setGhostWaypointIds([]);
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
    setGhostWaypointIds([]);
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

    // Compute new waypoints from ref (always current) and sync both ref and state
    const newWaypoints = [...ghostWaypointsRef.current];
    newWaypoints.splice(segmentIndex, 0, position);
    ghostWaypointsRef.current = newWaypoints;
    setGhostWaypoints(newWaypoints);

    // Generate stable ID for the new waypoint
    const newId = `gwp-${++nextGhostIdRef.current}`;
    const newIds = [...ghostWaypointIds];
    newIds.splice(segmentIndex, 0, newId);
    setGhostWaypointIds(newIds);

    // Path changed - reset vote state immediately
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);

    // Try client-side geometry splitting: if the insertion point is on the
    // existing route/segment geometry, split locally instead of server requests.
    const currentGeometry = splitDesirePaths.length > 0
      ? splitDesirePaths[segmentIndex]?.geometry
      : routeData?.geometry;

    if (currentGeometry) {
      const splitResult = splitGeometryAtPoint(currentGeometry, position);
      if (splitResult && splitResult.distanceMeters <= LOCAL_SPLIT_THRESHOLD_METERS) {
        // Point is on/near the route -- split locally
        const existingPaths = splitDesirePaths.length > 0
          ? [...splitDesirePaths]
          : [];

        const firstHalf: SplitDesirePath = {
          id: `split-${segmentIndex}`,
          segmentIndex,
          geometry: splitResult.first,
          segments: segmentsFromGeometry(splitResult.first),
        };
        const secondHalf: SplitDesirePath = {
          id: `split-${segmentIndex + 1}`,
          segmentIndex: segmentIndex + 1,
          geometry: splitResult.second,
          segments: segmentsFromGeometry(splitResult.second),
        };

        if (existingPaths.length > 0) {
          // Replace the split segment with two halves, keep the rest
          existingPaths.splice(segmentIndex, 1, firstHalf, secondHalf);
        } else {
          // First split: replace the single route with two segments
          existingPaths.push(firstHalf, secondHalf);
        }

        // Renumber segment indices
        for (let i = 0; i < existingPaths.length; i++) {
          existingPaths[i].segmentIndex = i;
          existingPaths[i].id = `split-${i}`;
        }

        setSplitDesirePaths(existingPaths);
        return;
      }
    }

    // Fallback: point is off-route or geometry unavailable -- use server requests
    setSplitDesirePaths([]);
    setIsCalculatingSplit(true);

    // Track this calculation version to discard stale responses
    splitCalcVersionRef.current++;
    const calcVersion = splitCalcVersionRef.current;

    try {
      const allPoints = [start.coords, ...newWaypoints, end.coords];
      const splitPaths = await calculateAllSegments(allPoints);

      // Discard if a newer calculation was started
      if (calcVersion !== splitCalcVersionRef.current) return;

      if (splitPaths.length === newWaypoints.length + 1) {
        setSplitDesirePaths(splitPaths);
      }
    } catch (err) {
      console.error("Failed to calculate split paths:", err);
    } finally {
      if (calcVersion === splitCalcVersionRef.current) {
        setIsCalculatingSplit(false);
      }
    }
  }, [start.coords, end.coords, ghostWaypointIds, splitDesirePaths, routeData, calculateAllSegments]);

  // ============================================
  // Update ghost waypoint position (drag existing waypoint)
  // ============================================
  const updateGhostWaypoint = useCallback(async (index: number, position: LatLng) => {
    if (!start.coords || !end.coords) return;

    suppressNextClickRef.current = true;

    // Compute new waypoints from ref (always current) and sync both ref and state
    const currentWaypoints = ghostWaypointsRef.current;
    if (index < 0 || index >= currentWaypoints.length) return;

    const newWaypoints = [...currentWaypoints];
    newWaypoints[index] = position;
    ghostWaypointsRef.current = newWaypoints;
    setGhostWaypoints(newWaypoints);

    // Path changed - reset vote state immediately
    routeVersionRef.current++;
    setHasVoted(false);
    setIsVoting(false);

    // Clear paths immediately - they'll reappear when calculation completes
    setSplitDesirePaths([]);
    setIsCalculatingSplit(true);

    // Track this calculation version to discard stale responses
    splitCalcVersionRef.current++;
    const calcVersion = splitCalcVersionRef.current;

    try {
      const allPoints = [start.coords, ...newWaypoints, end.coords];
      const splitPaths = await calculateAllSegments(allPoints);

      // Discard if a newer calculation was started
      if (calcVersion !== splitCalcVersionRef.current) return;

      if (splitPaths.length === newWaypoints.length + 1) {
        setSplitDesirePaths(splitPaths);
      }
    } catch (err) {
      console.error("Failed to recalculate split paths:", err);
    } finally {
      if (calcVersion === splitCalcVersionRef.current) {
        setIsCalculatingSplit(false);
      }
    }
  }, [start.coords, end.coords, calculateAllSegments]);

  // ============================================
  // Remove any point (unified logic)
  // All points conceptually: [start, ...ghostWaypoints, end]
  // After removal: reassign based on count
  // ============================================
  const removePoint = useCallback((which: "start" | "end" | number) => {
    // Build ordered point list and parallel ID list
    // IDs use null for start/end since they don't have ghost IDs
    const allPoints: LatLng[] = [];
    const allIds: (string | null)[] = [];
    const currentIds = ghostWaypointIds;
    if (start.coords) { allPoints.push(start.coords); allIds.push(null); }
    const currentGhostWaypoints = ghostWaypointsRef.current;
    for (let i = 0; i < currentGhostWaypoints.length; i++) {
      allPoints.push(currentGhostWaypoints[i]);
      allIds.push(currentIds[i] ?? null);
    }
    if (end.coords) { allPoints.push(end.coords); allIds.push(null); }

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
    const remainingIds = allIds.filter((_, i) => i !== removeIndex);

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
      setGhostWaypointIds([]);
    } else if (remaining.length === 1) {
      // One point left -> just start, no route
      setStart({ coords: remaining[0], timestamp: Date.now() });
      setEnd({ coords: null, timestamp: null });
      setGhostWaypoints([]);
      setGhostWaypointIds([]);
    } else {
      // Two or more: first=start, last=end, middle=ghostWaypoints
      const newStart = remaining[0];
      const newEnd = remaining[remaining.length - 1];
      const newGhostWaypoints = remaining.slice(1, -1);
      // Ghost waypoint IDs are the middle elements of remainingIds
      // (start/end IDs are null, ghost IDs survive from the original array)
      const newGhostIds = remainingIds.slice(1, -1).map(
        id => id ?? `gwp-${++nextGhostIdRef.current}`
      );

      // Only update start/end if coordinates actually changed
      // This prevents triggering the main effect unnecessarily when removing a ghost waypoint
      const startChanged = !start.coords ||
        start.coords.lat !== newStart.lat ||
        start.coords.lng !== newStart.lng;
      const endChanged = !end.coords ||
        end.coords.lat !== newEnd.lat ||
        end.coords.lng !== newEnd.lng;

      // Only skip main effect if we're actually changing start/end
      // (otherwise the effect won't fire and the ref stays true forever)
      if (startChanged || endChanged) {
        handlingRemovalRef.current = true;
      }

      if (startChanged) {
        setStart({ coords: newStart, timestamp: Date.now() });
      }
      if (endChanged) {
        setEnd({ coords: newEnd, timestamp: Date.now() });
      }
      setGhostWaypoints(newGhostWaypoints);
      setGhostWaypointIds(newGhostIds);

      // Recalculate based on new structure
      if (newGhostWaypoints.length > 0) {
        // Has ghost waypoints: calculate split paths
        setIsCalculatingSplit(true);
        splitCalcVersionRef.current++;
        const calcVersion = splitCalcVersionRef.current;
        calculateAllSegments(remaining)
          .then(splitPaths => {
            if (calcVersion !== splitCalcVersionRef.current) return;
            setSplitDesirePaths(splitPaths);
          })
          .catch(console.error)
          .finally(() => {
            if (calcVersion === splitCalcVersionRef.current) {
              setIsCalculatingSplit(false);
            }
          });
      } else {
        // Just start and end: calculate main route
        calculateRoute({ start: newStart, end: newEnd, mode, waypoints: [] });
      }
    }
  }, [start.coords, end.coords, ghostWaypointIds, mode, calculateAllSegments, calculateRoute, clearRoute]);

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
      setGhostWaypointIds([]);
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
      splitCalcVersionRef.current++;
      const calcVersion = splitCalcVersionRef.current;
      const allPoints = [start.coords, ...currentGhostWaypoints, end.coords];
      calculateAllSegments(allPoints)
        .then(splitPaths => {
          if (calcVersion !== splitCalcVersionRef.current) return;
          if (splitPaths.length === currentGhostWaypoints.length + 1) {
            setSplitDesirePaths(splitPaths);
          }
        })
        .catch(console.error)
        .finally(() => {
          if (calcVersion === splitCalcVersionRef.current) {
            setIsCalculatingSplit(false);
          }
        });
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
      startLabel,
      endLabel,
      mode,
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      ghostWaypoints,
      ghostWaypointIds,
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
      startLabel,
      endLabel,
      mode,
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      ghostWaypoints,
      ghostWaypointIds,
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
