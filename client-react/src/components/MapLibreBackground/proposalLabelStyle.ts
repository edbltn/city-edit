/**
 * Style expressions and text formatting for the TOP-PROPOSAL label layer — the
 * words drawn on the map beside each proposal pin.
 *
 * Sibling of placeLabelStyle.ts, and bound by the same silent-failure rule: an
 * expression may contain only ONE zoom-based `interpolate`/`step`, and breaking
 * it makes MapLibre reject the whole layer as a *non-fatal* style error, so
 * every proposal label simply stops rendering with nothing in the console but a
 * warning. Both expressions below are guarded by `countZoomCurves` in the tests.
 *
 * Everything here is pure so the reveal schedule, the wrap, and the count
 * formatting can be tested without a GL context.
 */
import type maplibregl from "maplibre-gl";

/** The font stack baked by `npm run build:glyphs` — the ONLY SDF stack we
 *  serve. There is no bold cut, so every step of the type hierarchy here is
 *  made of size, color and halo. */
export const PROPOSAL_LABEL_FONT = "Red Hat Mono Regular";

// ── The pin footprint ───────────────────────────────────────────────────────
// The proposal pin is a Leaflet divIcon, drawn in the OTHER renderer, on top of
// the GL canvas. MapLibre cannot see it, so it would happily place a POI name —
// or another proposal's own words — straight through one. These are
// voteTypeIcon.ts's SVG_W / SVG_H / TIP: a 34×42 element whose tail tip — the
// anchor, the point that sits on the location — is at (17, 36). A transparent
// image registered on the symbol keeps that footprint in MapLibre's collision
// index; the `proposal-pucks` layer is what places it (see addProposalLabels).
export const PIN_W = 34;
export const PIN_H = 42;
const PIN_TIP_Y = 36;
/** Top edge of the pin's INK — voteTypeIcon.ts draws its square from y=3. */
const PIN_INK_TOP_Y = 3;

/** The 1.1x the pin grows on hover and when selected. It pivots on the tail tip
 *  (`transform-origin: 17px 36px`), so the element's empty tail grows DOWN,
 *  into the label, while the ink can only grow UP. */
const PIN_HOVER_SCALE = 1.1;

/**
 * Height of the reserved footprint — the pin's INK, not its element box.
 *
 * The two differ, and the difference is what makes the collision reservation
 * safe to place before the text instead of alongside it. The element is 42px
 * tall anchored 36 down, so its box runs y ∈ [−36, +6]; but the shape inside it
 * runs from y=3 to the tail tip at y=36, i.e. y ∈ [−33, 0] — the bottom 6px are
 * empty, and nothing is ever drawn below the anchor. Reserving the empty strip
 * cost the label 6px of the ~7px of clearance it has under its own pin, which
 * is fine while the puck rides along with the text in one symbol (a symbol
 * never collides with itself) and fatal the moment the puck is placed first, in
 * its own layer, where it CAN suppress its own label.
 *
 * Hovered, the ink scales about the tip: 33 × 1.1 = 36.3 above the anchor, so
 * 37 covers a hovered pin and still stops exactly at the anchor.
 */
export const PROPOSAL_PUCK_H = Math.ceil((PIN_TIP_Y - PIN_INK_TOP_Y) * PIN_HOVER_SCALE);

/** Collision padding on the puck. Zero, deliberately: MapLibre's default of 2
 *  would push the reserved box 2px BELOW the anchor — back into the strip the
 *  label needs — for no gain, since the box is already 3px larger than the ink
 *  it stands in for. Every pixel here is spent twice, once against neighbouring
 *  labels and once against the label directly underneath. */
export const PROPOSAL_PUCK_PADDING = 0;

/** Collision padding around the label text. Tighter than the place labels' 6:
 *  padding is charged on EVERY side of a three-line block, and at 8 it was
 *  silencing neighbours with real room between them. Named here because the
 *  clearance under the pin is measured against it. */
export const PROPOSAL_TEXT_PADDING = 4;

/** How far the pin's tail hangs BELOW its anchor, before scaling. */
const PIN_TAIL_BELOW_ANCHOR = PIN_H - PIN_TIP_Y;

/**
 * Clearance between the pin's tail and the top of the label, in ems of the text
 * size (`text-offset`, paired with `text-anchor: "top"`).
 *
 * This is the ONLY defence the label has against its OWN pin, and it cannot be
 * replaced by a z-index. The pins are Leaflet DOM in a pane above the entire GL
 * canvas — MapLibreBackground renders as a sibling before MapContainer at
 * `zIndex: 0` — so a pin always paints over GL text no matter how the style is
 * ordered. Against every OTHER pin the defence is collision, not clearance: the
 * `proposal-pucks` layer reserves all of them before a single word is placed,
 * so a label that would land under a neighbour's pin now stands down instead of
 * being drawn and then buried.
 *
 * It started at 0.55em, which was flush and therefore wrong — ~6px at the mid
 * zooms against a tail that hangs exactly 6px, so the first line of text began
 * where the pin ended, and any hover or selected scale pushed the pin onto it.
 * `keepsClearOfPin` in the tests checks the real worst case across the zoom
 * range, since the pin and the type scale on DIFFERENT curves.
 */
