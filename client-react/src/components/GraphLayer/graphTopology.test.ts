import { describe, it, expect } from "vitest";
import {
  COORD_SCALE,
  TOPOLOGY_BIN_MAGIC_V1,
  TOPOLOGY_BIN_MAGIC_V2,
  decodeTopologyBin,
  buildBlockIndex,
  blockKeyOf,
  edgesOfBlockKey,
  touchedBlockKeys,
  nodeLat,
  nodeLon,
  edgeFrom,
  edgeTo,
  type GraphTopology,
} from "./graphTopology";

// ── blob builders (hand-built wire bytes, little-endian) ────────────────────

type Node = [number, number]; // [lat, lon]
type Edge = [number, number]; // [fromIdx, toIdx]

function fillBody(
  buf: ArrayBuffer,
  headerBytes: number,
  nodes: Node[],
  edges: Edge[],
) {
  const coords = new Int32Array(buf, headerBytes, nodes.length * 2);
  nodes.forEach(([lat, lon], i) => {
    coords[2 * i] = Math.round(lat * COORD_SCALE);
    coords[2 * i + 1] = Math.round(lon * COORD_SCALE);
  });
  const ends = new Uint32Array(buf, headerBytes + nodes.length * 8, edges.length * 2);
  edges.forEach(([u, v], i) => {
    ends[2 * i] = u;
    ends[2 * i + 1] = v;
  });
}

function gtb1(nodes: Node[], edges: Edge[]): ArrayBuffer {
  const buf = new ArrayBuffer(12 + nodes.length * 8 + edges.length * 8);
  const head = new Uint32Array(buf, 0, 3);
  head[0] = TOPOLOGY_BIN_MAGIC_V1;
  head[1] = nodes.length;
  head[2] = edges.length;
  fillBody(buf, 12, nodes, edges);
  return buf;
}

function gtb2(nodes: Node[], edges: Edge[], nBlocks: number, blockIds?: number[]): ArrayBuffer {
  const blockBytes = nBlocks > 0 ? edges.length * 4 : 0;
  const buf = new ArrayBuffer(16 + nodes.length * 8 + edges.length * 8 + blockBytes);
  const head = new Uint32Array(buf, 0, 4);
  head[0] = TOPOLOGY_BIN_MAGIC_V2;
  head[1] = nodes.length;
  head[2] = edges.length;
  head[3] = nBlocks;
  fillBody(buf, 16, nodes, edges);
  if (nBlocks > 0) {
    const blocks = new Int32Array(buf, 16 + nodes.length * 8 + edges.length * 8, edges.length);
    blocks.set(blockIds ?? []);
  }
  return buf;
}

const NODES: Node[] = [
  [40.70, -74.00],
  [40.71, -74.01],
  [40.72, -74.02],
];
const EDGES: Edge[] = [[0, 1], [1, 2], [2, 0]];

// ── decode ──────────────────────────────────────────────────────────────────

describe("decodeTopologyBin — GTB1", () => {
  it("round-trips nodes and edges from a hand-built blob", () => {
    const d = decodeTopologyBin(gtb1(NODES, EDGES));
    expect(d.nNodes).toBe(3);
    expect(d.nEdges).toBe(3);
    for (let i = 0; i < 3; i++) {
      expect(nodeLat(d, i)).toBeCloseTo(NODES[i][0], 6);
      expect(nodeLon(d, i)).toBeCloseTo(NODES[i][1], 6);
      expect(edgeFrom(d, i)).toBe(EDGES[i][0]);
      expect(edgeTo(d, i)).toBe(EDGES[i][1]);
    }
    expect(d.edgeBlockId).toBeUndefined();
    expect(d.nBlocks).toBeUndefined();
  });

  it("clamps out-of-range node indices to 0", () => {
    const d = decodeTopologyBin(gtb1(NODES, [[0, 99]]));
    expect(edgeTo(d, 0)).toBe(0);
  });

  it("rejects a bad magic, a truncated blob, and a size mismatch", () => {
    expect(() => decodeTopologyBin(new ArrayBuffer(4))).toThrow(/too small/);
    const bad = gtb1(NODES, EDGES);
    new Uint32Array(bad, 0, 1)[0] = 0xdeadbeef;
    expect(() => decodeTopologyBin(bad)).toThrow(/magic/);
    const truncated = gtb1(NODES, EDGES).slice(0, 20);
    expect(() => decodeTopologyBin(truncated)).toThrow(/size mismatch/);
  });
});

