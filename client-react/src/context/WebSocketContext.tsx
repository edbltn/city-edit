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
import type { MapState, TransportMode } from "../types";

interface WebSocketContextValue {
  mapState: MapState | null;
  connectionStatus: string;
  setMode: (mode: TransportMode) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [mapState, setMapState] = useState<MapState | null>(null);
  const [connectionStatus, setConnectionStatus] = useState("disconnected");
  const { mode } = useRoute();

  const wsRef = useRef<WebSocket | null>(null);
  const latestRevisionRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const modeRef = useRef(mode);

  // Keep mode ref updated
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    const ws = new WebSocket(CONFIG.wsUrl);

    ws.onopen = () => {
      setConnectionStatus("connected");
      // Send initial mode on connect
      ws.send(JSON.stringify({ type: "set_mode", mode: modeRef.current }));
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "map_state" && msg.state) {
          const state = msg.state as MapState;
          if (
            typeof state.revision === "number" &&
            state.revision > latestRevisionRef.current
          ) {
            latestRevisionRef.current = state.revision;
            setMapState(state);
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
  }, []);

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

  // Send mode when it changes
  useEffect(() => {
    setMode(mode);
  }, [mode, setMode]);

  const value = useMemo(
    () => ({
      mapState,
      connectionStatus,
      setMode,
    }),
    [mapState, connectionStatus, setMode]
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
