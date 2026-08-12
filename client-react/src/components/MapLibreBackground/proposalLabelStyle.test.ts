import { describe, it, expect } from "vitest";
import { countZoomCurves, PLACE_LABEL_TEXT_SIZE } from "./placeLabelStyle";
import {
  PIN_H,
  PIN_W,
  PROPOSAL_COUNT_SCALE,
  PROPOSAL_LABEL_FILTER,
  PROPOSAL_LABEL_TEXT_SIZE,
  PROPOSAL_MIN_LEAFLET_ZOOM,
  PROPOSAL_PUCK_OFFSET,
  PROPOSAL_PUCK_SIZE,
  PROPOSAL_TEXT_MAX_WIDTH,
  PROPOSAL_TEXT_OFFSET_EM,
  pinTailBelowAnchorPx,
  formatProposalCount,
  proposalLabelText,
  proposalRevealZoom,
  proposalTextField,
} from "./proposalLabelStyle";
import { MAP_STYLES, proposalLabelColors, POI_COLORS } from "../../mapStyles";

/** Evaluate a plain ["interpolate",["linear"],["zoom"], z, v, …] at one zoom. */
function rampAt(expr: unknown, zoom: number): number {
  const e = expr as unknown[];
  const stops: Array<[number, number]> = [];
  for (let i = 3; i < e.length; i += 2) stops.push([e[i] as number, e[i + 1] as number]);
  if (zoom <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (zoom >= last[0]) return last[1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [z0, v0] = stops[i], [z1, v1] = stops[i + 1];
    if (zoom >= z0 && zoom <= z1) return v0 + ((v1 - v0) * (zoom - z0)) / (z1 - z0);
  }
  throw new Error("unreachable");
}

/** Parse the two color notations the styles use: "#rrggbb" and "rgb(r, g, b)".
 *  (Scraping digits with a regex silently turns "#F4F5F0" into rgb(4, 5, 0) —
 *  the light basemap's paper reads as black and every contrast check inverts.) */
function parseColor(css: string): [number, number, number] {
  const hex = css.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
  }
  const rgb = css.match(/\d+/g)!.map(Number);
  return [rgb[0], rgb[1], rgb[2]];
}

