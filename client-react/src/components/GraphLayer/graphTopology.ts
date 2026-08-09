// Referenced by: docs/algorithms/06-ranking.md and
//   docs/algorithms/07-counts.md (touchedBlockKeys / edgesOfBlockKey —
//   the edge <-> block projection every selection number is computed over).
// ==========================================================================
// Graph topology — typed-array representation + accessors
// ==========================================================================
//
// The NYC street graph is ~1.3M nodes / ~3.3M edges. Held as boxed JS tuples
// (`[lat,lon][]` + `[from,to,name][]`) it decoded to ~500MB of heap and a
// `number[][]` adjacency added ~150MB more — over mobile Safari's per-tab
// budget, so the WebContent process was jetsam-killed on load and Safari showed
// "a problem repeatedly occurred". Backing the topology with flat typed arrays
// (the same bytes the binary wire format already carries) keeps it near the
// ~35MB blob size; accessors read coordinates/endpoints on demand.
//
// Coordinates are stored as integers scaled by 1e7 (~1cm precision), matching
// the binary topology's on-wire layout (see server graph_registry.topology_binary).

export const COORD_SCALE = 1e7;

/** Flat, mobile-safe topology. `coords`/`ends` mirror the binary wire format. */
export interface GraphTopology {
  nNodes: number;
  nEdges: number;
  /** 2·nNodes int32s: [lat, lon] per node, each scaled by COORD_SCALE. */
  coords: Int32Array;
  /** 2·nEdges uint32s: [fromNodeIdx, toNodeIdx] per edge. */
  ends: Uint32Array;
  /** Per-edge street name, only for station (JSON) networks where names matter;
   *  omitted for large street graphs (the client reverse-geocodes tooltips). */
  edgeNames?: string[];
  /** Per-edge block id (GTB2 blobs only); -1 = unmapped. See blockKeyOf. */
  edgeBlockId?: Int32Array;
  /** Number of blocks the edgeBlockId values index into. */
  nBlocks?: number;
}

// ── Element accessors ───────────────────────────────────────────────────────
// Hot loops (redraw, index build) should read `coords`/`ends` directly into a
// local for speed; these are for the many cold call sites.

export const nodeLat = (d: GraphTopology, i: number): number => d.coords[2 * i] / COORD_SCALE;
export const nodeLon = (d: GraphTopology, i: number): number => d.coords[2 * i + 1] / COORD_SCALE;
/** Node coordinate as a `[lat, lon]` tuple (allocates — avoid in tight loops). */
export const nodeLatLng = (d: GraphTopology, i: number): [number, number] => [
  d.coords[2 * i] / COORD_SCALE,
  d.coords[2 * i + 1] / COORD_SCALE,
];
export const edgeFrom = (d: GraphTopology, i: number): number => d.ends[2 * i];
export const edgeTo = (d: GraphTopology, i: number): number => d.ends[2 * i + 1];
export const edgeName = (d: GraphTopology, i: number): string =>
  d.edgeNames ? d.edgeNames[i] ?? "" : "";

// ── Construction ──────────────────────────────────────────────────────────

/**
 * Build a topology from the JSON wire shape (`{nodes:[lat,lon][], edges:[from,
 * to,name][]}`). Used only for tiny station networks; street graphs come in via
 * the binary decoder, which produces the typed arrays directly without boxing.
 */
export function topologyFromJson(json: {
  nodes: [number, number][];
  edges: [number, number, string][];
}): GraphTopology {
  const nNodes = json.nodes.length;
  const nEdges = json.edges.length;
  const coords = new Int32Array(nNodes * 2);
  for (let i = 0; i < nNodes; i++) {
    coords[2 * i] = Math.round(json.nodes[i][0] * COORD_SCALE);
    coords[2 * i + 1] = Math.round(json.nodes[i][1] * COORD_SCALE);
  }
  const ends = new Uint32Array(nEdges * 2);
  const edgeNames: string[] = new Array(nEdges);
  let anyName = false;
  for (let i = 0; i < nEdges; i++) {
    const a = json.edges[i][0];
    const b = json.edges[i][1];
    ends[2 * i] = a < nNodes ? a : 0;
    ends[2 * i + 1] = b < nNodes ? b : 0;
    const name = json.edges[i][2] || "";
    edgeNames[i] = name;
    if (name) anyName = true;
  }
  return { nNodes, nEdges, coords, ends, edgeNames: anyName ? edgeNames : undefined };
}

