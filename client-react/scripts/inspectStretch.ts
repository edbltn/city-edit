// Scratch: for a start/end pair, show what the app would select — the routed
// edges, the blocks they map to, and where the chain has holes.
// Run: cd client-react && node_modules/.bin/vite-node scripts/inspectStretch.ts <slug> <lat,lng> <lat,lng>
import {
  decodeTopologyBin,
  blockKeyOf,
  buildBlockIndex,
  edgesOfBlockKey,
  nodeLatLng,
  edgeLengthMeters,
} from "../src/components/GraphLayer/graphTopology";

const BASE = process.env.API_BASE ?? "http://localhost:5001";
const [slug, aStr, bStr] = process.argv.slice(2);
const [aLat, aLng] = aStr.split(",").map(Number);
const [bLat, bLng] = bStr.split(",").map(Number);

const topo = decodeTopologyBin(
  await (await fetch(`${BASE}/api/graph-topology?map=${slug}&format=bin`)).arrayBuffer(),
);
console.log(`topology: ${topo.nNodes} nodes, ${topo.nEdges} edges, nBlocks=${topo.nBlocks}, edgeBlockId=${topo.edgeBlockId ? "present" : "MISSING"}`);

const r = await (await fetch(`${BASE}/api/routes`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ map: slug, start: [aLat, aLng], end: [bLat, bLng] }),
})).json();
const edgeIds: number[] = r.edge_ids ?? [];
console.log(`route: ${r.route?.distance?.toFixed(0)}m, ${(r.route?.osm_node_ids ?? []).length} osm nodes → ${edgeIds.length} edge ids`);

const blockIndex = buildBlockIndex(topo);
let withBlock = 0, without = 0, routedM = 0;
const keys = new Set<number>();
for (const e of edgeIds) {
  const key = blockKeyOf(topo, e);
  const raw = topo.edgeBlockId ? topo.edgeBlockId[e] : -1;
  const members = edgesOfBlockKey(topo, blockIndex, key);
  const u = topo.ends[2 * e], v = topo.ends[2 * e + 1];
  const [uLat, uLng] = nodeLatLng(topo, u);
  const len = edgeLengthMeters(topo, e);
  routedM += len;
  if (raw >= 0) { withBlock++; keys.add(key); } else without++;
  console.log(
    `  e${String(e).padStart(8)} ${len.toFixed(0).padStart(4)}m` +
    ` block=${String(raw).padStart(8)} members=${String(members.length).padStart(3)}` +
    `  @${uLat.toFixed(5)},${uLng.toFixed(5)}`,
  );
}
console.log(`\nedges with a block: ${withBlock}, without: ${without}; distinct blocks: ${keys.size}; routed edge length ${routedM.toFixed(0)}m of ${r.route?.distance?.toFixed(0)}m`);

// Does the votable graph even HAVE this path? Walk the routed geometry and
// report, per sample point, how far the nearest topology node sits.
const coords: [number, number][] = r.route?.geometry?.coordinates ?? [];
console.log(`\ngeometry: ${coords.length} points — nearest topology node per sample:`);
const kx = 111320 * Math.cos((aLat * Math.PI) / 180), ky = 110574;
const nearest = (lat: number, lng: number) => {
  let bd = Infinity, bi = -1;
  for (let n = 0; n < topo.nNodes; n++) {
    const [nLat, nLng] = nodeLatLng(topo, n);
    if (Math.abs(nLat - lat) > 0.002 || Math.abs(nLng - lng) > 0.002) continue;
    const d = Math.hypot((nLat - lat) * ky, (nLng - lng) * kx);
    if (d < bd) { bd = d; bi = n; }
  }
  return { d: bd, n: bi };
};
const step = Math.max(1, Math.floor(coords.length / 24));
for (let i = 0; i < coords.length; i += step) {
  const [lng, lat] = coords[i];
  const { d, n } = nearest(lat, lng);
  // A few metres of offset means the graph has a PARALLEL feature here, not the
  // way OSRM used — the signature of an extract the topology was built without.
  console.log(
    `  ${String(i).padStart(3)} ${lat.toFixed(5)},${lng.toFixed(5)}` +
    `  nearest node ${String(n).padStart(8)} at ${d.toFixed(1).padStart(6)}m` +
    `${d > 3 ? "   <-- not OSRM's own node" : ""}`,
  );
}
