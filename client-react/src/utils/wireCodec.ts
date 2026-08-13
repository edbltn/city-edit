// Binary wire codec for the WebSocket vote stream (frame version 1).
//
// Byte-for-byte mirror of server/wire_codec.py — the parity is test-enforced
// (wireCodec.test.ts decodes golden vectors this repo's Python encoder wrote,
// the same arrangement voteKey.test.ts ↔ test_vote_codec.py already uses).
// Read the Python module for the full rationale; the short version:
//
//   * A JSON delta spelled every edge id twice (once in `edges`, once as a
//     quoted key of `vtCounts`) and every id in full. A route's edge ids are
//     near-consecutive, so ids are sorted and delta-varint encoded here.
//   * `vote_type_id` is a VARINT and lives once per group, never once per
//     cell. The 16-bit `vt` field in the Redis codec overflowed into the
//     direction bit at 52 and silently recorded upvotes as downvotes; a
//     varint is self-delimiting, so an oversized value cannot bleed into the
//     neighbouring field at all.
//   * Every varint is bounded at 2^53-1 (Number.MAX_SAFE_INTEGER), the same
//     ceiling voteKey.ts works to. Anything larger throws instead of wrapping.
//
// Layout:
//
//   FRAME   u8 magic 0xCE | u8 version 1 | u8 kind (1=DELTA 2=SYNC)
//           u8 flags (bit0 = TRUNCATED)
//           varint rev | varint baseRev | varint nGroups | GROUP x nGroups
//   GROUP   varint rev | u8 mode | varint vtId | varint labelLen | label bytes
//           varint nEdge | CELL x nEdge | varint nBlock | CELL x nBlock
//   CELL    varint idDelta (id - prevId, prevId starts at 0) | varint up
//           | varint down
//
// The decoder returns the SAME VoteDelta shape the JSON path produced, so
// applyDeltaToGraphData, the replay ring and gap detection are untouched:
// this is a transport swap, not a model change.

import type { VoteDelta } from "../types";
import { MODE_IDS } from "./voteKey";

export const MAGIC = 0xce;
export const VERSION = 1;

export const KIND_DELTA = 1;
export const KIND_SYNC = 2;

const FLAG_TRUNCATED = 0x01;

const MAX_VARINT = Number.MAX_SAFE_INTEGER; // 2^53 - 1
const MAX_LABEL_BYTES = 1024;

const MODE_NAMES: Record<number, string> = Object.fromEntries(
  Object.entries(MODE_IDS).map(([name, id]) => [id, name]),
);

export class WireFormatError extends Error {}

export interface WireGroup {
  rev: number;
  mode: number;
  vtId: number;
  label: string;
  edges: Map<number, [number, number]>;
  blocks: Map<number, [number, number]>;
}

export interface WireFrame {
  kind: number;
  rev: number;
  baseRev: number;
  truncated: boolean;
  groups: WireGroup[];
}

// ── varint ────────────────────────────────────────────────────────────────
// JS bitwise operators are 32-bit, so — exactly as voteKey.ts does for the
// 53-bit vote key — the shift-by-7 is arithmetic (* / %), never << / >>.

function writeVarint(out: number[], value: number) {
  if (!Number.isInteger(value) || value < 0 || value > MAX_VARINT) {
    throw new WireFormatError(`varint out of range: ${value}`);
  }
  let v = value;
  for (;;) {
    const byte = v % 128;
    v = Math.floor(v / 128);
    if (v > 0) out.push(byte + 128);
    else { out.push(byte); return; }
  }
}

class Reader {
  buf: Uint8Array;
  pos: number;

  constructor(buf: Uint8Array, pos = 0) {
    this.buf = buf;
    this.pos = pos;
  }

  /** CANONICAL varints only — see the note in server/wire_codec.py. Plain
   *  LEB128 spells 0 as both 0x00 and 0x80 0x00, so one flipped continuation
   *  bit would turn a valid frame into a different valid frame. Refusing the
   *  redundant spelling makes the encoding a bijection. */
  varint(): number {
    let value = 0;
    let scale = 1;
    const start = this.pos;
    for (;;) {
      if (this.pos >= this.buf.length) {
        throw new WireFormatError("varint runs past end of buffer");
      }
      const byte = this.buf[this.pos++];
      value += (byte % 128) * scale;
      if (byte < 128) {
        if (byte === 0 && this.pos - start > 1) {
          throw new WireFormatError("non-canonical varint (redundant trailing zero)");
        }
        break;
      }
      scale *= 128;
      if (scale > 2 ** 56) throw new WireFormatError("varint too long");
    }
    if (value > MAX_VARINT) {
      throw new WireFormatError(`varint exceeds 2^53-1: ${value}`);
    }
    return value;
  }

  byte(): number {
    if (this.pos >= this.buf.length) {
      throw new WireFormatError("read past end of buffer");
    }
    return this.buf[this.pos++];
  }

  get remaining(): number {
    return this.buf.length - this.pos;
  }
}

// ── cells ─────────────────────────────────────────────────────────────────

function writeCells(out: number[], cells: Map<number, [number, number]>) {
  const ids = [...cells.keys()].sort((a, b) => a - b);
  writeVarint(out, ids.length);
  let prev = 0;
  for (const id of ids) {
    writeVarint(out, id - prev);
    prev = id;
    const [up, down] = cells.get(id)!;
    writeVarint(out, Math.max(0, up));
    writeVarint(out, Math.max(0, down));
  }
}

