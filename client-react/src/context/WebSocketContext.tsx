import {
  createContext,
  useContext,
  useCallback,
  useMemo,
  useState,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { useRoute } from "./RouteContext";
import { CONFIG } from "../config";
import type { MapState, TransportMode, HexOverlay, HexOverlayCompact, RawMapState } from "../types";

// Resolution bounds
const MIN_RESOLUTION = 10;
const MAX_RESOLUTION = 15;

interface WebSocketContextValue {
  mapState: MapState | null;
  connectionStatus: string;
  currentZoom: number;
  currentResolution: number;
  setMode: (mode: TransportMode) => void;
  setZoom: (zoom: number) => void;
  getHexOverlayForResolution: (res: number) => HexOverlay | undefined;
}

/**
 * Convert zoom level to H3 resolution (every 2 zoom levels).
 * zoom 13-14 → res 10, zoom 15-16 → res 11, ..., zoom 23+ → res 15
 */
function zoomToResolution(zoom: number): number {
  const res = MIN_RESOLUTION + Math.floor((Math.max(zoom, 13) - 13) / 2);
  if (res <= MIN_RESOLUTION) return MIN_RESOLUTION;
  if (res >= MAX_RESOLUTION) return MAX_RESOLUTION;
  return res;
}

/**
 * Parse a single hex overlay from compact format to internal format.
 */
function parseHexOverlayCompact(data: HexOverlayCompact): HexOverlay {
  const hexes: Record<string, number> = {};
  for (const [hexId, weight] of data.h) {
    hexes[hexId] = weight;
  }
  return {
    res: data.res,
    hexes,
    max_votes: data.m,
    suggestionLegend: data.sl,
    suggestions: data.s,
  };
}

/**
 * Parse all hex overlays from server format.
 */
function parseAllHexOverlays(
  data: Record<number, HexOverlayCompact> | undefined
): Record<number, HexOverlay> | undefined {
  if (!data) return undefined;

  const result: Record<number, HexOverlay> = {};
  for (const [resStr, compactOverlay] of Object.entries(data)) {
    const res = parseInt(resStr, 10);
    result[res] = parseHexOverlayCompact(compactOverlay);
  }
  return result;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [mapState, setMapState] = useState<MapState | null>(null);
  const [connectionStatus, setConnectionStatus] = useState("disconnected");
  const [currentZoom, setCurrentZoom] = useState(14);
  const { mode } = useRoute();

  const wsRef = useRef<WebSocket | null>(null);
  const latestRevisionRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const modeRef = useRef(mode);
  const currentZoomRef = useRef(currentZoom);

  // Keep mode ref updated
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  // Keep zoom ref updated
  useEffect(() => {
    currentZoomRef.current = currentZoom;
  }, [currentZoom]);

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    const ws = new WebSocket(CONFIG.wsUrl);

    ws.onopen = () => {
      setConnectionStatus("connected");
      // Send initial mode and zoom on connect
      ws.send(JSON.stringify({ type: "set_mode", mode: modeRef.current }));
      ws.send(JSON.stringify({ type: "set_zoom", zoom: currentZoomRef.current }));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "map_state" && msg.state) {
          const rawState = msg.state as RawMapState;
          if (
            typeof rawState.revision === "number" &&
            rawState.revision > latestRevisionRef.current
          ) {
            latestRevisionRef.current = rawState.revision;
            // Merge new hex overlays with cached ones (server sends one resolution at a time)
            const newHexOverlays = parseAllHexOverlays(rawState.hex_overlays);
            setMapState((prev) => ({
              ...rawState,
              hex_overlays: { ...prev?.hex_overlays, ...newHexOverlays },
            }));
          }
        }
      } catch (e) {
        console.warn("bad ws message", e);
      }
    };

    ws.onclose = () => {
      setConnectionStatus("disconnected");
      reconnectTimeoutRef.current = setTimeout(connect, 1000);
    };

    ws.onerror = () => {
      setConnectionStatus("error");
    };

    wsRef.current = ws;
  }, []);  // No dependencies - uses refs for current values

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  const setMode = useCallback((newMode: TransportMode) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_mode", mode: newMode }));
    }
  }, []);

  const setZoom = useCallback((zoom: number) => {
    setCurrentZoom(zoom);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_zoom", zoom }));
    }
  }, []);

  // Send mode when it changes
  useEffect(() => {
    setMode(mode);
  }, [mode, setMode]);

  const currentResolution = useMemo(() => zoomToResolution(currentZoom), [currentZoom]);

  const getHexOverlayForResolution = useCallback(
    (res: number): HexOverlay | undefined => {
      return mapState?.hex_overlays?.[res];
    },
    [mapState]
  );

  const value = useMemo(
    () => ({
      mapState,
      connectionStatus,
      currentZoom,
      currentResolution,
      setMode,
      setZoom,
      getHexOverlayForResolution,
    }),
    [mapState, connectionStatus, currentZoom, currentResolution, setMode, setZoom, getHexOverlayForResolution]
  );

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  );
}

export function useWebSocketContext(): WebSocketContextValue {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error(
      "useWebSocketContext must be used within a WebSocketProvider"
    );
  }
  return context;
}
