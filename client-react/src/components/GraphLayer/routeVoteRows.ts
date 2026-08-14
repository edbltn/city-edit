// Algorithm doc: docs/algorithms/07-counts.md
// ==========================================================================
// Route vote rows — DISTINCT-voter counts for a multi-block selection
// ==========================================================================
// A route cast fans ONE device's vote onto every edge of every block the
// corridor covers, so the local per-block breakdown counts that person once
// PER BLOCK. Summed across a 12-block corridor, one voter reads as 12. That
// inflated stand-in is what the diamond's hover card used to show before
// anything corrected it (it only ever corrected on a corridor the route card
// had already resolved this session).
//
// /api/route-votes is the honest number: DISTINCT devices per (vote type,
// direction) across the whole edge set. This hook is the single place that
// asks for it — the route-summary card and the hovered corridor both use it,
// share one cache keyed by the edge set, and therefore always agree.
//
// The rows are `null` until the answer lands. Callers render that as PENDING
// rather than substituting local sums: a number that is wrong for 300ms and
// then silently changes is worse than a number that visibly hasn't arrived.
// That makes losing an answer expensive — the card doesn't degrade, it just
// keeps reading "‒" — so the cache and the one-request-per-key rule live in
// routeVotesCache.ts, which never discards rows it has already been given.

import { useEffect, useMemo, useRef, useState } from "react";

import { CONFIG } from "../../config";
import { getMapSlug, passcodeHeaders } from "../../map/runtime";
import { routeVotesKey } from "../../utils/blockSelection";
import {
  cachedRouteVoteRows, loadRouteVoteRows, loadRouteVoteRowsBatch,
} from "./routeVotesCache";
import { ROUTE_VOTES_DEBOUNCE_MS, ROUTE_VOTES_EDGE_CAP, type VoteTypeRow } from "./spatialLookup";

/** Hover asks on a short delay so sweeping the cursor across a cluster of
 *  diamonds doesn't fire a request per diamond. */
export const ROUTE_VOTES_HOVER_DELAY_MS = 200;

export interface RouteVoteRowsOptions {
  /** Bumping this refetches (a cast of ours, someone else's delta). */
  refetchToken?: number;
  /** Delay before the FIRST request for an unseen edge set. The route card
   *  asks immediately (it's pinned and being read); hover waits out the sweep. */
  firstDelayMs?: number;
}

export interface RouteVoteRowsResult {
  /** Distinct-voter rows, or null while unresolved. */
  rows: VoteTypeRow[] | null;
  /** The edge set these rows describe (also the cache key). */
  key: string | null;
}

/**
 * Distinct-voter rows for `edgeIds` (a selection's block-edge union).
 *
 * Key-matched: one selection's rows can never render on another's card while
 * its own request is in flight. Refetches for an already-resolved key are
 * debounced so a burst of casts coalesces into one request; the previously
 * resolved rows keep rendering meanwhile, which is what makes a cast feel
 * immediate instead of blanking the counts.
 */
/**
 * The exact ids one edge set is asked about, plus their cache key.
 *
 * Every asker MUST go through this. The cache key is a signature of the ids
 * actually sent, so a caller that deduped differently — or capped at a
 * different length — would compute a different key for the same corridor and
 * quietly get its own copy of the answer. That is precisely the drift this
 * whole module exists to prevent, so the normalization lives in one function
 * and the hook and the batch prime both call it.
 */
export function routeVotesQuery(
  edgeIds: readonly number[] | null | undefined,
): { ids: number[]; key: string } | null {
  if (!edgeIds || edgeIds.length === 0) return null;
  const ids: number[] = [];
  const seen = new Set<number>();
  for (const e of edgeIds) {
    if (seen.has(e)) continue;
    seen.add(e);
    ids.push(e);
    // A merged foot-component block can union thousands of edges; past the
    // cap the count degrades gracefully (undercounts) rather than sending an
    // unbounded body.
    if (ids.length >= ROUTE_VOTES_EDGE_CAP) break;
  }
  return { ids, key: routeVotesKey(getMapSlug(), ids) };
}

/**
 * Resolve a whole list of edge sets (the corridors the map is labelling) in
 * ONE request, into the shared cache. Read the results back with
 * `cachedRouteVoteRows(query.key)`; sets the server declined to count stay
 * unresolved (null) rather than resolving to zero.
 */
export function primeRouteVoteRows(
  queries: readonly { ids: number[]; key: string }[],
): Promise<void> {
  return loadRouteVoteRowsBatch(queries, async (sets) => {
    const r = await fetch(`${CONFIG.apiUrl}/route-votes`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...passcodeHeaders() },
      body: JSON.stringify({ map: getMapSlug(), sets }),
    });
    if (!r.ok) return null;
    const j = await r.json();
    const results = j?.results;
    if (!Array.isArray(results)) return null;
    return results.map((entry: { rows?: VoteTypeRow[] | null } | null) =>
      entry?.rows ?? null);
  });
}

export function useRouteVoteRows(
  edgeIds: readonly number[] | null | undefined,
  { refetchToken = 0, firstDelayMs = 0 }: RouteVoteRowsOptions = {},
): RouteVoteRowsResult {
  const query = useMemo(() => routeVotesQuery(edgeIds), [edgeIds]);

  const [fetched, setFetched] = useState<{ key: string; rows: VoteTypeRow[] } | null>(null);
  // The effect below keys on `key`, not on `query` — `query` is a fresh object
  // whenever the caller's edgeIds array is, which for an unchanged selection
  // would refetch on every render. It still needs the live query to read `ids`
  // from and to tell "is this answer still for what we're showing", so it comes
  // through a ref, synced after each commit (declared FIRST, so it is already
  // current by the time the fetch effect below runs).
  const queryRef = useRef(query);
  useEffect(() => { queryRef.current = query; });
  const key = query?.key ?? null;

  useEffect(() => {
    const q = queryRef.current;
    if (!q) return;
    const delay = cachedRouteVoteRows(q.key) ? ROUTE_VOTES_DEBOUNCE_MS : firstDelayMs;
    const timer = window.setTimeout(() => {
      loadRouteVoteRows(q.key, async () => {
        const r = await fetch(`${CONFIG.apiUrl}/route-votes`, {
          method: "POST",
          headers: { "Content-Type": "application/json", ...passcodeHeaders() },
          body: JSON.stringify({ map: getMapSlug(), edge_ids: q.ids }),
        });
        if (!r.ok) return null;
        const j = await r.json();
        return (j?.rows as VoteTypeRow[] | undefined) ?? null;
      }).then((rows) => {
        if (!rows) return;
        // Key-scoped, NOT run-scoped. This effect re-runs on every refetchToken
        // bump — i.e. on every vote anyone casts anywhere on the map — and it
        // used to mark the open request cancelled on the way out, throwing away
        // an answer that was still perfectly good for the selection on screen.
        // Since nothing then reached the cache, the next run took the cold path
        // again and the columns could stay pending indefinitely under traffic.
        // What actually makes an answer stale is the SELECTION moving on, which
        // is exactly what this compares.
        if (queryRef.current?.key !== q.key) return;
        setFetched({ key: q.key, rows });
      });
    }, delay);
    // Only the not-yet-fired timer is worth cancelling; a request already in
    // flight is left to land and populate the cache.
    return () => window.clearTimeout(timer);
  }, [key, refetchToken, firstDelayMs]);

  if (!key) return { rows: null, key: null };
  return {
    key,
    rows: cachedRouteVoteRows(key) ?? (fetched?.key === key ? fetched.rows : null),
  };
}
