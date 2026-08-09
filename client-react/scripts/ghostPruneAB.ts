// Scratch A/B: route proposals computed WITHOUT the corridor-recovery oracle
// (strict shortest-ness only — the pre-prune behaviour) vs WITH it, over live
// local maps. Reports ghost counts, corridor reach, recompute time, and — for
// every ghost that survives — how much of its stretch plain routing hands back.
// Run: cd client-react && node_modules/.bin/vite-node scripts/ghostPruneAB.ts [slug ...]
import {
  computeRouteProposals,
  makeSegmentRecoveryCheck,
  type RouteProposal,
} from "../src/components/GraphLayer/routeProposals";
import { TOP_PROPOSAL_MIN_NET } from "../src/components/GraphLayer/topProposals";
import {
  decodeTopologyBin,
  buildNodeAdj,
  buildBlockIndex,
  edgeLengthMeters,
  type GraphTopology,
  type NodeAdj,
} from "../src/components/GraphLayer/graphTopology";

const BASE = process.env.API_BASE ?? "http://localhost:5001";
const BARS = [1, 0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.25, 0];

const lengthM = (topo: GraphTopology, eids: number[]) =>
  eids.reduce((s, e) => s + edgeLengthMeters(topo, e), 0);

/** Coarse "how much of this stretch does routing return?" — the highest bar the
 *  recovery oracle still clears, probed by re-running it at each threshold. */
function coverageBar(topo: GraphTopology, adj: NodeAdj, p: RouteProposal, k: number): number {
  const wpPos = [0];
  for (const seg of p.segments) wpPos.push(wpPos[wpPos.length - 1] + seg.length);
  const from = p.waypointNodes[k - 1];
  const to = p.waypointNodes[k + 1];
  const stretch = p.edgeIds.slice(wpPos[k - 1], wpPos[k + 1]);
  for (const bar of BARS) {
    if (makeSegmentRecoveryCheck(topo, adj, { minCoverage: bar })(from, to, stretch)) return bar;
  }
  return -1; // unreachable inside the corridor's own length
}

async function run(slug: string) {
  const cfg = await (await fetch(`${BASE}/api/maps/${slug}`)).json();
  const mode = cfg.mode ?? cfg.defaultMode ?? slug;
  const topoR = await fetch(`${BASE}/api/graph-topology?map=${slug}&format=bin`);
  if (!topoR.ok) { console.log(`${slug}: topology ${topoR.status}`); return; }
  const topo = decodeTopologyBin(await topoR.arrayBuffer());
  const votesR = await fetch(`${BASE}/api/graph-votes?map=${slug}&mode=${mode}`);
  if (!votesR.ok) { console.log(`${slug}: votes ${votesR.status}`); return; }
  const votes = await votesR.json();
  const adj = buildNodeAdj(topo);
  const blockIndex = buildBlockIndex(topo);
  const base = { blockIndex, minRouteScore: TOP_PROPOSAL_MIN_NET + 1 };

  const shot = (label: string, opts: Record<string, unknown>) => {
    const t0 = performance.now();
    const ps = computeRouteProposals(topo, adj, votes, { ...base, ...opts });
    const ms = performance.now() - t0;
    const ghosts = ps.reduce((s, p) => s + p.waypointNodes.length - 2, 0);
    const ghosted = ps.filter((p) => p.waypointNodes.length > 2).length;
    const blocks = ps.reduce((s, p) => s + p.blocks.length, 0);
    const meters = ps.reduce((s, p) => s + lengthM(topo, p.edgeIds), 0);
    console.log(
      `  ${label.padEnd(8)} proposals=${String(ps.length).padStart(3)}` +
      ` ghosts=${String(ghosts).padStart(3)} ghosted=${String(ghosted).padStart(3)}` +
      ` blocks=${String(blocks).padStart(5)} km=${(meters / 1000).toFixed(1).padStart(6)}` +
      ` ${ms.toFixed(0)}ms`,
    );
    return ps;
  };

  console.log(`\n=== ${slug} (mode=${mode}) ===`);
  const before = shot("strict", { segmentRecoveryCheck: null });
  shot("final", { maxRecoveryChecks: 0 });   // cleanup only, no reclaimed reach
  const after = shot("pruned", {});
  const byId = new Map(before.map((p) => [p.id, p]));
  console.log(`  same proposal ids: ${after.filter((p) => byId.has(p.id)).length}/${after.length}`);
  for (const p of after) {
    const b = byId.get(p.id);
    if (b && b.waypointNodes.length !== p.waypointNodes.length) {
      console.log(`   #${p.id} ${p.label}: waypoints ${b.waypointNodes.length} → ${p.waypointNodes.length}`);
    }
  }
  // What the surviving pins are actually holding down.
  const bars: number[] = [];
  for (const p of after) {
    for (let k = 1; k < p.waypointNodes.length - 1; k++) {
      bars.push(coverageBar(topo, adj, p, k));
    }
  }
  if (bars.length) {
    const hist = new Map<number, number>();
    for (const b of bars) hist.set(b, (hist.get(b) ?? 0) + 1);
    const rows = [...hist].sort((a, b) => b[0] - a[0])
      .map(([b, c]) => `${b < 0 ? "unreachable" : `≥${b}`}:${c}`);
    console.log(`  surviving-pin recovery: ${rows.join("  ")}`);
  }
}

const slugs = process.argv.slice(2).length ? process.argv.slice(2) : ["nyc-bikes"];
for (const s of slugs) await run(s);