export const PROPOSAL_TEXT_OFFSET_EM: [number, number] = [0, 1.0];

/** Pixels the pin's tail reaches below the anchor at a Leaflet zoom, hovered —
 *  the thing the label has to clear. Mirrors GraphLayer's `--indicator-scale`. */
export function pinTailBelowAnchorPx(leafletZoom: number): number {
  const zoomedIn = Math.max(0, leafletZoom - 15) * 0.1;
  const zoomedOut = Math.max(0, 14 - leafletZoom) * 0.1;
  const scale = Math.max(0.5, Math.min(1, 1 - zoomedIn - zoomedOut));
  return PIN_TAIL_BELOW_ANCHOR * scale * PIN_HOVER_SCALE;
}

/** Name of the transparent collision image registered via `addImage`. */
export const PROPOSAL_PUCK_IMAGE = "proposal-pin-puck";

/**
 * Puck scale by zoom — a mirror of the `--indicator-scale` CSS variable that
 * GraphLayer sets on the Leaflet container (pins ease smaller at BOTH zoom
 * extremes, full size only around the middle). If these two drift apart the
 * reserved footprint stops matching the pin actually drawn, and labels start
 * either colliding with pins or standing off them by a visible margin.
 *
 * Stops are MAPLIBRE zooms — one below their Leaflet counterparts (ml 10 = the
 * Leaflet 11 end of GraphLayer's ramp, ml 13/14 = Leaflet 14/15's full size,
 * ml 19 = Leaflet 20's 0.5 floor).
 */
export const PROPOSAL_PUCK_SIZE = [
  "interpolate", ["linear"], ["zoom"],
  10, 0.7,
  13, 1,
  14, 1,
  19, 0.5,
] as unknown as maplibregl.ExpressionSpecification;

/**
 * Label type size, in px, ramped by zoom.
 *
 * Deliberately a clear step ABOVE the place labels (which run 8.6–10.5px): the
 * two tiers sit side by side on the same map, and if a proposal read at the
 * same size as a nail salon the hierarchy would rest on color alone. It stays
 * modest in absolute terms because mono is wide and these strings are whole
 * sentences, not names.
 *
 * The count line is not sized here — it rides this ramp scaled down by
 * PROPOSAL_COUNT_SCALE inside the `format` expression, which keeps the whole
 * label to ONE zoom curve.
 */
export const PROPOSAL_LABEL_TEXT_SIZE = [
  "interpolate", ["linear"], ["zoom"],
  11, 10.5,
  15, 11.5,
  18, 12.5,
] as unknown as maplibregl.ExpressionSpecification;

/** The count line's size relative to the label's. */
export const PROPOSAL_COUNT_SCALE = 0.82;

/**
 * Wrap width, in characters (mono, so characters are uniformly wide).
 *
 * 14 is the widest setting that still keeps "Add protected bike lane" — about
 * the longest label anyone actually writes — to two lines. Wider starts winning
 * the wrap but losing the collision, because a long single-line block sweeps
 * horizontally across neighbouring labels; narrower turns ordinary three-word
 * demands into three-line paragraphs.
 */
export const PROPOSAL_TEXT_MAX_WIDTH = 14;

// ── Reveal schedule ─────────────────────────────────────────────────────────

/** Leaflet zoom at which the strongest proposals are allowed to speak. */
const REVEAL_BASE_Z = 11;
/** Reveal zoom of each successive rank band, as offsets from REVEAL_BASE_Z.
 *  Index = band, value = how many proposals that band and all before it hold. */
const REVEAL_BANDS = [4, 10, 20];

/**
 * The Leaflet zoom at which the proposal ranked `rank` (0 = strongest) may show
 * its label.
 *
 * Collision thins by geometry, not by importance, so at a city-wide view it
 * would scatter whichever labels happened not to touch. This makes the coarse
 * decision on purpose — four headline claims at z11, ten by z12, twenty by z13,
 * everything from z14.
 *
 * The schedule is DELIBERATELY generous, and was loosened once already (it began
 * at two labels at z12). The reason is that the two mechanisms are not
 * symmetrical in their failure: collision can only ever remove labels, so being
 * permissive here cannot produce a wall of type — it just widens the pool
 * collision gets to choose the best-ranked from. Being stingy, on the other
 * hand, silences proposals that had room to speak. When in doubt, let more
 * through and let placement arbitrate.
 */
export function proposalRevealZoom(rank: number): number {
  for (let band = 0; band < REVEAL_BANDS.length; band++) {
    if (rank < REVEAL_BANDS[band]) return REVEAL_BASE_Z + band;
  }
  return REVEAL_BASE_Z + REVEAL_BANDS.length;
}

/** Earliest Leaflet zoom any proposal label can appear at — the layer's own
 *  `minzoom` is derived from this so MapLibre skips the layer entirely below it
 *  instead of placing symbols the filter then throws away. */
