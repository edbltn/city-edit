// Tracks which (segment, vote-type) pairs the current user has already voted on,
// scoped by mode and persisted to localStorage. This is the single source of truth
// for vote-blocking in the UI: the Cast Vote button and the VoteTypeSelector both
// derive "already voted" from these keys, keeping the client consistent with the
// server's per-edge IP-hash dedup across page reloads.

const STORAGE_KEY = "dpm:votedSegments";

let votedKeys: Set<string> | null = null;

function load(): Set<string> {
  if (votedKeys) return votedKeys;

  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    votedKeys = new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    // Corrupt JSON or storage unavailable (private mode) — start empty.
    votedKeys = new Set();
  }

  return votedKeys;
}

function persist(keys: Set<string>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...keys]));
  } catch {
    // Ignore quota/availability failures — the in-memory set still works this session.
  }
}

export function edgeKey(mode: string, edgeId: number, voteType: string): string {
  return `${mode}|e|${edgeId}|${voteType}`;
}

export function pointKey(mode: string, lat: number, lng: number, voteType: string): string {
  return `${mode}|p|${lat.toFixed(6)},${lng.toFixed(6)}|${voteType}`;
}

export function hasVotedKey(key: string): boolean {
  return load().has(key);
}

export function markVotedKeys(keys: string[]): void {
  if (keys.length === 0) return;

  const set = load();
  for (const key of keys) {
    set.add(key);
  }
  persist(set);
}
