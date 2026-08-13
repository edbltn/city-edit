// Binary WS frame codec — round-trip, boundaries, cross-language parity, fuzz.
//
// The GOLDEN vectors in __fixtures__/wireFrames.json are written by the Python
// encoder (server/tests/unit/test_wire_codec.py::test_write_golden_vectors).
// Decoding them here is what makes the two implementations one wire format:
// if either side's byte layout drifts, this suite fails. Same guard
// voteKey.test.ts ↔ test_vote_codec.py already puts on the 53-bit vote key.

import { describe, it, expect } from "vitest";
import {
  decodeFrame,
  encodeFrame,
  frameToDeltas,
  groupToDelta,
  WireFormatError,
  MAGIC,
  VERSION,
  KIND_DELTA,
  KIND_SYNC,
  type WireFrame,
  type WireGroup,
} from "./wireCodec";
import golden from "./__fixtures__/wireFrames.json";

const MAX_VARINT = Number.MAX_SAFE_INTEGER;

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function group(
  rev: number, mode: number, vtId: number, label: string,
  edges: Record<number, [number, number]> = {},
  blocks: Record<number, [number, number]> = {},
): WireGroup {
  return {
    rev, mode, vtId, label,
    edges: new Map(Object.entries(edges).map(([k, v]) => [Number(k), v])),
    blocks: new Map(Object.entries(blocks).map(([k, v]) => [Number(k), v])),
  };
}

function expectSameFrame(a: WireFrame, b: WireFrame) {
  expect(a.kind).toBe(b.kind);
  expect(a.rev).toBe(b.rev);
  expect(a.baseRev).toBe(b.baseRev);
  expect(a.truncated).toBe(b.truncated);
  expect(a.groups.length).toBe(b.groups.length);
  a.groups.forEach((g, i) => {
    const h = b.groups[i];
    expect([g.rev, g.mode, g.vtId, g.label]).toEqual([h.rev, h.mode, h.vtId, h.label]);
    expect([...g.edges.entries()].sort((x, y) => x[0] - y[0]))
      .toEqual([...h.edges.entries()].sort((x, y) => x[0] - y[0]));
    expect([...g.blocks.entries()].sort((x, y) => x[0] - y[0]))
      .toEqual([...h.blocks.entries()].sort((x, y) => x[0] - y[0]));
  });
}

// ── cross-language parity ─────────────────────────────────────────────────

describe("golden vectors from the Python encoder", () => {
  it("has vectors to check", () => {
    expect(golden.length).toBeGreaterThan(0);
  });

  for (const v of golden) {
    it(`decodes "${v.name}" to the frame Python encoded`, () => {
      const f = decodeFrame(hexToBytes(v.hex));
      expect(f.kind).toBe(v.frame.kind);
      expect(f.rev).toBe(v.frame.rev);
      expect(f.baseRev).toBe(v.frame.baseRev);
      expect(f.truncated).toBe(v.frame.truncated);
      expect(f.groups.length).toBe(v.frame.groups.length);
      f.groups.forEach((g, i) => {
        const e = v.frame.groups[i];
        expect([g.rev, g.mode, g.vtId, g.label]).toEqual([e.rev, e.mode, e.vtId, e.label]);
        expect(Object.fromEntries([...g.edges].map(([k, val]) => [String(k), val])))
          .toEqual(e.edges);
        expect(Object.fromEntries([...g.blocks].map(([k, val]) => [String(k), val])))
          .toEqual(e.blocks);
      });
    });

    it(`re-encodes "${v.name}" to Python's exact bytes`, () => {
      // The encoding is a bijection, so TS must reproduce Python's bytes
      // byte-for-byte — not merely something Python can read back.
      const bytes = encodeFrame(decodeFrame(hexToBytes(v.hex)));
      expect([...bytes]).toEqual([...hexToBytes(v.hex)]);
    });
  }
});

// ── round-trip ────────────────────────────────────────────────────────────