export const PROPOSAL_MIN_LEAFLET_ZOOM = REVEAL_BASE_Z;

/**
 * `minz` is a LEAFLET zoom — the units used by CONFIG.maxZoom, map URLs, the
 * place-label builder and proposalRevealZoom above. MapLibre drives its camera
 * one level lower (256px vs 512px tiles), hence the +1. Same idiom as
 * `place-labels`; getting it wrong is a silent one-zoom-level offset.
 */
export const PROPOSAL_LABEL_FILTER = [
  "<=", ["get", "minz"], ["+", ["zoom"], 1],
] as unknown as maplibregl.FilterSpecification;

// ── Text ────────────────────────────────────────────────────────────────────

/**
 * The claim, as drawn: the vote type's own label, uppercased.
 *
 * Uppercase is the tier marker. CARTO's street names and our place labels are
 * both mixed case, so caps read instantly as "this is a different kind of
 * thing" without needing a weight we do not have a glyph set for. The label is
 * NOT abbreviated: every one of these strings starts with a verb ("Add" /
 * "Improve" / "Widen" / "Fix"), and the verb is the entire difference between
 * "Add bike lane" and "Improve bike lane".
 */
export function proposalLabelText(label: string): string {
  return label.toLocaleUpperCase();
}

/**
 * The vote count, as drawn.
 *
 * Signed, because the number IS a differential (up − down) and "+412" says that
 * where "412" only implies it. Thousands are compacted: "+12.4k" holds the
 * magnitude in a third of the width, and at this size an exact five-digit count
 * is neither readable nor interesting. ASCII only — the SDF glyph ranges we
 * bake stop at General Punctuation, so there is no "−" (U+2212) and no arrows.
 */
export function formatProposalCount(net: number): string {
  const sign = net < 0 ? "-" : "+";
  const n = Math.abs(Math.round(net));
  if (n < 1000) return `${sign}${n}`;
  const k = n / 1000;
  // 10k and up loses the decimal: "+12.4k" and "+12k" read the same at a glance
  // and the shorter one costs less width in a wrap budget of 14 characters.
  return `${sign}${k < 10 ? k.toFixed(1).replace(/\.0$/, "") : Math.round(k)}k`;
}

/**
 * The `text-field`: the claim, then the vote count under it.
 *
 * The claim goes FIRST — nearest the pin, where the eye lands after the pin
 * itself, and where the tail tip visually points. Tried the other way round
 * (count as an eyebrow, mirroring the ProposalCard's eyebrow-above-title), and
 * on the map it is worse in two ways: it puts the smallest, faintest text in
 * the tightest slot, the few pixels directly under the pin's tail where
 * neighbouring labels crowd hardest; and it makes you read a number before you
 * know what the number is about. Below the claim, the count reads as what it
 * is — the evidence, after the demand.
 *
 * One `format` with two sections, not two layers: a single symbol means a
 * single collision box, so the count can never place without its claim or drift
 * away from it. The section's `text-color` override (MapLibre resolves these
 * through FormatSectionOverride) is what lets the two carry different colors
 * inside that one symbol; `countColor` is per-STYLE, not per-feature, so it is
 * baked into the expression rather than shipped on every proposal.
 *
 * BELOW `PROPOSAL_COUNT_MIN_LEAFLET_ZOOM` the count is dropped and the claim
 * stands alone. This is a placement decision, not a tidiness one: a wrapped
 * claim plus a count is three lines, and dropping to two shrinks the collision
 * box by about a third exactly where labels are competing hardest for room. It
 * costs nothing that matters — at a city-wide view you are reading what the city
 * is asking for, not comparing magnitudes, and the number is back the moment you
 * lean in. One `step` on zoom, so the whole field still holds ONE zoom curve.
 */
const PROPOSAL_COUNT_MIN_LEAFLET_ZOOM = 13;

export function proposalTextField(countColor: string): maplibregl.ExpressionSpecification {
  const claim: unknown[] = ["format", ["get", "text"], {}];
  // An empty `count` means "not resolved yet" (a corridor's voter count is a
  // round trip behind the corridor itself). The line BREAK lives inside the
  // count section rather than in a section of its own, so an unresolved label
  // is the claim exactly — not the claim plus a blank line, which would size
  // its collision box for a number that isn't drawn.
  const countSection: unknown[] = [
    "case",
    ["==", ["get", "count"], ""], "",
    ["concat", "\n", ["get", "count"]],
  ];
  const claimAndCount: unknown[] = [
    "format",
    ["get", "text"], {},
    countSection, { "font-scale": PROPOSAL_COUNT_SCALE, "text-color": countColor },
  ];
  return [
    "step", ["zoom"],
    claim,
    // A MAPLIBRE zoom stop, one below its Leaflet counterpart.
    PROPOSAL_COUNT_MIN_LEAFLET_ZOOM - 1, claimAndCount,
  ] as unknown as maplibregl.ExpressionSpecification;
}
