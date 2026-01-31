import type { TransportMode, VoteTypeSuggestion } from "../types";

// Default vote type suggestions by mode and point count
export const VOTE_TYPE_SUGGESTIONS: VoteTypeSuggestion[] = [
  // Route votes (2 points) - mode-specific
  { label: "🚴 Add bike lane", pointType: "route", modes: ["bike"] },
  { label: "🛡️ Add protected bike lane", pointType: "route", modes: ["bike"] },
  { label: "↔️ Widen bike lane", pointType: "route", modes: ["bike"] },
  { label: "🚶 Add crosswalks", pointType: "route", modes: ["walk"] },
  { label: "↔️ Widen sidewalk", pointType: "route", modes: ["walk"] },
  { label: "🌉 Add pedestrian bridge", pointType: "route", modes: ["walk"] },
  { label: "🚗 Add lane", pointType: "route", modes: ["drive"] },
  { label: "↩️ Add turn lane", pointType: "route", modes: ["drive"] },
  { label: "🛣️ Repave road", pointType: "route", modes: ["drive"] },

  // Route votes - universal
  { label: "💡 Add street lighting", pointType: "route", modes: ["bike", "walk", "drive"] },
  { label: "🚧 Add traffic calming", pointType: "route", modes: ["bike", "walk", "drive"] },

  // Point votes (1 point) - mode-specific
  { label: "🚲 Add Citi Bike station", pointType: "point", modes: ["bike"] },
  { label: "🅿️ Add bike parking", pointType: "point", modes: ["bike"] },
  { label: "🔧 Add bike repair station", pointType: "point", modes: ["bike"] },
  { label: "🪑 Add bench", pointType: "point", modes: ["walk"] },
  { label: "🚻 Add public restroom", pointType: "point", modes: ["walk"] },
  { label: "🚰 Add water fountain", pointType: "point", modes: ["walk"] },
  { label: "🅿️ Add parking", pointType: "point", modes: ["drive"] },
  { label: "🏢 Add parking garage", pointType: "point", modes: ["drive"] },

  // Point votes - universal
  { label: "🌳 Plant trees", pointType: "point", modes: ["bike", "walk", "drive"] },
  { label: "⚡ Add EV charger", pointType: "point", modes: ["bike", "walk", "drive"] },
  { label: "🗑️ Add trash can", pointType: "point", modes: ["bike", "walk", "drive"] },
  { label: "🚌 Add bus stop", pointType: "point", modes: ["bike", "walk", "drive"] },
];

/**
 * Get suggestions filtered by mode and point type.
 */
export function getSuggestions(
  mode: TransportMode,
  pointType: "route" | "point"
): VoteTypeSuggestion[] {
  return VOTE_TYPE_SUGGESTIONS.filter(
    (s) => s.pointType === pointType && s.modes.includes(mode)
  );
}

/**
 * Get the default vote type label for a given mode and point type.
 */
export function getDefaultVoteType(
  mode: TransportMode,
  pointType: "route" | "point"
): string {
  const suggestions = getSuggestions(mode, pointType);
  return suggestions.length > 0 ? suggestions[0].label : "";
}
