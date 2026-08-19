// ==========================================================================
// Debug logging + named debug tabs (docs/debugging.md)
// ==========================================================================
// One consistent way to see what the app is doing. Every debug line goes
// through dlog(channel, ...) and prints as `[channel] ...`, so both a human
// and an automated console reader (Claude's read_console_messages with
// pattern "\[cast\]|\[blocks\]|…") can filter reliably.
//
// Channels are off by default in normal use. Enable them per tab:
//   • open the app with ?tab=<name>   → names the tab AND enables TAB_CHANNELS
//   • add ?debug=cast,blocks (or ?debug=* / ?debug=off) to override that
//   • or in the console: cityedit.debug.enable("cast,blocks") / .enable("*")
//   • or persistently: localStorage.cityedit_debug = "*"
//
// Anything that can fire per hover, per delta or per frame goes through
// dburst() instead of dlog(), so a burst costs two lines, not two hundred.
//
// A named tab gets "[dbg:<name>]" appended to its <title> (kept there by a
// MutationObserver, since the app rewrites the title on map changes) and the
// name survives reloads via sessionStorage — so "the tab called eric" is
// findable by a person in the tab strip and by tooling in the tab list.

export type DebugChannel =
  | "topo"       // topology fetch/decode/cache (GTB blob, IndexedDB, block index)
  | "votes"      // /api/graph-votes loads, deltas, revision gaps
  | "cast"       // planBlockVote decisions + /api/vote responses
  | "store"      // local my-votes store resets/reconciles
  | "blocks"     // block heat/selection feature-state broadcasts
  | "proposals"  // route-proposal recomputes (counts + timing)
  | "maplibre"   // MapLibre lifecycle: load, webgl fallback, source errors
  | "sticker"    // scanned-sticker resolution: lookup, location, pinning
  | "audience"   // proposal view counts + live co-presence
  | "onboard"    // first-run flow: visitor probe, tile pick, step transitions
  | "ws";        // websocket connect/disconnect/messages

const ALL_CHANNELS: readonly DebugChannel[] = [
  "topo", "votes", "cast", "store", "blocks", "proposals", "maplibre",
  "sticker", "audience", "onboard", "ws",
];

// What a named ?tab=<name> turns on. Every channel is quiet enough now (the
// per-hover / per-delta streams go through dburst) that all-on stays readable,
// which is what a debug tab is for: you don't yet know which subsystem is
// lying to you. Narrow or widen per load with ?debug=<spec>.
const TAB_CHANNELS: readonly DebugChannel[] = ALL_CHANNELS;

const STORAGE_KEY = "cityedit_debug";
const TAB_KEY = "cityedit_tab_name";

let enabled = new Set<string>();

function parseSpec(spec: string | null | undefined): Set<string> {
  if (!spec) return new Set();
  if (spec.trim() === "*") return new Set(ALL_CHANNELS);
  return new Set(
    spec.split(",").map((s) => s.trim()).filter((s): s is DebugChannel =>
      (ALL_CHANNELS as readonly string[]).includes(s)),
  );
}

/** Log to `channel` if enabled. Values are passed straight to console.log, so
 *  objects stay expandable in devtools. No-op (one Set lookup) when off. */
export function dlog(channel: DebugChannel, ...args: unknown[]): void {
  if (!enabled.has(channel)) return;
  console.log(`[${channel}]`, ...args);
}

/** Always-on warn/error with the same greppable prefix. */
export function dwarn(channel: DebugChannel, ...args: unknown[]): void {
  console.warn(`[${channel}]`, ...args);
}
export function derror(channel: DebugChannel, ...args: unknown[]): void {
  console.error(`[${channel}]`, ...args);
}

// ── Bursty events ───────────────────────────────────────────────────────────
// Some things fire per hover, per delta, per frame. One line each turns the
// console into a scrollback that nobody reads, so those go through dburst:
// the first event of a burst prints as usual, the rest are counted, and when
// the burst goes quiet ONE summary line reports how many there were, how long
// it took, and what the last one said. `key` separates independent streams
// within a channel (e.g. "select" vs "heat" in `blocks`).

const BURST_QUIET_MS = 800;   // this much silence ends a burst
const BURST_MAX_MS = 5000;    // …and a burst that never stops reports anyway

type Burst = {
  channel: DebugChannel; count: number; last: string;
  t0: number; deadline: number; timer: ReturnType<typeof setTimeout>;
};
const bursts = new Map<string, Burst>();

