import {
  createContext,
  useContext,
  useCallback,
  useMemo,
  useState,
  useRef,
  useEffect,
  type ReactNode,
} from "react";
import { CONFIG } from "../config";
import { withMap } from "../map/runtime";
import type { VoteDelta } from "../types";

type DeltaListener = (delta: VoteDelta) => void;

interface WebSocketContextValue {
  connectionStatus: string;
  subscribeToDelta: (listener: DeltaListener) => () => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [connectionStatus, setConnectionStatus] = useState("disconnected");

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(1000);
  const MAX_BACKOFF = 30000;
  const deltaListenersRef = useRef<Set<DeltaListener>>(new Set());

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    const ws = new WebSocket(withMap(CONFIG.wsUrl));

    ws.onopen = () => {
      setConnectionStatus("connected");
      backoffRef.current = 1000;
    };

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "delta") {
          // Pass the whole payload through — must include dir/reversed/vtCounts
          // (directional + authoritative-count fields), not just the basics.
          const { type: _type, ...delta } = msg;
          void _type;
          for (const cb of deltaListenersRef.current) {
            cb(delta as VoteDelta);
          }
        }
        // "init" and "keepalive" are handled silently
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
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  const subscribeToDelta = useCallback((listener: DeltaListener) => {
    deltaListenersRef.current.add(listener);
    return () => {
      deltaListenersRef.current.delete(listener);
    };
  }, []);

  const value = useMemo(
    () => ({ connectionStatus, subscribeToDelta }),
    [connectionStatus, subscribeToDelta],
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
    throw new Error("useWebSocketContext must be used within a WebSocketProvider");
  }
  return context;
}
