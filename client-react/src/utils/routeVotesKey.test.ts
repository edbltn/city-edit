import { describe, expect, it } from "vitest";

import { routeVotesKey } from "./blockSelection";

describe("routeVotesKey", () => {
  it("is order-insensitive over the edge set", () => {
    expect(routeVotesKey("nyc", [3, 1, 2])).toBe(routeVotesKey("nyc", [1, 2, 3]));
    expect(routeVotesKey("nyc", [90210, 7, 40])).toBe(routeVotesKey("nyc", [40, 90210, 7]));
  });

  it("distinguishes different edge sets", () => {
    expect(routeVotesKey("nyc", [1, 2, 3])).not.toBe(routeVotesKey("nyc", [1, 2, 4]));
    expect(routeVotesKey("nyc", [1, 2, 3])).not.toBe(routeVotesKey("nyc", [1, 2]));
  });

  it("scopes by map slug", () => {
    expect(routeVotesKey("nyc", [1, 2, 3])).not.toBe(routeVotesKey("e-bikes-3", [1, 2, 3]));
  });

  it("handles large ids (beyond 16 bits) without losing high bits", () => {
    expect(routeVotesKey("nyc", [1_900_000])).not.toBe(routeVotesKey("nyc", [1_900_000 + 0x10000]));
  });

  it("does not mutate its input", () => {
    const ids = [5, 3, 9];
    routeVotesKey("nyc", ids);
    expect(ids).toEqual([5, 3, 9]);
  });
});
