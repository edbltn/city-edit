/**
 * Which top chrome the map wears.
 *
 *   "float"  — the controls sit on the map as separate plates (TopBarFloat.css)
 *   "banner" — the original opaque strip across the top
 *
 * This is the whole seam for the floating-chrome experiment. The value lands on
 * `.topbar` as `data-chrome`, and every float rule is scoped to that attribute,
 * so:
 *
 *   - reverting the experiment is DEFAULT_CHROME below, one word;
 *   - comparing the two is `?chrome=banner` on any map URL, live, no rebuild —
 *     the URL mirror preserves params outside its own selection/camera
 *     vocabulary, so the override survives normalisation (see urlSync.ts);
 *   - deleting it for good is this file, TopBarFloat.css, and its one import.
 */
export type ChromeMode = "float" | "banner";

/** Flip to "banner" to put the strip back everywhere. */
export const DEFAULT_CHROME: ChromeMode = "float";

/** The pure half: what a given query string asks for. Anything unrecognised —
 *  absent, empty, misspelled — falls back rather than throwing, because this
 *  value decides whether the app has any chrome at all. */
export function chromeModeFrom(search: string | URLSearchParams): ChromeMode {
  const params = typeof search === "string" ? new URLSearchParams(search) : search;
  const asked = params.get("chrome");
  return asked === "float" || asked === "banner" ? asked : DEFAULT_CHROME;
}

/**
 * Read per call rather than cached at module load: the URL is the override, and
 * a session that arrives on a deep link carrying ?chrome= should get what the
 * link says.
 */
export function chromeMode(): ChromeMode {
  if (typeof window === "undefined") return DEFAULT_CHROME;
  return chromeModeFrom(window.location.search);
}
