// ==========================================================================
// Route-votes cache — one in-flight request per edge set, answers never lost
// ==========================================================================
// /api/route-votes is asked the same question from two places (the route
// summary card and the hovered corridor) and re-asked every time votes move.
// This module owns the shared answer so those callers agree, and so that a
// burst of re-asks costs one request instead of one per bump.
//
// The rule that matters: an answer is keyed by the EDGE SET, not by whoever
// asked. Once rows come back for a key they are true for that key, so they go
// into the cache unconditionally — even if the asker has since re-rendered,
// re-asked, or gone away. Discarding an in-flight answer because the caller
// re-ran is what left the vote columns reading "‒" indefinitely: every vote
// that landed while the first request was open threw its reply away, and
// because nothing reached the cache the next attempt was another cold
// (undebounced) request that the next vote could throw away in turn.
//
// Pure except for the injected fetcher, so the coalescing rules are testable
// without a DOM or a network.

import { ROUTE_VOTES_CACHE_MAX } from "../../utils/blockSelection";
import type { VoteTypeRow } from "./spatialLookup";

/** Resolved rows per edge set. Insertion-ordered and bounded, so evicting the
 *  first key drops the least-recently-resolved entry. */
const cache = new Map<string, VoteTypeRow[]>();

interface Flight {
  promise: Promise<VoteTypeRow[] | null>;
  /** Someone asked again while this request was open — see loadRouteVoteRows. */
  stale: boolean;
}

const inflight = new Map<string, Flight>();

/** Rows already known for this edge set, or null if it has never resolved. */
export function cachedRouteVoteRows(key: string): VoteTypeRow[] | null {
  return cache.get(key) ?? null;
}

/** Test seam — drops every cached answer and forgets any open request. */
export function resetRouteVotesCache(): void {
  cache.clear();
  inflight.clear();
}

function remember(key: string, rows: VoteTypeRow[]): void {
  // Re-insert so the key counts as most recently resolved.
  cache.delete(key);
  cache.set(key, rows);
  while (cache.size > ROUTE_VOTES_CACHE_MAX) {
    cache.delete(cache.keys().next().value as string);
  }
}

/**
 * Resolve rows for `key`, fetching at most once at a time.
 *
 * A caller arriving while a request for the same key is open joins that
 * request rather than opening a second one. It still wants FRESH rows though
 * (it only re-asked because a vote landed), so the open flight is marked stale
 * and re-issues exactly once when it settles. That collapses a storm of vote
 * bumps into "the request now, plus one more after it" instead of one request
 * per bump.
 *
 * Never rejects: a failed request resolves to whatever is cached (or null), so
 * callers can't hang on a network blip.
 */
export function loadRouteVoteRows(
  key: string,
  fetchRows: () => Promise<VoteTypeRow[] | null>,
): Promise<VoteTypeRow[] | null> {
  const open = inflight.get(key);
  if (open) {
    open.stale = true;
    return open.promise;
  }

  const flight: Flight = { stale: false, promise: Promise.resolve(null) };
  // Start the request now rather than a microtask from now, so a second caller
  // in the same tick sees an open flight and joins it instead of opening its
  // own. The try/catch covers a fetcher that throws synchronously.
  let started: Promise<VoteTypeRow[] | null>;
  try {
    started = Promise.resolve(fetchRows());
  } catch {
    started = Promise.resolve(null);
  }
  flight.promise = started
    .catch(() => null)
    .then((rows) => {
      if (rows) remember(key, rows);
      if (flight.stale) {
        // Clear ourselves out first so the re-issue opens a fresh flight
        // rather than joining the one that is settling.
        if (inflight.get(key) === flight) inflight.delete(key);
        return loadRouteVoteRows(key, fetchRows);
      }
      return rows ?? cache.get(key) ?? null;
    })
    .finally(() => {
      // Only if we're still the current flight — the stale branch above may
      // have already replaced us.
      if (inflight.get(key) === flight) inflight.delete(key);
    });

  inflight.set(key, flight);
  return flight.promise;
}
