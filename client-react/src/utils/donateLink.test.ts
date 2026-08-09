import { beforeEach, describe, expect, it, vi } from "vitest";

import { buildProposalDonateUrl, fundableProposalName } from "./donateLink";

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

describe("fundableProposalName", () => {
  const rows = (...labels: string[]) => labels.map((label) => ({ label }));

  it("funds the proposal the card is headed by", () => {
    expect(fundableProposalName("Add bus lane", rows("Add bus lane", "Fix crossing")))
      .toBe("Add bus lane");
  });

  it("funds a lone vote type even with no top proposal", () => {
    expect(fundableProposalName(null, rows("Improve sidewalk"))).toBe("Improve sidewalk");
  });

  it("declines to guess between several proposals on one place", () => {
    expect(fundableProposalName(null, rows("Improve sidewalk", "Add bench", "Fix crossing")))
      .toBe("");
  });

  it("has nothing to fund where nobody has voted", () => {
    expect(fundableProposalName(null, [])).toBe("");
  });
});