function nowMs(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function flushBurst(key: string): void {
  const b = bursts.get(key);
  if (!b) return;
  bursts.delete(key);
  clearTimeout(b.timer);
  if (b.count > 0) {
    console.log(
      `[${b.channel}] +${b.count} more in ${Math.round(nowMs() - b.t0)}ms — last: ${b.last}`);
  }
}

/** Log `message` if `channel` is on, collapsing bursts into one summary line. */
export function dburst(channel: DebugChannel, key: string, message: string): void {
  if (!enabled.has(channel)) return;
  const k = `${channel}\u0000${key}`;
  const now = nowMs();
  let b = bursts.get(k);
  if (!b) {
    console.log(`[${channel}]`, message);
    b = { channel, count: 0, last: message, t0: now, deadline: now + BURST_MAX_MS,
          timer: 0 as unknown as ReturnType<typeof setTimeout> };
    bursts.set(k, b);
  } else {
    b.count += 1;
    b.last = message;
    clearTimeout(b.timer);
  }
  // Trailing-edge: the summary lands when the stream stops — but a stream that
  // never stops still reports every BURST_MAX_MS, so a runaway loop is visible
  // rather than hidden behind its own first line.
  b.timer = setTimeout(() => flushBurst(k), Math.max(0, Math.min(BURST_QUIET_MS, b.deadline - now)));
}

function enable(spec: string = "*"): string[] {
  for (const c of parseSpec(spec)) enabled.add(c);
  try { window.localStorage.setItem(STORAGE_KEY, [...enabled].join(",")); } catch { /* private mode */ }
  return [...enabled];
}

/** Test hook: set the enabled channels directly, bypassing URL and storage. */
export function _setDebugChannels(spec: string): void {
  enabled = parseSpec(spec);
  for (const b of bursts.values()) clearTimeout(b.timer);
  bursts.clear();
}

function disable(): void {
  enabled = new Set();
  for (const b of bursts.values()) clearTimeout(b.timer);
  bursts.clear();
  try { window.localStorage.removeItem(STORAGE_KEY); } catch { /* private mode */ }
}

// ── State registry (window.cityedit.dumpState) ─────────────────────────────
// Subsystems push facts here as they come up; dumpState() is the one-call
// health check ("is the topology loaded? did MapLibre load? what revision?").

const state: Record<string, unknown> = {};

/** Record a fact for dumpState(), e.g. debugState("maplibreLoaded", true). */
export function debugState(key: string, value: unknown): void {
  state[key] = value;
}

/** Attach a callable probe to window.cityedit (e.g. resolveAt(lat, lng) — the
 *  live selection resolver) so headless sessions can interrogate app logic
 *  directly instead of synthesizing mouse events. */
export function debugProbe(key: string, fn: (...args: never[]) => unknown): void {
  if (typeof window === "undefined") return;
  const w = window as unknown as Record<string, Record<string, unknown>>;
  if (w.cityedit) w.cityedit[key] = fn;
}

function dumpState(): Record<string, unknown> {
  return { tab: getTabName(), channels: [...enabled], ...state };
}

// ── Named debug tabs ────────────────────────────────────────────────────────

function getTabName(): string | null {
  try { return window.sessionStorage.getItem(TAB_KEY); } catch { return null; }
}

function applyTitleSuffix(name: string): void {
  const suffix = ` [dbg:${name}]`;
  const fix = () => {
    if (!document.title.endsWith(suffix)) document.title += suffix;
  };
  fix();
  // The app rewrites the title on map/theme changes; keep the tag on it.
  const el = document.querySelector("title");
  if (el) new MutationObserver(fix).observe(el, { childList: true });
}

/** Boot-time init: adopt ?tab=<name> (survives the app's URL canonicalization
 *  via sessionStorage), tag the title, and enable all channels for named tabs.
 *  Also restores any persisted channel spec. Called once from main.tsx. */
export function initDebug(): void {
  if (typeof window === "undefined") return;

  try { enabled = parseSpec(window.localStorage.getItem(STORAGE_KEY)); } catch { /* private mode */ }

  let name: string | null = null;
  try {
    name = new URLSearchParams(window.location.search).get("tab");
    if (name) window.sessionStorage.setItem(TAB_KEY, name);
    else name = getTabName();
  } catch { /* private mode */ }

  if (name) {
    applyTitleSuffix(name);
    for (const c of TAB_CHANNELS) enabled.add(c);
  }

  // ?debug=<spec> is the override, and it wins: "*" for everything, a comma
  // list for exactly those channels, "off" for none (a named tab you want
  // silent for one reload).
  let spec: string | null = null;
  try { spec = new URLSearchParams(window.location.search).get("debug"); } catch { /* private mode */ }
  if (spec != null) {
    enabled = spec.trim() === "off" ? new Set() : parseSpec(spec);
  }

  if (name || enabled.size > 0) {
    console.log(
      `[dbg]${name ? ` tab "${name}" —` : ""} channels: ${[...enabled].join(",") || "none"}`
      + ` (?debug=* for all, cityedit.debug.enable("blocks"), cityedit.dumpState())`);
  }

  (window as unknown as Record<string, unknown>).cityedit = {
    debug: { enable, disable, channels: () => [...enabled], all: ALL_CHANNELS },
    dumpState,
  };
}
