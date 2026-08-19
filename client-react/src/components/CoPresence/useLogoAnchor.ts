import { useEffect, useState } from "react";

/**
 * Track the live box of the CITY EDIT logo island, so anything can sit under it
 * without knowing how big it is.
 *
 * The logo is being reworked in parallel — resized, squared, and given padding
 * derived from the gap between its own crossword squares. Any offset written
 * here against today's numbers would be wrong by tomorrow and wrong SILENTLY,
 * which is the failure that matters: a strip 6px into the topbar's border looks
 * like a rendering bug, not like a stale constant.
 *
 * So nothing is assumed. We measure whichever logo element is actually on
 * screen and hand back its real rect. When the island grows, the thing under it
 * moves down by exactly as much, with no edit here.
 *
 * The RIGHT fix is a slot in the topbar — a child under the logo inside the
 * island, laid out by normal flow, inheriting the island's width and padding
 * for free and needing no JavaScript at all. That is a change to TopBar.tsx,
 * which belongs to the agent doing the rework, so it is requested rather than
 * taken. This hook is the version that touches only our own files.
 *
 * Two elements, not one: `.logo-container` is the desktop island and is
 * `display:none` under 1080px, where `.logo-mobile-banner` takes over as a
 * full-width strip. Which one is live is a media query we do not want to
 * duplicate, so we ask the DOM which is visible rather than matching the
 * breakpoint ourselves — the breakpoint is theirs to move too.
 *
 * And the underside of the logo is not always free. On desktop the island is
 * its own column and the space beneath it is map. On a phone the logo is the
 * FIRST ROW of a 194px stacked topbar, so "directly under the logo" is the
 * legend — measured, not guessed: the strip landed at y=42 behind
 * `.legend-item-coords`, invisible, because the topbar outranks it (z 600 vs
 * 200). So the anchor is the lower of the logo's underside and the chrome's:
 * as far up as the logo allows, never inside something else. One rule for both
 * breakpoints rather than a second copy of their media query — and it keeps
 * holding if the island stops stretching to the topbar's full height.
 */

export interface LogoBox {
  left: number;
  /** Where the strip may start: the logo's underside, pushed down to clear the
   *  topbar when the logo is only one row of it. */
  bottom: number;
  width: number;
}

const SELECTORS = [".logo-container", ".logo-mobile-banner"];

function visibleLogo(): Element | null {
  for (const sel of SELECTORS) {
    const el = document.querySelector(sel);
    // offsetParent is null for display:none; a zero rect catches the rest.
    if (el instanceof HTMLElement && el.offsetParent !== null) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return el;
    }
  }
  return null;
}

export function useLogoAnchor(): LogoBox | null {
  const [box, setBox] = useState<LogoBox | null>(null);

  useEffect(() => {
    let current: Element | null = null;
    let ro: ResizeObserver | null = null;

    const measure = () => {
      const el = visibleLogo();
      if (!el) {
        setBox(null);
        return;
      }
      if (el !== current) {
        // The breakpoint flipped which logo is showing; follow it.
        current = el;
        ro?.disconnect();
        ro = new ResizeObserver(measure);
        ro.observe(el);
      }
      const r = el.getBoundingClientRect();
      const chrome = document.querySelector(".topbar")?.getBoundingClientRect();
      const bottom = Math.max(r.bottom, chrome ? chrome.bottom : r.bottom);
      setBox((prev) =>
        prev && prev.left === r.left && prev.bottom === bottom
          && prev.width === r.width
          ? prev                       // same box: don't re-render the strip
          : { left: r.left, bottom, width: r.width });
    };

    measure();
    window.addEventListener("resize", measure);
    // The topbar's own contents settle after fonts land and after the map name
    // arrives, either of which can change the island's height.
    const mo = new MutationObserver(measure);
    const topbar = document.querySelector(".topbar");
    if (topbar) mo.observe(topbar, { childList: true, subtree: true, attributes: true });
    document.fonts?.ready.then(measure).catch(() => {});

    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
      mo.disconnect();
    };
  }, []);

  return box;
}
