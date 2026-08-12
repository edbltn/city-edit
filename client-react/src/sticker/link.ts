// ==========================================================================
// Where a scan lands
// ==========================================================================
// Builds the map URL a scanned sticker opens. Same address space as any other
// deep link (docs/url-routing.md) — slug path, canonical `?w=` selection, `?vt=`
// vote type — so a scan produces a link that is shareable, bookmarkable and
// indistinguishable from one a user made by hand.
//
// Two extra params ride along, and both are stripped by the app at boot:
//   ?src=  the campaign tag, for the map-load beacon
//   ?stk=  the sticker's own code, present only while it still owes us its
//          location (see pending.ts)

import { selectionToParams } from "../selection/serialize";
import type { StickerTarget } from "./api";

/** Close enough to see which corner you are standing on, wide enough to see
 *  the block it belongs to. */
const SCAN_ZOOM = 18;

export interface StickerLinkOptions {
  /** Carry the code through so the first vote can pin the sticker. Omit once
   *  the sticker is already resolved — there is nothing left to record. */
  pending?: boolean;
}

/**
 * The map URL for a scanned sticker at a known point.
 *
 * Same-origin and relative: the app's own canonical-subdomain redirect settles
 * the final host afterwards and preserves the query string, so there is no
 * reason to guess it here.
 */
export function stickerMapUrl(
  target: StickerTarget,
  point: { lat: number; lng: number },
  opts: StickerLinkOptions = {},
): string {
  const params = selectionToParams({
    waypoints: [{ coords: point, id: "sticker" }],
    voteType: target.voteType,
  });
  params.set("z", String(SCAN_ZOOM));
  params.set("lat", point.lat.toFixed(5));
  params.set("lng", point.lng.toFixed(5));
  params.set("src", target.src);
  if (opts.pending) params.set("stk", target.code);
  return `/m/${target.mapSlug}?${params.toString()}`;
}

/**
 * The map URL for a scan we could not place — the browser refused location, or
 * the device could not get a fix.
 *
 * Still worth opening: the vote type is preselected and the map is live, so the
 * scanner can drop the pin on the corner themselves. It carries no `?stk=`,
 * because a hand-placed pin is a guess about where the *user* is, not evidence
 * about where the *sticker* is, and pinning a sticker to a guess would be worse
 * than leaving it unresolved for the next person.
 */
export function stickerFallbackUrl(target: StickerTarget): string {
  const params = new URLSearchParams();
  params.set("vt", target.voteType);
  params.set("src", target.src);
  return `/m/${target.mapSlug}?${params.toString()}`;
}
