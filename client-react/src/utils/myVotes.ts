// ==========================================================================
// "My votes" cache
// ==========================================================================
// A localStorage-backed record of this browser's directional votes, keyed by
// `${mode}:${edgeId}:${voteType}` → 1 (up) | -1 (down). Drives instant +/-
// button state in proposal modals; reconciled against the server (which is
// authoritative across devices) via /api/my-votes when a modal opens.

export type VoteDirection = 1 | -1;

const STORAGE_KEY = "cityedit_my_votes";

let store: Record<string, VoteDirection> | null = null;

function load(): Record<string, VoteDirection> {
  if (store) return store;
  if (typeof window === "undefined") {
    store = {};
    return store;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    store = raw ? JSON.parse(raw) : {};
  } catch {
    store = {};
  }
  return store!;
}

function persist() {
  if (typeof window === "undefined" || !store) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  } catch {
    // ignore quota / private-mode failures
  }
}

function key(mode: string, edgeId: number, voteType: string): string {
  return `${mode}:${edgeId}:${voteType}`;
}

/** Current direction for a proposal: 1 (up), -1 (down), or 0 (no vote). */
export function getMyVote(mode: string, edgeId: number, voteType: string): VoteDirection | 0 {
  return load()[key(mode, edgeId, voteType)] ?? 0;
}

/** Set (or clear, when direction is 0) the local vote for a proposal. */
export function setMyVote(
  mode: string,
  edgeId: number,
  voteType: string,
  direction: VoteDirection | 0,
) {
  const s = load();
  const k = key(mode, edgeId, voteType);
  if (direction === 0) delete s[k];
  else s[k] = direction;
  persist();
}

/**
 * Reconcile against authoritative server state for a single edge.
 * `serverLabels` maps voteType → direction. Server values win for the labels
 * it reports; local-only entries (server lagging a fresh optimistic vote) are
 * left untouched.
 */
export function reconcileEdge(
  mode: string,
  edgeId: number,
  serverLabels: Record<string, number>,
) {
  const s = load();
  for (const [label, dir] of Object.entries(serverLabels)) {
    if (dir === 1 || dir === -1) s[key(mode, edgeId, label)] = dir;
  }
  persist();
}
