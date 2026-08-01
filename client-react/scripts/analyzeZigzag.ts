// One-off diagnosis: why is the nyc-proposals top route proposal (fa8b70e11,
// "Add traffic calming") a hairpin along the East River? Recomputes proposals
// from PROD data with the client's own builder and dumps the top corridor's
// geometry, per-edge nets, and block membership around the two parallel rails.
// Run: cd client-react && API_BASE=https://cityedit.org node_modules/.bin/vite-node scripts/analyzeZigzag.ts
import fs from "node:fs";
import {
  computeRouteProposals,
  type RouteProposal,
} from "../src/components/GraphLayer/routeProposals";
import {
  decodeTopologyBin,
  buildNodeAdj,
  buildBlockIndex,
  nodeLatLng,
} from "../src/components/GraphLayer/graphTopology";

const BASE = process.env.API_BASE ?? "https://cityedit.org";
const OUT = process.env.OUT ?? "/tmp/zigzag.json";
const slug = process.argv[2] ?? "nyc-proposals";

const cfg = await (await fetch(`${BASE}/api/maps/${slug}`)).json();
const mode = cfg.mode ?? "walk";
console.log(`map=${slug} mode=${mode} topProposalMinNet=${cfg.topProposalMinNet}`);
const topoBuf = await (await fetch(`${BASE}/api/graph-topology?map=${slug}&format=bin`)).arrayBuffer();
const topo = decodeTopologyBin(topoBuf);
console.log(`topo: ${topo.nNodes} nodes ${topo.nEdges} edges nBlocks=${topo.nBlocks}`);
const votes = await (await fetch(`${BASE}/api/graph-votes?map=${slug}&mode=${mode}`)).json();
console.log(`votes rev=${votes.rev} legend=${JSON.stringify(votes.vote_type_legend)}`);

const adj = buildNodeAdj(topo);
const blockIndex = buildBlockIndex(topo);
const kind = new Map<string, "route" | "point" | null>();
for (const vt of cfg.voteTypes ?? []) kind.set(vt.label, vt.pointType ?? null);
for (const vt of cfg.searchVoteTypes ?? []) {
  if (!kind.has(vt.label)) kind.set(vt.label, vt.pointType ?? null);
}
const minRouteScore = (cfg.topProposalMinNet ?? 100) + 1;
const ps = computeRouteProposals(topo, adj, votes, {
  blockIndex,
  minRouteScore,
  kindOf: (l: string) => kind.get(l) ?? null,
});
console.log(`\n${ps.length} proposals:`);
for (const p of ps) {
  const ghosts = p.waypointNodes.length - 2;
  console.log(
    `#${p.id} ${p.label} score=${p.score} edges=${p.edgeIds.length} blocks=${p.blocks.length}` +
      (ghosts > 0 ? ` GHOSTS=${ghosts}` : ""),
  );
}

const top = ps[0];
if (!top) process.exit(0);
const evt: [number, number, number][][] = votes.edge_vote_types ?? [];
const netOf = (eid: number, legendIdx: number): number => {
  let net = 0;
  for (const [li, up, down] of evt[eid] ?? []) if (li === legendIdx) net += up - down;
  return net;
};
const edgeInfo = (eid: number) => {
  const a = topo.ends[2 * eid], b = topo.ends[2 * eid + 1];
  return {
    eid,
    a: nodeLatLng(topo, a),
    b: nodeLatLng(topo, b),
    net: netOf(eid, top.legendIdx),
    block: topo.edgeBlockId ? topo.edgeBlockId[eid] : -1,
  };
};

// Every voted edge of the top proposal's type, everywhere on the map.
const votedEdges: ReturnType<typeof edgeInfo>[] = [];
for (let eid = 0; eid < topo.nEdges; eid++) {
  if (evt[eid] && netOf(eid, top.legendIdx) !== 0) votedEdges.push(edgeInfo(eid));
}

const dump = {
  top: {
    id: top.id,
    label: top.label,
    legendIdx: top.legendIdx,
    score: top.score,
    waypointCoords: top.waypointCoords,
    nEdges: top.edgeIds.length,
    nBlocks: top.blocks.length,
    segments: top.segments.map((seg) => seg.map(edgeInfo)),
    blockSizes: top.blocks.map((b) => b.length),
    blockEdgeCount: top.blockEdgeIds.length,
  },
  votedEdgesOfTopType: votedEdges,
};
fs.writeFileSync(OUT, JSON.stringify(dump, null, 1));
console.log(`\ntop=#${top.id} ${top.label} → dumped ${votedEdges.length} voted edges of that type to ${OUT}`);
