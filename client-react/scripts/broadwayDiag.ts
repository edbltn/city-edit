// Diagnostic: why isn't Broadway (bright on the heatmap) a top route proposal?
// Traces Broadway's edges through every RBTP pipeline stage: raw votes → nets
// → MIN_NET subgraph → components → peeled paths → final ranked proposals.
//
// Usage: cd client-react && node_modules/.bin/vite-node scripts/broadwayDiag.ts [slug]

import {
  computeRouteProposals,
  MIN_NET,
  type RouteProposal,
} from "../src/components/GraphLayer/routeProposals";
import {
  decodeTopologyBin,
  buildNodeAdj,
  buildBlockIndex,
  adjEdgesOf,
  nodeLatLng,
  type GraphTopology,
} from "../src/components/GraphLayer/graphTopology";

const BASE = process.env.API_BASE ?? "http://localhost:5001";
const SLUG = process.argv[2] ?? "nyc-bikes";

// Broadway, Bowling Green → Columbia (approx waypoints).
const BROADWAY: [number, number][] = [
  [40.7047, -74.0132], [40.7105, -74.0100], [40.7145, -74.0067],
  [40.7193, -74.0003], [40.7255, -73.9967], [40.7300, -73.9938],
  [40.7359, -73.9908], [40.7411, -73.9897], [40.7484, -73.9877],
  [40.7530, -73.9870], [40.7580, -73.9855], [40.7630, -73.9838],
  [40.7679, -73.9819], [40.7736, -73.9822], [40.7787, -73.9819],
  [40.7833, -73.9799], [40.7885, -73.9765], [40.7942, -73.9722],
  [40.8005, -73.9680], [40.8075, -73.9641],
];

const KY = 110574;
const kx = 111320 * Math.cos((40.75 * Math.PI) / 180);
const toXY = ([lat, lng]: [number, number]) => [lng * kx, lat * KY] as [number, number];
const BXY = BROADWAY.map(toXY);

/** Distance (m) from point p to segment ab, plus the segment's unit direction. */
function segDist(p: [number, number], a: [number, number], b: [number, number]) {
  const vx = b[0] - a[0], vy = b[1] - a[1];
  const len = Math.hypot(vx, vy);
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / (len * len)));
  const dx = p[0] - (a[0] + t * vx), dy = p[1] - (a[1] + t * vy);
  return { d: Math.hypot(dx, dy), ux: vx / len, uy: vy / len };
}

