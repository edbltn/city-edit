// ==========================================================================
// Vote store — the single source of truth for "how this browser has voted"
// ==========================================================================
// Replaces the two former stores (votedSegments.ts + myVotes.ts), which kept
// the route-cast "already voted" set and the modal +/- direction in separate
// key formats that never reconciled. Now both read/write one store.
//
// Primary layer (`byId`): keyed by the packed identity pack(edgeId, mode, vtId)
// — the SAME integer the backend uses (see voteKey.ts) — value = direction
// (1 up | -1 down). This is the fast "bit-vector" lookup: an O(1) integer-keyed
// Map probe per edge for route-wide availability.
//
// Fallback layer (`byLabel`): for a brand-new custom vote type whose id the
// server hasn't assigned yet (so it can't be packed). Migrated into `byId` as
// soon as setVoteTypeMap learns the id. Tiny and short-lived.
//
// The server (/api/my-votes, vote deltas) is authoritative across devices;
// reconcileEdge folds its truth in.

import { packVoteKey, modeToInt } from "./voteKey";

export type VoteDirection = 1 | -1;
export type Coverage = {
  /** edges already voted in the requested direction */
  atTarget: number[];
  /** edges holding the opposite direction (a cast would reverse them) */
  opposite: number[];
  /** edges with no vote from this user */
  unvoted: number[];
};

const ID_STORAGE_KEY = "cityedit_votes_v2"; // { packedKey: direction }
const LABEL_STORAGE_KEY = "cityedit_votes_lbl_v2"; // { "mode|edge|label": direction }

let byId: Map<number, VoteDirection> | null = null;
let byLabel: Map<string, VoteDirection> | null = null;
let labelToId: Map<string, number> = new Map();

// ── persistence ────────────────────────────────────────────────────────────

function loadById(): Map<number, VoteDirection> {
  if (byId) return byId;
  byId = new Map();
  if (typeof window === "undefined") return byId;
  try {
    const raw = window.localStorage.getItem(ID_STORAGE_KEY);
    if (raw) {
      for (const [k, v] of Object.entries(JSON.parse(raw) as Record<string, number>)) {
        if (v === 1 || v === -1) byId.set(Number(k), v);
      }
    }
  } catch {
    // corrupt / unavailable — start empty
  }
  return byId;
}

function loadByLabel(): Map<string, VoteDirection> {
  if (byLabel) return byLabel;
  byLabel = new Map();
  if (typeof window === "undefined") return byLabel;
  try {
    const raw = window.localStorage.getItem(LABEL_STORAGE_KEY);
    if (raw) {
      for (const [k, v] of Object.entries(JSON.parse(raw) as Record<string, number>)) {
        if (v === 1 || v === -1) byLabel.set(k, v);
      }
    }
  } catch {
    // ignore
  }
  return byLabel;
}

function persistById() {
  if (typeof window === "undefined" || !byId) return;
  try {
    const obj: Record<string, number> = {};
    for (const [k, v] of byId) obj[k] = v;
    window.localStorage.setItem(ID_STORAGE_KEY, JSON.stringify(obj));
  } catch {
    // ignore quota / private-mode
  }
}

function persistByLabel() {
  if (typeof window === "undefined" || !byLabel) return;
  try {
    const obj: Record<string, number> = {};
    for (const [k, v] of byLabel) obj[k] = v;
    window.localStorage.setItem(LABEL_STORAGE_KEY, JSON.stringify(obj));
  } catch {
    // ignore
  }
}

// ── vote-type id resolution ──────────────────────────────────────────────────

/**
 * Update the label→id resolver from a graph-votes payload's `vote_types`
 * (id→label). Any pending label-keyed votes whose id is now known migrate into
 * the fast packed store.
 */
export function setVoteTypeMap(voteTypes: Record<string, string> | undefined) {
  if (!voteTypes) return;
  let learnedNew = false;
  for (const [idStr, label] of Object.entries(voteTypes)) {
    if (!labelToId.has(label)) {
      labelToId.set(label, Number(idStr));
      learnedNew = true;
    }
  }
  if (learnedNew) migrateKnownLabels();
}

