// Both proposal families for one map, run through the REAL client pipelines
// against a live local Flask — the point family (topProposals.selectTopProposals)
// and the corridor family (routeProposals.createRouteProposalJob), with exactly
// the options GraphLayer passes them.
//
// Written to diagnose "two tied pins, kilometres apart, only one shows": it
// prints the per-type candidate set BEFORE the spacing pass alongside the
// finished winners, which is where that loss happens.
//
// Usage: cd client-react && node_modules/.bin/vite-node scripts/tacticalDiag.ts <slug>

import {
  candidatesForGraph, selectTopProposalsFrom, TOP_PROPOSALS_PER_TYPE,
  TOP_PROPOSAL_MIN_SPACING_M, TOP_PROPOSAL_MIN_NET, ROUTE_PROPOSAL_MIN_NET,
  edgeMidpointResolver,
} from "../src/components/GraphLayer/topProposals";
import { createRouteProposalJob, rankRouteProposals } from "../src/components/GraphLayer/routeProposals";
import {
  decodeTopologyBin, buildNodeAdj, buildBlockIndex, blockKeyOf, edgeLengthMeters,
} from "../src/components/GraphLayer/graphTopology";
// spatialLookup.ts pulls in Leaflet (no `window` under vite-node), so the pin
// cap is mirrored here rather than imported. Keep in step with TOP_PROPOSAL_LIMIT.
const TOP_PROPOSAL_LIMIT = 50;

const BASE = process.env.API_BASE ?? "http://localhost:5001";
const slug = process.argv[2] ?? "nyc-tactical";

function metersBetween(a: [number, number], b: [number, number]): number {
  const meanLatRad = (((a[0] + b[0]) / 2) * Math.PI) / 180;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLng = (((b[1] - a[1]) * Math.PI) / 180) * Math.cos(meanLatRad);
  return 6_371_000 * Math.hypot(dLat, dLng);
}

const cfgAll = await (await fetch(`${BASE}/api/maps`)).json();
const cfg = (cfgAll.maps as any[]).find((m) => m.slug === slug);
if (!cfg) throw new Error(`map ${slug} not found`);

const kinds = new Map<string, "point" | "route">();
for (const vt of [...(cfg.voteTypes ?? []), ...(cfg.searchVoteTypes ?? [])]) {
  if (vt.label && (vt.pointType === "point" || vt.pointType === "route")) kinds.set(vt.label, vt.pointType);
}
const kindOf = (label: string) => kinds.get(label) ?? null;

const topo = decodeTopologyBin(await (await fetch(`${BASE}/api/graph-topology?map=${slug}&format=bin`)).arrayBuffer());
const votes = await (await fetch(`${BASE}/api/graph-votes?map=${slug}&mode=${cfg.mode}`)).json();
const data = { ...votes, ...topo } as any;

// MIN_NET=<n> previews what a map-level `topProposalMinNet` override would do
// to both families, without having to set one on the map first.
const override = process.env.MIN_NET !== undefined ? Number(process.env.MIN_NET) : cfg.topProposalMinNet;
const pinMinNet = override ?? TOP_PROPOSAL_MIN_NET;
const routeMinNet = override ?? ROUTE_PROPOSAL_MIN_NET;
console.log(`\n=== ${slug} (mode=${cfg.mode}) — ${topo.nEdges} edges, ${votes.n_blocks} blocks`);
console.log(`pin floor >${pinMinNet}, corridor min score ${routeMinNet + 1}, `
  + `perType ${TOP_PROPOSALS_PER_TYPE}, spacing ${TOP_PROPOSAL_MIN_SPACING_M}m, limit ${TOP_PROPOSAL_LIMIT}`);
console.log(`legend: ${JSON.stringify(votes.vote_type_legend)}`);

// ── point family ──────────────────────────────────────────────────────────
const posOf = edgeMidpointResolver(data);
const candidates = candidatesForGraph(data, TOP_PROPOSALS_PER_TYPE, kindOf, pinMinNet);
console.log(`\nPBTP candidates (step 1, per type ≤${TOP_PROPOSALS_PER_TYPE}): ${candidates.length}`);
for (const c of candidates) {
  const p = posOf(c.edgeIdx);
  console.log(`  ${c.label}  net=${c.count}  edge=${c.edgeIdx}  block=${blockKeyOf(data, c.edgeIdx)}  `
    + `@ ${p ? p.map((v) => v.toFixed(5)).join(",") : "?"}`);
}
// How far apart are the candidates of each type?
const byType = new Map<number, typeof candidates>();
for (const c of candidates) byType.set(c.legendIdx, [...(byType.get(c.legendIdx) ?? []), c]);
for (const [li, cs] of byType) {
  let dmax = 0;
  for (let i = 0; i < cs.length; i++) for (let j = i + 1; j < cs.length; j++) {
    const a = posOf(cs[i].edgeIdx), b = posOf(cs[j].edgeIdx);
    if (a && b) dmax = Math.max(dmax, metersBetween(a, b));
  }
  console.log(`  → type "${votes.vote_type_legend[li]}": ${cs.length} candidates, max separation ${dmax.toFixed(0)} m`);
}

const winners = selectTopProposalsFrom(data, candidates, 0, TOP_PROPOSAL_LIMIT, TOP_PROPOSAL_MIN_SPACING_M);
console.log(`\nPBTP winners (steps 2–5): ${winners.length}`);
for (const w of winners) {
  const p = posOf(w.edgeIdx);
  console.log(`  ${w.label}  net=${w.count}  edge=${w.edgeIdx}  @ ${p ? p.map((v) => v.toFixed(5)).join(",") : "?"}`);
}

// ── corridor family ───────────────────────────────────────────────────────
const adj = buildNodeAdj(topo);
const job = createRouteProposalJob(topo, adj, data, {
  kindOf, blockIndex: buildBlockIndex(topo), minRouteScore: routeMinNet + 1,
});
console.log(`\nRBTP eligible types: ${job.types.map((li) => votes.vote_type_legend[li]).join(", ") || "(none)"}`);
const perType = job.types.map((li) => job.step(li));
const routes = rankRouteProposals(perType);
console.log(`RBTP corridors: ${routes.length}`);
for (const r of routes) {
  const len = r.edgeIds.reduce((s, e) => s + edgeLengthMeters(topo, e), 0);
  console.log(`  ${r.label}  score=${r.score}  edges=${r.edgeIds.length}  blocks=${r.blocks.length}  len=${Math.round(len)}m`);
}