describe("round-trip", () => {
  it("round-trips an empty frame", () => {
    const f: WireFrame = { kind: KIND_DELTA, rev: 7, baseRev: 6, truncated: false, groups: [] };
    expectSameFrame(decodeFrame(encodeFrame(f)), f);
  });

  it("round-trips a single-vote delta", () => {
    const f: WireFrame = {
      kind: KIND_DELTA, rev: 41, baseRev: 40, truncated: false,
      groups: [group(41, 0, 75107, "Protected bike lane",
        { 2079: [3, 0], 2080: [3, 0], 2088: [1, 2] }, { 1841: [3, 0] })],
    };
    expectSameFrame(decodeFrame(encodeFrame(f)), f);
  });

  it("round-trips a multi-group truncated sync", () => {
    const f: WireFrame = {
      kind: KIND_SYNC, rev: 120, baseRev: 100, truncated: true,
      groups: [
        group(118, 0, 15, "Protected bike lane", { 5: [1, 0] }, { 2: [1, 0] }),
        group(120, 2, 74421, "Widen sidewalk", { 9: [0, 4] }),
      ],
    };
    expectSameFrame(decodeFrame(encodeFrame(f)), f);
  });

  it("round-trips edge id 0 and the largest NYC edge id in one run", () => {
    // id 0 encodes as a zero delta from the initial prev=0 — that must not
    // read as "no cell".
    const f: WireFrame = {
      kind: KIND_DELTA, rev: 1, baseRev: 0, truncated: false,
      groups: [group(1, 0, 3, "x", { 0: [1, 0], 3299151: [2, 1] })],
    };
    expectSameFrame(decodeFrame(encodeFrame(f)), f);
  });

  it("carries counts past 16 bits without wrapping", () => {
    const f: WireFrame = {
      kind: KIND_DELTA, rev: 1, baseRev: 0, truncated: false,
      groups: [group(1, 0, 3, "x", { 5: [65535, 65536] })],
    };
    expect(decodeFrame(encodeFrame(f)).groups[0].edges.get(5)).toEqual([65535, 65536]);
  });

  it("carries a vote_type_id past 16 bits — the field that overflowed before", () => {
    // vt was 16 bits in the Redis codec, a bulk import pushed ids past 65535,
    // and the overflow landed on the direction bit. 75107 is a real id here.
    const f: WireFrame = {
      kind: KIND_DELTA, rev: 1, baseRev: 0, truncated: false,
      groups: [group(1, 0, 75107, "x", { 5: [1, 0] })],
    };
    expect(decodeFrame(encodeFrame(f)).groups[0].vtId).toBe(75107);
  });

  it("round-trips varint boundary values in the rev field", () => {
    for (const rev of [0, 1, 127, 128, 16383, 16384, 2 ** 32, MAX_VARINT]) {
      const f: WireFrame = { kind: KIND_DELTA, rev, baseRev: 0, truncated: false, groups: [] };
      expect(decodeFrame(encodeFrame(f)).rev).toBe(rev);
    }
  });

  it("round-trips a unicode label", () => {
    const f: WireFrame = {
      kind: KIND_DELTA, rev: 1, baseRev: 0, truncated: false,
      groups: [group(1, 1, 3, "Bäume pflanzen 🌳", { 0: [1, 0] })],
    };
    expect(decodeFrame(encodeFrame(f)).groups[0].label).toBe("Bäume pflanzen 🌳");
  });
});

// ── rejection ─────────────────────────────────────────────────────────────

