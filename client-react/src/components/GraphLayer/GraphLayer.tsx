/**
 * Graph Layer - OSM Network Vote Visualization
 *
 * Canvas-based renderer for edges/nodes with vote counts from /api/graph.
 * Edges glow blue based on vote intensity (log-scaled).
 * Hovering an edge or node highlights it and shows a tooltip with:
 *   - Street name (or reverse-geocoded address)
 *   - Vote count
 *   - Top 3 vote types
 * Clears on zoom, redraws on zoom end and pan.
 */

import { useEffect, useRef, useCallback, useState } from "react";
import { useMap } from "react-leaflet";
import { createPortal } from "react-dom";
import L from "leaflet";
import { CONFIG } from "../../config";
import { useWebSocketContext } from "../../context/WebSocketContext";
import { useGraphSnap, useTheme } from "../../context";
import type { GraphData } from "../../types";

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

/** Distance from point (px,py) to line segment (ax,ay)-(bx,by), in pixels. */
function pointToSegmentDist(
  px: number, py: number,
  ax: number, ay: number,
  bx: number, by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) {
    const ex = px - ax;
    const ey = py - ay;
    return Math.sqrt(ex * ex + ey * ey);
  }
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx - px;
  const cy = ay + t * dy - py;
  return Math.sqrt(cx * cx + cy * cy);
}

/** Loop-based max to avoid stack overflow with large arrays. */
function arrayMax(arr: number[]): number {
  let max = 0;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] > max) max = arr[i];
  }
  return max;
}

// ---------------------------------------------------------------------------
// Heatmap color stops — flame cross-section
// ---------------------------------------------------------------------------
// Each pass uses a different color and width so the gradient runs ACROSS the
// stroke (deep-red halo on the outside, white-hot core on the inside) rather
// than ALONG it. Combined with `globalCompositeOperation = "lighter"` this
// gives Strava-style natural intersection brightening: when edges cross, RGB
// channels add and the apparent hue shifts toward yellow→white as expected.

const HEAT_HALO = "rgb(180, 50, 30)";   // deep red — outer glow
const HEAT_WARM = "rgb(255, 110, 30)";  // warm orange — main "heat"
const HEAT_HOT = "rgb(255, 210, 90)";   // bright yellow — hot core
const HEAT_PEAK = "rgb(255, 240, 210)"; // pale warm white — peak intensity

// ---------------------------------------------------------------------------
// Reverse-geocode cache (module-level, survives re-renders)
// ---------------------------------------------------------------------------

const geocodeCache = new Map<string, string | null>();
const geocodeInFlight = new Set<string>();

function cacheKey(lat: number, lng: number): string {
  return `${lat.toFixed(5)},${lng.toFixed(5)}`;
}

function formatLatLng(lat: number, lng: number): string {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}

/**
 * Return cached address, or a lat-lon placeholder while fetching.
 * Calls onResolved() when a fetch completes so the caller can re-render.
 * Does NOT cache failures so they can be retried.
 */
