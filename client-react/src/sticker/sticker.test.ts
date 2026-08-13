import { describe, it, expect } from "vitest";
import { stickerCodeFromPath } from "./api";
import { stickerMapUrl, stickerFallbackUrl } from "./link";
import { readPendingSticker } from "./pending";
import type { StickerTarget } from "./api";

const TARGET: StickerTarget = {
  code: "xupkb",
  mapSlug: "nyc-proposals",
  voteType: "Add traffic calming",
  headline: "FIX THIS INTERSECTION.",
  src: "stk-fix",
  location: null,
  waypoints: null,
};

const POINT = { lat: 40.75612, lng: -73.98693 };

/** stickerMapUrl is relative by design (the app's own subdomain redirect
 *  settles the host), so parse against any origin to read the params. */
function params(url: string): URLSearchParams {
  return new URL(url, "https://cityedit.org").searchParams;
}

describe("stickerMapUrl", () => {
  it("opens the map at the scanned point with the vote type preselected", () => {
    const url = new URL(stickerMapUrl(TARGET, POINT), "https://cityedit.org");
    expect(url.pathname).toBe("/m/nyc-proposals");
    // The canonical selection param: a scan produces exactly the shape of link
    // a user gets by sharing a selection by hand.
    expect(url.searchParams.get("w")).toBe("40.756120,-73.986930");
    expect(url.searchParams.get("vt")).toBe("Add traffic calming");
    expect(url.searchParams.get("src")).toBe("stk-fix");
  });

  it("carries the code only while the sticker still owes us its location", () => {
    expect(params(stickerMapUrl(TARGET, POINT, { pending: true })).get("stk"))
      .toBe("xupkb");
    // A resolved sticker has nothing left to record, and a later scan must not
    // be able to move a pin that is already set.
    expect(params(stickerMapUrl(TARGET, POINT)).get("stk")).toBeNull();
  });

  it("frames the camera on the scanned spot", () => {
    const p = params(stickerMapUrl(TARGET, POINT));
    expect(p.get("z")).toBe("18");
    expect(p.get("lat")).toBe("40.75612");
    expect(p.get("lng")).toBe("-73.98693");
  });

  it("replays a bound ROUTE with every waypoint intact", () => {
    // The whole point of storing `w` rather than a pin: rebuilding a corridor
    // from its first coordinate would silently demote it to a point vote.
    const route = "40.756120,-73.986930;40.758400,-73.984100;40.760900,-73.981700";
    const bound = {
      ...TARGET,
      voteType: "Add bike lane",
      mapSlug: "nyc-bikes",
      waypoints: route,
    };
    const p = params(stickerMapUrl(bound, POINT));
    expect(p.get("w")).toBe(route);
    expect(p.get("vt")).toBe("Add bike lane");
  });

  it("follows a binding onto a different map than the one printed", () => {
    // The scanner changed map before voting. That was a decision, not a
    // mistake, so every later scan should land where they decided.
    const bound = { ...TARGET, mapSlug: "nyc-bikes", waypoints: "40.1,-73.9" };
    const url = new URL(stickerMapUrl(bound, POINT), "https://cityedit.org");
    expect(url.pathname).toBe("/m/nyc-bikes");
  });

  it("falls back to the point for codes bound before `w` was recorded", () => {
    const p = params(stickerMapUrl({ ...TARGET, waypoints: null }, POINT));
    expect(p.get("w")).toBe("40.756120,-73.986930");
  });
});

describe("stickerFallbackUrl", () => {
  it("still opens the map with the vote type when location was refused", () => {
    const url = new URL(stickerFallbackUrl(TARGET), "https://cityedit.org");
    expect(url.pathname).toBe("/m/nyc-proposals");
    expect(url.searchParams.get("vt")).toBe("Add traffic calming");
    expect(url.searchParams.get("src")).toBe("stk-fix");
  });

  it("still carries the code, so a hand-placed vote binds like any other", () => {
    // This is the regression that matters: withholding `stk` here meant the most
    // deliberate way to use a sticker — look at the map, pick the spot — was the
    // one way that never bound it, so a code could be voted from all day and
    // still ask the next person for a location.
    expect(params(stickerFallbackUrl(TARGET)).get("stk")).toBe("xupkb");
  });

  it("carries no selection: the visitor places it, we do not guess", () => {
    expect(params(stickerFallbackUrl(TARGET)).get("w")).toBeNull();
  });
});

describe("readPendingSticker", () => {
  it("takes the code and hands back the query string without it", () => {
    // Stripped so a link copied out of the address bar cannot carry someone
    // else's sticker and repin it.
    expect(readPendingSticker("?stk=xupkb&w=1,2")).toEqual({
      code: "xupkb",
      search: "?w=1%2C2",
    });
  });

  it("drops a malformed code but still strips it", () => {
    expect(readPendingSticker("?stk=../../etc&w=1,2")).toEqual({
      code: null,
      search: "?w=1%2C2",
    });
    expect(readPendingSticker("?stk=")).toEqual({ code: null, search: "" });
  });

  it("leaves an ordinary visit alone", () => {
    expect(readPendingSticker("?w=1,2")).toEqual({ code: null, search: "?w=1%2C2" });
    expect(readPendingSticker("")).toEqual({ code: null, search: "" });
  });

  it("empties the query string when the code was the only param", () => {
    // "" not "?" — a bare question mark left in the address bar is a smell.
    expect(readPendingSticker("?stk=xupkb")).toEqual({ code: "xupkb", search: "" });
  });
});

describe("stickerCodeFromPath", () => {
  it("reads the code out of /s/<code>, in the case it was printed", () => {
    expect(stickerCodeFromPath("/s/XUPKB")).toBe("XUPKB");
    expect(stickerCodeFromPath("/s/xupkb/")).toBe("xupkb");
  });

  it("does not hijack any other path", () => {
    expect(stickerCodeFromPath("/m/nyc-proposals")).toBeNull();
    expect(stickerCodeFromPath("/")).toBeNull();
    expect(stickerCodeFromPath("/s/")).toBeNull();
    expect(stickerCodeFromPath("/s/xupkb/extra")).toBeNull();
    // Too short / too long to be one of ours.
    expect(stickerCodeFromPath("/s/ab")).toBeNull();
    expect(stickerCodeFromPath("/s/abcdefghijklmnop")).toBeNull();
  });
});
