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
import { getMapSlug, getCurrentMap } from "../map/runtime";
import { getDefaultVoteTypeForTheme } from "../constants/voteTypes";
import { getInitialPoints, cleanNavParams } from "../utils/mapViewState";
import { coverage, type VoteDirection } from "../utils/voteStore";
import { castVotes } from "../utils/castVote";
import { useTheme } from "./ThemeContext";
import { useGraphSnap } from "./GraphSnapContext";
import type {
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

export type ActiveTool = "start" | "end";

interface RouteContextValue {
  start: RoutePoint;
  end: RoutePoint;
  waypoints: LatLng[];
  isCalculating: boolean;
  routeData: RouteData | null;
  desirePathData: DesirePathData | null;
  error: string | null;
  hasVoted: boolean;
  isVoting: boolean;
  /** Direction the Cast +/- control will apply: 1 (for) | -1 (against). */
  voteDirection: VoteDirection;
  setVoteDirection: (dir: VoteDirection) => void;
  /** True when the current target is fully voted in `voteDirection` (so casting
   *  again removes it — the control is a reversible toggle, never disabled). */
  isDirectionCast: (dir: VoteDirection) => boolean;
  ghostWaypoints: LatLng[];
  ghostWaypointIds: string[];
  splitDesirePaths: SplitDesirePath[];
  isCalculatingSplit: boolean;
  voteType: string;
  pointType: "route" | "point";
  activeTool: ActiveTool;
  suppressNextClick: () => boolean;
  setStartPoint: (coords: LatLng, address?: string) => void;
  setEndPoint: (coords: LatLng, address?: string) => void;
  setActiveTool: (tool: ActiveTool) => void;
  clearPoints: () => void;
  clearStart: () => void;
  clearEnd: () => void;
  clearError: () => void;
  setError: (message: string) => void;
  addWaypoint: (coords: LatLng) => void;
  updateWaypoint: (index: number, coords: LatLng) => void;
  clearWaypoints: () => void;
  castVote: (dir?: VoteDirection) => Promise<void>;
  insertWaypointAtSegment: (segmentIndex: number, position: LatLng) => Promise<void>;
  updateGhostWaypoint: (index: number, position: LatLng) => Promise<void>;
  removeGhostWaypoint: (index: number) => void;
  clearSplitPaths: () => void;
  clearSuppressClick: () => void;
  setSuppressClick: () => void;
  setVoteType: (voteType: string) => void;
}

// dedupe while preserving order
function uniq(ids: number[]): number[] {
  return [...new Set(ids)];
}

const RouteContext = createContext<RouteContextValue | null>(null);

export function RouteProvider({ children }: { children: ReactNode }) {
  const theme = useTheme();

  // Core state
  const [start, setStart] = useState<RoutePoint>({ coords: null, timestamp: null });
  const [end, setEnd] = useState<RoutePoint>({ coords: null, timestamp: null });
  const [waypoints, setWaypoints] = useState<LatLng[]>([]);
  const [ghostWaypoints, setGhostWaypoints] = useState<LatLng[]>([]);
  const [ghostWaypointIds, setGhostWaypointIds] = useState<string[]>([]);
  const nextGhostIdRef = useRef(0);
  const [splitDesirePaths, setSplitDesirePaths] = useState<SplitDesirePath[]>([]);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);
  // Bumped whenever a vote is recorded so derived "already voted" values recompute.
  const [votedVersion, setVotedVersion] = useState(0);
  const [isVoting, setIsVoting] = useState(false);
  const [voteType, setVoteTypeState] = useState<string>(() =>
    getDefaultVoteTypeForTheme(theme, theme.inputMode === "point" ? "point" : "route")
  );
  const [voteDirection, setVoteDirectionState] = useState<VoteDirection>(1);
  const [activeTool, setActiveToolState] = useState<ActiveTool>("start");

  // Snap resolver (registered by GraphLayer) — turns a clicked point into the
  // edge a vote lands on, so point casts use the same snap path as hover/click.
  const { resolveVoteEdgeId } = useGraphSnap();

  const setVoteDirection = useCallback((dir: VoteDirection) => {
    setVoteDirectionState(dir);
  }, []);

  const setActiveTool = useCallback((tool: ActiveTool) => {
    setActiveToolState(tool);
  }, []);

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
    edgeIds: routeEdgeIds,
    calculateRoute,
    clearRoute,
    clearError,
    setError,
  } = useRouteCalculation();

  // Restore start/end from URL params (set by theme switcher), then clean them up
  const restoredFromUrlRef = useRef(false);
  // A vote type restored from the URL (?vt=). Consumed once by the pointType
  // auto-update effect so a deep link selects it instead of the theme default.
  const restoredVtRef = useRef<string | null>(null);
  useEffect(() => {
    if (restoredFromUrlRef.current) return;
    restoredFromUrlRef.current = true;

    const initial = getInitialPoints();
    if (initial.start) {
      const s = initial.start;
      setStart({ coords: { lat: s.lat, lng: s.lng }, timestamp: Date.now(), address: null });
      fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${s.lat}&lng=${s.lng}`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
          setStart(prev =>
            prev.coords?.lat === s.lat && prev.coords?.lng === s.lng
              ? { ...prev, address: data.address }
              : prev
          );
        })
        .catch(() => {});
    }
    if (initial.end) {
      const e = initial.end;
      setEnd({ coords: { lat: e.lat, lng: e.lng }, timestamp: Date.now(), address: null });
      fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${e.lat}&lng=${e.lng}`)
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
          setEnd(prev =>
            prev.coords?.lat === e.lat && prev.coords?.lng === e.lng
              ? { ...prev, address: data.address }
              : prev
          );
        })
        .catch(() => {});
    }
    if (initial.vt) {
      restoredVtRef.current = initial.vt;
      setVoteTypeState(initial.vt);
    }
    cleanNavParams();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Station networks (e.g. ebikes) vote on single fixed points — never routes —
  // so there's no two-point routing regardless of how many points are set.
  const isStationNetwork = (getCurrentMap()?.network ?? "streets") !== "streets";

  // Compute point type based on whether both points are set
  const pointType: "route" | "point" =
    !isStationNetwork && start.coords && end.coords ? "route" : "point";

  // ============================================
  // Helper: Calculate all route segments for split paths
  // ============================================
  const splitAbortRef = useRef<AbortController | null>(null);

  const calculateAllSegments = useCallback(async (points: LatLng[]): Promise<SplitDesirePath[]> => {
    if (points.length < 2) return [];

    // Abort previous batch of split requests
    splitAbortRef.current?.abort();
    const controller = new AbortController();
    splitAbortRef.current = controller;

    const fetchPromises = [];
    for (let i = 0; i < points.length - 1; i++) {
      fetchPromises.push(
        fetch(`${CONFIG.apiUrl}/routes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: [points[i].lat, points[i].lng],
            end: [points[i + 1].lat, points[i + 1].lng],
            waypoints: [],
            map: getMapSlug(),
          }),
          signal: controller.signal,
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
      const geometry = data.route?.geometry;
      if (geometry) {
        splitPaths.push({
          id: `split-${i}`,
          segmentIndex: i,
          geometry,
          segments: data.desire_path_segments || [],
          edgeIds: data.edge_ids || [],
        });
      }
    }

    return splitPaths;
  }, []);

  // ============================================
  // Simple setters
  // ============================================
  const setStartPoint = useCallback((coords: LatLng, address?: string) => {
    setStart({ coords, timestamp: Date.now(), address: address ?? null });
    if (!address) {
      fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${coords.lat}&lng=${coords.lng}`)
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then(data => {
          setStart(prev =>
            prev.coords?.lat === coords.lat && prev.coords?.lng === coords.lng
              ? { ...prev, address: data.address }
              : prev
          );
        })
        .catch(() => {});
    }
  }, []);

  const setEndPoint = useCallback((coords: LatLng, address?: string) => {
    // No routing on station networks: a would-be "end" click just moves the
    // single selected station, so there's never a second endpoint to route to.
    if (isStationNetwork) { setStartPoint(coords, address); return; }
    setEnd({ coords, timestamp: Date.now(), address: address ?? null });
    if (!address) {
      fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${coords.lat}&lng=${coords.lng}`)
        .then(r => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then(data => {
          setEnd(prev =>
            prev.coords?.lat === coords.lat && prev.coords?.lng === coords.lng
              ? { ...prev, address: data.address }
              : prev
          );
        })
        .catch(() => {});
    }
  }, [isStationNetwork, setStartPoint]);

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
    setIsVoting(false);
    setActiveToolState("start");
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

    // Path changed - bump version so any in-flight vote isn't recorded
    routeVersionRef.current++;
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
          edgeIds: [],
        };
        const secondHalf: SplitDesirePath = {
          id: `split-${segmentIndex + 1}`,
          segmentIndex: segmentIndex + 1,
          geometry: splitResult.second,
          segments: segmentsFromGeometry(splitResult.second),
          edgeIds: [],
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

    // Path changed - bump version so any in-flight vote isn't recorded
    routeVersionRef.current++;
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
    const allAddresses: (string | null | undefined)[] = [];
    const currentIds = ghostWaypointIds;
    if (start.coords) { allPoints.push(start.coords); allIds.push(null); allAddresses.push(start.address); }
    const currentGhostWaypoints = ghostWaypointsRef.current;
    for (let i = 0; i < currentGhostWaypoints.length; i++) {
      allPoints.push(currentGhostWaypoints[i]);
      allIds.push(currentIds[i] ?? null);
      allAddresses.push(undefined);
    }
    if (end.coords) { allPoints.push(end.coords); allIds.push(null); allAddresses.push(end.address); }

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
    const remainingAddresses = allAddresses.filter((_, i) => i !== removeIndex);

    routeVersionRef.current++;
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
      setActiveToolState("start");
    } else if (remaining.length === 1) {
      // One point left -> just start, no route
      const addr = remainingAddresses[0] ?? null;
      setStart({ coords: remaining[0], timestamp: Date.now(), address: addr });
      setEnd({ coords: null, timestamp: null });
      setGhostWaypoints([]);
      setGhostWaypointIds([]);
      setActiveToolState("start");
      if (!addr) {
        const pt = remaining[0];
        fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${pt.lat}&lng=${pt.lng}`)
          .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
          .then(data => {
            setStart(prev =>
              prev.coords?.lat === pt.lat && prev.coords?.lng === pt.lng
                ? { ...prev, address: data.address }
                : prev
            );
          })
          .catch(() => {});
      }
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
        const startAddr = remainingAddresses[0] ?? null;
        setStart({ coords: newStart, timestamp: Date.now(), address: startAddr });
        // Fetch reverse geocode if promoted point lacks an address
        if (!startAddr) {
          fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${newStart.lat}&lng=${newStart.lng}`)
            .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
            .then(data => {
              setStart(prev =>
                prev.coords?.lat === newStart.lat && prev.coords?.lng === newStart.lng
                  ? { ...prev, address: data.address }
                  : prev
              );
            })
            .catch(() => {});
        }
      }
      if (endChanged) {
        const endAddr = remainingAddresses[remainingAddresses.length - 1] ?? null;
        setEnd({ coords: newEnd, timestamp: Date.now(), address: endAddr });
        if (!endAddr) {
          fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${newEnd.lat}&lng=${newEnd.lng}`)
            .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
            .then(data => {
              setEnd(prev =>
                prev.coords?.lat === newEnd.lat && prev.coords?.lng === newEnd.lng
                  ? { ...prev, address: data.address }
                  : prev
              );
            })
            .catch(() => {});
        }
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
        calculateRoute({ start: newStart, end: newEnd, waypoints: [] });
      }
    }
  }, [start.coords, end.coords, ghostWaypointIds, calculateAllSegments, calculateRoute, clearRoute]);

  // Convenience wrappers
  const clearStart = useCallback(() => removePoint("start"), [removePoint]);
  const clearEnd = useCallback(() => removePoint("end"), [removePoint]);
  const removeGhostWaypoint = useCallback((index: number) => removePoint(index), [removePoint]);

  // Edge IDs that make up the current vote target. Split paths (dragged route)
  // take precedence, then the main route's edges, then a point cast resolves the
  // single clicked location to an edge via the shared snap path.
  const currentEdgeIds = useMemo(() => {
    if (splitDesirePaths.length > 0) return uniq(splitDesirePaths.flatMap((sp) => sp.edgeIds));
    if (routeEdgeIds && routeEdgeIds.length > 0) return uniq(routeEdgeIds);
    if (start.coords && !end.coords) {
      const eid = resolveVoteEdgeId(start.coords.lat, start.coords.lng);
      return eid != null ? [eid] : [];
    }
    return [];
    // votedVersion forces a re-resolve of point votes after a cast settles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitDesirePaths, routeEdgeIds, start.coords, end.coords, resolveVoteEdgeId, votedVersion]);

  // ============================================
  // Cast vote — the single path (route, dragged route, or point), directional.
  // Delegates to castVotes(), which is coverage-aware (only changes edges that
  // need it), reversible (re-casting the held direction removes), optimistic,
  // and self-healing. Re-casting the same direction toggles the votes off.
  // ============================================
  const castVote = useCallback(async (dirOverride?: VoteDirection) => {
    const edges = currentEdgeIds;
    if (edges.length === 0) return;
    const dir = dirOverride ?? voteDirection;
    if (dirOverride && dirOverride !== voteDirection) setVoteDirectionState(dirOverride);

    const voteRouteVersion = routeVersionRef.current;
    setIsVoting(true);
    try {
      await castVotes({
        mode: theme.mode,
        edgeIds: edges,
        label: voteType,
        direction: dir,
      });
    } finally {
      // Only refresh derived "already voted" state if the path didn't change
      // mid-flight; either way stop the spinner.
      if (routeVersionRef.current === voteRouteVersion) setVotedVersion((v) => v + 1);
      setIsVoting(false);
    }
  }, [currentEdgeIds, voteType, voteDirection, theme.mode]);

  // Coverage of the current target for a (voteType, direction): which edges are
  // already at that direction. Drives button state + the "already cast" badge.
  const isDirectionCast = useCallback(
    (dir: VoteDirection) => {
      void votedVersion; // recompute after each cast
      if (currentEdgeIds.length === 0 || !voteType) return false;
      const cov = coverage(theme.mode, currentEdgeIds, voteType, dir);
      return cov.unvoted.length === 0 && cov.opposite.length === 0 && cov.atTarget.length > 0;
    },
    [votedVersion, currentEdgeIds, voteType, theme.mode]
  );

  // Whether the current target is fully cast in the selected direction.
  const hasVoted = isDirectionCast(voteDirection);

  // ============================================
  // Main calculation effect
  // Runs when start, end, or mode changes
  // Uses current ghost waypoints to determine what to calculate
  // ============================================
  // Extract coordinate values for effect deps — avoids re-triggering
  // the main calculation when only the address or timestamp changes
  const startLat = start.coords?.lat ?? null;
  const startLng = start.coords?.lng ?? null;
  const endLat = end.coords?.lat ?? null;
  const endLng = end.coords?.lng ?? null;

  useEffect(() => {
    // Skip if removal is being handled (it does its own calculation)
    if (handlingRemovalRef.current) {
      handlingRemovalRef.current = false;
      return;
    }

    // Path changed - bump version so a stale in-flight vote isn't recorded
    routeVersionRef.current++;
    setIsVoting(false);
    setWaypoints([]);

    // Need both start and end to calculate
    if (startLat === null || startLng === null || endLat === null || endLng === null) {
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

    const startCoords = { lat: startLat, lng: startLng };
    const endCoords = { lat: endLat, lng: endLng };
    const currentGhostWaypoints = ghostWaypointsRef.current;

    // Always calculate the main route
    calculateRoute({ start: startCoords, end: endCoords, waypoints: [] });

    // If there are ghost waypoints, also calculate split paths
    if (currentGhostWaypoints.length > 0) {
      setIsCalculatingSplit(true);
      splitCalcVersionRef.current++;
      const calcVersion = splitCalcVersionRef.current;
      const allPoints = [startCoords, ...currentGhostWaypoints, endCoords];
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
  }, [startLat, startLng, endLat, endLng, calculateRoute, clearRoute, calculateAllSegments]);

  // ============================================
  // Recalculate when waypoints change
  // ============================================
  useEffect(() => {
    if (start.coords && end.coords && waypoints.length > 0) {
      routeVersionRef.current++;
      setIsVoting(false);
      calculateRoute({ start: start.coords, end: end.coords, waypoints });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waypoints]);

  // ============================================
  // Smart default: clicking a top proposal selects its vote type, so the Cast
  // +/- control immediately acts on the proposal the user just clicked.
  // ============================================
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { label?: string; mode?: string };
      if (detail?.mode && detail.mode !== theme.mode) return;
      if (detail?.label) {
        // Apply now, and stage it in the one-shot ref so the pointType-change
        // effect (which may fire from the same click placing a point) keeps the
        // proposal's type instead of resetting to the theme default.
        restoredVtRef.current = detail.label;
        setVoteTypeState(detail.label);
      }
    };
    window.addEventListener("proposal-vote-type", handler);
    return () => window.removeEventListener("proposal-vote-type", handler);
  }, [theme.mode]);

  // ============================================
  // Auto-update vote type when pointType changes
  // ============================================
  useEffect(() => {
    // A URL-restored vote type wins once, so a deep link lands on its proposal.
    if (restoredVtRef.current) {
      setVoteTypeState(restoredVtRef.current);
      restoredVtRef.current = null;
      return;
    }
    setVoteTypeState(getDefaultVoteTypeForTheme(theme, pointType));
  }, [pointType, theme]);

  // ============================================
  // Context value
  // ============================================
  const value = useMemo(
    () => ({
      start,
      end,
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      voteDirection,
      setVoteDirection,
      isDirectionCast,
      ghostWaypoints,
      ghostWaypointIds,
      splitDesirePaths,
      isCalculatingSplit,
      voteType,
      pointType,
      activeTool,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setActiveTool,
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
      waypoints,
      isCalculating,
      routeData,
      desirePathData,
      error,
      hasVoted,
      isVoting,
      voteDirection,
      setVoteDirection,
      isDirectionCast,
      ghostWaypoints,
      ghostWaypointIds,
      splitDesirePaths,
      isCalculatingSplit,
      voteType,
      pointType,
      activeTool,
      suppressNextClick,
      setStartPoint,
      setEndPoint,
      setActiveTool,
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