// ── Binary decoding ─────────────────────────────────────────────────────────
// Wire layouts (little-endian, server graph_registry.topology_binary):
//   GTB1: [magic u32, nNodes u32, nEdges u32] + 2·nNodes i32 coords + 2·nEdges
//         u32 ends.
//   GTB2: [magic u32, nNodes u32, nEdges u32, nBlocks u32] + coords + ends,
//         then IFF nBlocks > 0 a trailing i32[nEdges] edge_block_id (-1 allowed).
// All section offsets are 4-byte multiples, so views are zero-copy onto the
// blob — the NYC graph stays near its wire size instead of expanding to boxed
// JS tuples (the allocation that OOM-crashed mobile Safari).

// 'GTB1' / 'GTB2' little-endian as uint32 (G=0x47, T=0x54, B=0x42).
export const TOPOLOGY_BIN_MAGIC_V1 = 0x31425447;
export const TOPOLOGY_BIN_MAGIC_V2 = 0x32425447;

/**
 * Decode a GTB1/GTB2 topology blob into a GraphTopology. Validates the header
 * and total size BEFORE creating views: a stale/truncated/corrupt cached blob
 * would otherwise be read as garbage nNodes/nEdges — views that index out of
 * bounds, or edges referencing nonexistent nodes (the downstream crash). On any
 * inconsistency we throw, and callers fall back to a fresh fetch / JSON.
 */
export function decodeTopologyBin(buf: ArrayBuffer): GraphTopology {
  if (buf.byteLength < 12) {
    throw new Error(`topology blob too small (${buf.byteLength} bytes)`);
  }
  const magic = new Uint32Array(buf, 0, 1)[0];
  const v2 = magic === TOPOLOGY_BIN_MAGIC_V2;
  if (!v2 && magic !== TOPOLOGY_BIN_MAGIC_V1) {
    throw new Error(`bad topology magic 0x${magic.toString(16)}`);
  }
  const headerBytes = v2 ? 16 : 12;
  if (buf.byteLength < headerBytes) {
    throw new Error(`topology blob too small (${buf.byteLength} bytes)`);
  }
  const head = new Uint32Array(buf, 0, headerBytes / 4);
  const nNodes = head[1];
  const nEdges = head[2];
  const nBlocks = v2 ? head[3] : 0;
  const blockBytes = nBlocks > 0 ? nEdges * 4 : 0;
  const expected = headerBytes + nNodes * 8 + nEdges * 8 + blockBytes;
  if (buf.byteLength !== expected) {
    throw new Error(
      `topology size mismatch: ${buf.byteLength} bytes, expected ${expected} ` +
        `(nNodes=${nNodes}, nEdges=${nEdges}, nBlocks=${nBlocks})`,
    );
  }
  const coords = new Int32Array(buf, headerBytes, nNodes * 2);
  const ends = new Uint32Array(buf, headerBytes + nNodes * 8, nEdges * 2);
  // Clamp out-of-range node indices to 0 in place rather than leaving a dangling
  // ref that reads NaN coords downstream — a degenerate self-edge is harmless.
  // Mutating the view also sanitizes the blob callers may cache.
  for (let i = 0; i < ends.length; i++) {
    if (ends[i] >= nNodes) ends[i] = 0;
  }
  const topo: GraphTopology = { nNodes, nEdges, coords, ends };
  if (nBlocks > 0) {
    topo.edgeBlockId = new Int32Array(buf, headerBytes + nNodes * 8 + nEdges * 8, nEdges);
    topo.nBlocks = nBlocks;
  }
  return topo;
}

