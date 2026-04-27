import type { VoteTypeSuggestion } from "../types";
import type { Theme } from "../themes";

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
 * Get the default vote type label for a theme and point type.
 */
export function getDefaultVoteTypeForTheme(
  theme: Theme,
  pointType: "route" | "point"
): string {
  const suggestions = getSuggestionsForTheme(theme, pointType);
  return suggestions.length > 0 ? suggestions[0].label : "";
}