function resolveAddress(
  lat: number,
  lng: number,
  onResolved: () => void,
): string {
  const key = cacheKey(lat, lng);
  const cached = geocodeCache.get(key);
  if (cached !== undefined) return cached || formatLatLng(lat, lng);

  // Not cached — show placeholder and fire async fetch
  if (!geocodeInFlight.has(key)) {
    geocodeInFlight.add(key);
    fetch(`${CONFIG.apiUrl}/reverse-geocode?lat=${lat}&lng=${lng}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        geocodeCache.set(key, data.address || null);
        onResolved();
      })
      .catch(() => {
        // Don't cache failures — allow retry on next hover
      })
      .finally(() => {
        geocodeInFlight.delete(key);
      });
  }
  return formatLatLng(lat, lng);
}


// ---------------------------------------------------------------------------
// Constants & hit-testing
// ---------------------------------------------------------------------------

// Unified radii — used for hover, snap, pinned highlight, and tooltip lookup
const SNAP_EDGE_PX = 4;   // hit-test radius for edges
const SNAP_NODE_PX = 3;   // node priority radius (wins over edges when very close)

// Highlight ring dimensions — matched to the desire path SVG filter so hover
// and pinned highlights look identical to the selected path:
//   - Edge ring: 7px wide stroke with a 4px hole = 1.5px white border each side
//   - Node ring: 5px outer radius with 3.5px hole = 1.5px white border
//   - Interior alpha: 0.12 (matches feColorMatrix in RouteLayer)
const HIGHLIGHT_RING_WIDTH = 7;
const HIGHLIGHT_INNER_WIDTH = 4;
const HIGHLIGHT_NODE_OUTER_R = 5;
const HIGHLIGHT_NODE_INNER_R = 3.5;
const HIGHLIGHT_INTERIOR_ALPHA = 0.12;

// Hover target type
interface HoverEdge { kind: "edge"; index: number }
interface HoverNode { kind: "node"; index: number }
type HoverTarget = HoverEdge | HoverNode;

interface HitResult {
  target: HoverTarget;
  snapLat: number;
  snapLng: number;
}

/**
 * Unified hit-test: find the nearest node or edge within snap radius.
 * Nodes within SNAP_NODE_PX win; otherwise edges within SNAP_EDGE_PX.
 * Returns the target and projected snap position.
 */
function hitTest(
  data: GraphData,
  map: L.Map,
  px: number, py: number,
  lat: number, lng: number
): HitResult | null {
  // 1. Nodes — small radius, highest priority
  let bestNode: number | null = null;
  let bestNodeDist = SNAP_NODE_PX;
  for (let i = 0; i < data.nodes.length; i++) {
    const node = data.nodes[i];
    const pt = map.latLngToContainerPoint([node[0], node[1]]);
    const dist = Math.sqrt((px - pt.x) ** 2 + (py - pt.y) ** 2);
    if (dist < bestNodeDist) {
      bestNodeDist = dist;
      bestNode = i;
    }
  }
  if (bestNode !== null) {
    const n = data.nodes[bestNode];
    return { target: { kind: "node", index: bestNode }, snapLat: n[0], snapLng: n[1] };
  }

  // 2. Edges — project onto segment for snap position
  let bestEdge: number | null = null;
  let bestEdgeDist = SNAP_EDGE_PX;
  for (let i = 0; i < data.edges.length; i++) {
    const [fromIdx, toIdx] = data.edges[i];
    const fromPt = map.latLngToContainerPoint([data.nodes[fromIdx][0], data.nodes[fromIdx][1]]);
    const toPt = map.latLngToContainerPoint([data.nodes[toIdx][0], data.nodes[toIdx][1]]);
    const dist = pointToSegmentDist(px, py, fromPt.x, fromPt.y, toPt.x, toPt.y);
    if (dist < bestEdgeDist) {
      bestEdgeDist = dist;
      bestEdge = i;
    }
  }
  if (bestEdge !== null) {
    const [fromIdx, toIdx] = data.edges[bestEdge];
    const fromNode = data.nodes[fromIdx];
    const toNode = data.nodes[toIdx];
    const dx = toNode[1] - fromNode[1];
    const dy = toNode[0] - fromNode[0];
    const lenSq = dx * dx + dy * dy;
    let snapLat: number, snapLng: number;
    if (lenSq === 0) {
      snapLat = fromNode[0]; snapLng = fromNode[1];
    } else {
      const t = Math.max(0, Math.min(1,
        ((lng - fromNode[1]) * dx + (lat - fromNode[0]) * dy) / lenSq
      ));
      snapLat = fromNode[0] + t * dy;
      snapLng = fromNode[1] + t * dx;
    }
    return { target: { kind: "edge", index: bestEdge }, snapLat, snapLng };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Tooltip content helper (shared by hover and pinned tooltips)
// ---------------------------------------------------------------------------

function renderTooltipContent(
  name: string,
  votes: number,
  voteTypes: { label: string; count: number }[]
) {
  const topTypes = voteTypes.slice(0, 3);
  const overflowCount = voteTypes.length - 3;
  return (
    <>
      {name && <div className="graph-tooltip-name">{name}</div>}
      <div className="graph-tooltip-meta">
        {votes > 0 ? `${votes} vote${votes !== 1 ? "s" : ""}` : "no votes yet"}
      </div>
      {topTypes.length > 0 && (
        <div className="graph-tooltip-types">
          {topTypes.map((t, i) => (
            <div key={i}>
              {t.label}
              <span className="graph-tooltip-type-count">{t.count}</span>
            </div>
          ))}
          {overflowCount > 0 && (
            <div className="graph-tooltip-types-more">
              and {overflowCount} other{overflowCount !== 1 ? "s" : ""}
            </div>
          )}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface GraphLayerProps {
  onSnap?: (pos: { lat: number; lng: number } | null) => void;
  /** When set, a tooltip is pinned at this point showing nearest node vote data. */
  pinnedPoint?: { lat: number; lng: number } | null;
}

export function GraphLayer({ onSnap, pinnedPoint }: GraphLayerProps) {
  const map = useMap();
  const { mapState } = useWebSocketContext();
  const { setSnapFn, setCurrentSnap, isDraggingRef: graphDraggingRef } = useGraphSnap();
  const theme = useTheme();
  const themeMode = theme.mode;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const ctxRef = useRef<CanvasRenderingContext2D | null>(null);
  const hoverCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const hoverCtxRef = useRef<CanvasRenderingContext2D | null>(null);
  const graphDataRef = useRef<GraphData | null>(null);
  const topologyRef = useRef<Pick<GraphData, "nodes" | "edges"> | null>(null);
  const redrawTimeoutRef = useRef<number | null>(null);
  const isZoomingRef = useRef(false);
  const voteDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Stable ref for onSnap callback
  const onSnapRef = useRef(onSnap);
  useEffect(() => { onSnapRef.current = onSnap; }, [onSnap]);

  // Register graph snap function for use by path drag
  useEffect(() => {
    setSnapFn((m: L.Map, lat: number, lng: number) => {
      const data = graphDataRef.current;
      if (!data) return null;
      const pt = m.latLngToContainerPoint([lat, lng]);
      const result = hitTest(data, m, pt.x, pt.y, lat, lng);
      if (!result) return null;
      return { lat: result.snapLat, lng: result.snapLng };
    });
  }, [setSnapFn]);

  // Hover state
  const [hoverTarget, setHoverTarget] = useState<HoverTarget | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const hoverTargetRef = useRef<HoverTarget | null>(null);
  const hoverRafRef = useRef<number | null>(null);

  // Pinned tooltip screen position (follows start pin on map pan/zoom)
  const [pinnedScreenPos, setPinnedScreenPos] = useState<{ x: number; y: number } | null>(null);
  const pinnedRafRef = useRef<number | null>(null);
  // Pinned target (node or edge) for highlight and tooltip
  const pinnedTargetRef = useRef<HoverTarget | null>(null);

  // Increments when a geocode resolves, forcing tooltip re-render
  const [geocodeVersion, setGeocodeVersion] = useState(0);
  const bumpGeocode = useCallback(() => setGeocodeVersion((v) => v + 1), []);

  // Initialize canvases once
  useEffect(() => {
    const canvas = document.createElement("canvas");
    canvas.className = "graph-layer";
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";
    // CSS-level softness: a hint of blur smooths the geometric snap, and
    // `screen` lightens the dark base map where heat accumulates.
    canvas.style.filter = "blur(0.6px)";
    canvas.style.mixBlendMode = "screen";

    const hoverCanvas = document.createElement("canvas");
    hoverCanvas.className = "graph-layer-hover";
    hoverCanvas.style.position = "absolute";
    hoverCanvas.style.top = "0";
    hoverCanvas.style.left = "0";
    hoverCanvas.style.pointerEvents = "none";

    const ctx = canvas.getContext("2d");
    const hoverCtx = hoverCanvas.getContext("2d");
    canvasRef.current = canvas;
    ctxRef.current = ctx;
    hoverCanvasRef.current = hoverCanvas;
    hoverCtxRef.current = hoverCtx;

    const pane = map.getPane("graphPane");
    if (pane) {
      pane.appendChild(canvas);
      pane.appendChild(hoverCanvas);
    }

    return () => {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      if (hoverCanvas.parentNode) hoverCanvas.parentNode.removeChild(hoverCanvas);
    };
  }, [map]);

  // Fetch votes and merge with cached topology. Always scoped to the active
  // theme — each subdomain shows only its own votes.
  const fetchVotes = useCallback(async () => {
    const topology = topologyRef.current;
    if (!topology) return;
    try {
      const url = `${CONFIG.apiUrl}/graph-votes?mode=${encodeURIComponent(themeMode)}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Vote fetch failed: ${response.status}`);
      const voteData = await response.json();
      graphDataRef.current = { ...topology, ...voteData };
      scheduleRedrawRef.current();
    } catch (error) {
      console.error("Failed to fetch graph votes:", error);
    }
  }, [themeMode]);

  const fetchVotesRef = useRef(fetchVotes);
  useEffect(() => { fetchVotesRef.current = fetchVotes; }, [fetchVotes]);

  // Debounced vote fetch (collapses rapid WebSocket updates)
  const debouncedFetchVotes = useCallback(() => {
    if (voteDebounceRef.current) clearTimeout(voteDebounceRef.current);
    voteDebounceRef.current = setTimeout(() => fetchVotesRef.current(), 500);
  }, []);

  // Fetch topology once on mount, then fetch votes
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`${CONFIG.apiUrl}/graph-topology`);
        if (!response.ok) throw new Error(`Topology fetch failed: ${response.status}`);
        const data = await response.json();
        if (cancelled) return;
        topologyRef.current = data;
        // Set topology-only graph data so hover/snap works immediately
        graphDataRef.current = data;
        scheduleRedrawRef.current();
        // Now fetch votes to overlay
        fetchVotesRef.current();
      } catch (error) {
        console.error("Failed to fetch graph topology:", error);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Draw hover and pinned highlights on separate canvas
  const redrawHoverHighlight = useCallback(() => {
    const hoverCanvas = hoverCanvasRef.current;
    const hoverCtx = hoverCtxRef.current;
    const data = graphDataRef.current;
    if (!hoverCanvas || !hoverCtx) return;

    const size = map.getSize();
    hoverCanvas.width = size.x;
    hoverCanvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(hoverCanvas, topLeft);
    hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);

    if (!data) return;

    hoverCtx.lineCap = "round";
    hoverCtx.lineJoin = "round";

    // Renders a hollow white ring with faint interior — same look as the
    // desire path. `alpha` scales overall opacity (e.g. hover dimmer than pinned).
    // Three passes:
    //   1. Stroke wide white (covers full path footprint)
    //   2. destination-out narrower stroke (carves the hole)
    //   3. Stroke narrower at low alpha (faint white interior)
    const drawNode = (nodeIndex: number, alpha: number) => {
      const node = data.nodes[nodeIndex];
      if (!node) return;
      const pt = map.latLngToContainerPoint([node[0], node[1]]);

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = alpha;
      hoverCtx.fillStyle = "#ffffff";
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_OUTER_R, 0, Math.PI * 2);
      hoverCtx.fill();

      hoverCtx.globalCompositeOperation = "destination-out";
      hoverCtx.globalAlpha = 1.0;
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_INNER_R, 0, Math.PI * 2);
      hoverCtx.fill();

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = HIGHLIGHT_INTERIOR_ALPHA * alpha;
      hoverCtx.beginPath();
      hoverCtx.arc(pt.x, pt.y, HIGHLIGHT_NODE_INNER_R, 0, Math.PI * 2);
      hoverCtx.fill();
    };

    const drawEdge = (edgeIndex: number, alpha: number) => {
      const edge = data.edges[edgeIndex];
      if (!edge) return;
      const [fromIdx, toIdx] = edge;
      const fromScreen = map.latLngToContainerPoint([data.nodes[fromIdx][0], data.nodes[fromIdx][1]]);
      const toScreen = map.latLngToContainerPoint([data.nodes[toIdx][0], data.nodes[toIdx][1]]);

      const strokeLine = () => {
        hoverCtx.beginPath();
        hoverCtx.moveTo(fromScreen.x, fromScreen.y);
        hoverCtx.lineTo(toScreen.x, toScreen.y);
        hoverCtx.stroke();
      };

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = alpha;
      hoverCtx.strokeStyle = "#ffffff";
      hoverCtx.lineWidth = HIGHLIGHT_RING_WIDTH;
      strokeLine();

      hoverCtx.globalCompositeOperation = "destination-out";
      hoverCtx.globalAlpha = 1.0;
      hoverCtx.lineWidth = HIGHLIGHT_INNER_WIDTH;
      strokeLine();

      hoverCtx.globalCompositeOperation = "source-over";
      hoverCtx.globalAlpha = HIGHLIGHT_INTERIOR_ALPHA * alpha;
      hoverCtx.lineWidth = HIGHLIGHT_INNER_WIDTH;
      strokeLine();
    };

    const drawTarget = (t: HoverTarget, alpha: number) => {
      if (t.kind === "edge") drawEdge(t.index, alpha);
      else drawNode(t.index, alpha);
    };

    // Pinned highlight (selected start point) — full opacity, exact path match
    if (pinnedTargetRef.current) drawTarget(pinnedTargetRef.current, 1.0);

    // Hover highlight — slightly dimmer to read as "preview"
    if (hoverTargetRef.current) drawTarget(hoverTargetRef.current, 0.6);

    // Reset state for any subsequent canvas operations
    hoverCtx.globalCompositeOperation = "source-over";
    hoverCtx.globalAlpha = 1.0;
  }, [map]);

  const redrawHoverHighlightRef = useRef(redrawHoverHighlight);
  useEffect(() => { redrawHoverHighlightRef.current = redrawHoverHighlight; }, [redrawHoverHighlight]);

  // Redraw function - renders edges with vote-scaled styling
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = ctxRef.current;
    const data = graphDataRef.current;

    if (!canvas || !ctx || !data) return;

    const size = map.getSize();
    canvas.width = size.x;
    canvas.height = size.y;

    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(canvas, topLeft);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!data.edges) return;

    const edgeVotes = data.edge_votes ?? [];
    const maxVotes = Math.max(1, arrayMax(edgeVotes));
    const bounds = map.getBounds();

    const zoom = map.getZoom();
    const zoomScale = Math.pow(2, (zoom - 14) / 2);

    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    const south = bounds.getSouth();
    const north = bounds.getNorth();
    const west = bounds.getWest();
    const east = bounds.getEast();

    // Returns [from, to] container points, or null if the edge is fully off-screen.
    const screenFor = (i: number): [L.Point, L.Point] | null => {
      const [fromIdx, toIdx] = data.edges[i];
      const a = data.nodes[fromIdx];
      const b = data.nodes[toIdx];
      if (
        (a[0] < south && b[0] < south) ||
        (a[0] > north && b[0] > north) ||
        (a[1] < west && b[1] < west) ||
        (a[1] > east && b[1] > east)
      ) return null;
      return [
        map.latLngToContainerPoint([a[0], a[1]]),
        map.latLngToContainerPoint([b[0], b[1]]),
      ];
    };

    const drawLine = (pts: [L.Point, L.Point]) => {
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      ctx.lineTo(pts[1].x, pts[1].y);
      ctx.stroke();
    };

    // ----------------------------------------------------------------
    // Phase 1 — zero-vote baseline (source-over, faint white)
    // Drawn before lighter mode so the network outline doesn't itself
    // accumulate at intersections (which would highlight random nodes).
    // ----------------------------------------------------------------
    ctx.globalCompositeOperation = "source-over";
    ctx.lineWidth = 0.5 * zoomScale;
    ctx.globalAlpha = 0.05;
    ctx.strokeStyle = "#ffffff";
    for (let i = 0; i < data.edges.length; i++) {
      if ((edgeVotes[i] ?? 0) > 0) continue;
      const pts = screenFor(i);
      if (pts) drawLine(pts);
    }

    // ----------------------------------------------------------------
    // Phase 2 — voted edges, additive blending (Strava-style)
    // Sort ascending so the hottest edges paint last and dominate at
    // overlaps; "lighter" composite means RGB channels sum, naturally
    // shifting toward yellow/white as intensity stacks.
    // ----------------------------------------------------------------
    const voted: number[] = [];
    for (let i = 0; i < data.edges.length; i++) {
      if ((edgeVotes[i] ?? 0) > 0) voted.push(i);
    }
    voted.sort((a, b) => (edgeVotes[a] ?? 0) - (edgeVotes[b] ?? 0));

    ctx.globalCompositeOperation = "lighter";

    for (const i of voted) {
      const pts = screenFor(i);
      if (!pts) continue;
      const norm = Math.log((edgeVotes[i] ?? 0) + 1) / Math.log(maxVotes + 1);

      // Pass 1 — wide deep-red outer halo (low alpha, broad falloff).
      // Approximates Gaussian halo via stroke width + low opacity.
      ctx.lineWidth = (2 + norm * 8) * zoomScale;
      ctx.globalAlpha = 0.025 + norm * 0.06;
      ctx.strokeStyle = HEAT_HALO;
      drawLine(pts);

      // Pass 2 — warm orange "heat" mid stroke (the dominant color).
      ctx.lineWidth = (1 + norm * 2) * zoomScale;
      ctx.globalAlpha = 0.08 + norm * 0.20;
      ctx.strokeStyle = HEAT_WARM;
      drawLine(pts);

      // Pass 3 — yellow hot core (kicks in past ~mid intensity).
      if (norm > 0.2) {
        const t = (norm - 0.2) / 0.8;
        ctx.lineWidth = (0.6 + t * 1.0) * zoomScale;
        ctx.globalAlpha = 0.10 + t * 0.30;
        ctx.strokeStyle = HEAT_HOT;
        drawLine(pts);
      }

      // Pass 4 — pale warm-white peak (only the hottest edges).
      if (norm > 0.7) {
        const t = (norm - 0.7) / 0.3;
        ctx.lineWidth = Math.max(0.3, 0.4 * zoomScale);
        ctx.globalAlpha = 0.30 * t;
        ctx.strokeStyle = HEAT_PEAK;
        drawLine(pts);
      }
    }

    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1.0;

    redrawHoverHighlightRef.current();
  }, [map]);

  // Schedule redraw
  const scheduleRedraw = useCallback(() => {
    if (redrawTimeoutRef.current) cancelAnimationFrame(redrawTimeoutRef.current);
    redrawTimeoutRef.current = requestAnimationFrame(redraw);
  }, [redraw]);

  const scheduleRedrawRef = useRef(scheduleRedraw);
  useEffect(() => { scheduleRedrawRef.current = scheduleRedraw; }, [scheduleRedraw]);

  // Re-fetch votes when WebSocket indicates new state (votes changed)
  const mapStateRevision = mapState?.revision;
  useEffect(() => {
    if (mapStateRevision === undefined) return;
    debouncedFetchVotes();
  }, [mapStateRevision, debouncedFetchVotes]);

  // Map event listeners — topology is pre-loaded, just redraw on pan/zoom
  useEffect(() => {
    const handleZoomStart = () => {
      isZoomingRef.current = true;
      const ctx = ctxRef.current;
      const canvas = canvasRef.current;
      if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      const hoverCtx = hoverCtxRef.current;
      const hoverCanvas = hoverCanvasRef.current;
      if (hoverCanvas && hoverCtx) hoverCtx.clearRect(0, 0, hoverCanvas.width, hoverCanvas.height);
      hoverTargetRef.current = null;
      setHoverTarget(null);
    };
    const handleZoomEnd = () => {
      isZoomingRef.current = false;
      scheduleRedrawRef.current();
    };
    const handleMoveEnd = () => {
      // Always redraw on moveend — isZoomingRef is cleared synchronously in zoomend
      scheduleRedrawRef.current();
    };
    const handleResize = () => scheduleRedrawRef.current();

    map.on("zoomstart", handleZoomStart);
    map.on("zoomend", handleZoomEnd);
    map.on("moveend", handleMoveEnd);
    map.on("resize", handleResize);
    return () => {
      map.off("zoomstart", handleZoomStart);
      map.off("zoomend", handleZoomEnd);
      map.off("moveend", handleMoveEnd);
      map.off("resize", handleResize);
    };
  }, [map]);

  // Hover detection and snap — uses hitTest for unified logic.
  useEffect(() => {
    const canHover = window.matchMedia("(hover: hover)").matches;
    if (!canHover) return;

    const handleMouseMove = (e: L.LeafletMouseEvent) => {
      if (hoverRafRef.current) return;
      hoverRafRef.current = requestAnimationFrame(() => {
        hoverRafRef.current = null;
        const data = graphDataRef.current;
        if (!data?.edges) {
          if (hoverTargetRef.current) {
            hoverTargetRef.current = null;
            setHoverTarget(null);
            redrawHoverHighlightRef.current();
          }
          onSnapRef.current?.(null);
          setCurrentSnap(null);
          return;
        }

        const hit = hitTest(data, map, e.containerPoint.x, e.containerPoint.y, e.latlng.lat, e.latlng.lng);
        const dragging = graphDraggingRef.current;

        // During drag: suppress hover highlight and tooltip, but still compute snap
        const newTarget = dragging ? null : (hit?.target ?? null);
        const prev = hoverTargetRef.current;
        const changed = !prev && newTarget
          || prev && !newTarget
          || (prev && newTarget && (prev.kind !== newTarget.kind || prev.index !== newTarget.index));

        if (changed) {
          hoverTargetRef.current = newTarget;
          setHoverTarget(newTarget);
          redrawHoverHighlightRef.current();
        }

        if (newTarget) {
          setTooltipPos({ x: e.originalEvent.clientX, y: e.originalEvent.clientY });
        }

        // Snap position: hit result or fallback to nearest node
        if (hit) {
          const snapPos = { lat: hit.snapLat, lng: hit.snapLng };
          onSnapRef.current?.(snapPos);
          setCurrentSnap(snapPos);
        } else {
          let nearestIdx = -1;
          let nearestDist = Infinity;
          for (let i = 0; i < data.nodes.length; i++) {
            const n = data.nodes[i];
            const d = (n[0] - e.latlng.lat) ** 2 + (n[1] - e.latlng.lng) ** 2;
            if (d < nearestDist) { nearestDist = d; nearestIdx = i; }
          }
          if (nearestIdx >= 0) {
            const n = data.nodes[nearestIdx];
            const snapPos = { lat: n[0], lng: n[1] };
            onSnapRef.current?.(snapPos);
            setCurrentSnap(snapPos);
          } else {
            onSnapRef.current?.(null);
            setCurrentSnap(null);
          }
        }
      });
    };

    const handleMouseOut = () => {
      if (hoverTargetRef.current) {
        hoverTargetRef.current = null;
        setHoverTarget(null);
        redrawHoverHighlightRef.current();
      }
      onSnapRef.current?.(null);
      setCurrentSnap(null);
    };

    map.on("mousemove", handleMouseMove);
    map.on("mouseout", handleMouseOut);
    return () => {
      map.off("mousemove", handleMouseMove);
      map.off("mouseout", handleMouseOut);
      if (hoverRafRef.current) cancelAnimationFrame(hoverRafRef.current);
    };
  }, [map]);

  // Clean up debounce timer on unmount
  useEffect(() => {
    return () => {
      if (voteDebounceRef.current) clearTimeout(voteDebounceRef.current);
    };
  }, []);

  // Track pinned tooltip screen position on map pan/zoom
  const pinnedLat = pinnedPoint?.lat ?? null;
  const pinnedLng = pinnedPoint?.lng ?? null;

  useEffect(() => {
    if (pinnedLat === null || pinnedLng === null) {
      setPinnedScreenPos(null);
      pinnedTargetRef.current = null;
      redrawHoverHighlightRef.current();
      return;
    }

    // Find nearest node/edge for highlight using unified hit-test
    const data = graphDataRef.current;
    if (data?.edges) {
      const pt = map.latLngToContainerPoint([pinnedLat, pinnedLng]);
      const hit = hitTest(data, map, pt.x, pt.y, pinnedLat, pinnedLng);
      pinnedTargetRef.current = hit?.target ?? null;
    } else {
      pinnedTargetRef.current = null;
    }
    redrawHoverHighlightRef.current();

    const update = () => {
      const pt = map.latLngToContainerPoint([pinnedLat, pinnedLng]);
      const rect = map.getContainer().getBoundingClientRect();
      setPinnedScreenPos({ x: rect.left + pt.x, y: rect.top + pt.y - 8 });
    };

    const throttledUpdate = () => {
      if (pinnedRafRef.current) return;
      pinnedRafRef.current = requestAnimationFrame(() => {
        pinnedRafRef.current = null;
        update();
      });
    };

    update();
    map.on("move", throttledUpdate);
    map.on("zoom", throttledUpdate);
    return () => {
      map.off("move", throttledUpdate);
      map.off("zoom", throttledUpdate);
      if (pinnedRafRef.current) {
        cancelAnimationFrame(pinnedRafRef.current);
        pinnedRafRef.current = null;
      }
    };
  }, [map, pinnedLat, pinnedLng]);

  // -------------------------------------------------------------------------
  // Tooltip content — hover
  // -------------------------------------------------------------------------

  const data = graphDataRef.current;
  const legend = data?.vote_type_legend ?? [];
  let tooltipName = "";
  let tooltipVotes = 0;
  let hoverVoteTypes: { label: string; count: number }[] = [];

  if (hoverTarget && data) {
    if (hoverTarget.kind === "edge") {
      const edge = data.edges[hoverTarget.index];
      if (edge) {
        const [fromIdx, toIdx] = edge;
        const fromNode = data.nodes[fromIdx];
        const toNode = data.nodes[toIdx];
        const midLat = (fromNode[0] + toNode[0]) / 2;
        const midLng = (fromNode[1] + toNode[1]) / 2;

        tooltipName = edge[2] || resolveAddress(midLat, midLng, bumpGeocode);
        tooltipVotes = (data.edge_votes ?? [])[hoverTarget.index] ?? 0;

        const vtPairs = (data.edge_vote_types ?? [])[hoverTarget.index] ?? [];
        hoverVoteTypes = vtPairs
          .map(([idx, cnt]) => ({ label: legend[idx], count: cnt }))
          .filter((v) => v.label);
      }
    } else {
      const node = data.nodes[hoverTarget.index];
      if (node) {
        tooltipName = resolveAddress(node[0], node[1], bumpGeocode);
        tooltipVotes = (data.node_votes ?? [])[hoverTarget.index] ?? 0;

        const vtMaxMap = new Map<string, number>();
        const edgeVoteTypes = data.edge_vote_types ?? [];
        for (let i = 0; i < (data.edges?.length ?? 0); i++) {
          const [fromIdx, toIdx] = data.edges[i];
          if (fromIdx === hoverTarget.index || toIdx === hoverTarget.index) {
            for (const [idx, cnt] of (edgeVoteTypes[i] ?? [])) {
              const label = legend[idx];
              if (label) vtMaxMap.set(label, Math.max(vtMaxMap.get(label) ?? 0, cnt));
            }
          }
        }
        hoverVoteTypes = [...vtMaxMap.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([label, count]) => ({ label, count }));
      }
    }
  }

  // -------------------------------------------------------------------------
  // Tooltip content — pinned (uses pinnedTargetRef from hitTest)
  // -------------------------------------------------------------------------

  let pinnedName = "";
  let pinnedVotes = 0;
  let pinnedVoteTypes: { label: string; count: number }[] = [];
  const pinnedTarget = pinnedTargetRef.current;
  const showPinned = pinnedPoint && pinnedScreenPos && pinnedTarget && data;

  if (showPinned) {
    if (pinnedTarget.kind === "edge") {
      const edge = data.edges[pinnedTarget.index];
      if (edge) {
        const [fromIdx, toIdx] = edge;
        const fromNode = data.nodes[fromIdx];
        const toNode = data.nodes[toIdx];
        const midLat = (fromNode[0] + toNode[0]) / 2;
        const midLng = (fromNode[1] + toNode[1]) / 2;
        pinnedName = edge[2] || resolveAddress(midLat, midLng, bumpGeocode);
        pinnedVotes = (data.edge_votes ?? [])[pinnedTarget.index] ?? 0;
        const vtPairs = (data.edge_vote_types ?? [])[pinnedTarget.index] ?? [];
        pinnedVoteTypes = vtPairs
          .map(([idx, cnt]) => ({ label: legend[idx], count: cnt }))
          .filter((v) => v.label);
      }
    } else {
      const node = data.nodes[pinnedTarget.index];
      if (node) {
        pinnedName = resolveAddress(node[0], node[1], bumpGeocode);
        pinnedVotes = (data.node_votes ?? [])[pinnedTarget.index] ?? 0;
        const vtMaxMap = new Map<string, number>();
        const edgeVoteTypes = data.edge_vote_types ?? [];
        for (let i = 0; i < (data.edges?.length ?? 0); i++) {
          const [fromIdx, toIdx] = data.edges[i];
          if (fromIdx === pinnedTarget.index || toIdx === pinnedTarget.index) {
            for (const [idx, cnt] of (edgeVoteTypes[i] ?? [])) {
              const label = legend[idx];
              if (label) vtMaxMap.set(label, Math.max(vtMaxMap.get(label) ?? 0, cnt));
            }
          }
        }
        pinnedVoteTypes = [...vtMaxMap.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([label, count]) => ({ label, count }));
      }
    }
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const mapContainer = map.getContainer();
  const flipped = tooltipPos.x > window.innerWidth / 2;
  const showHoverTooltip = hoverTarget !== null;

  // geocodeVersion used to re-render when async geocode completes
  void geocodeVersion;

  return (
    <>
      {showPinned && pinnedScreenPos && createPortal(
        <div
          className={`graph-tooltip${pinnedScreenPos.x > window.innerWidth / 2 ? " tooltip-flipped" : ""}`}
          style={{ left: pinnedScreenPos.x, top: pinnedScreenPos.y }}
        >
          {renderTooltipContent(pinnedName, pinnedVotes, pinnedVoteTypes)}
        </div>,
        mapContainer
      )}
      {showHoverTooltip && createPortal(
        <div
          className={`graph-tooltip${flipped ? " tooltip-flipped" : ""}`}
          style={{ left: tooltipPos.x, top: tooltipPos.y }}
        >
          {renderTooltipContent(tooltipName, tooltipVotes, hoverVoteTypes)}
        </div>,
        mapContainer
      )}
    </>
  );
}
