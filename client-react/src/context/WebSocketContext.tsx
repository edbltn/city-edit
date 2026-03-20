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
import { CONFIG } from "../config";
import type { MapState, RawMapState } from "../types";

// Parsed hex overlay for a single resolution
export interface HexOverlayData {
  hexes: Record<string, number>;
  max_votes: number;
  rawCounts?: Record<string, number>;
  suggestionLegend?: string[];
  suggestions?: Record<string, number[]>;
}

interface WebSocketContextValue {
  mapState: MapState | null;
  connectionStatus: string;
  currentZoom: number;
  setZoom: (zoom: number) => void;
  currentResolution: number;
  hexDataVersion: number;
  getHexOverlayForResolution: (res: number) => HexOverlayData | null;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

// Map zoom level to H3 resolution
function zoomToResolution(zoom: number): number {
  if (zoom >= 16) return 13;
  if (zoom >= 15) return 12;
  if (zoom >= 14) return 11;
  if (zoom >= 13) return 10;
  if (zoom >= 12) return 9;
  return 8;
}

// Parse compact hex overlay format from server into expanded format
function parseHexOverlay(compact: {
  res: number;
  h: [string, number][];
  m: number;
  sl?: string[];
  s?: Record<string, number[]>;
  rc?: Record<string, number>;
}): HexOverlayData {
  const hexes: Record<string, number> = {};
  for (const [hexId, weight] of compact.h) {
    hexes[hexId] = weight;
  }
  return {
    hexes,
    max_votes: compact.m,
    rawCounts: compact.rc,
    suggestionLegend: compact.sl,
    suggestions: compact.s,
  };
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [mapState, setMapState] = useState<MapState | null>(null);
  const [connectionStatus, setConnectionStatus] = useState("disconnected");
  const [currentZoom, setCurrentZoom] = useState(14);
  const [hexDataVersion, setHexDataVersion] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const latestRevisionRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const currentZoomRef = useRef(currentZoom);
  const mapStateRef = useRef(mapState);
  const hexCacheRef = useRef<Map<number, HexOverlayData>>(new Map());

  // Keep refs updated
  useEffect(() => { currentZoomRef.current = currentZoom; }, [currentZoom]);
  useEffect(() => { mapStateRef.current = mapState; }, [mapState]);

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    const ws = new WebSocket(CONFIG.wsUrl);

    ws.onopen = () => {
      setConnectionStatus("connected");
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
            setMapState(rawState);

            // Parse and cache hex overlays
            const hexOverlays = (rawState as unknown as Record<string, unknown>).hex_overlays as
              Record<string, { res: number; h: [string, number][]; m: number; sl?: string[]; s?: Record<string, number[]>; rc?: Record<string, number> }> | undefined;
            if (hexOverlays) {
              for (const [resStr, compact] of Object.entries(hexOverlays)) {
                hexCacheRef.current.set(Number(resStr), parseHexOverlay(compact));
              }
              setHexDataVersion((v) => v + 1);
            }
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

  const setZoom = useCallback((zoom: number) => {
    setCurrentZoom(zoom);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_zoom", zoom }));
    }
  }, []);

  const currentResolution = zoomToResolution(currentZoom);

  const getHexOverlayForResolution = useCallback((res: number): HexOverlayData | null => {
    return hexCacheRef.current.get(res) ?? null;
  }, []);

  const value = useMemo(
    () => ({
      mapState,
      connectionStatus,
      currentZoom,
      setZoom,
      currentResolution,
      hexDataVersion,
      getHexOverlayForResolution,
    }),
    [mapState, connectionStatus, currentZoom, setZoom, currentResolution, hexDataVersion, getHexOverlayForResolution]
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
