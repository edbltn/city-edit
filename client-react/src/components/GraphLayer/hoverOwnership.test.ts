import { describe, it, expect } from "vitest";
import {
  holdsIndicatorHover,
  shouldReleaseIndicatorHover,
  type IndicatorHoverState,
} from "./hoverOwnership";

const NOTHING_HELD: IndicatorHoverState = {
  overIndicator: false,
  routeHighlight: false,
  hoverCard: false,
};

/** What a corridor diamond's `mouseover` (activate) puts up. */
const CORRIDOR_HOVERED: IndicatorHoverState = {
  overIndicator: true,
  routeHighlight: true,
  hoverCard: true,
};

describe("holdsIndicatorHover", () => {
  it("is false only when nothing is held", () => {
    expect(holdsIndicatorHover(NOTHING_HELD)).toBe(false);
    expect(holdsIndicatorHover(CORRIDOR_HOVERED)).toBe(true);
  });

  it("counts each strand on its own — a corridor can be lit with no card", () => {
    // Touch: the tap-mouseover set the ref while the gated hover card never
    // mounted, so the corridor's blocks are lit with no card to notice.
    expect(holdsIndicatorHover({ ...NOTHING_HELD, routeHighlight: true })).toBe(true);
    expect(holdsIndicatorHover({ ...NOTHING_HELD, overIndicator: true })).toBe(true);
    expect(holdsIndicatorHover({ ...NOTHING_HELD, hoverCard: true })).toBe(true);
  });
});

describe("shouldReleaseIndicatorHover", () => {
  it("never releases while the pointer is on a marker icon", () => {
    expect(shouldReleaseIndicatorHover(true, CORRIDOR_HOVERED)).toBe(false);
  });

  it("releases when the pointer is on the bare map with state still held", () => {
    expect(shouldReleaseIndicatorHover(false, CORRIDOR_HOVERED)).toBe(true);
  });

  it("is a no-op when nothing is held (no needless re-broadcast)", () => {
    expect(shouldReleaseIndicatorHover(false, NOTHING_HELD)).toBe(false);
    expect(shouldReleaseIndicatorHover(true, NOTHING_HELD)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Regression: frozen highlight after dragging a start/end waypoint.
// ---------------------------------------------------------------------------
// A tiny model of the real wiring. The block-select broadcast is the union of
// the route's own blocks and — while a diamond is hovered — that corridor's
// whole block-edge union; the corridor half is dropped by the icon's
// `mouseout`, or by the mousemove reconciliation when the pointer is on the
// bare map.
function makeMap(routeBlocks: number, corridorBlocks: number) {
  let held: IndicatorHoverState = { ...NOTHING_HELD };

  return {
    /** Blocks currently lit — what MapLibre paints as `selected`. */
    lit: () => routeBlocks + (held.routeHighlight ? corridorBlocks : 0),
    /** The diamond's Leaflet `mouseover` → activate(). */
    hoverDiamond: () => { held = { ...CORRIDOR_HOVERED }; },
    /**
     * A re-render hands the hovered diamond a new DivIcon. `createIcon` reuses
     * the wrapper but rewrites its innerHTML, destroying the child node the
     * browser tracks as the hover target — so the eventual `mouseout` fires on
     * a detached node, never bubbles to the map, and `deactivate` never runs.
     * Deliberately a NO-OP on the state: that is precisely the bug.
     */
    rerenderIconUnderCursor: () => {},
    /** Map mousemove, with the reconciliation the fix added. */
    pointerMove: (overMarkerIcon: boolean) => {
      if (shouldReleaseIndicatorHover(overMarkerIcon, held)) {
        held = { ...NOTHING_HELD };
      }
    },
  };
}

describe("frozen highlight after a waypoint drag", () => {
  it("clears a corridor whose mouseout was eaten by the drag's icon re-render", () => {
    const map = makeMap(21, 50);
    expect(map.lit()).toBe(21);

    // 1. Cursor lands on a corridor diamond: its whole block union lights up.
    map.hoverDiamond();
    expect(map.lit()).toBe(71);

    // 2. Dragging a start/end waypoint re-renders that icon (the drop-target
    //    ring recomputes per drag frame; coverage/passthrough at the drop).
    //    The mouseout is swallowed.
    map.rerenderIconUnderCursor();

    // 3. The pointer leaves for the bare map. Before the fix nothing else ever
    //    fired and the corridor stayed lit for the rest of the session.
    map.pointerMove(false);
    expect(map.lit()).toBe(21);
  });

  it("keeps the corridor lit while the pointer is still on an icon", () => {
    const map = makeMap(21, 50);
    map.hoverDiamond();
    map.rerenderIconUnderCursor();
    map.pointerMove(true);
    expect(map.lit()).toBe(71);
  });

  it("stays clean across repeated hover/drag rounds", () => {
    const map = makeMap(21, 50);
    for (let i = 0; i < 3; i++) {
      map.hoverDiamond();
      map.rerenderIconUnderCursor();
      map.pointerMove(false);
      expect(map.lit()).toBe(21);
    }
  });
});
