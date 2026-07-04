import { describe, it, expect } from "vitest";
import { packVoteKey, unpackVoteKey, redisField, modeToInt, MODE_IDS } from "./voteKey";

// Parity vectors — these EXACT packed values are asserted identically in the
// backend test (server/tests/unit/test_vote_codec.py). If the bit layout drifts
// on either side, one of the two suites fails. Columns:
//   [edgeId, mode, voteTypeId, pack, redisFieldDown]
// Down fields carry the direction bit at 52 (24-bit vt — ids past 65535 used
// to overflow into the old bit-44 direction slot and read back as downvotes).
const VECTORS: [number, number, number, number, number][] = [
  [0, 3, 0, 50331648, 4503599677702144],
  [1, 3, 0, 50331649, 4503599677702145],
  [5, 2, 7, 1912602629, 4503601539973125],
  [16777215, 0, 0, 16777215, 4503599644147711],
  [123456, 3, 42, 11324744256, 4503610952114752],
  [99, 1, 65535, 17591934386275, 4521191561756771],
  [323, 2, 72525, 19468315001155, 4523067942371651],
];

describe("voteKey codec — backend parity", () => {
  it("packs the shared vectors to identical integers", () => {
    for (const [edge, mode, vt, packed] of VECTORS) {
      expect(packVoteKey(edge, mode, vt)).toBe(packed);
    }
  });

  it("packs the down-direction Redis field identically", () => {
    for (const [edge, mode, vt, , fieldDown] of VECTORS) {
      expect(redisField(edge, mode, vt, -1)).toBe(fieldDown);
      // up direction has no direction bit, so it equals the identity pack
      expect(redisField(edge, mode, vt, 1)).toBe(packVoteKey(edge, mode, vt));
    }
  });

  it("round-trips pack → unpack", () => {
    for (const [edge, mode, vt] of VECTORS) {
      expect(unpackVoteKey(packVoteKey(edge, mode, vt))).toEqual({
        edgeId: edge,
        modeInt: mode,
        voteTypeId: vt,
      });
    }
  });

  it("keeps all packed identities within JS safe-integer range", () => {
    for (const [edge, mode, vt, , fieldDown] of VECTORS) {
      expect(Number.isSafeInteger(packVoteKey(edge, mode, vt))).toBe(true);
      expect(Number.isSafeInteger(fieldDown)).toBe(true);
    }
  });

  it("matches the backend mode enum and falls back to walk", () => {
    expect(MODE_IDS).toEqual({ bikepaths: 0, trees: 1, walkways: 2, walk: 3 });
    expect(modeToInt("walkways")).toBe(2);
    expect(modeToInt("nonsense")).toBe(MODE_IDS.walk);
  });

  it("never collides between adjacent identities", () => {
    const a = packVoteKey(100, 3, 5);
    expect(packVoteKey(101, 3, 5)).toBe(a + 1); // edge increments by 1
    expect(packVoteKey(100, 3, 6)).toBe(a + 2 ** 28); // vote type increments by 2^28
  });
});
