// ==========================================================================
// Pending optimistic casts — the ledger that keeps a fresh vote from popping
// ==========================================================================
// An optimistic write has to survive until the server's own echo of it lands,
// and two background refreshes will happily walk over it in the meantime:
//
//   /api/my-votes   → resetMapVotes / reconcileEdge, which delete local rows
//                     the response doesn't confirm. A snapshot READ before the
//                     vote was persisted lacks it, so the button un-highlights.
//   /api/graph-votes→ the SWR refresh installs a whole new count snapshot over
//                     graphDataRef, discarding the optimistic edge/block bumps.
//
// Neither refresh knows which local entries are speculative, and it can't be
// inferred from the store — a confirmed vote and an optimistic one are the same
// row. So a cast registers here for the window between "dispatched" and "the
// server's delta for it applied", and both refresh paths re-apply what is still
// pending after they install server truth.
//
// A cast leaves the ledger when
//   · its authoritative delta arrives (settlePendingCastsForDelta), or
//   · it fails and rolls back, or
//   · PENDING_CAST_TTL_MS passes — the backstop for a WebSocket that dropped
//     the delta, so a dead socket can't shield a stale entry forever.

import type { VoteDirection } from "./voteStore";

/** How long a cast may shield its optimistic write without its delta arriving. */
export const PENDING_CAST_TTL_MS = 20_000;

export interface PendingTransition {
  edges: number[];
  prevDir: number;
  newDir: number;
}

export interface PendingBlockDelta {
  /** Real block id (singleton pseudo-blocks never reach the aggregate). */
  block: number;
  /** Move in the block's DEDUPED up/down count: -1, 0 or +1. */
  up: number;
  down: number;
}

export interface PendingVoteCast {
  id: number;
  mode: string;
  label: string;
  /** Edge → the direction this cast wrote. The shield the store reads. */
  edges: Map<number, VoteDirection | 0>;
  /** The same writes as transitions, for re-applying to fresh count arrays. */
  groups: PendingTransition[];
  blockDeltas: PendingBlockDelta[];
  /** True once the POST succeeded: still shielding, no longer rollback-able. */
  confirmed: boolean;
}

let nextId = 1;
const casts = new Map<number, PendingVoteCast>();
const timers = new Map<number, ReturnType<typeof setTimeout>>();

/** `${mode}|${label}` — the grain a press supersedes an earlier press at. */
function controlKey(mode: string, label: string): string {
  return `${mode}|${label}`;
}

/**
 * Record an optimistic cast. Returns its id; hand that back to
 * `confirmPendingCast` / `resolvePendingCast` when it settles.
 */
export function registerPendingCast(
  cast: Omit<PendingVoteCast, "id" | "confirmed">,
): number {
  const id = nextId++;
  casts.set(id, { ...cast, id, confirmed: false });
  timers.set(id, setTimeout(() => resolvePendingCast(id), PENDING_CAST_TTL_MS));
  return id;
}

/** The POST came back OK: keep shielding, but this cast can no longer roll back. */
export function confirmPendingCast(id: number) {
  const cast = casts.get(id);
  if (cast) cast.confirmed = true;
}

export function resolvePendingCast(id: number) {
  const timer = timers.get(id);
  if (timer !== undefined) {
    clearTimeout(timer);
    timers.delete(id);
  }
  casts.delete(id);
}

/**
 * Is this still the newest press on its control?
 *
 * Rollback reads it: a press that failed AFTER a later press on the same
 * (mode, vote type) already re-wrote the same edges must NOT restore what it
 * saw, or it would undo the newer press instead of its own. The newer press
 * owns the state and the server's response to it is what reconciles.
 */
export function isLatestPendingCast(id: number): boolean {
  const cast = casts.get(id);
  if (!cast) return false;
  const key = controlKey(cast.mode, cast.label);
  for (const other of casts.values()) {
    if (other.id > id && controlKey(other.mode, other.label) === key) return false;
  }
  return true;
}

/** Every cast still in flight for `mode`, oldest first (application order). */
export function pendingCastsFor(mode: string): PendingVoteCast[] {
  return [...casts.values()]
    .filter((c) => c.mode === mode)
    .sort((a, b) => a.id - b.id);
}

/**
 * The direction a pending cast wrote for (mode, edge, label), or undefined when
 * nothing speculative covers it. This is what tells a server snapshot's silence
 * about an edge apart from "the vote was retracted".
 */
export function pendingDirection(
  mode: string, edgeId: number, label: string,
): VoteDirection | 0 | undefined {
  let found: VoteDirection | 0 | undefined;
  for (const cast of casts.values()) {
    if (cast.mode !== mode || cast.label !== label) continue;
    const dir = cast.edges.get(edgeId);
    if (dir !== undefined) found = dir; // later cast wins
  }
  return found;
}

/** Whether any cast is pending at all — the cheap guard on the refresh paths. */
export function hasPendingCasts(): boolean {
  return casts.size > 0;
}

/**
 * Settle every pending cast this delta is the echo of: same mode + vote type,
 * and at least one edge in common. The server publishes exactly the edges it
 * changed, so an intersection means our optimistic guess has been replaced by
 * authoritative counts and the shield is no longer needed.
 */
export function settlePendingCastsForDelta(
  mode: string, label: string, edges: readonly number[],
): void {
  if (casts.size === 0 || edges.length === 0) return;
  const changed = new Set(edges);
  for (const cast of [...casts.values()]) {
    if (cast.mode !== mode || cast.label !== label) continue;
    for (const eid of cast.edges.keys()) {
      if (changed.has(eid)) { resolvePendingCast(cast.id); break; }
    }
  }
}

/** Test-only: drop the whole ledger. */
export function _resetPendingCasts() {
  for (const timer of timers.values()) clearTimeout(timer);
  timers.clear();
  casts.clear();
  nextId = 1;
}
