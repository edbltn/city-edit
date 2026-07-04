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
