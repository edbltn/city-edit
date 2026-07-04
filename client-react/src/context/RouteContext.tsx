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
import { getMapSlug, getCurrentMap, mapVoteTypesForPointType } from "../map/runtime";
import { getDefaultVoteTypeForTheme } from "../constants/voteTypes";
import { blockCoverage, type VoteDirection } from "../utils/voteStore";
import { useVotesVersion } from "../utils/useVotesVersion";
import { castVotes, voteButtonState } from "../utils/castVote";
import { useTheme } from "./ThemeContext";
import { useGraphSnap } from "./GraphSnapContext";
import type { Selection } from "../selection/types";
import {
  setStart as selSetStart,
  setEnd as selSetEnd,
  insertMid as selInsertMid,
  updateAt as selUpdateAt,
  removeAt as selRemoveAt,
  clearWaypoints as selClearWaypoints,
  setVoteType as selSetVoteType,
  fullIndexOf,
} from "../selection/reducer";
import { deriveStart, deriveEnd, deriveMids, deriveMidIds } from "../selection/selectors";
import { selectionToParams, selectionFromParams } from "../selection/serialize";
import { resolveEffectiveVoteType } from "../selection/voteType";
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

/** Two points are effectively the same location (within ~1m). Used to detect a
 *  waypoint dragged onto a neighbor (e.g. snapped onto a proposal that's already
 *  the adjacent waypoint), which would collapse a route segment to zero length. */
function sameLatLng(a: LatLng, b: LatLng): boolean {
  return Math.abs(a.lat - b.lat) < 1e-5 && Math.abs(a.lng - b.lng) < 1e-5;
}