/** sRGB relative luminance, for the contrast checks below. */
function luminance(css: string): number {
  const [r, g, b] = parseColor(css).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The two `format` branches of the zoom-stepped text-field: [wide, close]. */
function textFieldBranches(field: unknown): [unknown[], unknown[]] {
  const e = field as unknown[]; // ["step", ["zoom"], wide, stop, close]
  return [e[2] as unknown[], e[4] as unknown[]];
}

describe("proposal label expressions", () => {
  // The reason this file exists, same as its place-label sibling: MapLibre
  // allows ONE zoom curve per expression and rejects the whole layer for a
  // second one — as a non-fatal style error, so the only symptom is that every
  // proposal label silently stops rendering.
  it("keeps each expression to exactly one zoom curve", () => {
    expect(countZoomCurves(PROPOSAL_LABEL_TEXT_SIZE)).toBe(1);
    expect(countZoomCurves(PROPOSAL_PUCK_SIZE)).toBe(1);
  });

  // The count rides the claim's size ramp via font-scale rather than carrying a
  // ramp of its own, so the whole field spends its single zoom curve on the
  // step that drops the count at wide zoom.
  it("spends exactly one zoom curve on the text-field", () => {
    expect(countZoomCurves(proposalTextField("rgb(1, 2, 3)"))).toBe(1);
  });

  // Claim first, nearest the pin; count below it, smaller and dimmer. The other
  // order put the faintest text in the most crowded slot (directly under the
  // pin's tail) and made you read a number before knowing what it counted.
  it("puts the claim first and the count under it, dimmed and smaller", () => {
    const [wide, close] = textFieldBranches(proposalTextField("rgb(9, 9, 9)"));
    expect(close[0]).toBe("format");
    expect(close[1]).toEqual(["get", "text"]);
    expect(close[2]).toEqual({});
    expect(close[3]).toBe("\n");
    expect(close[5]).toEqual(["get", "count"]);
    expect(close[6]).toEqual({ "font-scale": PROPOSAL_COUNT_SCALE, "text-color": "rgb(9, 9, 9)" });
    expect(PROPOSAL_COUNT_SCALE).toBeLessThan(1);
    // And the claim is never the thing dropped.
    expect(wide[1]).toEqual(["get", "text"]);
  });

  // Dropping the count takes a wrapped label from three lines to two, which is
  // a third off the collision box exactly where labels compete hardest.
  it("drops the count at wide zoom and restores it further in", () => {
    const [wide, close] = textFieldBranches(proposalTextField("rgb(9, 9, 9)"));
    expect(JSON.stringify(wide)).not.toContain("count");
    expect(JSON.stringify(close)).toContain("count");
    expect(wide.filter((s) => s === "\n")).toHaveLength(0);
    expect(close.filter((s) => s === "\n")).toHaveLength(1);
  });

  it("switches the count in on a MapLibre stop, not a Leaflet one", () => {
    const field = proposalTextField("rgb(9, 9, 9)") as unknown[];
    expect(field[0]).toBe("step");
    expect(field[1]).toEqual(["zoom"]);
    // Leaflet z13 is MapLibre z12 — the same −1 the minz filter compensates for.
    expect(field[3]).toBe(12);
  });
});

describe("proposal labels vs place labels", () => {
  // The tier signal. If a proposal ever read at the same size as a nail salon,
  // the hierarchy would rest on color alone.
  it("draws a clear step larger than the place labels at every zoom", () => {
    for (const zoom of [11, 13, 15, 18, 20]) {
      const proposal = rampAt(PROPOSAL_LABEL_TEXT_SIZE, zoom);
      // Place-label ramp stops are ["match", …] — the non-subway (miss) arm.
      const place = (() => {
        const e = PLACE_LABEL_TEXT_SIZE as unknown as unknown[];
        const stops: Array<[number, number]> = [];
        for (let i = 3; i < e.length; i += 2) {
          stops.push([e[i] as number, (e[i + 1] as unknown[])[4] as number]);
        }
        if (zoom <= stops[0][0]) return stops[0][1];
        const last = stops[stops.length - 1];
        if (zoom >= last[0]) return last[1];
        for (let i = 0; i < stops.length - 1; i++) {
          const [z0, v0] = stops[i], [z1, v1] = stops[i + 1];
          if (zoom >= z0 && zoom <= z1) return v0 + ((v1 - v0) * (zoom - z0)) / (z1 - z0);
        }
        throw new Error("unreachable");
      })();
      expect(proposal).toBeGreaterThan(place + 1);
    }
  });

  // POI_MUTE exists to buy the proposals this headroom; the point of the layer
  // is spending it on the words. If someone mutes these too, or authors a new
  // theme around a dim accent, the design collapses back into "a pin you have
  // to click" — so the hierarchy is asserted, not assumed. This is the check
  // that caught transit's #3B8EE0 (5.7:1 against near-black, UNDER the place
  // labels' own 7–8:1) and trees' #5FA052 (5.0:1 against 5.5:1).
  it("clears the loudest place label by a margin, on every style", () => {
    for (const style of Object.values(MAP_STYLES)) {
      const { label, halo } = proposalLabelColors(style);
      const loudestPoi = Math.max(
        ...Object.values(POI_COLORS[style.basemap]).map((c) => contrast(c, style.base)),
      );
      expect(contrast(label, halo)).toBeGreaterThan(loudestPoi * 1.1);
    }
  });

  it("leaves an accent that already carries the type at full chroma", () => {
    // The bike green and the harbor teal are strong enough on near-black to
    // need no correction at all; a rule that quietly desaturated every theme
    // would be a worse rule.
    expect(proposalLabelColors(MAP_STYLES.bikepaths).label).toBe("rgb(43, 224, 107)");
    expect(proposalLabelColors(MAP_STYLES.waterfront).label).toBe("rgb(34, 201, 201)");
  });

  it("keeps the count subordinate to the claim, without losing it", () => {
    for (const style of Object.values(MAP_STYLES)) {
      const { label, count, halo } = proposalLabelColors(style);
      expect(contrast(count, halo)).toBeLessThan(contrast(label, halo) * 0.75);
      // Still a readable number, not a ghost — 3:1 is the floor for text this
      // size that nobody has to read word by word.
      expect(contrast(count, halo)).toBeGreaterThan(3);
    }
  });
});

describe("the reveal schedule", () => {
  it("never reveals anything before the layer's own minzoom", () => {
    for (let rank = 0; rank < 60; rank++) {
      expect(proposalRevealZoom(rank)).toBeGreaterThanOrEqual(PROPOSAL_MIN_LEAFLET_ZOOM);
    }
  });

  it("is monotonic — a stronger proposal never appears later than a weaker one", () => {
    for (let rank = 1; rank < 60; rank++) {
      expect(proposalRevealZoom(rank)).toBeGreaterThanOrEqual(proposalRevealZoom(rank - 1));
    }
  });

  it("opens with a handful of headline claims at the city-wide view", () => {
    expect(proposalRevealZoom(0)).toBe(11);
    expect(proposalRevealZoom(3)).toBe(11);
    expect(proposalRevealZoom(4)).toBeGreaterThan(11);
  });

  it("has everything revealed by z14, so zooming in stops adding text", () => {
    expect(proposalRevealZoom(999)).toBe(14);
  });

  // The schedule can only ever be a CEILING on how many labels are eligible;
  // collision does the real thinning and can only remove. So an over-tight
  // schedule silences proposals that had room, while a generous one cannot
  // produce a wall of type — it just gives placement a wider pool to pick the
  // best-ranked from. This pins the generous direction against a future
  // "let's calm it down" that would reintroduce the original 2-at-z12.
  it("lets a full screenful compete well before the deepest zooms", () => {
    // By z13 every proposal either family can produce (20 each) is eligible.
    expect(proposalRevealZoom(19)).toBeLessThanOrEqual(13);
    expect(proposalRevealZoom(9)).toBeLessThanOrEqual(12);
  });

  // `minz` is a LEAFLET zoom and MapLibre's camera runs one lower. Comparing it
  // against a bare ["zoom"] would reveal every label one level late — a silent
  // off-by-one, and the same trap the place-label filter documents.
  it("compares minz against the LEAFLET zoom, not MapLibre's", () => {
    expect(PROPOSAL_LABEL_FILTER).toEqual(["<=", ["get", "minz"], ["+", ["zoom"], 1]]);
  });
});

describe("the pin footprint", () => {
  // The puck is what keeps a POI name from landing underneath a proposal pin.
  // These mirror voteTypeIcon.ts's SVG_W / SVG_H / TIP; if the pin art changes
  // and these don't, the reserved hole stops matching the pin drawn over it.
  it("matches the divIcon geometry the Leaflet pin is drawn at", () => {
    expect([PIN_W, PIN_H]).toEqual([34, 42]);
    // Bottom-anchored the puck covers y ∈ [−42, 0]; the pin, anchored at its
    // tail tip (17, 36), covers [−36, +6]. The offset is the difference.
    expect(PROPOSAL_PUCK_OFFSET).toEqual([0, 6]);
  });

  // The label cannot win a z-fight with its own pin: the pins are Leaflet DOM
  // above the whole GL canvas, so a pin always paints over GL text. Staying out
  // from under it is the only defence, and the margin is not a constant — the
  // pin rides --indicator-scale while the type rides its own size ramp, so the
  // tightest zoom has to be found rather than assumed. (At 0.55em the label was
  // flush with the tail and a hovered pin covered its first line.)
  it("keeps the label clear of the pin's tail at every zoom, even hovered", () => {
    const offsetEm = PROPOSAL_TEXT_OFFSET_EM[1];
    for (let z = 11; z <= 20; z++) {
      const textTopPx = offsetEm * rampAt(PROPOSAL_LABEL_TEXT_SIZE, z - 1);
      const tailPx = pinTailBelowAnchorPx(z);
      expect(textTopPx).toBeGreaterThan(tailPx + 2);
    }
  });

  // GraphLayer eases the pins smaller at both zoom extremes via
  // `--indicator-scale`; the puck has to ride the same curve or the reserved
  // footprint drifts off the pin. Stops here are MapLibre zooms (Leaflet − 1).
  it("tracks the pins' zoom scaling", () => {
    const leafletScale = (z: number) =>
      Math.max(0.5, Math.min(1, 1 - Math.max(0, z - 15) * 0.1 - Math.max(0, 14 - z) * 0.1));
    for (const leaflet of [11, 12, 13, 14, 15, 17, 20]) {
      expect(rampAt(PROPOSAL_PUCK_SIZE, leaflet - 1)).toBeCloseTo(leafletScale(leaflet), 1);
    }
  });
});

describe("label text", () => {
  it("uppercases the claim as the tier marker, keeping the verb", () => {
    expect(proposalLabelText("Add protected bike lane")).toBe("ADD PROTECTED BIKE LANE");
    // "Add" vs "Improve" is the entire difference between two vote types, so
    // no abbreviation may eat the leading verb.
    expect(proposalLabelText("Improve bike lane")).toContain("IMPROVE");
  });

  it("signs the count, because it is a differential", () => {
    expect(formatProposalCount(412)).toBe("+412");
    expect(formatProposalCount(1)).toBe("+1");
    expect(formatProposalCount(-30)).toBe("-30");
  });

  it("compacts thousands so the count never dominates the wrap budget", () => {
    expect(formatProposalCount(1000)).toBe("+1k");
    expect(formatProposalCount(1240)).toBe("+1.2k");
    expect(formatProposalCount(12_400)).toBe("+12k");
    expect(formatProposalCount(999)).toBe("+999");
  });

  // The SDF glyph ranges we bake stop at General Punctuation (8192–8447): no
  // arrows, no geometric shapes, and no U+2212 minus. Anything outside them
  // renders as nothing at all.
  it("emits only glyphs the baked SDF ranges actually contain", () => {
    const inRange = (s: string) => [...s].every((c) => {
      const cp = c.codePointAt(0)!;
      return cp <= 767 || (cp >= 8192 && cp <= 8447);
    });
    for (const n of [0, 7, 412, 1240, 12_400, -30, 250_000]) {
      expect(inRange(formatProposalCount(n))).toBe(true);
    }
    expect(inRange(proposalLabelText("Add café seating"))).toBe(true);
  });

  it("wraps the longest realistic claim to two lines", () => {
    const longest = proposalLabelText("Add protected bike lane");
    const lines = longest.split(" ").reduce<string[]>((acc, word) => {
      const last = acc[acc.length - 1];
      if (last && (last + " " + word).length <= PROPOSAL_TEXT_MAX_WIDTH) {
        acc[acc.length - 1] = last + " " + word;
      } else acc.push(word);
      return acc;
    }, []);
    expect(lines.length).toBeLessThanOrEqual(2);
  });
});
