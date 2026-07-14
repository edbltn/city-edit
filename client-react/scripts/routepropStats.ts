// Route-proposal diagnostics — run the REAL client clustering pipeline
// (computeRouteProposals) against a live local Flask's topology + votes and
// report length / straightness / block-count distributions for the ranked
// proposals. Used to tune the corridor length budget, the min-blocks gate,
// and the loop-back splitter against real (imported) vote data.
//
// Usage: cd client-react && node_modules/.bin/vite-node scripts/routepropStats.ts [slug ...]
//        (defaults to nyc-bikes nyc-walkways; Flask must be up on :5001)

import {
  computeRouteProposals,
  type RouteProposal,
} from "../src/components/GraphLayer/routeProposals";
import {
  decodeTopologyBin,
  buildNodeAdj,
  buildBlockIndex,
  edgeLengthMeters,
  nodeLatLng,
  type GraphTopology,
} from "../src/components/GraphLayer/graphTopology";

const BASE = process.env.API_BASE ?? "http://localhost:5001";

function haversineM(a: [number, number], b: [number, number]): number {
  const R = 6371000;
  const dLat = ((b[0] - a[0]) * Math.PI) / 180;
  const dLng = ((b[1] - a[1]) * Math.PI) / 180;
  const la1 = (a[0] * Math.PI) / 180;
  const la2 = (b[0] * Math.PI) / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function pathLenM(topo: GraphTopology, p: RouteProposal): number {
  return p.edgeIds.reduce((s, e) => s + edgeLengthMeters(topo, e), 0);
}

/** Endpoint displacement / arc length — 1.0 is a ruler, ~0.33 a U-turn. */
function straightness(topo: GraphTopology, p: RouteProposal): number {
  const len = pathLenM(topo, p);
  if (len <= 0) return 1;
  const d = haversineM(nodeLatLng(topo, p.anchors[0]), nodeLatLng(topo, p.anchors[1]));
  return d / len;
}

/** Worst window straightness: min over ~800m arc windows of crow/arc — catches
 *  a hairpin buried inside an otherwise straight corridor. */
function worstWindowStraightness(topo: GraphTopology, p: RouteProposal, windowM = 800): number {
  const nodes = p.edgeIds.length + 1;
  if (nodes < 3) return 1;
  // Rebuild the node chain from anchors[0] along edgeIds.
  const chain: number[] = [p.anchors[0]];
  let cur = p.anchors[0];
  for (const e of p.edgeIds) {
    const u = topo.ends[2 * e], v = topo.ends[2 * e + 1];
    cur = u === cur ? v : u;
    chain.push(cur);
  }
  const arc: number[] = [0];
  for (let i = 0; i < p.edgeIds.length; i++) arc.push(arc[i] + edgeLengthMeters(topo, p.edgeIds[i]));
  let worst = 1;
  let i = 0;
  for (let j = 1; j < chain.length; j++) {
    while (arc[j] - arc[i] > windowM && i < j - 1) i++;
    const span = arc[j] - arc[i];
    if (span < windowM * 0.6) continue; // window not full — skip edge effects
    const crow = haversineM(nodeLatLng(topo, chain[i]), nodeLatLng(topo, chain[j]));
    worst = Math.min(worst, crow / span);
  }
  return worst;
}

async function fetchMapCfg(slug: string) {
  const r = await fetch(`${BASE}/api/maps`);
  const d = await r.json();
  const m = (d.maps as any[]).find((x) => x.slug === slug);
  if (!m) throw new Error(`map ${slug} not found`);
  return m;
}

async function run(slug: string) {
  const cfg = await fetchMapCfg(slug);
  const mode = cfg.mode;
  const kinds = new Map<string, "point" | "route">();
  for (const vt of [...(cfg.voteTypes ?? []), ...(cfg.searchVoteTypes ?? [])]) {
    if (vt.label && (vt.kind === "point" || vt.kind === "route")) kinds.set(vt.label, vt.kind);
  }
  const kindOf = (label: string) => kinds.get(label) ?? null;

  const t0 = Date.now();
  const binR = await fetch(`${BASE}/api/graph-topology?map=${slug}&format=bin`);
  if (!binR.ok) throw new Error(`topology ${binR.status}`);
  const topo = decodeTopologyBin(await binR.arrayBuffer());
  const votesR = await fetch(`${BASE}/api/graph-votes?map=${slug}&mode=${mode}`);
  if (!votesR.ok) throw new Error(`votes ${votesR.status}`);
  const votes = await votesR.json();
  const adj = buildNodeAdj(topo);
  const blockIndex = buildBlockIndex(topo);
  const tFetch = Date.now() - t0;

  const t1 = Date.now();
  const props = computeRouteProposals(topo, adj, votes, { kindOf, blockIndex });
  const tCompute = Date.now() - t1;

  console.log(`\n=== ${slug} (mode=${mode}) — ${topo.nEdges} edges, fetch ${tFetch}ms, compute ${tCompute}ms ===`);
  console.log(`proposals: ${props.length}`);
  const rows = props.map((p, i) => {
    const len = pathLenM(topo, p);
    const st = straightness(topo, p);
    const ww = worstWindowStraightness(topo, p);
    return { i, id: p.id, label: p.label.slice(0, 28), score: p.score, edges: p.edgeIds.length, blocks: p.blocks.length, lenM: Math.round(len), straight: +st.toFixed(2), worstWin: +ww.toFixed(2) };
  });
  console.table(rows);
  const lens = rows.map((r) => r.lenM).sort((a, b) => a - b);
  const q = (arr: number[], f: number) => arr[Math.min(arr.length - 1, Math.floor(f * arr.length))];
  if (rows.length) {
    console.log(`lenM  min/med/p90/max: ${lens[0]} / ${q(lens, 0.5)} / ${q(lens, 0.9)} / ${lens[lens.length - 1]}`);
    console.log(`straightness < 0.55: ${rows.filter((r) => r.straight < 0.55).length}/${rows.length}   worstWindow < 0.4: ${rows.filter((r) => r.worstWin < 0.4).length}/${rows.length}`);
    console.log(`blocks < 5: ${rows.filter((r) => r.blocks < 5).length}/${rows.length}`);
  }
}

const slugs = process.argv.slice(2).filter((a) => !a.startsWith("-"));
for (const slug of slugs.length ? slugs : ["nyc-bikes", "nyc-walkways"]) {
  await run(slug);
}
