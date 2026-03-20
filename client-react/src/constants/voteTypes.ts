import type { VoteTypeSuggestion } from "../types";

// Default vote type suggestions by point type
export const VOTE_TYPE_SUGGESTIONS: VoteTypeSuggestion[] = [
  // Route votes (2 points)
  { label: "🚴 Add bike lane", pointType: "route" },
  { label: "🛡️ Add protected bike lane", pointType: "route" },
  { label: "↔️ Widen bike lane", pointType: "route" },
  { label: "🚶 Add crosswalks", pointType: "route" },
  { label: "↔️ Widen sidewalk", pointType: "route" },
  { label: "🌉 Add pedestrian bridge", pointType: "route" },
  { label: "🚗 Add lane", pointType: "route" },
  { label: "↩️ Add turn lane", pointType: "route" },
  { label: "🛣️ Repave road", pointType: "route" },
  { label: "💡 Add street lighting", pointType: "route" },
  { label: "🚧 Add traffic calming", pointType: "route" },

  // Point votes (1 point)
  { label: "🚲 Add Citi Bike station", pointType: "point" },
  { label: "🅿️ Add bike parking", pointType: "point" },
  { label: "🔧 Add bike repair station", pointType: "point" },
  { label: "🪑 Add bench", pointType: "point" },
  { label: "🚻 Add public restroom", pointType: "point" },
  { label: "🚰 Add water fountain", pointType: "point" },
  { label: "🅿️ Add parking", pointType: "point" },
  { label: "🌳 Plant trees", pointType: "point" },
  { label: "⚡ Add EV charger", pointType: "point" },
  { label: "🗑️ Add trash can", pointType: "point" },
  { label: "🚌 Add bus stop", pointType: "point" },
];

/**
 * Get suggestions filtered by point type.
 */
export function getSuggestions(
  pointType: "route" | "point"
): VoteTypeSuggestion[] {
  return VOTE_TYPE_SUGGESTIONS.filter((s) => s.pointType === pointType);
}

/**
 * Get the default vote type label for a given point type.
 */
export function getDefaultVoteType(
  pointType: "route" | "point"
): string {
  const suggestions = getSuggestions(pointType);
  return suggestions.length > 0 ? suggestions[0].label : "";
}