function main2(topo: GraphTopology, votes: any) {
  const { nEdges, ends } = topo;
  const mid = (e: number): [number, number] => {
    const a = toXY(nodeLatLng(topo, ends[2 * e]));
    const b = toXY(nodeLatLng(topo, ends[2 * e + 1]));
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  };

  // ── Stage 0: which edges ARE Broadway (≤30m from the polyline, parallel) ──
  const bway: number[] = [];
  for (let e = 0; e < nEdges; e++) {
    const u = ends[2 * e], v = ends[2 * e + 1];
    if (u === v) continue;
    const m = mid(e);
    let best = { d: Infinity, ux: 0, uy: 0 };
    for (let i = 0; i < BXY.length - 1; i++) {
      const r = segDist(m, BXY[i], BXY[i + 1]);
      if (r.d < best.d) best = r;
    }
    if (best.d > 30) continue;
    const a = toXY(nodeLatLng(topo, u));
    const b = toXY(nodeLatLng(topo, v));
    const el = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (el < 1) continue;
    const dot = Math.abs(((b[0] - a[0]) / el) * best.ux + ((b[1] - a[1]) / el) * best.uy);
    if (dot >= 0.7) bway.push(e);
  }
  const bwaySet = new Set(bway);
  console.log(`Broadway edges (≤30m, parallel): ${bway.length}`);

  // ── Stage 1: votes on those edges, per type ──
  const legend: string[] = votes.vote_type_legend ?? [];
  const evt: [number, number, number][][] = votes.edge_vote_types ?? [];
  type Agg = { edges: number; up: number; dn: number; netPos: number; netZeroWithVotes: number };
  const perType = new Map<number, Agg>();
  for (const e of bway) {
    const pairs = evt[e];
    if (!pairs) continue;
    for (const [t, up, dn] of pairs) {
      let a = perType.get(t);
      if (!a) perType.set(t, (a = { edges: 0, up: 0, dn: 0, netPos: 0, netZeroWithVotes: 0 }));
      a.edges++;
      a.up += up;
      a.dn += dn;
      if (up - dn >= MIN_NET) a.netPos++;
      else if (up + dn > 0) a.netZeroWithVotes++;
    }
  }
  console.log("\nPer vote type on Broadway edges:");
  const rows = [...perType.entries()]
    .map(([t, a]) => ({
      type: legend[t]?.slice(0, 30), t,
      votedEdges: a.edges, up: a.up, down: a.dn, net: a.up - a.dn,
      "net≥1 edges": a.netPos, "net≤0 (voted)": a.netZeroWithVotes,
    }))
    .sort((x, y) => (y.up + y.down) - (x.up + x.down));
  console.table(rows.slice(0, 12));

  // ── Stage 2: contiguity of the net-positive subgraph along Broadway ──
  // For the top-activity types: order Broadway edges by projection along the
  // polyline and measure runs of net≥1 vs gaps.
  const proj = (e: number) => {
    const m = mid(e);
    let best = Infinity, s = 0, acc = 0;
    for (let i = 0; i < BXY.length - 1; i++) {
      const r = segDist(m, BXY[i], BXY[i + 1]);
      const segLen = Math.hypot(BXY[i + 1][0] - BXY[i][0], BXY[i + 1][1] - BXY[i][1]);
      if (r.d < best) {
        best = r.d;
        const vx = BXY[i + 1][0] - BXY[i][0], vy = BXY[i + 1][1] - BXY[i][1];
        const t = Math.max(0, Math.min(1, ((m[0] - BXY[i][0]) * vx + (m[1] - BXY[i][1]) * vy) / (segLen * segLen)));
        s = acc + t * segLen;
      }
      acc += segLen;
    }
    return s;
  };
  const ordered = [...bway].sort((a, b) => proj(a) - proj(b));
  for (const { t, type } of rows.slice(0, 4)) {
    const marks = ordered.map((e) => {
      const pair = (evt[e] ?? []).find((p) => p[0] === t);
      if (!pair) return ".";
      return pair[1] - pair[2] >= MIN_NET ? "#" : "x"; // # net≥1, x voted-but-net≤0
    });
    // Compress: longest # run and total # vs x.
    const s = marks.join("");
    const runs = s.split(/[^#]+/).filter(Boolean).map((r) => r.length).sort((a, b) => b - a);
    console.log(`\n[${type}] along Broadway (${ordered.length} edges): net≥1=${(s.match(/#/g) ?? []).length}, voted-net≤0=${(s.match(/x/g) ?? []).length}, unvoted=${(s.match(/\./g) ?? []).length}`);
    console.log(`  longest net≥1 runs: ${runs.slice(0, 8).join(", ")}`);
    console.log(`  map: ${s.slice(0, 400)}`);
  }

  // ── Stage 3: do the FINAL proposals touch Broadway? ──
  const adj = buildNodeAdj(topo);
  const blockIndex = buildBlockIndex(topo);
  const t0 = Date.now();
  const ps = computeRouteProposals(topo, adj, votes, { blockIndex, limit: 100 });
  console.log(`\ncomputeRouteProposals limit=100: ${ps.length} proposals in ${Date.now() - t0}ms`);
  const touching = ps
    .map((p, rank) => ({ rank, p, n: p.edgeIds.filter((e) => bwaySet.has(e)).length }))
    .filter((r) => r.n > 0);
  console.log(`proposals touching Broadway: ${touching.length}`);
  for (const r of touching.slice(0, 10)) {
    console.log(`  #${r.rank} ${r.p.label.slice(0, 28)} score=${r.p.score} edges=${r.p.edgeIds.length} onBway=${r.n}`);
  }

  // ── Stage 4: is Broadway's net-positive fabric connected to a monster
  // component (which would explain peel exhaustion)? For the top type, BFS the
  // component containing the most Broadway edges and report its size + the
  // component's peel-relevant numbers. ──
  const topT = rows[0].t;
  const nets = new Map<number, number>();
  for (let e = 0; e < nEdges; e++) {
    const pairs = evt[e];
    if (!pairs) continue;
    for (const [t, up, dn] of pairs) if (t === topT) nets.set(e, (nets.get(e) ?? 0) + up - dn);
  }
  const eligible = new Set<number>();
  for (const [e, w] of nets) if (w >= MIN_NET && ends[2 * e] !== ends[2 * e + 1]) eligible.add(e);
  const nodeSeen = new Set<number>();
  let bestComp: { nodes: number; edges: number; bwayEdges: number; weight: number } | null = null;
  for (const seed of eligible) {
    const su = ends[2 * seed];
    if (nodeSeen.has(su)) continue;
    const queue = [su];
    nodeSeen.add(su);
    let compEdges = 0, compBway = 0, compW = 0;
    const seenE = new Set<number>();
    for (let h = 0; h < queue.length; h++) {
      const nid = queue[h];
      const row = adjEdgesOf(adj, nid);
      for (let i = 0; i < row.length; i++) {
        const e = row[i];
        if (!eligible.has(e) || seenE.has(e)) continue;
        seenE.add(e);
        compEdges++;
        compW += nets.get(e) ?? 0;
        if (bwaySet.has(e)) compBway++;
        const u = ends[2 * e], v = ends[2 * e + 1];
        const nxt = u === nid ? v : u;
        if (!nodeSeen.has(nxt)) {
          nodeSeen.add(nxt);
          queue.push(nxt);
        }
      }
    }
    if (compBway > 0 && (!bestComp || compBway > bestComp.bwayEdges)) {
      bestComp = { nodes: queue.length, edges: compEdges, bwayEdges: compBway, weight: compW };
    }
  }
  console.log(`\n[${legend[topT]}] component holding the most Broadway edges:`, bestComp);
  console.log(`(eligible net≥1 edges for this type overall: ${eligible.size})`);
}

const binR = await fetch(`${BASE}/api/graph-topology?map=${SLUG}&format=bin`);
const topo = decodeTopologyBin(await binR.arrayBuffer());
const cfg = await (await fetch(`${BASE}/api/maps`)).json();
const mode = cfg.maps.find((m: any) => m.slug === SLUG)?.mode ?? "walk";
const votes = await (await fetch(`${BASE}/api/graph-votes?map=${SLUG}&mode=${mode}`)).json();
main2(topo, votes);
