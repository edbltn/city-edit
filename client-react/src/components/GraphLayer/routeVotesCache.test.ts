import { beforeEach, describe, expect, it, vi } from "vitest";

import { ROUTE_VOTES_CACHE_MAX } from "../../utils/blockSelection";
import {
  cachedRouteVoteRows,
  loadRouteVoteRows,
  resetRouteVotesCache,
} from "./routeVotesCache";
import type { VoteTypeRow } from "./spatialLookup";

const rows = (up: number): VoteTypeRow[] => [{ label: "Add bike lane", up, down: 0 }];

/** A fetcher whose reply we release by hand, so "while the request is open" is
 *  an actual state and not a timing guess. */
function deferred() {
  let release!: (r: VoteTypeRow[] | null) => void;
  const promise = new Promise<VoteTypeRow[] | null>((res) => { release = res; });
  return { promise, release };
}

beforeEach(() => resetRouteVotesCache());

describe("loadRouteVoteRows", () => {
  it("keeps an answer that arrives after the asker re-asked", async () => {
    // The regression: a vote landing mid-flight used to cancel the open
    // request, so its reply was dropped and nothing was ever cached — the
    // card's counts stayed pending.
    const d = deferred();
    const fetchRows = vi.fn(() => d.promise);

    const first = loadRouteVoteRows("k", fetchRows);
    // ...caller re-renders and asks again while the first is still open.
    const second = loadRouteVoteRows("k", fetchRows);

    d.release(rows(3));
    await first;
    await second;

    expect(cachedRouteVoteRows("k")).toEqual(rows(3));
  });

  it("coalesces concurrent asks into one request plus one trailing refetch", async () => {
    const first = deferred();
    const second = deferred();
    const fetchRows = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    const a = loadRouteVoteRows("k", fetchRows);
    loadRouteVoteRows("k", fetchRows);
    loadRouteVoteRows("k", fetchRows);
    expect(fetchRows).toHaveBeenCalledTimes(1); // three asks, one socket

    first.release(rows(1));
    await Promise.resolve();
    await Promise.resolve();
    // The asks that arrived mid-flight wanted FRESH rows, so exactly one more
    // trip happens — not one per ask.
    expect(fetchRows).toHaveBeenCalledTimes(2);

    second.release(rows(2));
    expect(await a).toEqual(rows(2));
    expect(cachedRouteVoteRows("k")).toEqual(rows(2));
  });

  it("does not refetch when nothing asked again mid-flight", async () => {
    const fetchRows = vi.fn(async () => rows(1));
    await loadRouteVoteRows("k", fetchRows);
    expect(fetchRows).toHaveBeenCalledTimes(1);
  });

  it("serves a later ask for a resolved key without refetching", async () => {
    const fetchRows = vi.fn(async () => rows(5));
    await loadRouteVoteRows("k", fetchRows);
    expect(cachedRouteVoteRows("k")).toEqual(rows(5));
    expect(fetchRows).toHaveBeenCalledTimes(1);
  });

  it("keeps different edge sets apart", async () => {
    await loadRouteVoteRows("a", async () => rows(1));
    await loadRouteVoteRows("b", async () => rows(9));
    expect(cachedRouteVoteRows("a")).toEqual(rows(1));
    expect(cachedRouteVoteRows("b")).toEqual(rows(9));
  });

  it("survives a failed request without caching or hanging", async () => {
    const boom = vi.fn(async () => { throw new Error("network"); });
    await expect(loadRouteVoteRows("k", boom)).resolves.toBeNull();
    expect(cachedRouteVoteRows("k")).toBeNull();

    // ...and the key is not left marked in-flight.
    const ok = vi.fn(async () => rows(2));
    expect(await loadRouteVoteRows("k", ok)).toEqual(rows(2));
    expect(ok).toHaveBeenCalledTimes(1);
  });

  it("keeps the previous answer when a refetch fails", async () => {
    await loadRouteVoteRows("k", async () => rows(4));
    const stillCached = await loadRouteVoteRows("k", async () => null);
    expect(stillCached).toEqual(rows(4));
    expect(cachedRouteVoteRows("k")).toEqual(rows(4));
  });

  it("evicts the least recently resolved key past the cap", async () => {
    for (let i = 0; i < ROUTE_VOTES_CACHE_MAX + 1; i++) {
      await loadRouteVoteRows(`k${i}`, async () => rows(i));
    }
    expect(cachedRouteVoteRows("k0")).toBeNull();
    expect(cachedRouteVoteRows("k1")).toEqual(rows(1));
    expect(cachedRouteVoteRows(`k${ROUTE_VOTES_CACHE_MAX}`))
      .toEqual(rows(ROUTE_VOTES_CACHE_MAX));
  });

  it("re-resolving a key refreshes its recency", async () => {
    await loadRouteVoteRows("old", async () => rows(1));
    for (let i = 0; i < ROUTE_VOTES_CACHE_MAX - 1; i++) {
      await loadRouteVoteRows(`k${i}`, async () => rows(i));
    }
    // Touch "old" so it is no longer the eviction candidate, then overflow.
    await loadRouteVoteRows("old", async () => rows(7));
    await loadRouteVoteRows("newest", async () => rows(8));
    expect(cachedRouteVoteRows("old")).toEqual(rows(7));
  });
});
