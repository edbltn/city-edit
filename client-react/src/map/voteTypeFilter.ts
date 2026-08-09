// ==========================================================================
// Vote-type visibility filter
// ==========================================================================
// Which vote types the map is currently DRAWING. The legend (the vote-type
// panel — see VoteTypeSelector) toggles labels in and out; everything that
// renders vote signal reads this one store:
//
//   block heat   — voteApply.topProposalDiffs, masked by legend index
//   edge heat    — GraphLayer's canvas path (no-block maps), per-type nets
//   PBTP pins    — topProposals.selectTopProposals via `allowLabel`
//   RBTP corridors — routeProposals.createRouteProposalJob via `allowLabel`
//
// The store holds HIDDEN labels, not visible ones, so the default is "show
// everything" and a vote type that appears later (a custom suggestion someone
// casts while you're looking at the map) is visible without anyone opting it
// in. Casting a type also force-clears it from the set — see
// castVote/noteVoteTypeCast — so you can never vote for something and watch
// nothing happen.
//
// Scoped per map slug and kept in sessionStorage: filtering is a browsing
// gesture for this visit, not a durable preference that could leave someone
// staring at an empty map weeks later wondering why.
// ==========================================================================

import { getMapSlug } from "./runtime";

const EMPTY: ReadonlySet<string> = new Set<string>();

let hidden: ReadonlySet<string> = EMPTY;
let loadedSlug: string | null = null;
/** Bumps on every change — cache key for filter-derived arrays (see GraphLayer). */
let version = 0;
const listeners = new Set<() => void>();

function storageKey(slug: string): string {
  return `cityedit_vt_hidden:${slug}`;
}

/** Load this map's hidden set on first read, and again if the map changed. */
function ensureLoaded(): void {
  const slug = getMapSlug();
  if (loadedSlug === slug) return;
  loadedSlug = slug;
  hidden = EMPTY;
  if (!slug) return;
  try {
    const raw = sessionStorage.getItem(storageKey(slug));
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) {
      hidden = new Set(parsed.filter((l): l is string => typeof l === "string"));
    }
  } catch {
    /* private mode / bad JSON — start unfiltered */
  }
}

function persist(): void {
  const slug = loadedSlug;
  if (!slug) return;
  try {
    if (hidden.size === 0) sessionStorage.removeItem(storageKey(slug));
    else sessionStorage.setItem(storageKey(slug), JSON.stringify([...hidden]));
  } catch {
    /* ignore */
  }
}

function commit(next: ReadonlySet<string>): void {
  if (next.size === hidden.size && [...next].every((l) => hidden.has(l))) return;
  hidden = next.size === 0 ? EMPTY : next;
  version++;
  persist();
  for (const fn of [...listeners]) fn();
}

/** The labels currently hidden from the map. Empty ⇒ everything is drawn. */
export function getHiddenVoteTypes(): ReadonlySet<string> {
  ensureLoaded();
  return hidden;
}

export function isVoteTypeVisible(label: string): boolean {
  return !getHiddenVoteTypes().has(label);
}

/** Cache key for anything derived from the filter (see GraphLayer's edge nets). */
export function getVoteTypeFilterVersion(): number {
  ensureLoaded();
  return version;
}

export function setVoteTypeVisible(label: string, visible: boolean): void {
  ensureLoaded();
  if (visible === !hidden.has(label)) return;
  const next = new Set(hidden);
  if (visible) next.delete(label);
  else next.add(label);
  commit(next);
}

export function toggleVoteTypeVisible(label: string): void {
  setVoteTypeVisible(label, !isVoteTypeVisible(label));
}

/** Un-hide a label — the cast path calls this so a vote is always visible. */
export function ensureVoteTypeVisible(label: string): void {
  if (label) setVoteTypeVisible(label, true);
}

export function showAllVoteTypes(): void {
  commit(EMPTY);
}

/**
 * Hide every label in `labels` except `keep` — the panel's "only" gesture
 * (isolate one proposal type) and its "none" gesture (keep nothing).
 */
export function showOnlyVoteTypes(labels: Iterable<string>, keep: Iterable<string> = []): void {
  const kept = new Set(keep);
  const next = new Set<string>();
  for (const l of labels) if (!kept.has(l)) next.add(l);
  commit(next);
}

/**
 * Per-legend-index visibility mask for a vote-type legend, or null when nothing
 * is hidden. Null is the "no filter" fast path every consumer short-circuits on,
 * so an unfiltered map does exactly the work it did before this feature.
 */
export function legendVisibilityMask(legend: readonly string[] | undefined | null): Uint8Array | null {
  const hiddenNow = getHiddenVoteTypes();
  if (hiddenNow.size === 0 || !legend?.length) return null;
  const mask = new Uint8Array(legend.length);
  for (let i = 0; i < legend.length; i++) {
    mask[i] = legend[i] && hiddenNow.has(legend[i]) ? 0 : 1;
  }
  return mask;
}

export function subscribeVoteTypeFilter(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}
