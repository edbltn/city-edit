import type { VoteTypeSuggestion } from "../types";
import type { Theme } from "../themes";
import { getCurrentMap, mapVoteTypesForPointType } from "../map/runtime";

/**
 * Get suggestions for a theme filtered by point type.
 */
export function getSuggestionsForTheme(
  theme: Theme,
  pointType: "route" | "point"
): VoteTypeSuggestion[] {
  return theme.suggestions.filter((s) => s.pointType === pointType);
}

/**
 * Default vote-type label for the active map (preferred) or theme, by point type.
 * Using the resolved map's list keeps the default valid for maps that restrict
 * vote types.
 */
export function getDefaultVoteTypeForTheme(
  theme: Theme,
  pointType: "route" | "point"
): string {
  const map = getCurrentMap();
  if (map?.voteTypes?.length) {
    // Suggestions-off maps with a single authored vote type lock to it in every
    // mode (see VoteTypeSelector's frozenLabel). The pointType filter below
    // could otherwise return nothing — e.g. a route-only type while in point
    // mode — desyncing the committed vote type from the frozen chip and getting
    // the cast rejected server-side (the fallback theme label isn't in the
    // map's list). Resolve straight to the sole type so the two always agree.
    if (!map.allowSuggestions && map.voteTypes.length === 1) {
      return map.voteTypes[0].label;
    }
    const fromMap = mapVoteTypesForPointType(map, pointType);
    if (fromMap.length > 0) return fromMap[0].label;
  }
  const suggestions = getSuggestionsForTheme(theme, pointType);
  return suggestions.length > 0 ? suggestions[0].label : "";
}
