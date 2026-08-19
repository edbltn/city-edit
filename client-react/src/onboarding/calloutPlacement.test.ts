import { describe, it, expect } from "vitest";
import { calloutFit, placeCallout } from "./calloutPlacement";
import type { AnchorBox } from "./coachAnchor";

/** The four widths the coach was asked to hold up at, against a 1800 desktop,
 *  plus the phone the two-mark cast step has to survive. */
const WIDTHS = [
  { name: "full", width: 1800 },
  { name: "half", width: 900 },
  { name: "a third", width: 600 },
  { name: "a quarter", width: 450 },
  { name: "a phone", width: 390 },
];

const VH = 900;

const anchor = (left: number, width: number, top = 24, height = 34): AnchorBox =>
  ({ left, width, top, height });

/** A box at the width the fit allows, two lines of the ask deep. */
function boxFor(a: AnchorBox, vw: number, height = 46, prefer: "left" | "right" = "left") {
  return { width: calloutFit(a, { width: vw, height: VH }, prefer).maxWidth, height };
}

/** Where the tip actually lands, in viewport coordinates. For a horizontal side
 *  that is a y; for a vertical one an x. This is the number the whole module
 *  exists to get right — everything else is bookkeeping. */
function tip(a: AnchorBox, vw: number, height = 46) {
  const p = placeCallout(a, boxFor(a, vw, height), { width: vw, height: VH });
  const horizontal = p.side === "left" || p.side === "right";
  return {
    side: p.side,
    horizontal,
    at: horizontal ? p.top + p.arrow : p.left + p.arrow,
    from: horizontal ? a.top : a.left,
    to: horizontal ? a.top + a.height : a.left + a.width,
  };
}

describe("the shape is horizontal wherever it fits", () => {
  it("sits to the LEFT of the control by default, tip on its right", () => {
    // The cast pair, hard right on a desktop bar: 300px of box fits easily.
    const p = placeCallout(anchor(1145, 72), { width: 300, height: 46 },
      { width: 1800, height: VH });
    expect(p.side).toBe("left");
    expect(p.left + 300).toBe(1145 - 10);
  });

  it("goes to the right only when the left has no room", () => {
    // A control hard against the left edge — nothing legible fits beside it.
    const p = placeCallout(anchor(9, 60), { width: 300, height: 46 },
      { width: 1800, height: VH });
    expect(p.side).toBe("right");
    expect(p.left).toBe(9 + 60 + 10);
  });

  it("centres the box on the control vertically", () => {
    const p = placeCallout(anchor(1145, 72, 100, 40), { width: 300, height: 46 },
      { width: 1800, height: VH });
    expect(p.top + 46 / 2).toBe(100 + 40 / 2);
  });
});

describe("falling back under the control", () => {
  it("goes vertical when neither side can hold a legible box", () => {
    // 390px phone, the Start/End plate running nearly the full width: 9px of
    // room on the left and ~30 on the right. A 30px-wide callout obeying the
    // shape rule would be worse than a readable one underneath.
    const a = anchor(9, 350);
    const fit = calloutFit(a, { width: 390, height: VH });
    expect(fit.side).toBeNull();
    expect(placeCallout(a, { width: fit.maxWidth, height: 46 }, { width: 390, height: VH }).side)
      .toBe("below");
  });

  it("flips above when there is no room below and there is room above", () => {
    const a = anchor(9, 350, 300, 34);
    const p = placeCallout(a, { width: 374, height: 46 }, { width: 390, height: 380 });
    expect(p.side).toBe("above");
    expect(p.top).toBe(300 - 10 - 46);
  });

  it("stays below when neither vertical side fits, rather than covering it", () => {
    const p = placeCallout(anchor(9, 350, 4, 30), { width: 374, height: 46 },
      { width: 390, height: 100 });
    expect(p.side).toBe("below");
    expect(p.top).toBeGreaterThanOrEqual(8);
  });
});

