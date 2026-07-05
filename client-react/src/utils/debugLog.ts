// ==========================================================================
// Debug logging + named debug tabs (docs/debugging.md)
// ==========================================================================
// One consistent way to see what the app is doing. Every debug line goes
// through dlog(channel, ...) and prints as `[channel] ...`, so both a human
// and an automated console reader (Claude's read_console_messages with
// pattern "\[cast\]|\[blocks\]|…") can filter reliably.
//
// Channels are off by default in normal use. Enable them per tab:
//   • open the app with ?tab=<name>   → names the tab AND enables all channels
//   • or in the console: cityedit.debug.enable("cast,blocks") / .enable("*")
//   • or persistently: localStorage.cityedit_debug = "*"
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
  | "ws";        // websocket connect/disconnect/messages

const ALL_CHANNELS: readonly DebugChannel[] = [
  "topo", "votes", "cast", "store", "blocks", "proposals", "maplibre", "ws",
];

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

function enable(spec: string = "*"): string[] {
  for (const c of parseSpec(spec)) enabled.add(c);
  try { window.localStorage.setItem(STORAGE_KEY, [...enabled].join(",")); } catch { /* private mode */ }
  return [...enabled];
}

function disable(): void {
  enabled = new Set();
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
    for (const c of ALL_CHANNELS) enabled.add(c);
    console.log(`[dbg] tab "${name}" — all debug channels on. cityedit.debug / cityedit.dumpState() available.`);
  }

  (window as unknown as Record<string, unknown>).cityedit = {
    debug: { enable, disable, channels: () => [...enabled], all: ALL_CHANNELS },
    dumpState,
  };
}
