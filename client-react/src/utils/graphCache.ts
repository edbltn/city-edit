// ==========================================================================
// Graph cache — IndexedDB persistence for topology + vote arrays
// ==========================================================================
//
// The graph topology is large (multiple MB) and rarely changes, while the
// browser HTTP cache still re-parses it on every load. Persisting the parsed
// structure in IndexedDB, keyed by a server-provided version, lets repeat
// loads skip both the download and the JSON parse. Vote arrays are cached too
// so the heatmap can paint instantly from the last snapshot, then reconcile
// against the authoritative fetch / WebSocket deltas.

const DB_NAME = "desire-path-cache";
const STORE = "graph";
// v2: a pre-v2 client could persist topology bytes that came from a STALE browser
// HTTP-cache entry (old edge ids, served because /graph-topology was cached with
// max-age=86400 under a version-independent URL) keyed by the NEW graph version —
// poisoning the cache so the heatmap paints against wrong edge ids and crashes
// mobile Safari. Bumping the version clears the store so those devices re-fetch
// fresh (now version-busted; see GraphLayer topology fetch).
const DB_VERSION = 2;

const TOPOLOGY_KEY = "topology";
const TOPOLOGY_BIN_KEY = "topology-bin";
const votesKey = (mode: string) => `votes:${mode}`;

let dbPromise: Promise<IDBDatabase> | null = null;

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB unavailable"));
      return;
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      // Drop any prior store on upgrade: a pre-v2 client may have persisted
      // topology bytes from a stale HTTP-cache response under the current
      // version key. Clearing forces a fresh, version-busted re-fetch.
      if (db.objectStoreNames.contains(STORE)) db.deleteObjectStore(STORE);
      db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

async function idbGet<T>(key: string): Promise<T | undefined> {
  try {
    const db = await openDb();
    return await new Promise<T | undefined>((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const req = tx.objectStore(STORE).get(key);
      req.onsuccess = () => resolve(req.result as T | undefined);
      req.onerror = () => reject(req.error);
    });
  } catch {
    // Cache is best-effort — never let a storage error break loading.
    return undefined;
  }
}

async function idbSet(key: string, value: unknown): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(value, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // Ignore — quota/availability failures shouldn't surface to the user.
  }
}

export interface CachedTopology<T> {
  version: string;
  data: T;
}

/** Return cached topology if its version matches, else undefined. */
export async function getCachedTopology<T>(
  version: string,
): Promise<T | undefined> {
  const entry = await idbGet<CachedTopology<T>>(TOPOLOGY_KEY);
  if (entry && entry.version === version) return entry.data;
  return undefined;
}

export async function setCachedTopology<T>(
  version: string,
  data: T,
): Promise<void> {
  await idbSet(TOPOLOGY_KEY, { version, data } satisfies CachedTopology<T>);
}

/**
 * Binary topology cache. We persist the raw ArrayBuffer (a few tens of MB) rather
 * than the decoded node/edge arrays: structured-cloning millions of small arrays
 * into IndexedDB is itself a memory spike on mobile, while an ArrayBuffer clones
 * cheaply. On load it's decoded back into node/edge arrays (see decodeTopologyBin).
 */
export async function getCachedTopologyBin(
  version: string,
): Promise<ArrayBuffer | undefined> {
  const entry = await idbGet<{ version: string; data: ArrayBuffer }>(TOPOLOGY_BIN_KEY);
  if (entry && entry.version === version) return entry.data;
  return undefined;
}

export async function setCachedTopologyBin(
  version: string,
  data: ArrayBuffer,
): Promise<void> {
  await idbSet(TOPOLOGY_BIN_KEY, { version, data });
}

interface CachedVotes<T> {
  version: string;
  data: T;
}

/**
 * Return cached votes only if they were stored against the same graph version.
 * Vote arrays are indexed by edge id, so a graph change invalidates them.
 */
export async function getCachedVotes<T>(
  mode: string,
  version: string,
): Promise<T | undefined> {
  const entry = await idbGet<CachedVotes<T>>(votesKey(mode));
  if (entry && entry.version === version) return entry.data;
  return undefined;
}

export async function setCachedVotes<T>(
  mode: string,
  version: string,
  data: T,
): Promise<void> {
  await idbSet(votesKey(mode), { version, data } satisfies CachedVotes<T>);
}

/**
 * Wipe every persisted graph entry (topology + binary topology + all vote
 * snapshots). Used as the recovery path when a topology/vote mismatch is
 * detected or a render crashes: a poisoned cache must not survive into the next
 * load, or the crash repeats ("a problem repeatedly occurred"). Best-effort.
 */
export async function clearGraphCache(): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch {
    // Nothing more we can do — the caller still reloads/refetches fresh.
  }
}
