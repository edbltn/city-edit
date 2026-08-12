// ==========================================================================
// Printed stickers — what a scan resolves to
// ==========================================================================
// A sticker is a QR code on a lamp post. Its URL is /s/<code> and nothing else:
// short enough to stay a small, dense code (tools/stickers/codes.py explains
// why the printed form is uppercase), and opaque enough that the sticker itself
// carries no map, no coordinates and no vote type — those live on the server,
// keyed by the code, so the campaign can be corrected after the sheets are
// printed.
//
// The scan has two possible outcomes, and the difference between them is the
// entire point of the feature:
//
//   unresolved  Nobody has voted from this sticker yet, so nobody knows which
//               pole it is on. Ask the scanner to share their location.
//   resolved    Someone already did. The sticker IS that place now — open the
//               vote there and never ask anyone for a location again.
//
// nginx serves the SPA shell for any path, so /s/<code> loads the app and the
// resolution happens here rather than as an HTTP redirect.

import { CONFIG } from "../config";

/** What the server says a scanned code should do. */
export interface StickerTarget {
  code: string;
  /** Map the vote lands on (currently always `nyc-proposals`). */
  mapSlug: string;
  /** Vote-type label this sticker's line asks for. */
  voteType: string;
  /** The line printed on the sticker — shown back to the scanner so the screen
   *  they land on obviously belongs to the thing in their hand. */
  headline: string;
  /** Campaign tag for the map-load beacon (`?src=`), per MESSAGE not per
   *  sticker — see tools/stickers/codes.py on why the per-sticker identity is
   *  a database row and not a metric label. */
  src: string;
  /** Where the sticker turned out to be, or null while still unresolved. */
  location: { lat: number; lng: number } | null;
}

/** The code in a `/s/<code>` path, or null for any other path. Pure, so the
 *  route rule is testable without a DOM. */
export function stickerCodeFromPath(pathname: string): string | null {
  const m = pathname.match(/^\/s\/([a-z0-9]{4,12})\/?$/i);
  return m ? m[1] : null;
}

/** The code this page was scanned with, or null if this isn't a scan. */
export function detectStickerCodeFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return stickerCodeFromPath(window.location.pathname);
}

/** Look a scanned code up. Returns null for an unknown code (a 404 here means
 *  a sticker went out before its row was seeded, or someone typed a code that
 *  was never printed). */
export async function fetchStickerTarget(code: string): Promise<StickerTarget | null> {
  try {
    const res = await fetch(`${CONFIG.apiUrl}/stickers/${encodeURIComponent(code)}`);
    if (!res.ok) return null;
    return (await res.json()) as StickerTarget;
  } catch {
    return null;
  }
}

/**
 * Pin a sticker to where it turned out to be.
 *
 * Called once the scan's first vote has actually landed, not when the browser
 * hands over a location: a shared location is a claim, a cast vote is a
 * commitment, and only the second one should be allowed to fix a sticker's
 * identity for everyone who scans it afterwards. Fire-and-forget — the vote is
 * already recorded, and failing to pin the sticker only costs the next scanner
 * one location prompt.
 */
export async function resolveSticker(
  code: string,
  point: { lat: number; lng: number },
  voterId: string | null,
): Promise<void> {
  try {
    await fetch(`${CONFIG.apiUrl}/stickers/${encodeURIComponent(code)}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: point.lat, lng: point.lng, voter_id: voterId }),
      keepalive: true,
    });
  } catch {
    // Never surface as an app error: the vote is in, which is what mattered.
  }
}
