// React bindings for the two vote-type stores (map/voteTypeRegistry —
// what's on the map, map/voteTypeFilter — what's drawn). Both are module-level
// singletons so non-React hot paths (GraphLayer's canvas + proposal recomputes)
// can read them synchronously; these hooks are the reactive view for the UI.

import { useMemo, useSyncExternalStore } from "react";
import { getCurrentMap } from "../map/runtime";
import {
  buildVoteTypeLegend,
  getVoteTypeRegistryVersion,
  subscribeVoteTypeRegistry,
  type VoteTypeLegendEntry,
} from "../map/voteTypeRegistry";
import {
  getHiddenVoteTypes,
  subscribeVoteTypeFilter,
} from "../map/voteTypeFilter";

/** The set of labels currently toggled OFF. Re-renders on every filter change. */
export function useHiddenVoteTypes(): ReadonlySet<string> {
  return useSyncExternalStore(subscribeVoteTypeFilter, getHiddenVoteTypes, getHiddenVoteTypes);
}

/**
 * The map's vote types in legend order, each stamped with its live net support
 * and whether it can be cast in the current selection mode. Rebuilt only when
 * the registry actually changes (version-keyed), so the panel doesn't re-render
 * on every vote poll.
 */
export function useVoteTypeLegend(pointType: "route" | "point"): VoteTypeLegendEntry[] {
  const version = useSyncExternalStore(
    subscribeVoteTypeRegistry,
    getVoteTypeRegistryVersion,
    getVoteTypeRegistryVersion
  );
  const map = getCurrentMap();
  return useMemo(
    () => buildVoteTypeLegend(map, pointType),
    // `version` is the registry's snapshot key — buildVoteTypeLegend reads the
    // module state it guards. `map` is a load-time constant.
    [version, map, pointType]
  );
}
