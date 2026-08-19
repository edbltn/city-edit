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
 * FIRST ROW of a stacked topbar, so "directly under the logo" is the legend —
 * measured, not guessed: the strip landed at y=42 behind
 * `.legend-item-coords`, invisible, because the topbar outranks it (z 600 vs
 * 200). So the anchor is the lower of the logo's underside and the chrome's:
 * as far up as the logo allows, never inside something else.
 *
 * Which is the chrome's underside, and it is NOT `.topbar`'s box. That box is
 * a grid which RESERVES its rows whether or not they are used — below 1080px
 * the template is `44px 31.39 31.39 8px 31.39 31.39`, six rows for the banner,
 * the Start/End card and the two pills. Between 600px and 1079px the Map and
 * Vote pills sit SIDE BY SIDE and share one row, so the last row is reserved
 * and empty, and `.topbar.bottom` is 39.4px below anything anyone can see.
 * Measured at 720px: the pill band ends at 154.2, the box ends at 193.6, and
 * the strip sat on the box — one pip adrift, floating on bare map with a
 * visible seam above it. Full width and phone width never showed it because
 * there the last row IS occupied and the two numbers coincide.
 *
 * So we measure what the chrome actually PAINTS: the lowest visible, in-flow
 * descendant that stays inside the padded box, plus that padding. No class
 * names, so the rows can be renamed, reordered or re-broken without touching
 * this file; the reserved-but-empty tail simply stops counting. The three
 * exclusions are what "visible" has to mean here, each one load-bearing:
 * absolutely positioned nodes are the dropdowns, which hang far BELOW the
 * chrome when open; `visibility:hidden` is how those same dropdowns park when
 * closed (`.mode-search` is 26px lower than the pill that owns it, in flow and
 * with a real rect, and it moved the anchor until it was excluded); and
 * anything reaching past the padded box is by definition not one of the rows.
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

/** The lowest pixel the chrome actually paints inside its own padded box.
 *
 *  Returns the top of the box when it can find nothing — an empty chrome has
 *  no underside to sit under, and the caller falls back to the logo. */
function paintedChromeBottom(topbar: HTMLElement): number {
  const box = topbar.getBoundingClientRect();
  const padBottom = parseFloat(getComputedStyle(topbar).paddingBottom) || 0;
  // Reserved-but-empty rows live below this line; so does every open dropdown.
  const floor = box.bottom - padBottom + 0.5;
  let lowest = box.top;
  for (const el of topbar.querySelectorAll("*")) {
    if (!(el instanceof HTMLElement) || el.offsetParent === null) continue;
    const cs = getComputedStyle(el);
    if (cs.position === "absolute" || cs.position === "fixed") continue;
    if (cs.visibility === "hidden" || cs.opacity === "0") continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0 || r.bottom > floor) continue;
    if (r.bottom > lowest) lowest = r.bottom;
  }
  return lowest === box.top ? box.top : lowest + padBottom;
}

export function useLogoAnchor(): LogoBox | null {
  const [box, setBox] = useState<LogoBox | null>(null);

  useEffect(() => {
    let current: Element | null = null;
    let ro: ResizeObserver | null = null;
    let chromeRo: ResizeObserver | null = null;

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
      const topbar = document.querySelector(".topbar");
      const chromeBottom = topbar instanceof HTMLElement
        ? paintedChromeBottom(topbar) : r.bottom;
      // Never ON the logo, never below the chrome's last painted row.
      const bottom = Math.max(r.bottom, chromeBottom);
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
    if (topbar) {
      mo.observe(topbar, { childList: true, subtree: true, attributes: true });
      // Its HEIGHT can change with no mutation and no window resize — fonts
      // landing, or a pill band re-wrapping — and the anchor reads its rows.
      chromeRo = new ResizeObserver(measure);
      chromeRo.observe(topbar);
    }
    document.fonts?.ready.then(measure).catch(() => {});

    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
      chromeRo?.disconnect();
      mo.disconnect();
    };
  }, []);

  return box;
}
