// ==========================================================================
// The address bar as a projection of the selection
// ==========================================================================
// One function decides what a map URL looks like. It is a PROJECTION of the
// canonical selection, not a second source of truth: nothing reads state back
// out of the address bar after boot.
//
// The param vocabulary splits three ways (docs/url-routing.md#query-param-inventory):
//
//   state    w, vt          mirrored — rewritten on every selection change
//   input    z, lat, lng    consumed by the camera at load, then dropped
//   input    slat…, elng    legacy selection, parsed at load, then respelled as w
//   other    map, style, …  none of our business; preserved verbatim
//
// Consuming an input param is what makes a legacy link SELF-HEALING rather than
// merely tolerated: a printed ?slat/?slng QR poster restores its selection from
// the legacy params (the RouteProvider seed) and the first mirror pass rewrites
// the same selection as ?w=. The link on the sticker never changes; the link in
// the visitor's address bar — the one they might re-share — is always canonical.

import { CAMERA_PARAM_KEYS } from "../utils/mapViewState";
import {
  selectionToParams,
  CONSUMED_SELECTION_PARAM_KEYS,
  type SerializableSelection,
} from "./serialize";

/** Every param the map consumes at load and therefore must clear before it
 *  writes the canonical form. */
export const CONSUMED_PARAM_KEYS = [
  ...CONSUMED_SELECTION_PARAM_KEYS,
  ...CAMERA_PARAM_KEYS,
];

/**
 * The canonical query string for `selection`, given the URL we are on.
 *
 * Params outside the selection/camera vocabulary are preserved untouched —
 * `?map=`, `?style=`, `?tab=`, and crucially `?src=` / `?stk=`, which have their
 * own capture-and-strip lifecycles and must survive a mirror pass that happens
 * to land first (see utils/sourceTag.ts, sticker/pending.ts).
 *
 * Returns the search string WITHOUT a leading "?" (empty when no params remain).
 */
export function canonicalSearch(search: string, selection: SerializableSelection): string {
  const params = new URLSearchParams(search);
  for (const k of CONSUMED_PARAM_KEYS) params.delete(k);
  for (const [k, v] of selectionToParams(selection)) params.set(k, v);
  return params.toString();
}
