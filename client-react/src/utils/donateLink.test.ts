import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildProposalDonateUrl } from "./donateLink";

vi.mock("../map/runtime", () => ({
  getCurrentMap: () => ({ name: "NYC Proposals" }),
  getMapSlug: () => "nyc-proposals",
}));

describe("buildProposalDonateUrl", () => {
  let url: URL;

  beforeEach(() => {
    url = new URL(buildProposalDonateUrl({
      name: "Protected bike lane",
      place: "Grand St & Bowery",
      url: "https://cityedit.org/m/nyc-proposals?w=40.72,-73.99&vt=Protected+bike+lane",
    }));
  });

  it("points at our donation page, not the payment processor directly", () => {
    expect(url.origin + url.pathname).toBe("https://donate.cityedit.org/");
  });

  it("names the proposal, its place and its map in the message", () => {
    expect(url.searchParams.get("utm_content"))
      .toBe("Fund: Protected bike lane — Grand St & Bowery — NYC Proposals");
  });

  it("carries the deep link back to the proposal", () => {
    expect(url.searchParams.get("utm_term"))
      .toBe("https://cityedit.org/m/nyc-proposals?w=40.72,-73.99&vt=Protected+bike+lane");
  });

  it("tags the source so proposal donations are distinguishable from plain ones", () => {
    expect(url.searchParams.get("utm_source")).toBe("cityedit");
    expect(url.searchParams.get("utm_medium")).toBe("proposal");
    expect(url.searchParams.get("utm_campaign")).toBe("nyc-proposals");
  });

  it("omits an absent place rather than leaving a dangling separator", () => {
    const routeUrl = new URL(buildProposalDonateUrl({
      name: "Protected bike lane",
      url: "https://cityedit.org/m/nyc-proposals",
    }));
    expect(routeUrl.searchParams.get("utm_content"))
      .toBe("Fund: Protected bike lane — NYC Proposals");
  });

  it("clips an overlong value instead of emitting an unbounded parameter", () => {
    const long = new URL(buildProposalDonateUrl({
      name: "x".repeat(400),
      url: "https://cityedit.org/m/nyc-proposals",
    }));
    const content = long.searchParams.get("utm_content")!;
    expect(content.length).toBeLessThanOrEqual(160);
    expect(content.endsWith("…")).toBe(true);
  });
});

describe("per-row donation targets", () => {
  // Each row of a card is its own proposal, so two rows on the same place must
  // produce two distinguishable links — that is the whole point of moving the
  // chip onto the row.
  it("names the row's own vote type, not the card's", () => {
    const bus = new URL(buildProposalDonateUrl({
      name: "Add bus lane", place: "21st St", url: "https://cityedit.org/m/nyc-proposals?w=1,2",
    }));
    const bike = new URL(buildProposalDonateUrl({
      name: "Add protected bike lane", place: "21st St", url: "https://cityedit.org/m/nyc-proposals?w=1,2",
    }));
    expect(bus.searchParams.get("utm_content")).toContain("Add bus lane");
    expect(bike.searchParams.get("utm_content")).toContain("Add protected bike lane");
    expect(bus.searchParams.get("utm_content")).not.toBe(bike.searchParams.get("utm_content"));
  });
});