// ── Node adjacency (CSR) ────────────────────────────────────────────────────
// Replaces the old `number[][]` (1.3M sub-array objects, ~150MB) with two flat
// typed arrays: `start[nid]..start[nid+1]` slices `edges` into nid's incident
// edge ids. ~30MB for the NYC graph.

export interface NodeAdj {
  /** nNodes+1 offsets into `edges` (CSR row pointers). */
  start: Uint32Array;
  /** 2·nEdges edge ids, grouped by node. */
  edges: Uint32Array;
}

/** Build the CSR adjacency: each edge contributes its id to both endpoints. */
export function buildNodeAdj(d: GraphTopology): NodeAdj {
  const { nNodes, nEdges, ends } = d;
  const start = new Uint32Array(nNodes + 1);
  // Pass 1: count incident edges per node (degree).
  for (let i = 0; i < nEdges; i++) {
    start[ends[2 * i]]++;
    start[ends[2 * i + 1]]++;
  }
  // Prefix-sum the degrees into row-start offsets (start[nNodes] == 2·nEdges).
  let acc = 0;
  for (let n = 0; n <= nNodes; n++) {
    const deg = start[n];
    start[n] = acc;
    acc += deg;
  }
  // Pass 2: scatter edge ids into their rows, advancing a per-node cursor.
  const edges = new Uint32Array(acc);
  const cursor = start.slice(0, nNodes);
  for (let i = 0; i < nEdges; i++) {
    const a = ends[2 * i];
    const b = ends[2 * i + 1];
    edges[cursor[a]++] = i;
    edges[cursor[b]++] = i;
  }
  return { start, edges };
}

/** Edge ids incident to node `nid` as a cheap subarray view (no copy). */
export function adjEdgesOf(adj: NodeAdj, nid: number): Uint32Array {
  return adj.edges.subarray(adj.start[nid], adj.start[nid + 1]);
}

/** First incident edge id for `nid`, or null when the node has no edges. */
export function adjFirst(adj: NodeAdj | null, nid: number): number | null {
  if (!adj) return null;
  const s = adj.start[nid];
  return s < adj.start[nid + 1] ? adj.edges[s] : null;
}

/**
 * The SHORTEST incident edge id for `nid` (straight-line, cos-corrected), or
 * null when the node has no edges. This is the edge that stands in for a node
 * everywhere a node needs a block/vote target (docs §2.1): junction nodes own
 * a small disc block, and the shortest incident edge is the one whose midpoint
 * sits inside that disc — so node selections resolve to the junction's own
 * block instead of whichever full street block `adjFirst`'s array order lands
 * on (the mis-oriented-highlight bug).
 */
export function adjShortest(
  d: GraphTopology,
  adj: NodeAdj | null,
  nid: number,
): number | null {
  if (!adj) return null;
  const s = adj.start[nid];
  const e = adj.start[nid + 1];
  if (s >= e) return null;
  const cosLat = Math.cos((d.coords[2 * nid] / COORD_SCALE) * (Math.PI / 180));
  let best = -1;
  let bestD2 = Infinity;
  for (let k = s; k < e; k++) {
    const eid = adj.edges[k];
    const a = d.ends[2 * eid];
    const b = d.ends[2 * eid + 1];
    const dLat = d.coords[2 * a] - d.coords[2 * b];
    const dLon = (d.coords[2 * a + 1] - d.coords[2 * b + 1]) * cosLat;
    const d2 = dLat * dLat + dLon * dLon;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = eid;
    }
  }
  return best;
}

export const EARTH_RADIUS_M = 6_371_000;

/**
 * An edge's straight-line length in meters (fast equirectangular — exact
 * enough at city scale; graph edges are short and mostly straight). Shared by
 * the route-proposal length cap and anything else that budgets in meters.
 */
export function edgeLengthMeters(d: GraphTopology, edgeId: number): number {
  const a = d.ends[2 * edgeId];
  const b = d.ends[2 * edgeId + 1];
  const aLat = d.coords[2 * a] / COORD_SCALE;
  const aLon = d.coords[2 * a + 1] / COORD_SCALE;
  const bLat = d.coords[2 * b] / COORD_SCALE;
  const bLon = d.coords[2 * b + 1] / COORD_SCALE;
  const meanLatRad = (((aLat + bLat) / 2) * Math.PI) / 180;
  const dLat = ((bLat - aLat) * Math.PI) / 180;
  const dLon = (((bLon - aLon) * Math.PI) / 180) * Math.cos(meanLatRad);
  return EARTH_RADIUS_M * Math.hypot(dLat, dLon);
}

