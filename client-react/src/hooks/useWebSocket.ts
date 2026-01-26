import { useEffect, useRef, useCallback } from "react";
import { CONFIG } from "../config";
import type { MapState, TransportMode } from "../types";

interface WebSocketOptions {
  onState?: (state: MapState) => void;
  onStatus?: (status: string) => void;
  onConnect?: () => void;
}

interface WebSocketConnection {
  send: (obj: Record<string, unknown>) => void;
  setMode: (mode: TransportMode) => void;
}

export function useWebSocket(options: WebSocketOptions): WebSocketConnection {
  const wsRef = useRef<WebSocket | null>(null);
  const latestRevisionRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { onState, onStatus, onConnect } = options;

  const connect = useCallback(() => {
    onStatus?.(`connecting to ${CONFIG.wsUrl}...`);
    const ws = new WebSocket(CONFIG.wsUrl);

    ws.onopen = () => {
      onStatus?.("ws connected");
      onConnect?.();
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
            onState?.(state);
          }
        }
      } catch (e) {
        console.warn("bad ws message", e);
      }
    };

    ws.onclose = () => {
      onStatus?.("ws disconnected (retrying...)");
      reconnectTimeoutRef.current = setTimeout(connect, 1000);
    };

    ws.onerror = () => {
      onStatus?.("ws error");
    };

    wsRef.current = ws;
  }, [onState, onStatus, onConnect]);

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

  const send = useCallback((obj: Record<string, unknown>) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj));
    }
  }, []);

  const setMode = useCallback(
    (mode: TransportMode) => {
      send({ type: "set_mode", mode });
    },
    [send]
  );

  return { send, setMode };
}