function migrateKnownLabels() {
  const lbl = loadByLabel();
  if (lbl.size === 0) return;
  const ids = loadById();
  let changed = false;
  for (const [key, dir] of [...lbl]) {
    // key = `${mode}|${edgeId}|${label}`
    const sep1 = key.indexOf("|");
    const sep2 = key.indexOf("|", sep1 + 1);
    const mode = key.slice(0, sep1);
    const edgeId = Number(key.slice(sep1 + 1, sep2));
    const label = key.slice(sep2 + 1);
    const vtId = labelToId.get(label);
    if (vtId !== undefined) {
      ids.set(packVoteKey(edgeId, modeToInt(mode), vtId), dir);
      lbl.delete(key);
      changed = true;
    }
  }
  if (changed) {
    persistById();
    persistByLabel();
    notify();
  }
}

function labelKey(mode: string, edgeId: number, label: string): string {
  return `${mode}|${edgeId}|${label}`;
}

// ── change notification ────────────────────────────────────────────────────────
// This store is module-level mutable state shared by every vote view: the
// top-bar banner's +/- buttons, the proposal modal's +/- rows, and the heatmap.
// They ALL must re-read after ANY mutation, no matter which one triggered it —
// or their views silently drift apart. (The bug this fixes: un-clicking [+] in
// the modal left the banner's [+] stuck "cast", because the modal bumped its own
// re-render counter and the banner never heard about it.)
//
// So mutations are the single source of change too: each bumps `version` and
// notifies subscribers. React views subscribe via useVotesVersion()
// (useSyncExternalStore) and re-render together. DO NOT reintroduce per-consumer
// vote "version" counters — that is exactly what drifts. Read the version from
// the hook wherever you call getVote()/coverage().

let version = 0;
const listeners = new Set<() => void>();

function notify() {
  version += 1;
  for (const fn of listeners) fn();
}

