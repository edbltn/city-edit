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
import { dlog, dwarn } from "../utils/debugLog";
import { withMap, getMapSlug, getPasscodeToken } from "../map/runtime";
import type { VoteDelta } from "../types";
import {
  decodeFrame, frameToDeltas, KIND_SYNC, WireFormatError,
} from "../utils/wireCodec";

type DeltaListener = (delta: VoteDelta) => void;

/** A catch-up packet: everything that changed in (baseRev, rev].
 *  `truncated` means the server could not prove it covered the gap — the
 *  client must fall back to a full /api/graph-votes refetch. */
export interface VoteSync {
  rev: number;
  baseRev: number;
  truncated: boolean;
  deltas: VoteDelta[];
}

type SyncListener = (sync: VoteSync) => void;

interface WebSocketContextValue {
  connectionStatus: string;
  subscribeToDelta: (listener: DeltaListener) => () => void;
  subscribeToSync: (listener: SyncListener) => () => void;
  /** Fires on every socket open, initial and reconnect. */
  subscribeToOpen: (listener: () => void) => () => void;
  /** Ask the server for everything after `sinceRev`. Fired on foreground and
   *  on reconnect — the two moments the tab knows it may have missed deltas
   *  but has no arriving delta to notice the gap with. */
  requestSync: (sinceRev: number) => void;
}

const WebSocketContext = createContext<WebSocketContextValue | null>(null);

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [connectionStatus, setConnectionStatus] = useState("disconnected");

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(1000);
  const MAX_BACKOFF = 30000;
  const deltaListenersRef = useRef<Set<DeltaListener>>(new Set());
  const syncListenersRef = useRef<Set<SyncListener>>(new Set());
  const openListenersRef = useRef<Set<() => void>>(new Set());

  const connect = useCallback(() => {
    setConnectionStatus("connecting...");
    // Browsers can't set headers on a WS handshake, so a gated map's token
    // rides in the query string (?token=) the same way the slug does.
    let wsUrl = withMap(CONFIG.wsUrl);
    const token = getPasscodeToken(getMapSlug());
    if (token) wsUrl += (wsUrl.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
    // Announce binary-frame support in the HANDSHAKE. A hello message sent
    // after connect can lose the race with the first delta flush; the query
    // string is known before the socket opens. A server that predates the
    // codec ignores it and keeps sending JSON, which this client still reads.
    wsUrl += (wsUrl.includes("?") ? "&" : "?") + "bin=1";
    const ws = new WebSocket(wsUrl);
    // Binary frames arrive as ArrayBuffer rather than Blob, so they decode
    // synchronously in onmessage instead of behind a promise.
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      dlog("ws", "connected", wsUrl);
      setConnectionStatus("connected");
      backoffRef.current = 1000;
      // A reconnect is a gap by definition: deltas published while the socket
      // was down were never delivered, and gap detection only fires on the
      // NEXT delta to arrive. Tell subscribers so they can ask for a catch-up.
      for (const cb of openListenersRef.current) cb();
    };

    ws.onmessage = (evt) => {
      // Binary = a wire-codec frame (a merged delta flush, or a sync reply).
      if (evt.data instanceof ArrayBuffer) {
        try {
          const frame = decodeFrame(evt.data);
          const deltas = frameToDeltas(frame);
          if (frame.kind === KIND_SYNC) {
            dlog("ws", `sync rev ${frame.baseRev}→${frame.rev} `
              + `groups=${frame.groups.length}${frame.truncated ? " TRUNCATED" : ""}`);
            for (const cb of syncListenersRef.current) {
              cb({ rev: frame.rev, baseRev: frame.baseRev, truncated: frame.truncated, deltas });
            }
          } else {
            for (const d of deltas) {
              for (const cb of deltaListenersRef.current) cb(d);
            }
          }
        } catch (e) {
          // A frame we can't trust (bad magic, unknown version, corruption).
          // Never apply a partial decode — ask for a full resync instead,
          // which is the pre-codec behaviour.
          dwarn("ws", "undecodable binary frame — forcing resync", e);
          if (e instanceof WireFormatError) {
            for (const cb of syncListenersRef.current) {
              cb({ rev: 0, baseRev: 0, truncated: true, deltas: [] });
            }
          }
        }
        return;
      }
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "resync") {
          // Server can't serve us a sync packet (e.g. we never announced
          // binary support). Fall back to the full refetch.
          for (const cb of syncListenersRef.current) {
            cb({ rev: 0, baseRev: 0, truncated: true, deltas: [] });
          }
        } else if (msg.type === "delta") {
          // Pass the whole payload through — must include dir/reversed/vtCounts
          // (directional + authoritative-count fields), not just the basics.
          const { type: _type, ...delta } = msg;
          void _type;
          for (const cb of deltaListenersRef.current) {
            cb(delta as VoteDelta);
          }
        } else if (msg.type === "deltas" && Array.isArray(msg.items)) {
          // Server-coalesced batch (delta_hub): unwrap into individual deltas
          // in rev order so downstream gap detection and count application see
          // exactly the per-vote stream they always did.
          const items = [...msg.items].sort(
            (a, b) => (a.rev ?? 0) - (b.rev ?? 0));
          for (const item of items) {
            const { type: _t, ...delta } = item;
            void _t;
            for (const cb of deltaListenersRef.current) {
              cb(delta as VoteDelta);
            }
          }
        }
        // "init" and "keepalive" are handled silently
      } catch (e) {
        dwarn("ws", "bad message", e);
      }
    };

    ws.onclose = () => {
      dwarn("ws", `disconnected — reconnecting in ${backoffRef.current}ms`);
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

  const subscribeToSync = useCallback((listener: SyncListener) => {
    syncListenersRef.current.add(listener);
    return () => {
      syncListenersRef.current.delete(listener);
    };
  }, []);

  const subscribeToOpen = useCallback((listener: () => void) => {
    openListenersRef.current.add(listener);
    return () => {
      openListenersRef.current.delete(listener);
    };
  }, []);

  const requestSync = useCallback((sinceRev: number) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "sync", since: sinceRev }));
  }, []);

  const value = useMemo(
    () => ({ connectionStatus, subscribeToDelta, subscribeToSync, subscribeToOpen, requestSync }),
    [connectionStatus, subscribeToDelta, subscribeToSync, subscribeToOpen, requestSync],
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
