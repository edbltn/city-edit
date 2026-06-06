// ==========================================================================
// Effective vote-type resolution
// ==========================================================================
// A selection carries the *requested* vote-type label (from a click or a deep
// link). The label the Cast control actually uses is derived through a fallback
// chain so a stale or foreign label never produces a server-rejected cast:
//
//   1. the requested label, if it's valid for this map
//        valid ⇔ the map allows free-form suggestions, OR the label is in the
//        map's authored vote-type set for the current point type
//   2. else the most-voted vote type on the current path (if any, and valid)
//   3. else the theme/map default
//
// All inputs are passed in, so this stays a pure, unit-testable function — the
// map-config lookup and the path tally live at the call site.

export interface VoteTypeResolutionInput {
  /** The label the selection requested (Selection.voteType). */
  requested: string;
  /** Authored vote-type labels valid for the current map + point type. */
  validLabels: readonly string[];
  /** Whether the map accepts arbitrary (suggested) labels. */
  allowSuggestions: boolean;
  /** The highest net-voted label on the current path, or null. */
  topLabelOnPath: string | null;
  /** Always-valid final fallback (getDefaultVoteTypeForTheme). */
  themeDefault: string;
}

function isValidFor(
  label: string,
  validLabels: readonly string[],
  allowSuggestions: boolean
): boolean {
  if (!label) return false;
  if (allowSuggestions) return true;
  return validLabels.includes(label);
}

export function resolveEffectiveVoteType(input: VoteTypeResolutionInput): string {
  const { requested, validLabels, allowSuggestions, topLabelOnPath, themeDefault } = input;

  if (isValidFor(requested, validLabels, allowSuggestions)) return requested;
  if (topLabelOnPath && isValidFor(topLabelOnPath, validLabels, allowSuggestions)) {
    return topLabelOnPath;
  }
  return themeDefault;
}