describe("rejection", () => {
  const good = () => encodeFrame({
    kind: KIND_DELTA, rev: 41, baseRev: 40, truncated: false,
    groups: [group(41, 0, 75107, "Protected bike lane", { 1: [1, 0], 9: [2, 0] }, { 3: [1, 0] })],
  });

  it("rejects bad magic", () => {
    const b = good(); b[0] = 0;
    expect(() => decodeFrame(b)).toThrow(WireFormatError);
  });

  it("rejects an unknown version so the client can fall back to a refetch", () => {
    const b = good(); b[1] = VERSION + 1;
    expect(() => decodeFrame(b)).toThrow(/unsupported frame version/);
  });

  it("rejects an unknown kind", () => {
    const b = good(); b[2] = 9;
    expect(() => decodeFrame(b)).toThrow(WireFormatError);
  });

  it("ignores unknown flag bits, so adding a flag stays compatible", () => {
    const b = good(); b[3] |= 0x80;
    expect(decodeFrame(b).truncated).toBe(false);
  });

  it("rejects a value past 2^53-1 at encode rather than wrapping", () => {
    expect(() => encodeFrame({
      kind: KIND_DELTA, rev: MAX_VARINT + 2, baseRev: 0, truncated: false, groups: [],
    })).toThrow(/out of range/);
  });

  it("rejects a mode wider than a byte", () => {
    expect(() => encodeFrame({
      kind: KIND_DELTA, rev: 1, baseRev: 0, truncated: false,
      groups: [group(1, 256, 3, "x")],
    })).toThrow(/does not fit one byte/);
  });

  it("rejects a non-canonical varint", () => {
    // 0x80 0x00 is a redundant spelling of 0. Accepting it would mean one
    // flipped continuation bit turns a valid frame into a different one.
    const b = new Uint8Array([MAGIC, VERSION, KIND_DELTA, 0, 0x80, 0x00, 0, 0]);
    expect(() => decodeFrame(b)).toThrow(/non-canonical/);
  });

  it("rejects trailing bytes after the frame", () => {
    const b = good();
    const padded = new Uint8Array(b.length + 1);
    padded.set(b);
    expect(() => decodeFrame(padded)).toThrow(/trailing/);
  });

  it("rejects an absurd cell count without allocating", () => {
    const b: number[] = [MAGIC, VERSION, KIND_DELTA, 0,
      1,          // rev
      0,          // baseRev
      1,          // nGroups
      1,          // group rev
      0,          // mode
      3,          // vtId
      0,          // labelLen
      // nEdge = 2^28 as a varint — far past the buffer
      0x80, 0x80, 0x80, 0x80, 0x01];
    expect(() => decodeFrame(Uint8Array.from(b))).toThrow(/exceeds remaining buffer/);
  });

  it("throws on every truncation rather than returning a partial frame", () => {
    const full = good();
    for (let cut = 4; cut < full.length; cut++) {
      expect(() => decodeFrame(full.subarray(0, cut))).toThrow(WireFormatError);
    }
  });

  it("rejects a label that is not valid UTF-8 instead of inventing one", () => {
    // A lossy decode would materialize a phantom vote-type label, which the
    // client appends to its legend.
    const b: number[] = [MAGIC, VERSION, KIND_DELTA, 0,
      1, 0, 1,    // rev, baseRev, nGroups
      1, 0, 3,    // group rev, mode, vtId
      2, 0xff, 0xfe,  // labelLen 2 + two invalid bytes
      0, 0];      // nEdge, nBlock
    expect(() => decodeFrame(Uint8Array.from(b))).toThrow(/UTF-8/);
  });
});

// ── adapter to the JSON delta shape ───────────────────────────────────────

describe("groupToDelta", () => {
  it("rebuilds `edges` from the cell ids instead of transmitting them twice", () => {
    const d = groupToDelta(group(41, 0, 15, "Protected bike lane",
      { 2079: [3, 0], 2080: [3, 0] }, { 1841: [3, 0] }));
    expect(d.edges.sort((a, b) => a - b)).toEqual([2079, 2080]);
    expect(d.vtCounts).toEqual({ "2079": [3, 0], "2080": [3, 0] });
    expect(d.blockCounts).toEqual({ "1841": [3, 0] });
    expect(d.rev).toBe(41);
    expect(d.vt).toBe(15);
    expect(d.vtLabel).toBe("Protected bike lane");
  });

  it("maps the mode byte back to the mode name the client filters on", () => {
    expect(groupToDelta(group(1, 0, 3, "x")).m).toBe("bikepaths");
    expect(groupToDelta(group(1, 2, 3, "x")).m).toBe("walkways");
    // An unknown mode falls back to "walk", matching modeToInt's backend rule.
    expect(groupToDelta(group(1, 9, 3, "x")).m).toBe("walk");
  });

  it("turns a multi-group frame into one delta per group, in order", () => {
    const deltas = frameToDeltas({
      kind: KIND_DELTA, rev: 43, baseRev: 41, truncated: false,
      groups: [group(42, 0, 15, "a", { 5: [1, 0] }), group(43, 0, 16, "b", { 9: [2, 0] })],
    });
    expect(deltas.map((d) => d.rev)).toEqual([42, 43]);
    expect(deltas.map((d) => d.vtLabel)).toEqual(["a", "b"]);
  });
});

