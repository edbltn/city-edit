// ==========================================================================
// The wall — which sentences a map offers
// ==========================================================================
// Built from the map's REAL vote types (MapConfig.voteTypes, straight from
// /api/maps/<slug>), never from a list in this file. That is the difference
// between an opening screen that stays true and one that rots the first time
// somebody edits a map: a type added to nyc-proposals shows up on its wall on
// the next load, and a type retired from nyc-tactical leaves it.
//
// EVERY SLIP IS A VOTE TYPE. There is no fallback tier of sentences that name
// no type. An earlier version had one — twelve generic openers ("The worst part
// of my commute is…") appended to every wall and carrying a map that authored
// nothing — and they are gone, because picking one committed to nothing: the
// vote type resolved at cast time to whatever the map's default happened to be,
// so the sentence on the slip and the vote in the database were only loosely
// related. A slip now says what it will cast.
//
// That leaves the question the fallback used to hide, and the answer is stated
// here rather than papered over:
//
//   A MAP WHOSE TYPES ARE ENTIRELY CUSTOM — one that overlaps none of the three
//   default vote sets (Walkways, Bikes, Trees) — GETS ITS OWN TYPES ANYWAY, with
//   sentences from the phrasebook's verb rules rather than its curated lines.
//   A custom type is a real vote type: it is what that map votes on, and a slip
//   for it casts a real vote on a real proposal. Filtering the wall down to
//   preset labels would show a visitor either nothing or — worse — sentences for
//   types their map cannot cast. The presets are the vocabulary the curated
//   sentences are WRITTEN for, not a whitelist the wall is filtered by.
//
//   A MAP THAT AUTHORS NO VOTE TYPES AT ALL — nyc-ebike-charging authors zero —
//   HAS NOTHING TO PUT ON A WALL, so it gets no wall. buildTiles returns an
//   empty array and the flow does not open (components/Onboarding/Onboarding.tsx
//   checks before it probes). An empty wall in front of a real visitor is worse
//   than no wall, and this is deliberately NOT a reason to reintroduce a generic
//   floor: a first run that opens on that map is not spent, so the visitor's
//   next map — one with vote types — still shows it.
//
// One other thing this module decides:
//
//   ORDER   Route and point types are INTERLEAVED rather than grouped. A map's
//           authored list tends to arrive clustered by family (nine point types,
//           then five route ones), and a wall that reads as two solid blocks
//           invites you to pick from the top block only. Alternating keeps both
//           kinds in the first screenful, which matters because the kind is what
//           decides whether the flow will ask you for one point or two.
// ==========================================================================

import type { MapConfig, MapVoteType } from "../map/runtime";
import { iconForLabel } from "../themes";
import { openerFor } from "./phrasebook";

/** One slip on the wall. Always a vote type — see the header. */
export interface OpenerTile {
  /** Stable key — also what a test or a debug tool can address a tile by. */
  id: string;
  /** The sentence a person would say for this vote type. */
  text: string;
  /** The vote type choosing this commits to. */
  voteType: string;
  /** Route-kind types need an end point; point-kind ones don't. */
  pointType: "route" | "point";
  /** Themed icon name (`/icons/<icon>.svg`), or null → the suggestion glyph. */
  icon: string | null;
}

/** Above this the wall stops being a wall and becomes a scroll. */
const MAX_TILES = 32;

/** Alternate the two families, longest-first, so neither can own the top of the
 *  wall. Runs out gracefully when a map is all one kind. */
function interleave(a: MapVoteType[], b: MapVoteType[]): MapVoteType[] {
  const [long, short] = a.length >= b.length ? [a, b] : [b, a];
  const out: MapVoteType[] = [];
  for (let i = 0; i < long.length; i++) {
    out.push(long[i]);
    if (i < short.length) out.push(short[i]);
  }
  return out;
}

/**
 * The wall for a map — empty when the map authors no vote types.
 *
 * `network` is deliberately ignored: a station map (ebikes) votes on fixed
 * points whatever kind its types were authored as, and the flow derives that
 * from the live selection rather than from the tile — see onboarding/state.ts,
 * which treats a station map as point-kind throughout.
 */
export function buildTiles(map: MapConfig | null): OpenerTile[] {
  const authored = map?.voteTypes ?? [];
  const ordered = interleave(
    authored.filter((v) => v.pointType === "route"),
    authored.filter((v) => v.pointType === "point")
  );

  const seen = new Set<string>();
  const tiles: OpenerTile[] = [];
  for (const vt of ordered) {
    const text = openerFor(vt.label);
    // Several labels share a sentence on purpose ("Add Citi Bike station" and
    // "Add Citibike station" are the same complaint). Showing it twice makes
    // the wall look broken; the first spelling wins, which is the map's own
    // ordering and therefore the map author's preference.
    if (seen.has(text)) continue;
    seen.add(text);
    tiles.push({
      id: `vt:${vt.label}`,
      text,
      voteType: vt.label,
      pointType: vt.pointType,
      icon: iconForLabel(vt.label, authored),
    });
  }

  return tiles.slice(0, MAX_TILES);
}
