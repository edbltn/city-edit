// ==========================================================================
// Vote sources — where the votes under a card came from.
//
// Imported votes (e.g. nyc-proposals' official NYC DOT projects, scraped from
// nycdotprojects.info) carry a synthetic voter per source document. This
// resolves a selection's edges to those documents via /api/vote-sources, so a
// card can cite the actual proposals that produced the votes beneath it.
//
// Only the two INTERACTIVE cards (a pinned point, a route summary) ask: they
// persist while you read them. Transient hover cards skip it — a request per
// mouse-over, for links you can't click before the card vanishes.
// ==========================================================================

import { useEffect, useMemo, useRef, useState } from "react";

import { CONFIG } from "../../config";
import { getMapSlug, passcodeHeaders } from "../../map/runtime";
import { routeVotesKey } from "../../utils/blockSelection";

/** A source document behind a vote: the page the proposal was scraped from. */
export interface VoteSource {
  url: string;
  /** The proposal's own title — the anchor's tooltip. */
  title?: string;
}

/** Vote-type label → the sources whose votes are on the queried edges. */
export type SourcesByLabel = Record<string, VoteSource[]>;

const EMPTY: SourcesByLabel = {};

// Selections revisit constantly (re-click, vote, pan back). Keyed by the same
// map+edges key the route-votes cache uses; bounded and insertion-ordered, so
// dropping the first key evicts the least-recently-resolved entry.
const CACHE_MAX = 64;
const cache = new Map<string, SourcesByLabel>();

/**
 * The source documents behind the votes on `edgeIds`, by vote-type label.
 *
 * Returns {} until the fetch lands (and for maps with no source registry, which
 * is every crowdsourced map — the endpoint answers empty and the card simply
 * shows no links). Key-matched, so one selection's sources can never render on
 * another selection's card while its fetch is in flight.
 */
export function useVoteSources(edgeIds: ArrayLike<number> | null | undefined): SourcesByLabel {
  const query = useMemo(() => {
    if (!edgeIds || edgeIds.length === 0) return null;
    const ids: number[] = [];
    const seen = new Set<number>();
    for (let i = 0; i < edgeIds.length; i++) {
      const e = edgeIds[i];
      if (!seen.has(e)) {
        seen.add(e);
        ids.push(e);
      }
    }
    return { ids, key: routeVotesKey(getMapSlug(), ids) };
  }, [edgeIds]);

  const [fetched, setFetched] = useState<{ key: string; sources: SourcesByLabel } | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;
  const key = query?.key ?? null;

  useEffect(() => {
    const q = queryRef.current;
    if (!q || cache.has(q.key)) return;
    let cancelled = false;
    fetch(`${CONFIG.apiUrl}/vote-sources`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...passcodeHeaders() },
      body: JSON.stringify({ map: getMapSlug(), edge_ids: q.ids }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (cancelled || !j?.sources) return;
        const sources = j.sources as SourcesByLabel;
        cache.set(q.key, sources);
        while (cache.size > CACHE_MAX) {
          cache.delete(cache.keys().next().value as string);
        }
        setFetched({ key: q.key, sources });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [key]);

  if (!key) return EMPTY;
  return cache.get(key) ?? (fetched?.key === key ? fetched.sources : EMPTY);
}