/** Whether two optional coords are the same location (null-safe). */
function coordsEqual(a: LatLng | null, b: LatLng | null): boolean {
  if (!a || !b) return a === b;
  return a.lat === b.lat && a.lng === b.lng;
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

// Max distance (meters) from the dropped point to the existing route geometry to
// qualify for the fast, instant local geometry split (no server round-trip). The
// split is forced to pass through the ACTUAL dropped coordinate (see below), so
// the connector/anchor is correct even for an off-route proposal within range; a
// far drop (> this) still falls back to server routing for a real path.
const LOCAL_SPLIT_THRESHOLD_METERS = 30;

export type ActiveTool = "start" | "end";

/** Selection edges → the touched blocks' materialized edge lists (docs
 *  three-layer-model §4). Registered by GraphLayer, which owns the topology. */
export type BlockMaterializer = (edgeIds: number[]) => ArrayLike<number>[];

interface RouteContextValue {
  start: RoutePoint;
  end: RoutePoint;
  waypoints: LatLng[];
  isCalculating: boolean;
  routeData: RouteData | null;
  desirePathData: DesirePathData | null;
  error: string | null;
  hasVoted: boolean;
  /** Direction the Cast +/- control will apply: 1 (for) | -1 (against). */
  voteDirection: VoteDirection;
  setVoteDirection: (dir: VoteDirection) => void;
  /** True when the current target is fully voted in `voteDirection` (so casting
   *  again removes it — the control is a reversible toggle, never disabled). */
  isDirectionCast: (dir: VoteDirection) => boolean;
  /** Register (or clear, with null) the selection→blocks materializer. Set by
   *  GraphLayer once topology loads; casts and pressed-state fall back to
   *  singleton [edge] blocks until then. */
  setBlockMaterializer: (fn: BlockMaterializer | null) => void;
  ghostWaypoints: LatLng[];
  ghostWaypointIds: string[];
  splitDesirePaths: SplitDesirePath[];
  isCalculatingSplit: boolean;
  /** Graph edge IDs of the direct (start→end) route. Combined with the split
   *  segments to tell GraphLayer which top proposals the path passes through. */
  routeEdgeIds: number[] | null;
  /** The EFFECTIVE vote-type label the Cast control acts on — the selection's
   *  requested label resolved through the map's valid set / most-voted-on-path /
   *  theme default (see resolveEffectiveVoteType). */
  voteType: string;
  pointType: "route" | "point";
  activeTool: ActiveTool;
  /** True when the start tool was explicitly armed from the legend to MOVE the
   *  start in place — the next map click replaces the start and keeps the
   *  existing end + waypoints, instead of wiping the whole path. One-shot:
   *  cleared the moment a point is placed/cleared. */
  startReplaceArmed: boolean;
  suppressNextClick: () => boolean;
  setStartPoint: (coords: LatLng, address?: string, voteEdgeId?: number) => void;
  /** Pin the resolved proposal edge onto the selected start point WITHOUT moving
   *  it (no coord/geocode churn). The map publishes the modal's vote target here
   *  so the banner votes on the same edge. See RoutePoint.voteEdgeId. */
  setStartVoteEdgeId: (voteEdgeId: number | null) => void;
  setEndPoint: (coords: LatLng, address?: string) => void;
  setActiveTool: (tool: ActiveTool) => void;
  /** Arm the start tool to move the start in place (keep the route). See
   *  `startReplaceArmed`. */
  armStartReplace: () => void;
  clearPoints: () => void;
  clearStart: () => void;
  clearEnd: () => void;
  clearError: () => void;
  setError: (message: string) => void;
  addWaypoint: (coords: LatLng) => void;
  updateWaypoint: (index: number, coords: LatLng) => void;
  clearWaypoints: () => void;
  castVote: (dir?: VoteDirection) => void;
  insertWaypointAtSegment: (segmentIndex: number, position: LatLng) => Promise<void>;
  updateGhostWaypoint: (index: number, position: LatLng) => Promise<void>;
  removeGhostWaypoint: (index: number) => void;
  clearSplitPaths: () => void;
  clearSuppressClick: () => void;
  setSuppressClick: () => void;
  setVoteType: (voteType: string) => void;
  // ── Selection history (back / forward) ────────────────────────────────────
  /** Step to the previous selection in history (no-op at the start). */
  stepBack: () => void;
  /** Step to the next selection in history (no-op at the end). */
  stepForward: () => void;
  /** Whether a previous / next selection exists to step to. */
  canStepBack: boolean;
  canStepForward: boolean;
}

// dedupe while preserving order
function uniq(ids: number[]): number[] {
  return [...new Set(ids)];
}

/** Two selections represent the SAME navigable state — same ordered coordinates and
 *  requested vote type. Addresses / ids / voteEdgeId (runtime sugar) are ignored, so
 *  an async reverse-geocode or a pinned edge never spawns a spurious history entry. */
function selectionsEqual(a: Selection, b: Selection): boolean {
  if (a.voteType !== b.voteType) return false;
  if (a.waypoints.length !== b.waypoints.length) return false;
  for (let i = 0; i < a.waypoints.length; i++) {
    const x = a.waypoints[i].coords;
    const y = b.waypoints[i].coords;
    if (x.lat !== y.lat || x.lng !== y.lng) return false;
  }
  return true;
}

const RouteContext = createContext<RouteContextValue | null>(null);

export function RouteProvider({ children }: { children: ReactNode }) {
  const theme = useTheme();

  // Station networks (e.g. ebikes) vote on single fixed points — never routes —
  // so there's no two-point routing regardless of how many points are set.
  const isStationNetwork = (getCurrentMap()?.network ?? "streets") !== "streets";

  // Monotonic id source for waypoints (stable React keys / drag identity).
  const nextIdRef = useRef(0);
  const makeId = useCallback(() => `wp-${++nextIdRef.current}`, []);

  // ── Canonical selection: the single source of truth ───────────────────────
  // Seeded synchronously from the URL so a deep link renders its points on the
  // first paint (and history entry 0 is correct). start/end/mids are DERIVED.
  const [selection, setSelectionRaw] = useState<Selection>(() => {
    const defaultPointType = theme.inputMode === "point" || isStationNetwork ? "point" : "route";
    const defaultVt = getDefaultVoteTypeForTheme(theme, defaultPointType);
    if (typeof window === "undefined") return { waypoints: [], voteType: defaultVt };
    const parsed = selectionFromParams(new URLSearchParams(window.location.search), {
      stationNetwork: isStationNetwork,
    });
    if (!parsed) return { waypoints: [], voteType: defaultVt };
    return {
      waypoints: parsed.waypoints.map((c) => ({
        coords: c,
        id: `wp-${++nextIdRef.current}`,
        address: null,
        voteEdgeId: null,
      })),
      voteType: parsed.voteType ?? defaultVt,
    };
  });

  // Mirror of `selection` for reading the latest value inside async callbacks /
  // event handlers without re-subscribing (the established ref pattern here).
  const selectionRef = useRef<Selection>(selection);
  // Mids of the current selection, read by the main effect after a commit.
  const ghostWaypointsRef = useRef<LatLng[]>(deriveMids(selection));

  // The explicit (legacy) waypoints feature — separate from the canonical mids.
  const [waypoints, setWaypoints] = useState<LatLng[]>([]);
  const [splitDesirePaths, setSplitDesirePaths] = useState<SplitDesirePath[]>([]);
  const [isCalculatingSplit, setIsCalculatingSplit] = useState(false);
  // Re-renders this context on any vote change (from here OR the proposal modal)
  // so derived "already voted" values recompute. See useVotesVersion.
  const votesVersion = useVotesVersion();
  const [voteDirection, setVoteDirectionState] = useState<VoteDirection>(1);
  const [activeTool, setActiveToolState] = useState<ActiveTool>("start");
  // One-shot: armed by the legend's start tool so the next map click MOVES the
  // start (keeping the end + waypoints) instead of wiping the path. Cleared by
  // any point placement (setStartPoint/setEndPoint) or clearPoints.
  const [startReplaceArmed, setStartReplaceArmed] = useState(false);

  // Snap + path resolvers (registered by GraphLayer). resolveVoteEdgeId turns a
  // clicked point into the edge a vote lands on; resolveTopLabelForPath ranks the
  // vote types on a path (for the vote-type fallback).
  const { resolveVoteEdgeId, resolveTopLabelForPath } = useGraphSnap();

  const setVoteDirection = useCallback((dir: VoteDirection) => {
    setVoteDirectionState(dir);
  }, []);

  // GraphLayer registers topology access here (it owns the graph + block index);
  // this context has none of its own. Null / unregistered → singleton fallback.
  const blockMaterializerRef = useRef<BlockMaterializer | null>(null);
  const setBlockMaterializer = useCallback((fn: BlockMaterializer | null) => {
    blockMaterializerRef.current = fn;
  }, []);

  const setActiveTool = useCallback((tool: ActiveTool) => {
    setActiveToolState(tool);
    // Switching away from the start tool drops a pending in-place move.
    if (tool !== "start") setStartReplaceArmed(false);
  }, []);

  const armStartReplace = useCallback(() => {
    setActiveToolState("start");
    setStartReplaceArmed(true);
  }, []);

  // Ref for click suppression (needs immediate effect, not async like state)
  const suppressNextClickRef = useRef(false);

  // Ref to track if we're handling point removal / a history restore (skip the main
  // effect's own recalc, since those paths recalculate explicitly).
  const handlingRemovalRef = useRef(false);

  // removePoint is defined below updateGhostWaypoint; this ref lets the latter
  // delegate to it (delete the dragged waypoint) when a drag collapses a segment.
  const removePointRef = useRef<(which: "start" | "end" | number) => void>(() => {});

  // Route version counter - increments when path changes, used to detect stale votes
  const routeVersionRef = useRef(0);

  // Split calculation version - increments on each calculateAllSegments call, used to discard stale responses
  const splitCalcVersionRef = useRef(0);

  // ── Selection history (in-app back/forward stack) ─────────────────────────
  const historyRef = useRef<Selection[]>([selection]);
  const cursorRef = useRef(0);
  const [cursor, setCursor] = useState(0);
  const [historyLen, setHistoryLen] = useState(1);
  // Task-debounced commit (setTimeout, deliberately NOT requestAnimationFrame): a
  // single user gesture (a click → its synchronous cascade of state updates)
  // settles into ONE history entry. rAF is paint-driven — it pauses for
  // background/occluded tabs and never fires in a display-less headless browser —
  // which would silently drop history entries (the back/forward control then never
  // appears). A macrotask fires regardless of rendering.
  const historyCommitRef = useRef(0);

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

  // ── Derived legacy shape (so every consumer keeps working unchanged) ──────
  const start = useMemo(() => deriveStart(selection), [selection]);
  const end = useMemo(() => deriveEnd(selection), [selection]);
  const ghostWaypoints = useMemo(() => deriveMids(selection), [selection]);
  const ghostWaypointIds = useMemo(() => deriveMidIds(selection), [selection]);

  // Compute point type based on whether both points are set
  const pointType: "route" | "point" =
    !isStationNetwork && start.coords && end.coords ? "route" : "point";

  // ============================================
  // Selection plumbing
  // ============================================
  const scheduleHistoryCommit = useCallback(() => {
    if (historyCommitRef.current) return;
    const run = () => {
      historyCommitRef.current = 0;
      const sel = selectionRef.current;
      const hist = historyRef.current;
      const idx = cursorRef.current;
      if (selectionsEqual(hist[idx], sel)) return; // nothing navigable changed
      const truncated = hist.slice(0, idx + 1);
      truncated.push(sel);
      historyRef.current = truncated;
      cursorRef.current = truncated.length - 1;
      setCursor(cursorRef.current);
      setHistoryLen(truncated.length);
    };
    historyCommitRef.current = setTimeout(run, 0) as unknown as number;
  }, []);

  /** Commit a new selection: update state + refs, and (unless history:false)
   *  schedule a coalesced history entry. The single mutation funnel. */
  const applySelection = useCallback(
    (next: Selection, opts?: { history?: boolean }) => {
      selectionRef.current = next;
      ghostWaypointsRef.current = deriveMids(next);
      setSelectionRaw(next);
      if (opts?.history !== false) scheduleHistoryCommit();
    },
    [scheduleHistoryCommit]
  );

  /** Patch a waypoint's reverse-geocoded address in place (no history entry). */
  const patchWaypointAddress = useCallback((coords: LatLng, address: string) => {
    const cur = selectionRef.current;
    const idx = cur.waypoints.findIndex(
      (w) => w.coords.lat === coords.lat && w.coords.lng === coords.lng
    );
    if (idx < 0 || cur.waypoints[idx].address === address) return;
    const wps = cur.waypoints.slice();
    wps[idx] = { ...wps[idx], address };
    const next = { ...cur, waypoints: wps };
    selectionRef.current = next;
    setSelectionRaw(next);
  }, []);

  const geocodeInto = useCallback(
    (coords: LatLng) => {
      fetch(`${CONFIG.apiUrl}/reverse-geocode?map=${getMapSlug()}&lat=${coords.lat}&lng=${coords.lng}`)
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => patchWaypointAddress(coords, data.address))
        .catch(() => {});
    },
    [patchWaypointAddress]
  );

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

    // Compute one segment per consecutive pair, INDEPENDENTLY. A single segment
    // that can't route (e.g. a waypoint snapped onto a proposed bike lane that
    // isn't in the routable network) must NOT discard the whole route — it falls
    // back to a straight connector. So we always return exactly points.length-1
    // segments and the route always renders.
    const fetchSegment = async (i: number): Promise<SplitDesirePath> => {
      const a = points[i], b = points[i + 1];
      const straight: SplitDesirePath = {
        id: `split-${i}`,
        segmentIndex: i,
        geometry: { type: "LineString", coordinates: [[a.lng, a.lat], [b.lng, b.lat]] },
        segments: [],
        edgeIds: [],
      };
      try {
        const resp = await fetch(`${CONFIG.apiUrl}/routes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start: [a.lat, a.lng],
            end: [b.lat, b.lng],
            waypoints: [],
            map: getMapSlug(),
          }),
          signal: controller.signal,
        });
        if (!resp.ok) return straight;
        const data = await resp.json();
        const geometry = data.route?.geometry;
        if (!geometry) return straight;
        return {
          id: `split-${i}`,
          segmentIndex: i,
          geometry,
          segments: data.desire_path_segments || [],
          edgeIds: data.edge_ids || [],
        };
      } catch {
        // Abort or network error: return the straight fallback. Stale aborted
        // batches are discarded by the caller's calcVersion check regardless.
        return straight;
      }
    };

    return Promise.all(points.slice(0, -1).map((_, i) => fetchSegment(i)));
  }, []);

  /** Recompute the split paths for [start, ...mids, end], discarding stale batches. */
  const runSplitCalc = useCallback(
    (startC: LatLng, endC: LatLng, mids: LatLng[]) => {
      setIsCalculatingSplit(true);
      splitCalcVersionRef.current++;
      const calcVersion = splitCalcVersionRef.current;
      calculateAllSegments([startC, ...mids, endC])
        .then((splitPaths) => {
          if (calcVersion !== splitCalcVersionRef.current) return;
          if (splitPaths.length === mids.length + 1) setSplitDesirePaths(splitPaths);
        })
        .catch(console.error)
        .finally(() => {
          if (calcVersion === splitCalcVersionRef.current) setIsCalculatingSplit(false);
        });
    },
    [calculateAllSegments]
  );

  // ============================================
  // Simple setters
  // ============================================
  // `voteEdgeId` pins the vote to a specific edge (a top-proposal selection) so
  // the banner votes on exactly what the modal does; omit it for a bare-coordinate
  // drop (plain click/drag/search), which re-snaps. See RoutePoint.voteEdgeId.
  const setStartPoint = useCallback(
    (coords: LatLng, address?: string, voteEdgeId?: number) => {
      setStartReplaceArmed(false); // consume any pending in-place move
      applySelection(selSetStart(selectionRef.current, { coords, address, voteEdgeId }, makeId));
      if (!address) geocodeInto(coords);
    },
    [applySelection, geocodeInto, makeId]
  );

  // Sync the resolved vote edge onto the existing start point WITHOUT moving it.
  // No-op unless a start is set and the edge actually changed — never disturbs the
  // coords/address (which would re-run snapping/geocode and loop) and never adds a
  // history entry (voteEdgeId is runtime sugar, not a navigable state).
  const setStartVoteEdgeId = useCallback((voteEdgeId: number | null) => {
    const cur = selectionRef.current;
    const w0 = cur.waypoints[0];
    if (!w0) return;
    if ((w0.voteEdgeId ?? null) === (voteEdgeId ?? null)) return;
    const wps = cur.waypoints.slice();
    wps[0] = { ...w0, voteEdgeId };
    const next = { ...cur, waypoints: wps };
    selectionRef.current = next;
    setSelectionRaw(next);
  }, []);

  const setEndPoint = useCallback(
    (coords: LatLng, address?: string) => {
      // No routing on station networks: a would-be "end" click just moves the
      // single selected station, so there's never a second endpoint to route to.
      if (isStationNetwork) {
        setStartPoint(coords, address);
        return;
      }
      setStartReplaceArmed(false); // consume any pending in-place move
      applySelection(selSetEnd(selectionRef.current, { coords, address }, makeId));
      if (!address) geocodeInto(coords);
    },
    [isStationNetwork, setStartPoint, applySelection, geocodeInto, makeId]
  );

  const addWaypoint = useCallback((coords: LatLng) => {
    setWaypoints((prev) => [...prev, coords]);
  }, []);

  const updateWaypoint = useCallback((index: number, coords: LatLng) => {
    setWaypoints((prev) => prev.map((wp, i) => (i === index ? coords : wp)));
  }, []);

  const clearWaypoints = useCallback(() => {
    setWaypoints([]);
  }, []);

  // Drop the mids (collapse to [start, end]) and clear the rendered split paths —
  // used in the click orchestration before a fresh placement. rAF history
  // coalescing folds this intermediate step into the gesture's final entry.
  const clearSplitPaths = useCallback(() => {
    const cur = selectionRef.current;
    if (cur.waypoints.length > 2) {
      applySelection({
        ...cur,
        waypoints: [cur.waypoints[0], cur.waypoints[cur.waypoints.length - 1]],
      });
    }
    setSplitDesirePaths([]);
  }, [applySelection]);

  const clearSuppressClick = useCallback(() => {
    suppressNextClickRef.current = false;
  }, []);

  const setSuppressClick = useCallback(() => {
    suppressNextClickRef.current = true;
  }, []);

  const suppressNextClick = useCallback(() => {
    return suppressNextClickRef.current;
  }, []);

  const setVoteType = useCallback(
    (newVoteType: string) => {
      applySelection(selSetVoteType(selectionRef.current, newVoteType));
    },
    [applySelection]
  );

  // ============================================
  // Clear all points
  // ============================================
  const clearPoints = useCallback(() => {
    applySelection(selClearWaypoints(selectionRef.current));
    setWaypoints([]);
    setSplitDesirePaths([]);
    routeVersionRef.current++;
    setActiveToolState("start");
    setStartReplaceArmed(false);
    clearRoute();
  }, [applySelection, clearRoute]);

  // ============================================
  // Insert ghost waypoint at segment (drag on path)
  // ============================================
  const insertWaypointAtSegment = useCallback(
    async (segmentIndex: number, position: LatLng) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;

      suppressNextClickRef.current = true;

      // Inserting onto one of the segment's own endpoints (e.g. snapping onto a
      // proposal that's already the adjacent waypoint) would make a zero-length
      // segment — skip the no-op insert entirely.
      const seq = [startC, ...deriveMids(cur), endC];
      const prev = seq[segmentIndex];
      const next = seq[segmentIndex + 1];
      if ((prev && sameLatLng(position, prev)) || (next && sameLatLng(position, next))) return;

      const nextSel = selInsertMid(cur, segmentIndex, { coords: position }, makeId);
      // Path changed - bump version so any in-flight vote isn't recorded
      routeVersionRef.current++;
      // Inserting a mid leaves start/end unchanged, so the main effect won't fire
      // and clobber the instant local split below.
      applySelection(nextSel);
      const newMids = deriveMids(nextSel);

      // Try client-side geometry splitting: if the insertion point is on the
      // existing route/segment geometry, split locally instead of server requests.
      const currentGeometry = splitDesirePaths.length > 0
        ? splitDesirePaths[segmentIndex]?.geometry
        : routeData?.geometry;

      if (currentGeometry) {
        const splitResult = splitGeometryAtPoint(currentGeometry, position);
        if (splitResult && splitResult.distanceMeters <= LOCAL_SPLIT_THRESHOLD_METERS) {
          // Point is on/near the route -- split locally (instant, no server round
          // trip — the same fast path normal mids use). Force both halves to meet
          // at the ACTUAL dropped coordinate (not the route projection), so a
          // waypoint snapped to an off-route proposal still connects exactly at the
          // proposal: the connector and drag-trail anchor are correct, and a mid
          // inserted next to it paths from the proposal, not from the projection.
          const conn: [number, number] = [position.lng, position.lat];
          const firstGeom: RouteGeometry = {
            type: "LineString",
            coordinates: [...splitResult.first.coordinates.slice(0, -1), conn],
          };
          const secondGeom: RouteGeometry = {
            type: "LineString",
            coordinates: [conn, ...splitResult.second.coordinates.slice(1)],
          };
          const existingPaths = splitDesirePaths.length > 0
            ? [...splitDesirePaths]
            : [];

          // The two halves inherit the source segment's edge ids so the route's
          // edge coverage survives an instant client-side split. The geometry is
          // unchanged, so the union of both halves equals the source — keep them
          // all on the first half (every consumer flattens the per-segment ids:
          // currentEdgeIds for the vote target and MapView's pathEdgeIds for the
          // "proposals the path passes through" highlight). Leaving them empty
          // dropped both — de-selecting the other on-path proposals when one was
          // upgraded, and emptying the vote target after a drag-split.
          const sourceEdgeIds = splitDesirePaths.length > 0
            ? (splitDesirePaths[segmentIndex]?.edgeIds ?? [])
            : (routeEdgeIds ?? []);

          const firstHalf: SplitDesirePath = {
            id: `split-${segmentIndex}`,
            segmentIndex,
            geometry: firstGeom,
            segments: segmentsFromGeometry(firstGeom),
            edgeIds: sourceEdgeIds,
          };
          const secondHalf: SplitDesirePath = {
            id: `split-${segmentIndex + 1}`,
            segmentIndex: segmentIndex + 1,
            geometry: secondGeom,
            segments: segmentsFromGeometry(secondGeom),
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
        const allPoints = [startC, ...newMids, endC];
        const splitPaths = await calculateAllSegments(allPoints);

        // Discard if a newer calculation was started
        if (calcVersion !== splitCalcVersionRef.current) return;

        if (splitPaths.length === newMids.length + 1) {
          setSplitDesirePaths(splitPaths);
        }
      } catch (err) {
        console.error("Failed to calculate split paths:", err);
      } finally {
        if (calcVersion === splitCalcVersionRef.current) {
          setIsCalculatingSplit(false);
        }
      }
    },
    [splitDesirePaths, routeData, routeEdgeIds, calculateAllSegments, applySelection, makeId]
  );

  // ============================================
  // Update ghost waypoint position (drag existing waypoint)
  // ============================================
  const updateGhostWaypoint = useCallback(
    async (index: number, position: LatLng) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;

      suppressNextClickRef.current = true;

      const mids = deriveMids(cur);
      if (index < 0 || index >= mids.length) return;

      // Dragged onto a sequence neighbor (e.g. snapped onto a proposal that's
      // already the adjacent waypoint): that segment would collapse to zero length
      // and break the recalc. Just delete the dragged waypoint instead.
      const prev = index === 0 ? startC : mids[index - 1];
      const next = index === mids.length - 1 ? endC : mids[index + 1];
      if ((prev && sameLatLng(position, prev)) || (next && sameLatLng(position, next))) {
        removePointRef.current(index);
        return;
      }

      const nextSel = selUpdateAt(cur, fullIndexOf(cur, index), position);
      // Path changed - bump version so any in-flight vote isn't recorded
      routeVersionRef.current++;
      // A mid move leaves start/end unchanged → main effect won't fire; recalc here.
      applySelection(nextSel);

      // Clear paths immediately - they'll reappear when calculation completes
      setSplitDesirePaths([]);
      setIsCalculatingSplit(true);

      // Track this calculation version to discard stale responses
      splitCalcVersionRef.current++;
      const calcVersion = splitCalcVersionRef.current;

      try {
        const newMids = deriveMids(nextSel);
        const allPoints = [startC, ...newMids, endC];
        const splitPaths = await calculateAllSegments(allPoints);

        // Discard if a newer calculation was started
        if (calcVersion !== splitCalcVersionRef.current) return;

        if (splitPaths.length === newMids.length + 1) {
          setSplitDesirePaths(splitPaths);
        }
      } catch (err) {
        console.error("Failed to recalculate split paths:", err);
      } finally {
        if (calcVersion === splitCalcVersionRef.current) {
          setIsCalculatingSplit(false);
        }
      }
    },
    [calculateAllSegments, applySelection]
  );

  // ============================================
  // Remove any point (unified logic)
  // All points conceptually: [start, ...mids, end]
  // removeAt rebalances automatically; we then drive the right recalculation.
  // ============================================
  const removePoint = useCallback(
    (which: "start" | "end" | number) => {
      const cur = selectionRef.current;
      const fullIdx = fullIndexOf(cur, which);
      if (fullIdx < 0 || fullIdx >= cur.waypoints.length) return;

      const prevStartC = deriveStart(cur).coords;
      const prevEndC = deriveEnd(cur).coords;

      const next = selRemoveAt(cur, fullIdx);
      const nextStartC = deriveStart(next).coords;
      const nextEndC = deriveEnd(next).coords;
      const mids = deriveMids(next);

      routeVersionRef.current++;

      // Clear paths immediately - they'll reappear when calculation completes
      clearRoute();
      setSplitDesirePaths([]);

      const startChanged = !coordsEqual(prevStartC, nextStartC);
      const endChanged = !coordsEqual(prevEndC, nextEndC);
      // When an endpoint changed, the main effect WILL fire on the derived coord
      // change — suppress its duplicate recalc; we recompute explicitly below.
      if (startChanged || endChanged) handlingRemovalRef.current = true;

      applySelection(next);

      // Refetch addresses for any endpoint promoted in from the middle.
      if (startChanged && nextStartC && !deriveStart(next).address) geocodeInto(nextStartC);
      if (endChanged && nextEndC && !deriveEnd(next).address) geocodeInto(nextEndC);

      // Recalculate the new structure (mirrors the old removePoint: it always
      // recomputes itself, and handlingRemovalRef keeps the main effect from
      // doubling up when an endpoint moved).
      if (nextStartC && nextEndC) {
        if (mids.length > 0) runSplitCalc(nextStartC, nextEndC, mids);
        else calculateRoute({ start: nextStartC, end: nextEndC, waypoints: [] });
      }
      // 0 or 1 points remaining: no route — already cleared above. activeTool
      // returns to "start" when nothing routable is left.
      if (next.waypoints.length <= 1) setActiveToolState("start");
    },
    [applySelection, geocodeInto, calculateRoute, clearRoute, runSplitCalc]
  );

  // Keep the ref current so updateGhostWaypoint (defined above) can delegate.
  useEffect(() => { removePointRef.current = removePoint; }, [removePoint]);

  // Convenience wrappers
  const clearStart = useCallback(() => removePoint("start"), [removePoint]);
  const clearEnd = useCallback(() => removePoint("end"), [removePoint]);
  const removeGhostWaypoint = useCallback((index: number) => removePoint(index), [removePoint]);

  // ============================================
  // History: back / forward
  // ============================================
  const stepTo = useCallback(
    (idx: number) => {
      const hist = historyRef.current;
      if (idx < 0 || idx >= hist.length || idx === cursorRef.current) return;
      const restored = hist[idx];
      const cur = selectionRef.current;

      const prevStartC = deriveStart(cur).coords;
      const prevEndC = deriveEnd(cur).coords;
      const nextStartC = deriveStart(restored).coords;
      const nextEndC = deriveEnd(restored).coords;
      const endpointsChanged =
        !coordsEqual(prevStartC, nextStartC) || !coordsEqual(prevEndC, nextEndC);

      cursorRef.current = idx;
      setCursor(idx);
      routeVersionRef.current++;
      setStartReplaceArmed(false);

      // Restore WITHOUT pushing a new history entry.
      if (endpointsChanged) handlingRemovalRef.current = true; // suppress main effect dup
      applySelection(restored, { history: false });

      // Recompute geometry explicitly so a mids-only difference also refreshes.
      clearRoute();
      setSplitDesirePaths([]);
      const mids = deriveMids(restored);
      if (nextStartC && nextEndC) {
        calculateRoute({ start: nextStartC, end: nextEndC, waypoints: [] });
        if (mids.length > 0) runSplitCalc(nextStartC, nextEndC, mids);
      }
      if (nextStartC && !deriveStart(restored).address) geocodeInto(nextStartC);
      if (nextEndC && !deriveEnd(restored).address) geocodeInto(nextEndC);
    },
    [applySelection, calculateRoute, clearRoute, runSplitCalc, geocodeInto]
  );

  const stepBack = useCallback(() => stepTo(cursorRef.current - 1), [stepTo]);
  const stepForward = useCallback(() => stepTo(cursorRef.current + 1), [stepTo]);
  const canStepBack = cursor > 0;
  const canStepForward = cursor < historyLen - 1;

  // Switching to a point/station map (no routing) while an endpoint is set: drop
  // the now-meaningless endpoint so we carry over only the start, as if a single
  // point had been selected. The selected start persists across the switch.
  useEffect(() => {
    if (isStationNetwork && end.coords) clearEnd();
  }, [isStationNetwork, end.coords, clearEnd]);

  // ============================================
  // URL mirror — keep the address bar in sync with the current selection.
  // ============================================
  // Writes ?w=…&vt=… via replaceState (never pushState — back/forward is the
  // in-app stack), and strips the camera + legacy point params consumed at load.
  // Other params (map, style, …) are preserved.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    for (const k of ["w", "vt", "slat", "slng", "elat", "elng", "z", "lat", "lng"]) {
      url.searchParams.delete(k);
    }
    for (const [k, v] of selectionToParams(selection)) url.searchParams.set(k, v);
    window.history.replaceState({}, "", url.toString());
  }, [selection]);

  // One-shot: reverse-geocode addresses for any URL-seeded start/end.
  const restoredFromUrlRef = useRef(false);
  useEffect(() => {
    if (restoredFromUrlRef.current) return;
    restoredFromUrlRef.current = true;
    const sel = selectionRef.current;
    const s = deriveStart(sel);
    if (s.coords && !s.address) geocodeInto(s.coords);
    const e = deriveEnd(sel);
    if (e.coords && !e.address) geocodeInto(e.coords);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Edge IDs that make up the current vote target. Split paths (dragged route)
  // take precedence, then the main route's edges, then a point cast resolves the
  // single clicked location to an edge via the shared snap path.
  const currentEdgeIds = useMemo(() => {
    if (splitDesirePaths.length > 0) return uniq(splitDesirePaths.flatMap((sp) => sp.edgeIds));
    if (routeEdgeIds && routeEdgeIds.length > 0) return uniq(routeEdgeIds);
    if (start.coords && !end.coords) {
      // A proposal selection pins the exact edge (start.voteEdgeId) so we vote on
      // the SAME edge the modal does. A bare coordinate re-snaps to the nearest
      // edge. Re-snapping the proposal's midpoint can land on a neighbour, which
      // is what used to desync the banner from the modal.
      if (start.voteEdgeId != null) return [start.voteEdgeId];
      const eid = resolveVoteEdgeId(start.coords.lat, start.coords.lng);
      return eid != null ? [eid] : [];
    }
    return [];
    // votesVersion forces a re-resolve of point votes after a cast settles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [splitDesirePaths, routeEdgeIds, start.coords, start.voteEdgeId, end.coords, resolveVoteEdgeId, votesVersion]);

  // ============================================
  // Effective vote type — the requested label resolved against the map.
  // ============================================
  // The Cast control + selector use THIS (never the raw selection.voteType), so a
  // deep link's stale/foreign label, or a point-only default after a route forms,
  // never produces a server-rejected cast. See resolveEffectiveVoteType.
  const effectiveVoteType = useMemo(() => {
    const map = getCurrentMap();
    const validLabels = mapVoteTypesForPointType(map, pointType).map((s) => s.label);
    const allowSuggestions = map?.allowSuggestions ?? true;
    const themeDefault = getDefaultVoteTypeForTheme(theme, pointType);
    const topLabelOnPath =
      currentEdgeIds.length > 0 ? resolveTopLabelForPath(currentEdgeIds) : null;
    return resolveEffectiveVoteType({
      requested: selection.voteType,
      validLabels,
      allowSuggestions,
      topLabelOnPath,
      themeDefault,
    });
    // votesVersion: the path's top label can change as votes land.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection.voteType, pointType, theme, currentEdgeIds, resolveTopLabelForPath, votesVersion]);

  // ============================================
  // Cast vote — the single path (route, dragged route, or point), directional.
  // Delegates to castVotes(), which is coverage-aware (only changes edges that
  // need it), reversible (re-casting the held direction removes), optimistic,
  // and self-healing. Re-casting the same direction toggles the votes off.
  // ============================================
  const castVote = useCallback((dirOverride?: VoteDirection) => {
    const edges = currentEdgeIds;
    if (edges.length === 0) return;
    const dir = dirOverride ?? voteDirection;
    if (dirOverride && dirOverride !== voteDirection) setVoteDirectionState(dirOverride);

    // Fire-and-forget, exactly like the in-map modal's castProposalVote. The
    // optimistic apply + local-store write inside castVotes() are synchronous and
    // bump votesVersion, so the "already voted" banner state updates instantly.
    // Awaiting the network POST here (while disabling the +/- buttons) was the
    // ~1s banner hang the modal never had; castVotes() self-heals (rolls back)
    // on failure, so nothing needs to await the result.
    castVotes({
      mode: theme.mode,
      edgeIds: edges,
      label: effectiveVoteType,
      direction: dir,
      // Blocks of the selection (castVotes falls back to singletons when the
      // materializer isn't registered yet — pre-topology, or no block layer).
      blocks: blockMaterializerRef.current?.(edges),
    });
  }, [currentEdgeIds, effectiveVoteType, voteDirection, theme.mode]);

  // Block coverage of the current target for a (voteType, direction): pressed
  // ("active") iff EVERY touched block already holds my vote in that direction
  // (docs three-layer-model §4.1) — the same rule the in-map modal buttons use.
  const isDirectionCast = useCallback(
    (dir: VoteDirection) => {
      void votesVersion; // recompute after each cast (from anywhere)
      if (currentEdgeIds.length === 0 || !effectiveVoteType) return false;
      const blocks =
        blockMaterializerRef.current?.(currentEdgeIds)
        ?? currentEdgeIds.map((e) => [e]);
      const cov = blockCoverage(theme.mode, blocks, effectiveVoteType);
      return voteButtonState(cov, dir) === "active";
    },
    [votesVersion, currentEdgeIds, effectiveVoteType, theme.mode]
  );

  // Whether the current target is fully cast in the selected direction.
  const hasVoted = isDirectionCast(voteDirection);

  // ============================================
  // Main calculation effect
  // Runs when start/end (the derived endpoints) or mode changes. Uses the current
  // mids (via ref) to decide what to calculate.
  // ============================================
  const startLat = start.coords?.lat ?? null;
  const startLng = start.coords?.lng ?? null;
  const endLat = end.coords?.lat ?? null;
  const endLng = end.coords?.lng ?? null;

  useEffect(() => {
    // Skip if a removal / history restore already did its own calculation.
    if (handlingRemovalRef.current) {
      handlingRemovalRef.current = false;
      return;
    }

    // Path changed - bump version so a stale in-flight vote isn't recorded
    routeVersionRef.current++;
    setWaypoints([]);

    // Need both start and end to calculate
    if (startLat === null || startLng === null || endLat === null || endLng === null) {
      // Clear everything - can't have a route without both endpoints
      clearRoute();
      setSplitDesirePaths([]);
      return;
    }

    // Clear existing paths immediately - they'll reappear when calculation completes
    clearRoute();
    setSplitDesirePaths([]);

    const startCoords = { lat: startLat, lng: startLng };
    const endCoords = { lat: endLat, lng: endLng };
    const currentMids = ghostWaypointsRef.current;

    // Always calculate the main route
    calculateRoute({ start: startCoords, end: endCoords, waypoints: [] });

    // If there are mids, also calculate split paths
    if (currentMids.length > 0) runSplitCalc(startCoords, endCoords, currentMids);
  }, [startLat, startLng, endLat, endLng, calculateRoute, clearRoute, runSplitCalc]);

  // ============================================
  // Recalculate when (legacy) explicit waypoints change
  // ============================================
  useEffect(() => {
    if (start.coords && end.coords && waypoints.length > 0) {
      routeVersionRef.current++;
      calculateRoute({ start: start.coords, end: end.coords, waypoints });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waypoints]);

  // ============================================
  // Smart default: clicking a top proposal selects its vote type, so the Cast
  // +/- control immediately acts on the proposal the user just clicked. The label
  // is a real vote type, so resolveEffectiveVoteType keeps it.
  // ============================================
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { label?: string; mode?: string };
      if (detail?.mode && detail.mode !== theme.mode) return;
      if (detail?.label) {
        applySelection(selSetVoteType(selectionRef.current, detail.label));
      }
    };
    window.addEventListener("proposal-vote-type", handler);
    return () => window.removeEventListener("proposal-vote-type", handler);
  }, [theme.mode, applySelection]);

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
      voteDirection,
      setVoteDirection,
      isDirectionCast,
      setBlockMaterializer,
      ghostWaypoints,
      ghostWaypointIds,
      splitDesirePaths,
      isCalculatingSplit,
      routeEdgeIds,
      voteType: effectiveVoteType,
      pointType,
      activeTool,
      startReplaceArmed,
      suppressNextClick,
      setStartPoint,
      setStartVoteEdgeId,
      setEndPoint,
      setActiveTool,
      armStartReplace,
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
      stepBack,
      stepForward,
      canStepBack,
      canStepForward,
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
      voteDirection,
      setVoteDirection,
      isDirectionCast,
      setBlockMaterializer,
      ghostWaypoints,
      ghostWaypointIds,
      splitDesirePaths,
      isCalculatingSplit,
      routeEdgeIds,
      effectiveVoteType,
      pointType,
      activeTool,
      startReplaceArmed,
      suppressNextClick,
      setStartPoint,
      setStartVoteEdgeId,
      setEndPoint,
      setActiveTool,
      armStartReplace,
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
      stepBack,
      stepForward,
      canStepBack,
      canStepForward,
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
