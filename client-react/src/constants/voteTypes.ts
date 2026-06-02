import type { VoteTypeSuggestion } from "../types";
import type { Theme } from "../themes";
import { getCurrentMap } from "../map/runtime";

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
    const fromMap = map.voteTypes.filter((s) => s.pointType === pointType);
    if (fromMap.length > 0) return fromMap[0].label;
  }
  const suggestions = getSuggestionsForTheme(theme, pointType);
  return suggestions.length > 0 ? suggestions[0].label : "";
}