describe("the box stays on screen", () => {
  it("never leaves the viewport, at any width, wherever the control is", () => {
    for (const { name, width } of WIDTHS) {
      const anchors = [
        anchor(0, 60),                          // hard left
        anchor(9, Math.min(350, width - 60)),   // the legend plate
        anchor(Math.round(width / 2) - 40, 80),
        anchor(width - 90, 82),                 // the cast pair, hard right
        anchor(width - 24, 24),                 // a 24px control in the corner
      ];
      for (const a of anchors) {
        const box = boxFor(a, width);
        const p = placeCallout(a, box, { width, height: VH });
        expect(p.left, `${name} @ ${a.left}`).toBeGreaterThanOrEqual(8);
        expect(p.left + box.width, `${name} @ ${a.left}`).toBeLessThanOrEqual(width - 8);
        expect(p.top, `${name} @ ${a.left}`).toBeGreaterThanOrEqual(8);
      }
    }
  });

  it("stays on screen even when the box is wider than the viewport", () => {
    // Not reachable through calloutFit, but a font-size change or a long
    // unbreakable label could get there, and the clamp must not invert: an
    // unguarded Math.min(Math.max(…)) with high < low throws the box off the
    // LEFT edge, where there is no scrollbar to reveal it.
    const p = placeCallout(anchor(100, 60), { width: 500, height: 46 },
      { width: 320, height: VH });
    expect(p.left).toBe(8);
  });
});

describe("the tip stays on the control", () => {
  it("points inside the anchor at every width, wherever the anchor sits", () => {
    for (const { name, width } of WIDTHS) {
      const anchors = [
        anchor(0, 60),
        anchor(9, Math.min(350, width - 60)),
        anchor(Math.round(width / 2) - 40, 80),
        anchor(width - 90, 82),
        anchor(width - 24, 24),
      ];
      for (const a of anchors) {
        const t = tip(a, width);
        expect(t.at, `${name} @ ${a.left} (${t.side})`).toBeGreaterThanOrEqual(t.from);
        expect(t.at, `${name} @ ${a.left} (${t.side})`).toBeLessThanOrEqual(t.to);
      }
    }
  });

  it("keeps the tip off the box's own corners", () => {
    for (const { width } of WIDTHS) {
      for (const a of [anchor(0, 40), anchor(width - 40, 40)]) {
        const box = boxFor(a, width);
        const p = placeCallout(a, box, { width, height: VH });
        const along = p.side === "left" || p.side === "right" ? box.height : box.width;
        expect(p.arrow).toBeGreaterThanOrEqual(16);
        expect(p.arrow).toBeLessThanOrEqual(Math.max(16, along - 16));
      }
    }
  });

  it("slides the tip rather than the box once the box is clamped", () => {
    // The failure this guards: centring the tip on the BOX. Two controls at
    // different heights that both clamp the box to the same top edge must still
    // get different tips.
    const box = { width: 300, height: 200 };
    const vp = { width: 1800, height: 300 };
    const a = placeCallout(anchor(1145, 72, 10, 34), box, vp);
    const b = placeCallout(anchor(1145, 72, 60, 34), box, vp);
    expect(a.top).toBe(b.top);
    expect(a.arrow).not.toBe(b.arrow);
    expect(b.arrow).toBeGreaterThan(a.arrow);
  });
});

