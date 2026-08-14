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

/** The server's answer to one view report: how many distinct people have
 *  opened a proposal of this type covering any part of this one, and whether
 *  the sketch behind it is still small enough to be exact. */
export interface ViewCount {
  n: number;
  exact: boolean;
}

/** How long to wait for a `views` reply before giving up on it. Generous:
 *  the server drains inbound once per socket tick, so a second is normal. */
const VIEW_REPLY_TIMEOUT_MS = 8000;

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
  /** Distinct people with this map open and in the foreground, INCLUDING us.
   *  0 means "we don't know" as well as "nobody" — both render as silence,
   *  which is the only safe reading of an unknown. */
  viewerCount: number;
  /** Report that a proposal card has been read, and get its audience back.
   *  Resolves null if the socket is down or the reply never lands: a count we
   *  did not hear is not a count we may guess at. */
  reportProposalView: (
    label: string, blockIds: number[]) => Promise<ViewCount | null>;
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

  // ── Audience state (co-presence + proposal view counts) ───────────────────
  // Both ride this socket rather than a channel of their own. A second
  // connection for a viewer count would double the connection count on every
  // map, and — worse for a feature whose entire product is trustworthiness —
  // it would let presence and votes disagree about whether a client is still
  // there.
  const [viewerCount, setViewerCount] = useState(0);
  // In-flight `view` reports, keyed by the token echoed back in the reply, so
  // an answer that lands after the reader has moved to another card is
  // discarded rather than painted onto the wrong proposal.
  const pendingViewsRef = useRef<Map<string, (v: ViewCount | null) => void>>(new Map());
  const viewSeqRef = useRef(0);

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
      // A fresh connection is assumed VISIBLE by the server, which is right
      // for the common case and wrong for a reconnect that happened while the
      // tab was buried. Correct it immediately rather than let a background
      // tab stand in someone's room.
      if (document.hidden) {
        try { ws.send(JSON.stringify({ type: "here", visible: false })); }
        catch { /* the onclose path will retry the whole connection */ }
      }
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
        } else if (msg.type === "presence") {
          // Server-pushed and edge-triggered: it only arrives when the number
          // actually changed, so a quiet map costs nothing.
          setViewerCount(typeof msg.n === "number" && msg.n > 0 ? msg.n : 0);
        } else if (msg.type === "views") {
          const resolve = pendingViewsRef.current.get(String(msg.k));
          if (resolve) {
            pendingViewsRef.current.delete(String(msg.k));
            resolve({ n: Number(msg.n) || 0, exact: msg.exact !== false });
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
      // We can no longer see the room, so we must stop claiming to. Leaving
      // the last count on screen would keep asserting company we have no
      // evidence for — the one direction this feature cannot be wrong in.
      setViewerCount(0);
      for (const resolve of pendingViewsRef.current.values()) resolve(null);
      pendingViewsRef.current.clear();
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

  // "You are looking at this with N other people" has to mean LOOKING. This
  // app throttles background tabs hard enough that a buried viewer is doing
  // nothing at all, and counting them would pad the number with people who
  // are, truthfully, elsewhere. visibilitychange is not throttled, so one
  // message on each transition keeps the room honest without a timer.
  useEffect(() => {
    const report = () => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      try {
        ws.send(JSON.stringify({ type: "here", visible: !document.hidden }));
      } catch { /* reconnect will re-report on open */ }
      // Hidden means we stop being told the count, so stop showing one.
      if (document.hidden) setViewerCount(0);
    };
    document.addEventListener("visibilitychange", report);
    // bfcache restore doesn't fire visibilitychange in every browser.
    window.addEventListener("pageshow", report);
    return () => {
      document.removeEventListener("visibilitychange", report);
      window.removeEventListener("pageshow", report);
    };
  }, []);

  const reportProposalView = useCallback(
    (label: string, blockIds: number[]): Promise<ViewCount | null> => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN
          || !label || blockIds.length === 0 || document.hidden) {
        return Promise.resolve(null);
      }
      const key = String(++viewSeqRef.current);
      return new Promise<ViewCount | null>((resolve) => {
        let done = false;
        const settle = (v: ViewCount | null) => {
          if (done) return;
          done = true;
          pendingViewsRef.current.delete(key);
          resolve(v);
        };
        pendingViewsRef.current.set(key, settle);
        setTimeout(() => settle(null), VIEW_REPLY_TIMEOUT_MS);
        try {
          ws.send(JSON.stringify(
            { type: "view", k: key, t: label, b: blockIds }));
        } catch {
          settle(null);
        }
      });
    }, []);

  const value = useMemo(
    () => ({
      connectionStatus, subscribeToDelta, subscribeToSync, subscribeToOpen,
      requestSync, viewerCount, reportProposalView,
    }),
    [connectionStatus, subscribeToDelta, subscribeToSync, subscribeToOpen,
      requestSync, viewerCount, reportProposalView],
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