// ── Block index (CSR) ───────────────────────────────────────────────────────
// Blocks are the aggregation/interaction grain (docs/three-layer-model.md §2):
// every edge maps to at most one block via edgeBlockId. Same CSR layout as
// NodeAdj: `start[bid]..start[bid+1]` slices `edges` into block bid's edge ids.

export interface BlockIndex {
  /** nBlocks+1 offsets into `edges` (CSR row pointers). */
  start: Uint32Array;
  /** Edge ids grouped by block (ascending within each block). */
  edges: Uint32Array;
}

/**
 * Build the block → edge-ids CSR index. Returns null when the topology carries
 * no block mapping (maps without block artifacts run edge-as-singleton blocks
 * via blockKeyOf below). Edges with block id -1 (unmapped) are skipped.
 */
export function buildBlockIndex(d: GraphTopology): BlockIndex | null {
  const blockId = d.edgeBlockId;
  if (!blockId) return null;
  let nBlocks = d.nBlocks ?? 0;
  if (!nBlocks) {
    for (let i = 0; i < blockId.length; i++) {
      if (blockId[i] >= nBlocks) nBlocks = blockId[i] + 1;
    }
  }
  const start = new Uint32Array(nBlocks + 1);
  // Pass 1: count edges per block (skip -1 / out-of-range).
  for (let i = 0; i < blockId.length; i++) {
    const b = blockId[i];
    if (b >= 0 && b < nBlocks) start[b]++;
  }
  let acc = 0;
  for (let b = 0; b <= nBlocks; b++) {
    const count = start[b];
    start[b] = acc;
    acc += count;
  }
  // Pass 2: scatter edge ids (ascending edge order → ascending within a block).
  const edges = new Uint32Array(acc);
  const cursor = start.slice(0, nBlocks);
  for (let i = 0; i < blockId.length; i++) {
    const b = blockId[i];
    if (b >= 0 && b < nBlocks) edges[cursor[b]++] = i;
  }
  return { start, edges };
}

// ── Block-key convention ────────────────────────────────────────────────────
// A "block key" identifies the block an edge belongs to across BOTH regimes:
//   key >= 0        → a real block id (edgeBlockId value)
//   key <= -2       → a singleton block for edge (-key - 2): no artifacts, or
//                     the edge is unmapped (-1). -1 itself stays reserved for
//                     "unmapped" so it can never collide with a singleton key.

/** Block key for `edgeId`: its block id, or the singleton key -(edgeId + 2). */
export function blockKeyOf(d: GraphTopology, edgeId: number): number {
  const blockId = d.edgeBlockId;
  if (blockId) {
    const b = blockId[edgeId];
    if (b >= 0) return b;
  }
  return -(edgeId + 2);
}

/** All edge ids of a block key: CSR subarray for a real block (no copy), the
 *  decoded single edge for a singleton key. */
export function edgesOfBlockKey(
  d: GraphTopology,
  index: BlockIndex | null,
  key: number,
): ArrayLike<number> {
  if (key >= 0) {
    if (!index || key + 1 >= index.start.length) return [];
    return index.edges.subarray(index.start[key], index.start[key + 1]);
  }
  const edgeId = -key - 2;
  return edgeId >= 0 && edgeId < d.nEdges ? [edgeId] : [];
}

/** Distinct block keys touched by `edgeIds`, sorted ascending. */
export function touchedBlockKeys(d: GraphTopology, edgeIds: ArrayLike<number>): number[] {
  const keys = new Set<number>();
  for (let i = 0; i < edgeIds.length; i++) {
    keys.add(blockKeyOf(d, edgeIds[i]));
  }
  return [...keys].sort((a, b) => a - b);
}
