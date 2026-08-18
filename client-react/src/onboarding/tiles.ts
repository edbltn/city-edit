// ==========================================================================
// The wall — which sentences a map offers
// ==========================================================================
// Built from the map's REAL vote types (MapConfig.voteTypes, straight from
// /api/maps/<slug>), never from a list in this file. That is the difference
// between an opening screen that stays true and one that rots the first time
// somebody edits a map: a type added to nyc-proposals shows up on its wall on
// the next load, and a type retired from nyc-tactical leaves it.
//
// Two things this module decides:
//
//   ORDER   Route and point types are INTERLEAVED rather than grouped. A map's
//           authored list tends to arrive clustered by family (nine point types,
//           then five route ones), and a wall that reads as two solid blocks
//           invites you to pick from the top block only. Alternating keeps both
//           kinds in the first screenful, which matters because the kind is what
//           decides whether the flow will ask you for one point or two.
//
//   FLOOR   Generic sentences (phrasebook.GENERIC — no vote type at all) are
//           always appended, and carry the whole wall for a map that authored no
//           vote types (nyc-ebike-charging authors none). They are last because
//           a named type produces a better vote: it says what you want, so the
//           cast lands on a proposal other people can join.
// ==========================================================================

import type { MapConfig, MapVoteType } from "../map/runtime";
import { iconForLabel } from "../themes";
import { GENERIC, openerFor } from "./phrasebook";

/** One slip on the wall. */
export interface OpenerTile {
  /** Stable key — also what a test or a debug tool can address a tile by. */
  id: string;
  /** The half-finished sentence. */
  text: string;
  /** The vote type choosing this commits to, or null for a generic sentence. */
  voteType: string | null;
  /** Route-kind types need an end point; point-kind ones don't; null (generic)
   *  asks for one anyway and lets it be skipped. */
  pointType: "route" | "point" | null;
  /** Themed icon name (`/icons/<icon>.svg`), or null → the suggestion glyph. */
  icon: string | null;
}

/** How many generic sentences ride along with an already-full wall. All of them
 *  when the map has nothing of its own. */
const GENERIC_ALONGSIDE = 4;

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
 * The wall for a map. `network` is deliberately ignored: a station map (ebikes)
 * votes on fixed points whatever kind its types were authored as, and the flow
 * derives that from the live selection rather than from the tile — see
 * onboarding/state.ts, which treats a station map as point-kind throughout.
 */
export function buildTiles(map: MapConfig | null): OpenerTile[] {
  const authored = map?.voteTypes ?? [];
  const ordered = interleave(
    authored.filter((v) => v.pointType === "route"),
    authored.filter((v) => v.pointType === "point")
  );

  const seen = new Set<string>();
  const typed: OpenerTile[] = [];
  for (const vt of ordered) {
    const text = openerFor(vt.label);
    // Several labels share a sentence on purpose ("Add Citi Bike station" and
    // "Add Citibike station" are the same complaint). Showing it twice makes
    // the wall look broken; the first spelling wins, which is the map's own
    // ordering and therefore the map author's preference.
    if (seen.has(text)) continue;
    seen.add(text);
    typed.push({
      id: `vt:${vt.label}`,
      text,
      voteType: vt.label,
      pointType: vt.pointType,
      icon: iconForLabel(vt.label, authored),
    });
  }

  const genericCount = typed.length ? GENERIC_ALONGSIDE : GENERIC.length;
  const generic: OpenerTile[] = GENERIC.filter((t) => !seen.has(t))
    .slice(0, genericCount)
    .map((text, i) => ({
      id: `generic:${i}`,
      text,
      voteType: null,
      pointType: null,
      icon: null,
    }));

  return [...typed, ...generic].slice(0, MAX_TILES);
}
