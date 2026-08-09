// ==========================================================================
// Per-proposal donation links
// ==========================================================================
// donate.cityedit.org is our donation page (an nginx 301 onto the Donorbox
// campaign, forwarding the query string). Donorbox does NOT prefill its
// "leave a comment" box from the URL — that field is donor-authored — but it
// DOES stamp UTM parameters onto the donation record server-side. So that is
// where a proposal's identity rides:
//
//   utm_content  the message: what is being funded, in words
//   utm_term     the deep link back to that exact proposal on the map
//   utm_campaign the map slug it lives on
//
// Every donation started from a proposal card therefore arrives naming the
// proposal it was meant for, with a link that re-selects it.

import { getCurrentMap, getMapSlug } from "../map/runtime";

/** Our donation page. Keep the client pointed at the cityedit host (not the
 *  Donorbox URL directly) so the campaign can move without a client deploy. */
const DONATE_URL = "https://donate.cityedit.org/";

/** Ceiling on each UTM value. These ride in a URL and land in a reporting
 *  column of unspecified width, so clip long vote-type/street names here —
 *  where we can end them legibly — rather than letting something downstream
 *  cut them mid-word. */
const MAX_UTM_CHARS = 160;

function clip(text: string): string {
  const trimmed = text.trim();
  if (trimmed.length <= MAX_UTM_CHARS) return trimmed;
  return `${trimmed.slice(0, MAX_UTM_CHARS - 1).trimEnd()}…`;
}

/**
 * The one proposal a card may offer to fund, or "" when it names none.
 *
 * A card headed by a top proposal funds that proposal; a card listing exactly
 * one vote type funds that one. Anything else is a place with several
 * proposals on it — "fund this proposal" there would have to guess which, so
 * the row is simply absent rather than wrong.
 */
export function fundableProposalName(
  winnerLabel: string | null | undefined,
  rows: readonly { label: string }[],
): string {
  if (winnerLabel) return winnerLabel;
  return rows.length === 1 ? rows[0].label : "";
}

export interface ProposalDonationTarget {
  /** The proposal's vote-type label — "Protected bike lane". */
  name: string;
  /** Where it is, in words: the card's street or intersection name. Route
   *  cards span many blocks and have no single place, so they omit it. */
  place?: string | null;
  /** Deep link that re-selects this proposal on the map (see buildSelectionUrl
   *  for points; the live URL already is one for a route selection). */
  url: string;
}

/**
 * Donation-page URL carrying the identity of the proposal being funded.
 *
 * The message is written to be read cold in the donation record — "Fund:
 * Protected bike lane — Grand St & Bowery — NYC Proposals" — because that
 * record is the only place it is ever read.
 */
export function buildProposalDonateUrl(target: ProposalDonationTarget): string {
  const mapName = getCurrentMap()?.name ?? "";
  const place = target.place?.trim();
  const message = [
    `Fund: ${target.name}`,
    place || null,
    mapName || null,
  ].filter(Boolean).join(" — ");

  const params = new URLSearchParams({
    utm_source: "cityedit",
    // Distinguishes a proposal-funding donation from a plain topbar/landing
    // donation, which carries no UTM at all.
    utm_medium: "proposal",
    utm_campaign: getMapSlug() || "cityedit",
    utm_content: clip(message),
    utm_term: clip(target.url),
  });
  return `${DONATE_URL}?${params.toString()}`;
}