describe("decodeTopologyBin — GTB2", () => {
  it("round-trips the trailing edge_block_id section (incl. -1 values)", () => {
    const d = decodeTopologyBin(gtb2(NODES, EDGES, 2, [0, 1, -1]));
    expect(d.nNodes).toBe(3);
    expect(d.nEdges).toBe(3);
    expect(d.nBlocks).toBe(2);
    expect(Array.from(d.edgeBlockId!)).toEqual([0, 1, -1]);
    expect(nodeLat(d, 2)).toBeCloseTo(40.72, 6);
    expect(edgeFrom(d, 1)).toBe(1);
  });

  it("decodes nBlocks=0 as a blockless topology (no trailing section)", () => {
    const d = decodeTopologyBin(gtb2(NODES, EDGES, 0));
    expect(d.nEdges).toBe(3);
    expect(d.edgeBlockId).toBeUndefined();
    expect(d.nBlocks).toBeUndefined();
  });

  it("rejects a GTB2 blob whose size does not match its header", () => {
    // Claims blocks but ships none.
    const noTrailer = gtb2(NODES, EDGES, 0);
    new Uint32Array(noTrailer, 0, 4)[3] = 2;
    expect(() => decodeTopologyBin(noTrailer)).toThrow(/size mismatch/);
  });
});

// ── block index + block keys ────────────────────────────────────────────────

function topoWithBlocks(blockIds: number[] | null, nBlocks?: number): GraphTopology {
  const nEdges = blockIds ? blockIds.length : 4;
  const ends = new Uint32Array(nEdges * 2);
  for (let i = 0; i < nEdges; i++) {
    ends[2 * i] = i % 2;
    ends[2 * i + 1] = (i + 1) % 2;
  }
  const topo: GraphTopology = {
    nNodes: 2,
    nEdges,
    coords: new Int32Array(4),
    ends,
  };
  if (blockIds) {
    topo.edgeBlockId = Int32Array.from(blockIds);
    if (nBlocks !== undefined) topo.nBlocks = nBlocks;
  }
  return topo;
}

describe("buildBlockIndex", () => {
  it("builds a CSR block → edge-ids index, skipping -1 edges", () => {
    const topo = topoWithBlocks([0, 0, 2, -1, 1], 3);
    const index = buildBlockIndex(topo)!;
    expect(Array.from(edgesOfBlockKey(topo, index, 0))).toEqual([0, 1]);
    expect(Array.from(edgesOfBlockKey(topo, index, 1))).toEqual([4]);
    expect(Array.from(edgesOfBlockKey(topo, index, 2))).toEqual([2]);
    // Edge 3 (-1) appears in no block row.
    expect(index.edges.length).toBe(4);
  });

  it("infers nBlocks from the mapping when the topology lacks it", () => {
    const topo = topoWithBlocks([1, 0, 1]);
    const index = buildBlockIndex(topo)!;
    expect(index.start.length).toBe(3); // 2 blocks + 1
    expect(Array.from(edgesOfBlockKey(topo, index, 1))).toEqual([0, 2]);
  });

  it("returns null when the topology has no block mapping", () => {
    expect(buildBlockIndex(topoWithBlocks(null))).toBeNull();
  });
});

describe("blockKeyOf / edgesOfBlockKey — the block-key convention", () => {
  it("uses the block id for mapped edges and -(edgeId+2) singletons otherwise", () => {
    const topo = topoWithBlocks([7, -1, 7], 8);
    expect(blockKeyOf(topo, 0)).toBe(7);
    expect(blockKeyOf(topo, 2)).toBe(7);
    expect(blockKeyOf(topo, 1)).toBe(-3); // unmapped (-1) → singleton, never -1 itself
  });

  it("falls back to singletons for every edge on a blockless topology", () => {
    const topo = topoWithBlocks(null);
    expect(blockKeyOf(topo, 0)).toBe(-2);
    expect(blockKeyOf(topo, 3)).toBe(-5);
  });

  it("resolves a singleton key back to its single edge without an index", () => {
    const topo = topoWithBlocks(null);
    expect(Array.from(edgesOfBlockKey(topo, null, -2))).toEqual([0]);
    expect(Array.from(edgesOfBlockKey(topo, null, -5))).toEqual([3]);
    // Out-of-range singleton / unknown real key → empty.
    expect(Array.from(edgesOfBlockKey(topo, null, -99))).toEqual([]);
    expect(Array.from(edgesOfBlockKey(topo, null, 3))).toEqual([]);
  });
});

describe("touchedBlockKeys", () => {
  it("dedupes to sorted distinct keys across real blocks and singletons", () => {
    const topo = topoWithBlocks([5, 5, -1, 2], 6);
    // edges 0,1 → block 5; edge 2 → singleton -4; edge 3 → block 2
    expect(touchedBlockKeys(topo, [3, 1, 0, 2, 0])).toEqual([-4, 2, 5]);
  });

  it("is all singletons on a blockless topology", () => {
    const topo = topoWithBlocks(null);
    expect(touchedBlockKeys(topo, [2, 0, 2])).toEqual([-4, -2]);
  });
});