describe("two marks at once", () => {
  // The cast step: one box on the vote-type picker, one on the −/+ pair, from a
  // real 1200px float bar. They are neighbours, so both taking the same side
  // would overlap — and two overlapping callouts read as one broken one.
  const picker = anchor(621, 170, 62, 37);
  const cast = anchor(804, 72, 26, 36);
  const VP = { width: 1200, height: VH };

  it("puts them on opposite sides, so they cannot meet", () => {
    const a = placeCallout(picker, boxFor(picker, VP.width), VP, undefined, "left");
    const b = placeCallout(cast, boxFor(cast, VP.width, 46, "right"), VP, undefined, "right");
    expect(a.side).toBe("left");
    expect(b.side).toBe("right");
    const aw = calloutFit(picker, VP, "left").maxWidth;
    // The picker's box ends left of the picker; the pair's starts right of it.
    expect(a.left + aw).toBeLessThanOrEqual(b.left);
  });

  it("gives each box the width of the side it actually takes", () => {
    // Flipping AFTER measuring would leave a box sized for the roomier side
    // trying to fit in the narrower one. The pair has 306px to its right.
    expect(calloutFit(cast, VP, "right").maxWidth).toBe(306);
    expect(calloutFit(picker, VP, "left").maxWidth).toBe(340);
  });

  it("still points at its own control, on either side", () => {
    for (const [a, side] of [[picker, "left"], [cast, "right"]] as const) {
      const p = placeCallout(a, boxFor(a, VP.width, 46, side), VP, undefined, side);
      const at = p.top + p.arrow;
      expect(at).toBeGreaterThanOrEqual(a.top);
      expect(at).toBeLessThanOrEqual(a.top + a.height);
    }
  });

  it("falls back to the far side when the preferred one has no room", () => {
    // A control hard against the right edge cannot host a box to its right.
    const hard = anchor(1100, 80, 26, 36);
    expect(placeCallout(hard, { width: 200, height: 46 }, VP, undefined, "right").side)
      .toBe("left");
  });

  it("takes the far side rather than overlap, when told what to avoid", () => {
    const first = placeCallout(picker, { width: 200, height: 46 }, VP, undefined, "left");
    const avoid = { top: first.top, left: first.left, width: 200, height: 46 };
    // Same anchor, same preferred side: without `avoid` it lands on the first.
    const naive = placeCallout(anchor(621, 170, 62, 37), { width: 200, height: 46 }, VP, undefined, "left");
    expect(naive.left).toBe(first.left);
    const avoided = placeCallout(anchor(621, 170, 62, 37), { width: 200, height: 46 }, VP, avoid, "left");
    expect(avoided.side).toBe("right");
  });

  it("keeps off the OTHER mark's control, not just its box", () => {
    // The 390px bar, measured: the picker runs nearly the full width on the row
    // BELOW the −/+ pair. The pair's box would take the space to its left, which
    // is exactly where the picker is — covering the one control this step is
    // telling the reader to go and use.
    const phone = { width: 390, height: 844 };
    const pickerAnchor = { left: 8, top: 152, width: 303, height: 31 };
    const pairAnchor = { left: 320, top: 122, width: 61, height: 30 };
    const box = { width: 302, height: 54 };

    const naive = placeCallout(pairAnchor, box, phone, undefined, "right");
    const naiveHits = !(
      naive.left + box.width <= pickerAnchor.left ||
      naive.left >= pickerAnchor.left + pickerAnchor.width ||
      naive.top + box.height <= pickerAnchor.top ||
      naive.top >= pickerAnchor.top + pickerAnchor.height
    );
    expect(naiveHits).toBe(true);

    const p = placeCallout(pairAnchor, box, phone, [pickerAnchor], "right");
    const clear = (
      p.left + box.width <= pickerAnchor.left ||
      p.left >= pickerAnchor.left + pickerAnchor.width ||
      p.top + box.height <= pickerAnchor.top ||
      p.top >= pickerAnchor.top + pickerAnchor.height
    );
    expect(clear).toBe(true);
    // …and it is still pointing at its own control.
    const at = p.side === "left" || p.side === "right" ? p.top + p.arrow : p.left + p.arrow;
    const from = p.side === "left" || p.side === "right" ? pairAnchor.top : pairAnchor.left;
    const to = from + (p.side === "left" || p.side === "right" ? pairAnchor.height : pairAnchor.width);
    expect(at).toBeGreaterThanOrEqual(from);
    expect(at).toBeLessThanOrEqual(to);
  });

  it("leaves a box alone when there is nothing to avoid", () => {
    const far = { top: 600, left: 20, width: 100, height: 40 };
    expect(placeCallout(cast, { width: 200, height: 46 }, VP, far, "right"))
      .toEqual(placeCallout(cast, { width: 200, height: 46 }, VP, undefined, "right"));
  });
});

describe("how wide the box may be", () => {
  it("takes only the room its own side has", () => {
    // 200px of clear space to the left → a 200px box, not a clipped 300px one.
    const fit = calloutFit(anchor(218, 60), { width: 900, height: VH });
    expect(fit.side).toBe("left");
    expect(fit.maxWidth).toBe(200);
  });

  it("stops at a comfortable reading width on a wide screen", () => {
    expect(calloutFit(anchor(1145, 72), { width: 1800, height: VH }).maxWidth).toBe(340);
    expect(calloutFit(anchor(1145, 72), { width: 2560, height: VH }).maxWidth).toBe(340);
  });

  it("refuses to go horizontal below a legible width", () => {
    // 160px of room is under the floor: it falls back rather than shipping a
    // sliver beside the control.
    expect(calloutFit(anchor(178, 60), { width: 400, height: VH }).side).toBeNull();
    expect(calloutFit(anchor(186, 60), { width: 400, height: VH }).side).toBe("left");
  });
});