// ── fuzz ──────────────────────────────────────────────────────────────────

describe("fuzz", () => {
  // Deterministic PRNG — a flaky codec test is worse than no codec test.
  function mulberry32(seed: number) {
    return () => {
      seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function randomFrame(rnd: () => number): WireFrame {
    const int = (n: number) => Math.floor(rnd() * n);
    const pick = <T,>(xs: T[]): T => xs[int(xs.length)];
    const groups: WireGroup[] = [];
    for (let gi = 0; gi < int(5); gi++) {
      const edges = new Map<number, [number, number]>();
      let id = int(3_299_152);
      for (let i = 0; i < int(60); i++) {
        id += 1 + int(40);
        if (id > 3_299_151) break;
        edges.set(id, [pick([0, 1, 5, 127, 128, 65536]), pick([0, 1, 3, 300])]);
      }
      const blocks = new Map<number, [number, number]>();
      let bid = int(287_352);
      for (let i = 0; i < int(20); i++) {
        bid += 1 + int(10);
        if (bid > 287_351) break;
        blocks.set(bid, [int(500), int(500)]);
      }
      let label = "";
      for (let i = 0; i < int(30); i++) label += pick([..."abc XYZ éü🌳_-#"]);
      groups.push({
        rev: int(2 ** 40), mode: int(16),
        // Keep vt ids past 65535 in the pool — that is the real overflow that
        // happened to this codec's predecessor.
        vtId: pick([int(100), 65000 + int(15000), int(2 ** 31)]),
        label, edges, blocks,
      });
    }
    return {
      kind: pick([KIND_DELTA, KIND_SYNC]),
      rev: int(2 ** 40), baseRev: int(2 ** 20),
      truncated: rnd() < 0.1, groups,
    };
  }

  it("round-trips 2000 random frames byte-identically", () => {
    const rnd = mulberry32(20260813);
    for (let i = 0; i < 2000; i++) {
      const f = randomFrame(rnd);
      const bytes = encodeFrame(f);
      const got = decodeFrame(bytes);
      expectSameFrame(got, f);
      // Canonical: re-encoding a decoded frame reproduces the exact bytes.
      expect([...encodeFrame(got)]).toEqual([...bytes]);
    }
  });

  it("never returns a silently different frame from corrupted bytes", () => {
    const rnd = mulberry32(4242);
    let raised = 0;
    for (let i = 0; i < 2000; i++) {
      const f = randomFrame(rnd);
      const bytes = encodeFrame(f);
      bytes[Math.floor(rnd() * bytes.length)] ^= 1 << Math.floor(rnd() * 8);
      let got: WireFrame;
      try {
        got = decodeFrame(bytes);
      } catch (e) {
        expect(e).toBeInstanceOf(WireFormatError);
        raised++;
        continue;
      }
      // Reserved flag bits are ignored by design — the one non-canonical byte.
      bytes[3] &= 0x01;
      expect([...encodeFrame(got)]).toEqual([...bytes]);
    }
    expect(raised).toBeGreaterThan(0);
  });

  it("never hangs or throws a non-WireFormatError on random bytes", () => {
    const rnd = mulberry32(99);
    for (let i = 0; i < 2000; i++) {
      const n = Math.floor(rnd() * 64);
      const b = new Uint8Array(n);
      for (let j = 0; j < n; j++) b[j] = Math.floor(rnd() * 256);
      if (n >= 2) { b[0] = MAGIC; b[1] = VERSION; }  // get past the header
      try {
        decodeFrame(b);
      } catch (e) {
        expect(e).toBeInstanceOf(WireFormatError);
      }
    }
  });
});