/** Subscribe to any vote change; returns an unsubscribe fn. For useSyncExternalStore. */
export function subscribeVotes(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Monotonic version, bumped on every mutation. Stable snapshot for useSyncExternalStore. */
export function getVotesVersion(): number {
  return version;
}

// ── public API ───────────────────────────────────────────────────────────────

/** Current direction for (mode, edge, vote type): 1 (up), -1 (down), 0 (none). */
export function getVote(mode: string, edgeId: number, label: string): VoteDirection | 0 {
  if (!label) return 0;
  const vtId = labelToId.get(label);
  if (vtId !== undefined) {
    return loadById().get(packVoteKey(edgeId, modeToInt(mode), vtId)) ?? 0;
  }
  return loadByLabel().get(labelKey(mode, edgeId, label)) ?? 0;
}

/**
 * Mutate one entry WITHOUT persisting or notifying. Returns true if the stored
 * direction actually changed. The public setters below batch persist + a single
 * notify() around one or more writes, so a multi-edge cast re-renders views once
 * rather than once per edge.
 */
function writeVote(
  mode: string,
  edgeId: number,
  label: string,
  direction: VoteDirection | 0,
): boolean {
  if (!label) return false;
  const vtId = labelToId.get(label);
  if (vtId !== undefined) {
    const ids = loadById();
    const k = packVoteKey(edgeId, modeToInt(mode), vtId);
    const prev = ids.get(k) ?? 0;
    if (direction === 0) ids.delete(k);
    else ids.set(k, direction);
    return direction !== prev;
  }
  const lbl = loadByLabel();
  const k = labelKey(mode, edgeId, label);
  const prev = lbl.get(k) ?? 0;
  if (direction === 0) lbl.delete(k);
  else lbl.set(k, direction);
  return direction !== prev;
}

/** Set (direction 1/-1) or clear (0) the local vote for one (mode, edge, type). */
export function setVote(
  mode: string,
  edgeId: number,
  label: string,
  direction: VoteDirection | 0,
) {
  if (writeVote(mode, edgeId, label, direction)) {
    persistById();
    persistByLabel();
    notify();
  }
}

/** Apply the same direction (or clear) to many edges at once. */
export function setVotes(
  mode: string,
  edgeIds: number[],
  label: string,
  direction: VoteDirection | 0,
) {
  let changed = false;
  for (const eid of edgeIds) {
    changed = writeVote(mode, eid, label, direction) || changed;
  }
  if (changed) {
    persistById();
    persistByLabel();
    notify();
  }
}

/**
 * Classify a set of edges against a target direction, for the multi-select
 * availability check: which are already at `dir`, which hold the opposite
 * (a cast reverses them), which are unvoted.
 */
export function coverage(
  mode: string,
  edgeIds: number[],
  label: string,
  dir: VoteDirection,
): Coverage {
  const atTarget: number[] = [];
  const opposite: number[] = [];
  const unvoted: number[] = [];
  for (const eid of edgeIds) {
    const v = getVote(mode, eid, label);
    if (v === dir) atTarget.push(eid);
    else if (v === 0) unvoted.push(eid);
    else opposite.push(eid);
  }
  return { atTarget, opposite, unvoted };
}

// ── Block coverage (docs/three-layer-model.md §4.1) ──────────────────────────
// Blocks are the interaction grain: a block "holds" a direction when I hold
// ≥1 edge-vote of that (type, direction) inside it. This store stays free of
// topology imports — callers materialize each touched block's edge list (via
// graphTopology's blockIndex, or `[e]` singletons) and pass them in.

export type CoverageLevel = "all" | "some" | "none";

export interface BlockCoverage {
  up: CoverageLevel;
  down: CoverageLevel;
}

/**
 * Per-direction coverage of `blocks` for (mode, label): `all` when every block
 * holds the direction, `some` when at least one does, `none` otherwise.
 */
export function blockCoverage(
  mode: string,
  blocks: ArrayLike<number>[],
  label: string,
): BlockCoverage {
  let upBlocks = 0;
  let downBlocks = 0;
  for (const block of blocks) {
    let hasUp = false;
    let hasDown = false;
    for (let i = 0; i < block.length; i++) {
      const v = getVote(mode, block[i], label);
      if (v === 1) hasUp = true;
      else if (v === -1) hasDown = true;
      if (hasUp && hasDown) break;
    }
    if (hasUp) upBlocks++;
    if (hasDown) downBlocks++;
  }
  const level = (n: number): CoverageLevel =>
    blocks.length > 0 && n === blocks.length ? "all" : n > 0 ? "some" : "none";
  return { up: level(upBlocks), down: level(downBlocks) };
}

/** Every edge in `blocks` where I currently hold a `label` vote (either direction). */
export function myVotesInBlocks(
  mode: string,
  blocks: ArrayLike<number>[],
  label: string,
): Map<number, VoteDirection> {
  const mine = new Map<number, VoteDirection>();
  for (const block of blocks) {
    for (let i = 0; i < block.length; i++) {
      const eid = block[i];
      if (mine.has(eid)) continue;
      const v = getVote(mode, eid, label);
      if (v !== 0) mine.set(eid, v);
    }
  }
  return mine;
}

/**
 * Reconcile against authoritative server state for one edge. `serverLabels`
 * maps label → direction; the server wins for the labels it reports. Local-only
 * entries (server lagging a fresh optimistic vote) are left untouched.
 */
export function reconcileEdge(
  mode: string,
  edgeId: number,
  serverLabels: Record<string, number>,
) {
  let changed = false;
  for (const [label, dir] of Object.entries(serverLabels)) {
    if (dir === 1 || dir === -1) changed = writeVote(mode, edgeId, label, dir) || changed;
  }
  if (changed) {
    persistById();
    persistByLabel();
    notify();
  }
}

/** Test-only: reset all in-memory + persisted state. */
export function _resetVoteStore() {
  byId = null;
  byLabel = null;
  labelToId = new Map();
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(ID_STORAGE_KEY);
      window.localStorage.removeItem(LABEL_STORAGE_KEY);
    } catch {
      // ignore
    }
  }
  notify();
}
