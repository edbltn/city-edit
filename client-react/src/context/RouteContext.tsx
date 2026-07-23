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
import { reverseGeocode } from "../utils/geocode";
import { derror } from "../utils/debugLog";
import { useTheme } from "./ThemeContext";
import { useGraphSnap } from "./GraphSnapContext";
import type { ForcedCorridor, Selection } from "../selection/types";
import {
  setStart as selSetStart,
  setEnd as selSetEnd,
  insertMid as selInsertMid,
  updateAt as selUpdateAt,
  removeAt as selRemoveAt,
  clearWaypoints as selClearWaypoints,
  setVoteType as selSetVoteType,
  setForcedCorridorAt as selSetForcedCorridorAt,
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

/** Corridor geometry for a segment the selection FLAGS as forcibly routed
 *  through a route proposal (SelWaypoint.forcedCorridor): the corridor's path as
 *  a GeoJSON [lng, lat] chain oriented a→b, plus its path edge ids. Registered
 *  by GraphLayer (which owns the proposals + topology). Resolution prefers the
 *  LIVE proposal by id (deep links carry only the id), then the flag's edge-id
 *  snapshot (stable under proposal churn). Null = can't resolve — route the
 *  segment normally. This is what makes a threaded RBTP follow its corridor
 *  VERBATIM instead of whatever OSRM picks between the anchors. */
export type CorridorSegmentResolver = (
  a: LatLng,
  b: LatLng,
  forced: ForcedCorridor,
) => { coordinates: [number, number][]; edgeIds: number[] } | null;

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
  /** Register (or clear) the anchors→corridor resolver. Set by GraphLayer once
   *  proposals + topology exist; until then every segment routes via OSRM. */
  setCorridorSegmentResolver: (fn: CorridorSegmentResolver | null) => void;
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
  /** Replace the mid at `index` with TWO waypoints (a route proposal's corridor
   *  anchors, already in insertion order) as ONE selection change + recalc —
   *  dropping a mid onto a route-proposal diamond threads the route through the
   *  whole corridor, stamping the pair's forced-corridor flag. An anchor that
   *  coincides with the neighboring waypoint is skipped (a zero-length segment
   *  breaks the recalc). */
  replaceGhostWaypointWithPair: (
    index: number,
    pair: [LatLng, LatLng],
    corridor: ForcedCorridor
  ) => void;
  /** Insert TWO waypoints into segment `segmentIndex` (corridor anchors, in
   *  order) — the pair analogue of insertWaypointAtSegment, for a path drag
   *  dropped onto a route-proposal diamond. Stamps the forced-corridor flag. */
  insertWaypointPairAtSegment: (
    segmentIndex: number,
    pair: [LatLng, LatLng],
    corridor: ForcedCorridor
  ) => void;
  /** Dropping the START onto a route-proposal diamond: the start becomes
   *  `pair[0]`, `pair[1]` joins as the first mid (spec: SE + drop S on AZ →
   *  AZ…E or ZA…E), and the pair's segment is flagged as forcibly routed
   *  through the proposal. One atomic selection change + recalc. */
  replaceStartWithPair: (pair: [LatLng, LatLng], corridor: ForcedCorridor) => void;
  /** Dropping the END onto a route-proposal diamond: `pair[0]` joins as the
   *  last mid, the end becomes `pair[1]` (spec: SE + drop E on AZ → S…AZ or
   *  S…ZA), flagged as forced. One atomic selection change + recalc. */
  replaceEndWithPair: (pair: [LatLng, LatLng], corridor: ForcedCorridor) => void;
  /** Clicking a route-proposal diamond with no route to thread into: the corridor
   *  BECOMES the selection — [anchorA, anchorB] with the forced flag stamped. */
  selectCorridor: (pair: [LatLng, LatLng], corridor: ForcedCorridor) => void;
  /** GraphLayer signals that the live route-proposal set changed. If a forced
   *  segment previously failed to resolve (deep link restored before proposals
   *  computed, or the proposal was churned away and came back), recalc so the
   *  corridor snaps into place. */
  notifyCorridorsChanged: () => void;
  /** Remove every waypoint within a few meters of any of `coords` as ONE
   *  selection change + recalc — the [×] on a selected route-proposal diamond
   *  pulls both corridor anchors back out together. */
  removeWaypointsNear: (coords: LatLng[]) => void;
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

/** Two selections represent the SAME navigable state — same ordered coordinates,
 *  same forced-corridor flags, and same requested vote type. Addresses / ids /
 *  voteEdgeId (runtime sugar) are ignored, so an async reverse-geocode or a pinned
 *  edge never spawns a spurious history entry. Forcing IS navigable (it changes
 *  the route), so re-threading the same coords through a proposal still records. */
function selectionsEqual(a: Selection, b: Selection): boolean {
  if (a.voteType !== b.voteType) return false;
  if (a.waypoints.length !== b.waypoints.length) return false;
  for (let i = 0; i < a.waypoints.length; i++) {
    const x = a.waypoints[i];
    const y = b.waypoints[i];
    if (x.coords.lat !== y.coords.lat || x.coords.lng !== y.coords.lng) return false;
    if ((x.forcedCorridor?.proposalId ?? null) !== (y.forcedCorridor?.proposalId ?? null)) {
      return false;
    }
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
      waypoints: parsed.waypoints.map((pw, i) => ({
        coords: pw.coords,
        id: `wp-${++nextIdRef.current}`,
        address: null,
        voteEdgeId: null,
        // A deep link carries only the proposal id (no edge snapshot); the
        // corridor resolver re-resolves the live proposal once it computes. A
        // marker on the last waypoint is meaningless (no segment leaves it).
        forcedCorridor:
          pw.forcedProposalId && i < parsed.waypoints.length - 1
            ? { proposalId: pw.forcedProposalId }
            : null,
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

  // GraphLayer's anchors→corridor resolver (same registration pattern). Every
  // segment calculation consults it first, so any consecutive waypoint pair
  // that IS a route proposal's two anchors routes through the corridor verbatim.
  const corridorResolverRef = useRef<CorridorSegmentResolver | null>(null);
  const setCorridorSegmentResolver = useCallback((fn: CorridorSegmentResolver | null) => {
    corridorResolverRef.current = fn;
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
      reverseGeocode(coords.lat, coords.lng).then(({ address }) => {
        if (address) patchWaypointAddress(coords, address);
      });
    },
    [patchWaypointAddress]
  );

  // ============================================
  // Helper: Calculate all route segments for split paths
  // ============================================
  const splitAbortRef = useRef<AbortController | null>(null);

  // True when a FLAGGED segment couldn't resolve its corridor (resolver not
  // registered yet / proposal not computed yet / retired with no snapshot) and
  // fell back to OSRM. notifyCorridorsChanged() uses it to recalc exactly once
  // when the proposal set changes — a deep-linked forced route snaps onto its
  // corridor as soon as proposals arrive, without polling.
  const unresolvedForcedRef = useRef(false);

  // The corridor-verbatim SplitDesirePath for consecutive waypoints a→b when the
  // selection FLAGS that segment as forcibly routed through a route proposal
  // (SelWaypoint.forcedCorridor on the leading point — `segmentIndex` IS the
  // leading full index in [start, ...mids, end]). Unflagged segments route
  // normally; a flagged segment that can't resolve falls back to OSRM (graceful
  // degradation) and is remembered as unresolved. Built locally from the
  // proposal/snapshot geometry — no OSRM round-trip, and the segment's edgeIds
  // are the corridor's own path edges, so the heat/hover highlight, block
  // coverage, and the vote target all match what's selected.
  const corridorSegmentFor = useCallback(
    (a: LatLng, b: LatLng, segmentIndex: number): SplitDesirePath | null => {
      const forced = selectionRef.current.waypoints[segmentIndex]?.forcedCorridor ?? null;
      if (!forced) return null;
      const c = corridorResolverRef.current?.(a, b, forced);
      if (!c || c.coordinates.length < 2) {
        unresolvedForcedRef.current = true;
        return null;
      }
      const geometry: RouteGeometry = { type: "LineString", coordinates: c.coordinates };
      return {
        id: `split-${segmentIndex}`,
        segmentIndex,
        geometry,
        segments: segmentsFromGeometry(geometry),
        edgeIds: c.edgeIds,
      };
    },
    []
  );

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
      // A segment flagged as forced routes through its proposal's corridor
      // VERBATIM — locally, before (and instead of) any OSRM request.
      const corridor = corridorSegmentFor(a, b, i);
      if (corridor) return corridor;
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
  }, [corridorSegmentFor]);

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
        .catch((err) => derror("proposals", "split path calculation failed:", err))
        .finally(() => {
          if (calcVersion === splitCalcVersionRef.current) setIsCalculatingSplit(false);
        });
    },
    [calculateAllSegments]
  );

  // Route start→end directly (no mids): through the corridor VERBATIM when the
  // start's forced-corridor flag says so — represented as a single split
  // segment, the same shape a mids-only recalc leaves (routeData stays null) —
  // else the normal OSRM route. Every "route the two endpoints" call site
  // funnels through here so a corridor selection always traces its proposal.
  const routeDirect = useCallback(
    (startC: LatLng, endC: LatLng) => {
      const corridor = corridorSegmentFor(startC, endC, 0);
      if (corridor) {
        // Invalidate any in-flight split batch so it can't clobber the corridor.
        splitCalcVersionRef.current++;
        setSplitDesirePaths([corridor]);
        setIsCalculatingSplit(false);
        return;
      }
      calculateRoute({ start: startC, end: endC, waypoints: [] });
    },
    [corridorSegmentFor, calculateRoute]
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
      // Collapsing to [start, end] drops the mids — a forced corridor departing
      // the start pointed at a mid that no longer exists.
      const first = cur.waypoints[0];
      applySelection({
        ...cur,
        waypoints: [
          first.forcedCorridor ? { ...first, forcedCorridor: null } : first,
          cur.waypoints[cur.waypoints.length - 1],
        ],
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

      // Splitting a FORCED segment un-forces it (spec: a mid introduced between a
      // corridor's anchors reverts the pair to computed routing). selInsertMid
      // clears the flag; remember it so we skip the local geometry split below —
      // splitting the corridor polyline in place would silently KEEP the forced
      // shape the user just asked to break. Full recalc instead.
      const wasForced = !!cur.waypoints[segmentIndex]?.forcedCorridor;

      const nextSel = selInsertMid(cur, segmentIndex, { coords: position }, makeId);
      // Path changed - bump version so any in-flight vote isn't recorded
      routeVersionRef.current++;
      // Inserting a mid leaves start/end unchanged, so the main effect won't fire
      // and clobber the instant local split below.
      applySelection(nextSel);
      const newMids = deriveMids(nextSel);

      // Try client-side geometry splitting: if the insertion point is on the
      // existing route/segment geometry, split locally instead of server requests.
      const currentGeometry = wasForced
        ? undefined
        : splitDesirePaths.length > 0
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
        derror("proposals", "Failed to calculate split paths:", err);
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
        derror("proposals", "Failed to recalculate split paths:", err);
      } finally {
        if (calcVersion === splitCalcVersionRef.current) {
          setIsCalculatingSplit(false);
        }
      }
    },
    [calculateAllSegments, applySelection]
  );

  // ============================================
  // Corridor (waypoint-pair) operations — dropping onto a route-proposal
  // diamond threads the route through the proposal's TWO anchors at once.
  // ============================================
  const replaceGhostWaypointWithPair = useCallback(
    (index: number, pair: [LatLng, LatLng], corridor: ForcedCorridor) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;
      const mids = deriveMids(cur);
      if (index < 0 || index >= mids.length) return;

      suppressNextClickRef.current = true;

      // An anchor that coincides with the neighboring waypoint (the corridor
      // starts/ends AT an existing point) is skipped, not doubled — a
      // zero-length segment breaks the recalc (same rule as updateGhostWaypoint).
      const fullIdx = fullIndexOf(cur, index);
      const prevC = cur.waypoints[fullIdx - 1].coords;
      const nextC = cur.waypoints[fullIdx + 1].coords;
      const keepFirst = !sameLatLng(pair[0], prevC);
      const keepSecond = !sameLatLng(pair[1], nextC);

      let nextSel: Selection;
      let leadIdx: number; // full index of the forced pair's LEADING point
      if (!keepFirst && !keepSecond) {
        // Both anchors already ARE the neighbors — the mid is redundant; drop it
        // and (re)force the now-adjacent pair.
        nextSel = selRemoveAt(cur, fullIdx);
        leadIdx = fullIdx - 1;
      } else if (keepFirst && keepSecond) {
        nextSel = selInsertMid(selUpdateAt(cur, fullIdx, pair[0]), fullIdx, { coords: pair[1] }, makeId);
        leadIdx = fullIdx;
      } else if (keepFirst) {
        // pair[1] IS the next waypoint: the mid becomes the first anchor and the
        // forced segment runs mid→next.
        nextSel = selUpdateAt(cur, fullIdx, pair[0]);
        leadIdx = fullIdx;
      } else {
        // pair[0] IS the previous waypoint: the mid becomes the second anchor and
        // the forced segment runs prev→mid.
        nextSel = selUpdateAt(cur, fullIdx, pair[1]);
        leadIdx = fullIdx - 1;
      }
      nextSel = selSetForcedCorridorAt(nextSel, leadIdx, corridor);

      routeVersionRef.current++;
      applySelection(nextSel);
      setSplitDesirePaths([]);
      const newMids = deriveMids(nextSel);
      if (newMids.length > 0) runSplitCalc(startC, endC, newMids);
      else routeDirect(startC, endC);
    },
    [applySelection, runSplitCalc, routeDirect, makeId]
  );

  const insertWaypointPairAtSegment = useCallback(
    (segmentIndex: number, pair: [LatLng, LatLng], corridor: ForcedCorridor) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;

      suppressNextClickRef.current = true;

      const seq = [startC, ...deriveMids(cur), endC];
      const prevC = seq[segmentIndex];
      const nextC = seq[segmentIndex + 1];
      if (!prevC || !nextC) return;
      const keepFirst = !sameLatLng(pair[0], prevC);
      const keepSecond = !sameLatLng(pair[1], nextC);

      let nextSel: Selection;
      let leadIdx: number; // full index of the forced pair's LEADING point
      if (!keepFirst && !keepSecond) {
        // The segment's endpoints already ARE the anchors — nothing to insert;
        // just (re)force the pair (a drop on the diamond after a break re-threads).
        nextSel = cur;
        leadIdx = segmentIndex;
      } else if (keepFirst && keepSecond) {
        nextSel = selInsertMid(
          selInsertMid(cur, segmentIndex, { coords: pair[0] }, makeId),
          segmentIndex + 1, { coords: pair[1] }, makeId
        );
        leadIdx = segmentIndex + 1;
      } else if (keepFirst) {
        // pair[1] IS the segment's far end: insert only the first anchor; the
        // forced segment runs anchor→next.
        nextSel = selInsertMid(cur, segmentIndex, { coords: pair[0] }, makeId);
        leadIdx = segmentIndex + 1;
      } else {
        // pair[0] IS the segment's near end: insert only the second anchor; the
        // forced segment runs prev→anchor.
        nextSel = selInsertMid(cur, segmentIndex, { coords: pair[1] }, makeId);
        leadIdx = segmentIndex;
      }
      nextSel = selSetForcedCorridorAt(nextSel, leadIdx, corridor);
      if (nextSel === cur) return;
      routeVersionRef.current++;
      applySelection(nextSel);
      setSplitDesirePaths([]);
      runSplitCalc(startC, endC, deriveMids(nextSel));
    },
    [applySelection, runSplitCalc, makeId]
  );

  // Dropping an ENDPOINT onto a route-proposal diamond threads the corridor at
  // that end of the chain: the dropped endpoint lands on the far anchor, the
  // near anchor joins as a mid, and the pair is flagged forced. Both ops mirror
  // removePoint's recalc choreography — the endpoint coordinate changes, so the
  // main effect WOULD fire; handlingRemovalRef suppresses its duplicate recalc
  // and we recompute explicitly (one atomic selection change, one recalc).
  const replaceEndWithPair = useCallback(
    (pair: [LatLng, LatLng], corridor: ForcedCorridor) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;

      suppressNextClickRef.current = true;

      const n = cur.waypoints.length;
      const prevC = cur.waypoints[n - 2].coords; // the point before the end
      // Re-drop of an already-threaded corridor in the same orientation: no-op.
      if (
        cur.waypoints[n - 2].forcedCorridor?.proposalId === corridor.proposalId &&
        sameLatLng(prevC, pair[0]) && sameLatLng(endC, pair[1])
      ) return;

      let nextSel = selSetEnd(cur, { coords: pair[1] }, makeId);
      let leadIdx = n - 2;
      if (!sameLatLng(pair[0], prevC)) {
        // Near anchor joins as the last mid (full index n-1, before the end).
        nextSel = selInsertMid(nextSel, n - 2, { coords: pair[0] }, makeId);
        leadIdx = n - 1;
      }
      nextSel = selSetForcedCorridorAt(nextSel, leadIdx, corridor);

      routeVersionRef.current++;
      clearRoute();
      setSplitDesirePaths([]);
      const endChanged = !sameLatLng(endC, pair[1]);
      if (endChanged) handlingRemovalRef.current = true;
      applySelection(nextSel);
      if (endChanged) geocodeInto(pair[1]);

      const mids = deriveMids(nextSel);
      if (mids.length > 0) runSplitCalc(startC, deriveEnd(nextSel).coords!, mids);
      else routeDirect(startC, deriveEnd(nextSel).coords!);
    },
    [applySelection, geocodeInto, clearRoute, runSplitCalc, routeDirect, makeId]
  );

  const replaceStartWithPair = useCallback(
    (pair: [LatLng, LatLng], corridor: ForcedCorridor) => {
      const cur = selectionRef.current;
      const startC = deriveStart(cur).coords;
      const endC = deriveEnd(cur).coords;
      if (!startC || !endC) return;

      suppressNextClickRef.current = true;

      const nextC = cur.waypoints[1].coords; // the point after the start
      // Re-drop of an already-threaded corridor in the same orientation: no-op.
      if (
        cur.waypoints[0].forcedCorridor?.proposalId === corridor.proposalId &&
        sameLatLng(startC, pair[0]) && sameLatLng(nextC, pair[1])
      ) return;

      let nextSel = selSetStart(cur, { coords: pair[0], forcedCorridor: null }, makeId);
      if (!sameLatLng(pair[1], nextC)) {
        // Far anchor joins as the first mid (full index 1, after the start).
        nextSel = selInsertMid(nextSel, 0, { coords: pair[1] }, makeId);
      }
      nextSel = selSetForcedCorridorAt(nextSel, 0, corridor);

      routeVersionRef.current++;
      clearRoute();
      setSplitDesirePaths([]);
      const startChanged = !sameLatLng(startC, pair[0]);
      if (startChanged) handlingRemovalRef.current = true;
      applySelection(nextSel);
      if (startChanged) geocodeInto(pair[0]);

      const mids = deriveMids(nextSel);
      if (mids.length > 0) runSplitCalc(deriveStart(nextSel).coords!, endC, mids);
      else routeDirect(deriveStart(nextSel).coords!, endC);
    },
    [applySelection, geocodeInto, clearRoute, runSplitCalc, routeDirect, makeId]
  );

  // Clicking a diamond with no route to thread into: the corridor becomes the
  // WHOLE selection — start/end seeded at its anchors with the forced flag on
  // the leading point. Replaces the old clearPoints+setStart+setEnd dance so the
  // flag, the recalc, and the history entry land as one atomic change.
  const selectCorridor = useCallback(
    (pair: [LatLng, LatLng], corridor: ForcedCorridor) => {
      const cur = selectionRef.current;
      const nextSel: Selection = {
        ...cur,
        waypoints: [
          { coords: pair[0], address: null, voteEdgeId: null, forcedCorridor: corridor, id: makeId() },
          { coords: pair[1], address: null, voteEdgeId: null, forcedCorridor: null, id: makeId() },
        ],
      };
      routeVersionRef.current++;
      setActiveToolState("start");
      setStartReplaceArmed(false);
      clearRoute();
      setSplitDesirePaths([]);
      // Recalc explicitly (the endpoints usually change → suppress the main
      // effect's duplicate; when they don't change it wouldn't fire at all).
      handlingRemovalRef.current = true;
      applySelection(nextSel);
      geocodeInto(pair[0]);
      geocodeInto(pair[1]);
      routeDirect(pair[0], pair[1]);
    },
    [applySelection, geocodeInto, clearRoute, routeDirect, makeId]
  );

  // GraphLayer pokes this when its live proposal set changes: if a FLAGGED
  // segment previously fell back to OSRM because its corridor couldn't resolve
  // (deep link restored before proposals computed, or churn), recalc once so
  // the forced geometry snaps in.
  const notifyCorridorsChanged = useCallback(() => {
    if (!unresolvedForcedRef.current) return;
    const sel = selectionRef.current;
    if (!sel.waypoints.some((w) => w.forcedCorridor)) {
      unresolvedForcedRef.current = false;
      return;
    }
    const startC = deriveStart(sel).coords;
    const endC = deriveEnd(sel).coords;
    if (!startC || !endC) return;
    unresolvedForcedRef.current = false;
    const mids = deriveMids(sel);
    if (mids.length > 0) runSplitCalc(startC, endC, mids);
    else routeDirect(startC, endC);
  }, [runSplitCalc, routeDirect]);

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
        else routeDirect(nextStartC, nextEndC);
      }
      // 0 or 1 points remaining: no route — already cleared above. activeTool
      // returns to "start" when nothing routable is left.
      if (next.waypoints.length <= 1) setActiveToolState("start");
    },
    [applySelection, geocodeInto, routeDirect, clearRoute, runSplitCalc]
  );

  // Keep the ref current so updateGhostWaypoint (defined above) can delegate.
  useEffect(() => { removePointRef.current = removePoint; }, [removePoint]);

  // Convenience wrappers
  const clearStart = useCallback(() => removePoint("start"), [removePoint]);
  const clearEnd = useCallback(() => removePoint("end"), [removePoint]);
  const removeGhostWaypoint = useCallback((index: number) => removePoint(index), [removePoint]);

  // Meters within which a waypoint counts as sitting ON a given coordinate.
  // Corridor anchors are inserted at the proposal's exact anchor coords and only
  // move if the user edits them, so a tight match is the right identity test
  // (mirrors GraphLayer's anchorsAreWaypoints threshold).
  const WAYPOINT_NEAR_M = 5;

  // Remove every waypoint near any of `coords` in ONE atomic selection change +
  // recalc — removing a corridor's two anchors via removePoint twice would fire
  // two recalcs and shift indices between them. Mirrors removePoint's recalc
  // choreography exactly.
  const removeWaypointsNear = useCallback(
    (coords: LatLng[]) => {
      const cur = selectionRef.current;
      const near = (w: LatLng, c: LatLng) =>
        haversineMeters([w.lng, w.lat], [c.lng, c.lat]) < WAYPOINT_NEAR_M;
      // Dropping a waypoint breaks the forced corridor ARRIVING at it — the flag
      // lives on the last kept waypoint before it (the removed point's own flag
      // leaves with it). Mirrors selRemoveAt's rule for the multi-remove case.
      const kept: typeof cur.waypoints = [];
      for (const w of cur.waypoints) {
        if (coords.some((c) => near(w.coords, c))) {
          const p = kept[kept.length - 1];
          if (p?.forcedCorridor) kept[kept.length - 1] = { ...p, forcedCorridor: null };
        } else {
          kept.push(w);
        }
      }
      if (kept.length === cur.waypoints.length) return;

      const prevStartC = deriveStart(cur).coords;
      const prevEndC = deriveEnd(cur).coords;
      const next: Selection = { ...cur, waypoints: kept };
      const nextStartC = deriveStart(next).coords;
      const nextEndC = deriveEnd(next).coords;
      const mids = deriveMids(next);

      routeVersionRef.current++;
      clearRoute();
      setSplitDesirePaths([]);

      const startChanged = !coordsEqual(prevStartC, nextStartC);
      const endChanged = !coordsEqual(prevEndC, nextEndC);
      if (startChanged || endChanged) handlingRemovalRef.current = true;

      applySelection(next);

      if (startChanged && nextStartC && !deriveStart(next).address) geocodeInto(nextStartC);
      if (endChanged && nextEndC && !deriveEnd(next).address) geocodeInto(nextEndC);

      if (nextStartC && nextEndC) {
        if (mids.length > 0) runSplitCalc(nextStartC, nextEndC, mids);
        else routeDirect(nextStartC, nextEndC);
      }
      if (next.waypoints.length <= 1) setActiveToolState("start");
    },
    [applySelection, geocodeInto, routeDirect, clearRoute, runSplitCalc]
  );

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
        if (mids.length > 0) runSplitCalc(nextStartC, nextEndC, mids);
        else routeDirect(nextStartC, nextEndC);
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
      // Selection kind — lets the server record whether a brand-new suggestion
      // label was proposed on a route or a point (see vote_types.point_type).
      pointType,
    });
  }, [currentEdgeIds, effectiveVoteType, voteDirection, theme.mode, pointType]);

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

    // Mids → split paths per pair (each corridor-aware); no mids → the direct
    // route, through the corridor verbatim when start/end are its anchors.
    if (currentMids.length > 0) {
      calculateRoute({ start: startCoords, end: endCoords, waypoints: [] });
      runSplitCalc(startCoords, endCoords, currentMids);
    } else {
      routeDirect(startCoords, endCoords);
    }
  }, [startLat, startLng, endLat, endLng, calculateRoute, routeDirect, clearRoute, runSplitCalc]);

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
      setCorridorSegmentResolver,
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
      replaceGhostWaypointWithPair,
      insertWaypointPairAtSegment,
      replaceStartWithPair,
      replaceEndWithPair,
      selectCorridor,
      notifyCorridorsChanged,
      removeWaypointsNear,
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
      setCorridorSegmentResolver,
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
      replaceGhostWaypointWithPair,
      insertWaypointPairAtSegment,
      replaceStartWithPair,
      replaceEndWithPair,
      selectCorridor,
      notifyCorridorsChanged,
      removeWaypointsNear,
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
