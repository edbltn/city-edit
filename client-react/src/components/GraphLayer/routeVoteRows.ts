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

import { useEffect, useMemo, useRef, useState } from "react";

import { CONFIG } from "../../config";
import { getMapSlug, passcodeHeaders } from "../../map/runtime";
import { ROUTE_VOTES_CACHE_MAX, routeVotesKey } from "../../utils/blockSelection";
import { ROUTE_VOTES_DEBOUNCE_MS, ROUTE_VOTES_EDGE_CAP, type VoteTypeRow } from "./spatialLookup";

// Session cache of resolved rows, keyed by map + capped edge set. Reopening a
// selection (or re-hovering a diamond) renders server truth immediately
// instead of flashing pending. Bounded and insertion-ordered, so dropping the
// first key evicts the least-recently-resolved entry.
const cache = new Map<string, VoteTypeRow[]>();

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
export function useRouteVoteRows(
  edgeIds: readonly number[] | null | undefined,
  { refetchToken = 0, firstDelayMs = 0 }: RouteVoteRowsOptions = {},
): RouteVoteRowsResult {
  const query = useMemo(() => {
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
  }, [edgeIds]);

  const [fetched, setFetched] = useState<{ key: string; rows: VoteTypeRow[] } | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;
  const key = query?.key ?? null;

  useEffect(() => {
    const q = queryRef.current;
    if (!q) return;
    let cancelled = false;
    const delay = cache.has(q.key) ? ROUTE_VOTES_DEBOUNCE_MS : firstDelayMs;
    const timer = window.setTimeout(() => {
      fetch(`${CONFIG.apiUrl}/route-votes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...passcodeHeaders() },
        body: JSON.stringify({ map: getMapSlug(), edge_ids: q.ids }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => {
          if (cancelled || !j?.rows) return;
          const rows = j.rows as VoteTypeRow[];
          cache.delete(q.key);
          cache.set(q.key, rows);
          while (cache.size > ROUTE_VOTES_CACHE_MAX) {
            cache.delete(cache.keys().next().value as string);
          }
          setFetched({ key: q.key, rows });
        })
        .catch(() => {});
    }, delay);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [key, refetchToken, firstDelayMs]);

  if (!key) return { rows: null, key: null };
  return {
    key,
    rows: cache.get(key) ?? (fetched?.key === key ? fetched.rows : null),
  };
}
