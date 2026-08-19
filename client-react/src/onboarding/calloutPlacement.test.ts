import { describe, it, expect } from "vitest";
import { calloutMaxWidth, placeCallout } from "./calloutPlacement";
import type { AnchorBox } from "./coachAnchor";

/** A callout at its usual size: the ask on two lines. */
const BOX = { width: 300, height: 84 };

/** The four widths the coach was asked to hold up at, against a 1440 desktop. */
const WIDTHS = [
  { name: "full", width: 1440 },
  { name: "half", width: 720 },
  { name: "a third", width: 480 },
  { name: "a quarter", width: 360 },
];

const anchor = (left: number, width: number, top = 12, height = 34): AnchorBox =>
  ({ left, width, top, height });

/** Where the tip actually lands, in viewport coordinates. This is the number
 *  the whole module exists to get right — everything else is bookkeeping. */
function tipX(a: AnchorBox, box = BOX, vpWidth = 1440): number {
  const p = placeCallout(a, box, { width: vpWidth, height: 900 });
  return p.left + p.arrowLeft;
}

describe("the box stays on screen", () => {
  it("centres under the anchor when there is room either side", () => {
    const p = placeCallout(anchor(600, 200), BOX, { width: 1440, height: 900 });
    expect(p.left).toBe(700 - BOX.width / 2);
    expect(p.side).toBe("below");
    expect(p.top).toBe(12 + 34 + 10);
  });

  it("never runs off the right edge, however far right the anchor is", () => {
    for (const { width } of WIDTHS) {
      const max = calloutMaxWidth(width);
      const box = { width: max, height: 84 };
      // An anchor hard against the right edge — the cast pair's real position.
      const p = placeCallout(anchor(width - 90, 82), box, { width, height: 900 });
      expect(p.left + box.width).toBeLessThanOrEqual(width - 8);
      expect(p.left).toBeGreaterThanOrEqual(8);
    }
  });

  it("never runs off the left edge, however far left the anchor is", () => {
    for (const { width } of WIDTHS) {
      const max = calloutMaxWidth(width);
      const box = { width: max, height: 84 };
      const p = placeCallout(anchor(0, 60), box, { width, height: 900 });
      expect(p.left).toBeGreaterThanOrEqual(8);
      expect(p.left + box.width).toBeLessThanOrEqual(width - 8);
    }
  });

  it("stays on screen even when the box is wider than the viewport", () => {
    // Not reachable through calloutMaxWidth, but a font-size change or a long
    // unbreakable address could get there, and the clamp must not invert: an
    // unguarded Math.min(Math.max(…)) with high < low throws the box off the
    // LEFT edge, where there is no scrollbar to reveal it.
    const p = placeCallout(anchor(100, 60), { width: 500, height: 84 }, { width: 320, height: 900 });
    expect(p.left).toBe(8);
  });
});

describe("the tip stays on the control", () => {
  it("points inside the anchor at every width, wherever the anchor sits", () => {
    for (const { name, width } of WIDTHS) {
      const box = { width: calloutMaxWidth(width), height: 84 };
      const anchors = [
        anchor(0, 60),                       // hard left
        anchor(8, 220),                      // the legend plate, float chrome
        anchor(Math.round(width / 2) - 40, 80),
        anchor(width - 90, 82),              // the cast pair, hard right
        anchor(width - 24, 24),              // a 24px control in the corner
      ];
      for (const a of anchors) {
        const x = tipX(a, box, width);
        expect(x, `${name} @ anchor.left=${a.left}`).toBeGreaterThanOrEqual(a.left);
        expect(x, `${name} @ anchor.left=${a.left}`).toBeLessThanOrEqual(a.left + a.width);
      }
    }
  });

  it("keeps the tip off the box's own corners", () => {
    for (const { width } of WIDTHS) {
      const box = { width: calloutMaxWidth(width), height: 84 };
      for (const a of [anchor(0, 40), anchor(width - 40, 40)]) {
        const p = placeCallout(a, box, { width, height: 900 });
        expect(p.arrowLeft).toBeGreaterThanOrEqual(16);
        expect(p.arrowLeft).toBeLessThanOrEqual(box.width - 16);
      }
    }
  });

  it("slides the tip rather than the box once the box is clamped", () => {
    // The failure this guards: centring the arrow on the BOX. Two anchors that
    // both clamp the box to the same right edge must still get different tips.
    const box = { width: 300, height: 84 };
    const vp = { width: 480, height: 900 };
    const a = placeCallout(anchor(400, 60), box, vp);
    const b = placeCallout(anchor(340, 60), box, vp);
    expect(a.left).toBe(b.left);
    expect(a.arrowLeft).not.toBe(b.arrowLeft);
    expect(a.arrowLeft).toBeGreaterThan(b.arrowLeft);
  });
});

describe("which side the tip grows from", () => {
  it("sits below the control by default — that is where the map is", () => {
    expect(placeCallout(anchor(600, 200), BOX, { width: 1440, height: 900 }).side)
      .toBe("below");
  });

  it("flips above when there is no room below and there is room above", () => {
    // Short landscape: a 380px-tall viewport with the control near the bottom.
    const p = placeCallout(anchor(600, 200, 260, 34), BOX, { width: 1440, height: 380 });
    expect(p.side).toBe("above");
    expect(p.top).toBe(260 - 10 - BOX.height);
  });

  it("stays below when neither side fits, rather than covering the control", () => {
    const p = placeCallout(anchor(600, 200, 4, 30), BOX, { width: 1440, height: 100 });
    expect(p.side).toBe("below");
    expect(p.top).toBeGreaterThanOrEqual(8);
  });
});

describe("how wide the box may be", () => {
  it("shrinks with the viewport and never fills it edge to edge", () => {
    for (const { width } of WIDTHS) {
      expect(calloutMaxWidth(width)).toBeLessThanOrEqual(width - 16);
    }
  });

  it("stops at a comfortable reading width on a wide screen", () => {
    expect(calloutMaxWidth(1440)).toBe(300);
    expect(calloutMaxWidth(2560)).toBe(300);
  });

  it("keeps a floor, so a very narrow window gets a box rather than a sliver", () => {
    expect(calloutMaxWidth(120)).toBe(160);
  });
});
