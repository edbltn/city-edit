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
  // A bound code carries its whole selection, which is the only form that can
  // express a route — a route proposal is an ordered list of waypoints, and
  // rebuilding one from a single point would silently turn it into a pin on its
  // start. Codes bound before that was recorded have only the point, so fall
  // back to it rather than dropping them.
  const params = target.waypoints
    ? new URLSearchParams({ w: target.waypoints, vt: target.voteType })
    : selectionToParams({
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
 * The map URL for a scan with no location yet — the visitor skipped the prompt,
 * the browser refused, or the device could not get a fix.
 *
 * The vote type is preselected and the map is live, so the scanner places the
 * selection themselves. It carries `?stk=` exactly like the located path: the
 * binding is earned by the CAST, and a vote someone placed by hand is no less a
 * commitment than one placed over a GPS fix — arguably more, since they looked
 * at the map and chose the spot.
 *
 * (This used to withhold the code, on the theory that a hand-placed pin says
 * where the user guessed rather than where the sticker is. That was wrong in
 * practice: it meant the single most deliberate way to use a sticker was the one
 * way that never bound it, so a code could be scanned and voted from all day and
 * still ask the next person for a location.)
 */
export function stickerFallbackUrl(target: StickerTarget): string {
  const params = new URLSearchParams();
  params.set("vt", target.voteType);
  params.set("src", target.src);
  params.set("stk", target.code);
  return `/m/${target.mapSlug}?${params.toString()}`;
}
