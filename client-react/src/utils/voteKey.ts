// ==========================================================================
// Vote key codec (the single client-side encoder/decoder)
// ==========================================================================
// A vote field is packed into one integer, byte-for-byte identical to the
// backend (server/vote_store.py — pack/unpack/redis_field/MODE_IDS). Keeping
// one bit layout on both sides means the client store key and the Redis hash
// field are the same number, so "have I voted" lookups are O(1) integer
// compares and the parity is test-enforced (voteKey.test.ts ↔
// server/tests/unit/test_vote_codec.py).
//
//   bit  [44]      direction    (0 = up, 1 = down)  — only on Redis count fields
//   bits [43..28]  vote_type_id (16 bits, <= 65535)
//   bits [27..24]  mode         (4 bits — the map's mode)
//   bits [23..0]   edge_id      (24 bits, <= ~16M edges)
//
// NOTE: the packed value exceeds 2^32, and JS bitwise operators are 32-bit, so
// this codec uses arithmetic (* / %), never << / >>.

const EDGE_BITS = 24;
const MODE_BITS = 4;

const EDGE_SPAN = 2 ** EDGE_BITS; // 16,777,216
const MODE_SPAN = 2 ** MODE_BITS; // 16
const MODE_SHIFT = EDGE_SPAN; // mode occupies the next 4 bits above edge_id
const VT_SHIFT = EDGE_SPAN * MODE_SPAN; // 2^28 — vote_type_id above mode
const DIR_SHIFT = 2 ** 44; // direction bit (only on Redis fields)

// Mode enum — MUST match server/vote_store.py MODE_IDS.
export const MODE_IDS: Record<string, number> = {
  bikepaths: 0,
  trees: 1,
  walkways: 2,
  walk: 3,
};

/** Mode string → 4-bit int. Unknown modes fall back to "walk" (matches backend). */
export function modeToInt(mode: string): number {
  const id = MODE_IDS[mode];
  return id === undefined ? MODE_IDS.walk : id;
}

/**
 * Direction-less identity field (bits 0..43) — the client vote-store key and the
 * base of the Redis field. `pack` in server/vote_store.py.
 */
export function packVoteKey(edgeId: number, modeInt: number, voteTypeId: number): number {
  return voteTypeId * VT_SHIFT + modeInt * MODE_SHIFT + edgeId;
}

/** Inverse of packVoteKey. Returns the identity components (no direction bit). */
export function unpackVoteKey(key: number): {
  edgeId: number;
  modeInt: number;
  voteTypeId: number;
} {
  const edgeId = key % EDGE_SPAN;
  const modeInt = Math.floor(key / MODE_SHIFT) % MODE_SPAN;
  const voteTypeId = Math.floor(key / VT_SHIFT) % (1 << 16);
  return { edgeId, modeInt, voteTypeId };
}

/**
 * Full Redis hash field — identity plus the direction bit (0 = up, 1 = down).
 * `redis_field` in server/vote_store.py. Provided for parity tests / debugging;
 * the client store keys on the direction-less identity and stores direction as
 * the value.
 */
export function redisField(
  edgeId: number,
  modeInt: number,
  voteTypeId: number,
  direction: number,
): number {
  const base = packVoteKey(edgeId, modeInt, voteTypeId);
  return direction < 0 ? base + DIR_SHIFT : base;
}
