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

interface WebSocketContextValue {
  mapState: MapState | null;
  connectionStatus: string;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [mapState, setMapState] = useState<MapState | null>(null);
  const [connectionStatus, setConnectionStatus] = useState("disconnected");

  const wsRef = useRef<WebSocket | null>(null);
  const latestRevisionRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const backoffRef = useRef(1000);
  const MAX_BACKOFF = 30000;

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    const ws = new WebSocket(CONFIG.wsUrl);

    ws.onopen = () => {
      setConnectionStatus("connected");
      backoffRef.current = 1000;
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
          }
        }
      } catch (e) {
        console.warn("bad ws message", e);
      }
    };

    ws.onclose = () => {
      setConnectionStatus("disconnected");
      const delay = backoffRef.current;
      backoffRef.current = Math.min(delay * 2, MAX_BACKOFF);
      reconnectTimeoutRef.current = setTimeout(connect, delay);
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

  const value = useMemo(
    () => ({ mapState, connectionStatus }),
    [mapState, connectionStatus]
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
