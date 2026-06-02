// ==========================================================================
// Shareable selection links
// ==========================================================================
// Builds a deep link that re-selects a point (and optional vote type) on the
// current map. Uses the slug-based mapHref() so links are /m/<slug>?... with the
// camera + point encoded — never a theme= param.

import { mapHref } from "../themes";
import { getMapSlug } from "../map/runtime";
import { getMapViewState } from "./mapViewState";

/** Absolute URL that selects `point` (with optional vote type) on the active map. */
export function buildSelectionUrl(
  point: { lat: number; lng: number },
  voteType?: string | null,
): string {
  const view = getMapViewState();
  const href = mapHref(getMapSlug(), {
    zoom: view.zoom,
    center: view.center,
    start: point,
    vt: voteType ?? null,
  });
  if (typeof window === "undefined") return href;
  // mapHref returns a relative URL — resolve to absolute for copy.
  return new URL(href, window.location.origin).toString();
}

/** Copy text to the clipboard, returning whether it succeeded. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