function readCells(r: Reader): Map<number, [number, number]> {
  const n = r.varint();
  // A cell is at least 3 bytes; a longer run than the buffer holds is corrupt.
  // Refuse it before allocating, so a bad length can't drive a huge Map.
  if (n * 3 > r.remaining) {
    throw new WireFormatError(`cell count ${n} exceeds remaining buffer`);
  }
  const cells = new Map<number, [number, number]>();
  let id = 0;
  for (let i = 0; i < n; i++) {
    const delta = r.varint();
    // Strictly ascending: only the first cell may have a zero delta (id 0).
    // A later zero would overwrite the previous cell and silently drop it.
    if (delta === 0 && i > 0) throw new WireFormatError("non-ascending cell id");
    id += delta;
    const up = r.varint();
    const down = r.varint();
    cells.set(id, [up, down]);
  }
  return cells;
}

// ── frame ─────────────────────────────────────────────────────────────────

/** Encode a frame. Present for the round-trip tests and for any future
 *  client→server frame; the live client only decodes. */
export function encodeFrame(frame: WireFrame): Uint8Array {
  const out: number[] = [MAGIC, VERSION, frame.kind, frame.truncated ? FLAG_TRUNCATED : 0];
  if (frame.kind !== KIND_DELTA && frame.kind !== KIND_SYNC) {
    throw new WireFormatError(`unknown frame kind ${frame.kind}`);
  }
  writeVarint(out, frame.rev);
  writeVarint(out, frame.baseRev);
  writeVarint(out, frame.groups.length);
  const enc = new TextEncoder();
  for (const g of frame.groups) {
    writeVarint(out, g.rev);
    if (!Number.isInteger(g.mode) || g.mode < 0 || g.mode > 255) {
      throw new WireFormatError(`mode ${g.mode} does not fit one byte`);
    }
    out.push(g.mode);
    writeVarint(out, g.vtId);
    const label = enc.encode(g.label);
    if (label.length > MAX_LABEL_BYTES) {
      throw new WireFormatError(`label too long: ${label.length} bytes`);
    }
    writeVarint(out, label.length);
    for (const b of label) out.push(b);
    writeCells(out, g.edges);
    writeCells(out, g.blocks);
  }
  return Uint8Array.from(out);
}

export function decodeFrame(input: ArrayBuffer | Uint8Array): WireFrame {
  const buf = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (buf.length < 4) throw new WireFormatError("frame shorter than header");
  if (buf[0] !== MAGIC) {
    throw new WireFormatError(`bad magic 0x${buf[0].toString(16)}`);
  }
  if (buf[1] !== VERSION) {
    // Not corrupt — just newer/older than us. Callers treat this as "fall back
    // to a full /api/graph-votes refetch", i.e. the pre-codec behaviour, so a
    // layout change degrades instead of mis-applying counts.
    throw new WireFormatError(`unsupported frame version ${buf[1]}`);
  }
  const kind = buf[2];
  if (kind !== KIND_DELTA && kind !== KIND_SYNC) {
    throw new WireFormatError(`unknown frame kind ${kind}`);
  }
  const truncated = (buf[3] & FLAG_TRUNCATED) !== 0;
  const r = new Reader(buf, 4);
  const rev = r.varint();
  const baseRev = r.varint();
  const nGroups = r.varint();
  if (nGroups * 6 > r.remaining) {
    throw new WireFormatError(`group count ${nGroups} exceeds remaining buffer`);
  }
  // fatal: a lossy decode would invent a vote-type label, and the client
  // appends unknown labels to its legend — a corrupted byte would materialize
  // a phantom proposal type. Reject the frame and refetch instead.
  const dec = new TextDecoder("utf-8", { fatal: true });
  const groups: WireGroup[] = [];
  for (let i = 0; i < nGroups; i++) {
    const gRev = r.varint();
    const mode = r.byte();
    const vtId = r.varint();
    const labelLen = r.varint();
    if (labelLen > r.remaining) {
      throw new WireFormatError("label runs past end of buffer");
    }
    let label: string;
    try {
      label = dec.decode(buf.subarray(r.pos, r.pos + labelLen));
    } catch (e) {
      throw new WireFormatError(`label is not valid UTF-8: ${e}`);
    }
    r.pos += labelLen;
    const edges = readCells(r);
    const blocks = readCells(r);
    groups.push({ rev: gRev, mode, vtId, label, edges, blocks });
  }
  // No trailing garbage — a length corrupted SHORT would otherwise decode a
  // valid prefix and silently drop real cells.
  if (r.remaining !== 0) {
    throw new WireFormatError(`${r.remaining} trailing bytes after frame`);
  }
  return { kind, rev, baseRev, truncated, groups };
}

// ── Adapter back to the JSON delta shape ──────────────────────────────────

function countsRecord(cells: Map<number, [number, number]>): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  for (const [id, pair] of cells) out[String(id)] = pair;
  return out;
}

/**
 * Turn one decoded group into the VoteDelta the app already applies.
 *
 * `edges` is rebuilt from the cell ids rather than transmitted: it was always
 * exactly the key set of `vtCounts`, and sending it twice was a third of the
 * old packet. `dir`/`reversed` are omitted because they only ever fed the
 * increment fallback in applyDeltaToGraphData, which is unreachable when
 * vtCounts is present — and a binary frame always carries counts.
 */
export function groupToDelta(g: WireGroup): VoteDelta {
  return {
    rev: g.rev,
    edges: [...g.edges.keys()],
    m: MODE_NAMES[g.mode] ?? "walk",
    vt: g.vtId,
    vtLabel: g.label,
    vtCounts: countsRecord(g.edges),
    blockCounts: countsRecord(g.blocks),
  };
}

export function frameToDeltas(frame: WireFrame): VoteDelta[] {
  return frame.groups.map(groupToDelta);
}
